#!/usr/bin/env python3
"""
nanod-bridge.py — Nano_D++ macOS serial bridge (config-driven)

Reads ~/.config/nanod/bridge.json at startup and re-reads it whenever
the file's mtime changes (polled each loop iteration; no inotify needed).

Config contract (app writes, bridge reads):
  {
    "buttons": ["playpause","next","previous","mute"],  // index 0-3 = device buttons A-D
    "knobVolume": true,    // knob position -> macOS volume
    "artwork": false       // push album art to device screen (handled by artwork agent)
  }

Allowed button action strings:
  "playpause"  — AppleScript playpause on running Kaset/Spotify/Music
  "next"       — next track
  "previous"   — previous track
  "mute"       — toggle output mute
  "volup"      — output volume +6
  "voldown"    — output volume -6
  "none"       — do nothing
  "shortcut:<Name>" — run a named macOS Shortcut via `shortcuts run`
"""

import fcntl
import glob
import json
import os
import subprocess
import sys
import time

# Artwork module — no-PSRAM RGB565 streaming + album-color ring glow.
# Optional; gracefully absent if Pillow/numpy or the module aren't present.
try:
    from artstream import push_artstream as _push_artwork_impl
    _ARTWORK_AVAILABLE = True
except ImportError:
    _ARTWORK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.config/nanod/bridge.json")

DEFAULT_CONFIG = {
    "buttons": ["playpause", "next", "previous", "mute"],
    "knobVolume": True,
    "artwork": False,
    "player": "Music",   # now-playing source: "Music" | "Spotify" | "<AppName>" (best-effort)
}

_config = dict(DEFAULT_CONFIG)
_config_mtime: float = 0.0  # sentinel: force load on first call


def _load_config() -> None:
    global _config, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except FileNotFoundError:
        # File absent — use defaults, reset mtime so we retry next cycle
        _config = dict(DEFAULT_CONFIG)
        _config_mtime = 0.0
        return

    if mtime == _config_mtime:
        return  # unchanged

    try:
        with open(CONFIG_PATH, "r") as fh:
            raw = json.load(fh)
        cfg: dict = dict(DEFAULT_CONFIG)
        if isinstance(raw.get("buttons"), list):
            cfg["buttons"] = raw["buttons"]
        if isinstance(raw.get("knobVolume"), bool):
            cfg["knobVolume"] = raw["knobVolume"]
        if isinstance(raw.get("artwork"), bool):
            cfg["artwork"] = raw["artwork"]
        if isinstance(raw.get("player"), str) and raw["player"]:
            cfg["player"] = raw["player"]
        _config = cfg
        _config_mtime = mtime
        print(f"[config] loaded: {cfg}", flush=True)
    except Exception as exc:
        print(f"[config] parse error ({exc}), keeping previous config", flush=True)


def get_config() -> dict:
    _load_config()
    return _config


# ---------------------------------------------------------------------------
# osascript helpers (all non-blocking via Popen)
# ---------------------------------------------------------------------------

def osa(script: str) -> None:
    """Fire-and-forget osascript."""
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_VOLCTL_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "volctl")
_volctl = None


def set_volume(vol: int) -> None:
    """Set output volume in ~1 ms via the persistent CoreAudio helper (real-time
    knob feel — no per-change osascript spawn)."""
    global _volctl
    if _volctl is None or _volctl.poll() is not None:
        try:
            _volctl = subprocess.Popen([_VOLCTL_BIN], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            print(f"[vol] helper unavailable ({exc}); falling back to osascript", flush=True)
            osa(f"set volume output volume {vol}")
            return
    try:
        _volctl.stdin.write(f"{vol}\n".encode())
        _volctl.stdin.flush()
    except (BrokenPipeError, OSError):
        _volctl = None  # respawn next call


def media(verb: str) -> None:
    """
    Send an AppleScript verb to whichever of Kaset / Spotify / Music is running.
    Kaset supports: playpause, next track, previous track
    (No track-property or artwork access on Kaset.)
    """
    for app in ("Kaset", "Spotify", "Music"):
        osa(f'if application "{app}" is running then tell application "{app}" to {verb}')


# ---------------------------------------------------------------------------
# Button action dispatcher
# ---------------------------------------------------------------------------

def run_button_action(action: str) -> None:
    """Dispatch a single button action string. Non-blocking."""
    if not action or action == "none":
        return
    if action == "playpause":
        media("playpause")
    elif action == "next":
        media("next track")
    elif action == "previous":
        media("previous track")
    elif action == "mute":
        osa("set volume output muted (not (output muted of (get volume settings)))")
    elif action == "volup":
        osa(
            "set volume output volume "
            "(min 100 ((output volume of (get volume settings)) + 6))"
        )
    elif action == "voldown":
        osa(
            "set volume output volume "
            "(max 0 ((output volume of (get volume settings)) - 6))"
        )
    elif action.startswith("shortcut:"):
        shortcut_name = action[len("shortcut:"):]
        subprocess.Popen(
            ["shortcuts", "run", shortcut_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        print(f"[button] unknown action: {action!r}", flush=True)


def on_key(index: int) -> None:
    cfg = get_config()
    buttons = cfg.get("buttons", DEFAULT_CONFIG["buttons"])
    if not isinstance(buttons, list) or index < 0 or index >= len(buttons):
        print(f"[button] index {index} out of range (buttons len={len(buttons)})", flush=True)
        return
    action = buttons[index]
    print(f"[button] key={index} action={action!r}", flush=True)
    run_button_action(action)


# ---------------------------------------------------------------------------
# Artwork hook — wired to artwork.py
# ---------------------------------------------------------------------------

_artwork_state: dict = {}          # track-ID cache persisted across calls
_last_art_check: float = 0.0
_ART_INTERVAL = 2.0                # seconds between now-playing polls


def _artwork_send_line(line: str) -> None:
    """Write one JSON line to the active serial port (raw bytes)."""
    if _active_port is None:
        return
    try:
        _active_port.write((line + "\n").encode())
    except OSError as exc:
        print(f"[art] serial write error: {exc}", flush=True)


def maybe_push_artwork(now: float) -> None:
    """
    Called each main-loop tick.  Polls now-playing every _ART_INTERVAL seconds
    and pushes artwork to the device when the track changes.
    """
    global _last_art_check
    if not get_config().get("artwork", False):
        return
    if not _ARTWORK_AVAILABLE:
        return
    if _active_port is None:
        return
    if now - _last_art_check < _ART_INTERVAL:
        return
    _last_art_check = now
    try:
        # push_artstream drives the port directly (lock-step upload reading acks).
        # The main loop is paused inside this call, so there's no read contention;
        # knob/button events during the ~13 s upload are dropped (Phase 3 fixes speed).
        t0 = time.time()
        if _push_artwork_impl(_active_port, last=_artwork_state,
                              player=get_config().get("player", "Music")):
            print(f"[art] cover streamed in {time.time() - t0:.1f}s", flush=True)
    except Exception as exc:
        print(f"[art] push error: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Serial port helpers
# ---------------------------------------------------------------------------

def find_port() -> str | None:
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    return ports[0] if ports else None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global _active_port

    print("nanod-bridge v1 (config-driven, serial)", flush=True)
    _load_config()  # eager first load

    # Volume throttle state
    target_vol: int | None = None
    last_set: float = 0.0
    last_vol: int | None = None
    THROTTLE = 0.01  # CoreAudio helper is ~1 ms, so we can track the knob closely

    _active_port = None  # used by push_artwork stub

    while True:
        port = find_port()
        if not port:
            time.sleep(1.0)
            continue

        try:
            fh = open(port, "r+b", buffering=0)   # r+b: read events AND write art/ring
            fcntl.fcntl(fh, fcntl.F_SETFL, os.O_NONBLOCK)
            _active_port = fh
            print(f"[serial] connected: {port}", flush=True)
            buf = b""

            while True:
                # Check config for changes each iteration (cheap mtime poll)
                _load_config()

                # Port disappeared (device power-cycled / re-enumerated)
                if not os.path.exists(port):
                    break

                try:
                    data = fh.read(8192)
                except OSError:
                    break

                if data:
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if b"kd" in line:
                            print(
                                f"[raw] {line[:80].decode('utf-8', 'replace')}",
                                flush=True,
                            )
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue

                        # Knob position -> volume
                        if "p" in obj and isinstance(obj["p"], (int, float)):
                            if get_config().get("knobVolume", True):
                                target_vol = max(0, min(100, int(obj["p"])))

                        # Button down
                        if "kd" in obj and isinstance(obj["kd"], int):
                            on_key(obj["kd"])
                else:
                    time.sleep(0.004)

                # Apply pending volume change (throttled)
                now = time.time()
                if (
                    target_vol is not None
                    and target_vol != last_vol
                    and now - last_set > THROTTLE
                ):
                    set_volume(target_vol)   # ~1 ms CoreAudio — real-time knob
                    last_vol = target_vol
                    last_set = now

                # Album-art push (every 2 s when config.artwork = true)
                maybe_push_artwork(now)

            try:
                fh.close()
            except Exception:
                pass
            _active_port = None

        except Exception as exc:
            print(f"[serial] reconnecting ({exc})", flush=True)
            _active_port = None

        time.sleep(1.0)


if __name__ == "__main__":
    main()

"""
artwork.py — Album-art pusher for Nano_D++ 240x240 round screen.

Public API
----------
push_artwork(send_line_fn, last={})
    Checks now-playing track; if the track has changed since the last call,
    fetches artwork, resizes to 240x240 PNG (≤60 KB), and pushes it to the
    device via the sprite protocol using send_line_fn.

    send_line_fn(line: str) — callable that writes one UTF-8 line to the
        device serial/TCP connection (must NOT append its own newline; the
        bridge already does that).

    last — mutable dict used as persistent state across calls (caller may
        supply their own dict to share state, or omit to use the module-level
        default).

Integration
-----------
See module docstring section "Bridge integration" below.

Sprite protocol recap (sent as JSON lines)
------------------------------------------
{"sprite":{"op":"begin","name":"art.png","size":<total_bytes>}}
{"op":"data","seq":0,"data":"<base64_chunk>"}   # ≤512 B decoded per chunk
...
{"op":"end","name":"art.png","crc32":<uint32>}
{"op":"select","name":"art.png"}
"""

import base64
import json
import os
import subprocess
import tempfile
import zlib

# Path to the compiled MediaRemote helper, located next to this file.
_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nowplaying-art")

# Maximum encoded size accepted by the device (64 KB raw).
_MAX_SIZE = 63 * 1024          # 60 KB — comfortable margin below 64 KB limit
_TARGET_DIM = 240              # device screen width/height in px
_CHUNK_BYTES = 512             # raw bytes per base64 chunk (~682 b64 chars)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_helper():
    """Run nowplaying-art; return (track_id, raw_art_path) or (None, None)."""
    try:
        result = subprocess.run(
            [_HELPER],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None

    if result.returncode != 0 or not result.stdout.strip():
        return None, None

    # Output format: <trackID>\t<artworkPath>
    # trackID may itself contain tabs (title+artist+album), path is always last.
    parts = result.stdout.rstrip("\n").rsplit("\t", 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def _resize_to_png(src_path):
    """
    Use sips to center-crop and resize src_path to 240x240, writing to a new
    temp PNG.  Returns the temp path, or None on failure.  Caller must delete.
    """
    fd, dst_path = tempfile.mkstemp(suffix=".png", prefix="nanod-art-resized-")
    os.close(fd)
    try:
        # sips: resampleHeightWidthMax crops the longest side first.
        # We pad/crop to square then resize — sips does this in two passes:
        #   1. fit into 240x240 box (letterbox, no crop)  [--resampleHeightWidth]
        # For a perfect fill+crop use --cropToHeightWidth after resample.
        # Two-pass approach:
        #   pass 1 — resize so shortest side = 240 (upscale allowed)
        #   pass 2 — crop center to 240x240

        # Pass 1: resize shortest side to 240
        r1 = subprocess.run(
            [
                "sips",
                "--resampleHeightWidthMax", str(_TARGET_DIM),
                src_path,
                "--out", dst_path,
            ],
            capture_output=True, timeout=10,
        )
        if r1.returncode != 0:
            return None

        # After pass 1 the image might be e.g. 240×300 or 320×240.
        # Pass 2: crop to 240×240 from center.
        r2 = subprocess.run(
            [
                "sips",
                "--cropToHeightWidth", str(_TARGET_DIM), str(_TARGET_DIM),
                dst_path,
            ],
            capture_output=True, timeout=10,
        )
        if r2.returncode != 0:
            return None

        # Convert to PNG (sips output format follows the --out extension,
        # but the source might be JPEG; force PNG explicitly).
        r3 = subprocess.run(
            [
                "sips",
                "-s", "format", "png",
                dst_path,
                "--out", dst_path,
            ],
            capture_output=True, timeout=10,
        )
        if r3.returncode != 0:
            return None

        return dst_path

    except (subprocess.TimeoutExpired, OSError):
        try:
            os.unlink(dst_path)
        except OSError:
            pass
        return None


def _shrink_if_needed(png_path):
    """
    If png_path > _MAX_SIZE, re-export at progressively smaller dimensions
    until it fits.  Returns path to a (possibly new) file within budget, or
    None if it cannot be shrunk.  The caller owns cleanup of the returned path
    (which may differ from png_path if re-exported).
    """
    size = os.path.getsize(png_path)
    if size <= _MAX_SIZE:
        return png_path

    for dim in (200, 160, 128):
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="nanod-art-shrunk-")
        os.close(fd)
        ret = subprocess.run(
            [
                "sips",
                "--resampleHeightWidth", str(dim), str(dim),
                "-s", "format", "png",
                png_path,
                "--out", tmp,
            ],
            capture_output=True, timeout=10,
        )
        if ret.returncode != 0:
            os.unlink(tmp)
            continue
        if os.path.getsize(tmp) <= _MAX_SIZE:
            return tmp
        os.unlink(tmp)

    return None  # could not fit


def _push_sprite(send_line_fn, png_path):
    """
    Read png_path and send it to the device as sprite "art.png" using the
    Nano_D++ sprite protocol over send_line_fn.
    """
    with open(png_path, "rb") as fh:
        data = fh.read()

    total = len(data)
    crc = zlib.crc32(data) & 0xFFFFFFFF

    # begin
    send_line_fn(json.dumps({
        "sprite": {"op": "begin", "name": "art.png", "size": total}
    }))

    # data chunks — ALL sprite commands MUST be nested under "sprite" (firmware contract)
    seq = 0
    offset = 0
    while offset < total:
        chunk = data[offset: offset + _CHUNK_BYTES]
        send_line_fn(json.dumps({
            "sprite": {"op": "data", "seq": seq, "data": base64.b64encode(chunk).decode("ascii")}
        }))
        offset += _CHUNK_BYTES
        seq += 1

    # end
    send_line_fn(json.dumps({
        "sprite": {"op": "end", "name": "art.png", "crc32": crc}
    }))

    # select (activate on screen)
    send_line_fn(json.dumps({"sprite": {"op": "select", "name": "art.png"}}))


# ── Public API ────────────────────────────────────────────────────────────────

def push_artwork(send_line_fn, last={}):  # noqa: B006 — mutable default intentional
    """
    Check now-playing artwork; push to device if the track has changed.

    Parameters
    ----------
    send_line_fn : callable(str)
        Sends a single JSON line to the device (no trailing newline needed).
    last : dict
        Persistent state dict.  Keys used: 'track_id'.
        Defaults to a module-level dict so callers can omit it.

    Returns
    -------
    True if artwork was pushed, False otherwise.
    """
    track_id, art_src = _run_helper()
    if track_id is None or art_src is None:
        return False

    # Skip if track unchanged
    if last.get("track_id") == track_id:
        return False

    resized = None
    final   = None
    try:
        resized = _resize_to_png(art_src)
        if resized is None:
            return False

        final = _shrink_if_needed(resized)
        if final is None:
            return False

        _push_sprite(send_line_fn, final)
        last["track_id"] = track_id
        return True

    finally:
        # Clean up temp files (resized and final may be the same path)
        for p in {resized, final} - {None}:
            try:
                os.unlink(p)
            except OSError:
                pass
        # art_src is written by the Swift helper in /tmp; clean it up too
        try:
            os.unlink(art_src)
        except OSError:
            pass


# ── Bridge integration ────────────────────────────────────────────────────────
#
# INTEGRATION POINT FOR /tmp/nanod-vol.py
# ========================================
#
# 1. Import at the top of nanod-vol.py (or the new unified bridge):
#
#       import sys, os
#       sys.path.insert(0, '/Users/dviros/Downloads/binaris/integ/macos')
#       from artwork import push_artwork
#
# 2. Add a write helper that wraps the open file handle `f`:
#
#       def send_line(line):
#           f.write((line + '\n').encode())
#
# 3. Add a coarse timer in the main loop, after the existing volume/key
#    handling block, inside the `while True` that already runs every ~4 ms:
#
#       ARTWORK_INTERVAL = 2.0          # seconds between artwork checks
#       last_art_check   = 0.0
#       artwork_state    = {}           # persistent state across calls
#
#       # Inside the inner while True loop, after key/volume processing:
#       if config.get('artwork') and now - last_art_check >= ARTWORK_INTERVAL:
#           last_art_check = now
#           try:
#               push_artwork(send_line, last=artwork_state)
#           except Exception as e:
#               print('artwork error:', e, flush=True)
#
# 4. Load config from ~/.config/nanod/bridge.json before the loop:
#
#       import json, pathlib
#       _cfg_path = pathlib.Path.home() / '.config/nanod/bridge.json'
#       def load_config():
#           try:
#               return json.loads(_cfg_path.read_text())
#           except Exception:
#               return {}
#       config = load_config()
#
#    Re-load config whenever bridge.json changes (optional: use inotify/kqueue,
#    or simply reload every N seconds via a separate timer).
#
# 5. The knob/key path continues unchanged; send_line must only be called when
#    `f` is open and valid (already guarded by the existing try/except that
#    reconnects on IOError).
#
# NOTE: push_artwork blocks the loop for ~1-2 s on first call while sips runs.
# If blocking is unacceptable, run it in a threading.Thread.

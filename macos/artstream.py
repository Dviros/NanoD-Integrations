"""
artstream.py — No-PSRAM album artwork for the Nano_D++ 240x240 round screen.

The device has no PSRAM, so it can't decode a full-screen image in RAM. Instead
the Mac does all the work: decode the cover, resize to 240x240, convert to a raw
RGB565 frame, and upload it as a ".rgb565" sprite. The firmware streams that
frame straight from flash to the GC9A01 in strips (lcd_stream_rgb565) — the whole
image never lives in device RAM.

Also extracts the cover's two dominant colors and pushes them to the LED ring
({"ring":...}) for an album-matched "now playing" glow.

Public API
----------
push_artstream(send_line_fn, last={}, *, ring=True)
    If the now-playing track changed, render + upload the frame and (optionally)
    set the ring. send_line_fn(line) writes one JSON line to the device (no
    trailing newline — the bridge adds it). Returns True if it pushed.

clear_artstream(send_line_fn)
    Deselect the frame (screen returns to the dial). Call when playback stops.
"""

import base64
import json
import os
import subprocess
import time
import zlib

import numpy as np
from PIL import Image

from nowplaying import get_nowplaying

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nowplaying-art")
_DIM = 240                 # device screen
_CHUNK = 4096              # raw bytes per chunk-acked binary block (fits the device's 8 KB RX queue)
_NAME = "art.rgb565"


# ── now-playing (reused contract: <trackID>\t<artworkPath>) ────────────────────

def _now_playing():
    """Return (track_id, artwork_path) or (None, None)."""
    try:
        r = subprocess.run([_HELPER], capture_output=True, text=True, timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None
    if r.returncode != 0 or not r.stdout.strip():
        return None, None
    parts = r.stdout.rstrip("\n").rsplit("\t", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


# ── image → RGB565 + dominant colors ───────────────────────────────────────────

def _render(path):
    """
    Load + center-crop-to-square + resize to 240x240. Returns
    (rgb565_le_bytes, [primary_0xRRGGBB, secondary_0xRRGGBB]).
    """
    img = Image.open(path).convert("RGB")
    # center-crop to square so a non-square cover fills the round screen
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    img = img.resize((_DIM, _DIM), Image.LANCZOS)

    a = np.asarray(img, dtype=np.uint16)
    r = (a[:, :, 0] >> 3) & 0x1F
    g = (a[:, :, 1] >> 2) & 0x3F
    b = (a[:, :, 2] >> 3) & 0x1F
    rgb565 = (r << 11) | (g << 5) | b              # (240,240) uint16
    data = rgb565.astype("<u2").tobytes()          # little-endian; fw pushColors(swap=true)

    return data, _dominant(img, 2)


def _dominant(img, n):
    """Two most-common colors, skewed toward vivid ones for a nicer ring glow."""
    q = img.quantize(colors=8, method=Image.FASTOCTREE)
    pal = q.getpalette()
    cols = []
    for cnt, idx in sorted(q.getcolors() or [], reverse=True):
        rr, gg, bb = pal[idx * 3:idx * 3 + 3]
        # skip near-black / near-white (they make a dead-looking ring)
        mx, mn = max(rr, gg, bb), min(rr, gg, bb)
        if mx < 40 or (mx > 230 and mx - mn < 25):
            continue
        cols.append((rr << 16) | (gg << 8) | bb)
        if len(cols) >= n:
            break
    while len(cols) < n:
        cols.append(cols[-1] if cols else 0x08596C)
    return cols


# ── sprite upload + ring ───────────────────────────────────────────────────────

# fh is the bridge's raw non-blocking read+write port handle. Lock-step
# (write a line, wait for its ack) is REQUIRED for reliability: fire-and-forget
# overruns the tiny CDC RX buffer and the upload aborts "size mismatch".

def _send(fh, obj):
    fh.write((json.dumps(obj) + "\n").encode())


def _read_ack(fh, timeout=1.5):
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            d = fh.read(4096)
        except (BlockingIOError, OSError):
            d = None
        if d:
            buf += d
            for ln in buf.split(b"\n"):
                if b'"ack"' in ln:
                    try:
                        return json.loads(ln)
                    except ValueError:
                        pass
        else:
            time.sleep(0.002)
    return None


def _write_all(fh, data):
    """Write all bytes to the non-blocking port, flow-controlled by the device."""
    mv = memoryview(data)
    off = 0
    while off < len(data):
        try:
            n = fh.write(mv[off:])
        except (BlockingIOError, OSError):
            n = 0
        if n:
            off += n
        else:
            time.sleep(0.001)   # OS buffer full — device still draining; back off


def _read_binack(fh, timeout=2.0):
    """Wait for a {"binack":{...}} line; returns the inner object or None."""
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            d = fh.read(4096)
        except (BlockingIOError, OSError):
            d = None
        if d:
            buf += d
            for ln in buf.split(b"\n"):
                if b'"binack"' in ln:
                    try:
                        return json.loads(ln).get("binack")
                    except ValueError:
                        pass
        else:
            time.sleep(0.001)
    return None


def _upload(fh, data):
    """Chunk-acked binary upload (~2.5 s vs ~13 s base64). Each {"op":"binchunk"}
    line is followed by exactly CHUNK raw bytes; the device reads them into RAM
    (no flash → its 8 KB RX queue can't overflow), commits the chunk to flash while
    the host waits, then acks. Reads and flash writes never overlap, so a slow
    LittleFS write can't make macOS time out and silently drop bytes."""
    crc = zlib.crc32(data) & 0xFFFFFFFF
    _send(fh, {"sprite": {"op": "binbegin", "name": _NAME, "size": len(data)}})
    if not (_read_ack(fh) or {}).get("ok"):
        return False
    for off in range(0, len(data), _CHUNK):
        chunk = data[off:off + _CHUNK]
        _send(fh, {"sprite": {"op": "binchunk", "len": len(chunk)}})
        _write_all(fh, chunk)
        b = _read_binack(fh)
        if not b or not b.get("ok"):
            return False  # device aborts on a bad chunk; leave track_id uncached to retry
    _send(fh, {"sprite": {"op": "end", "name": _NAME, "crc32": crc}})
    if not (_read_ack(fh, 3) or {}).get("ok"):
        return False
    # Re-select even when the name is unchanged: deselect first so the firmware's
    # activeSprite poll sees a change and re-streams the new frame.
    _send(fh, {"sprite": {"op": "select", "name": ""}})
    _read_ack(fh)
    _send(fh, {"sprite": {"op": "select", "name": _NAME}})
    _read_ack(fh)
    return True


# ── public API ─────────────────────────────────────────────────────────────────

def push_artstream(fh, last={}, *, player="Music", ring=True):  # noqa: B006 — persistent state
    info = get_nowplaying(player)
    if not info:
        return False
    track_id, art = info["track_id"], info["art"]
    if last.get("track_id") == track_id:
        return False
    try:
        data, cols = _render(art)
        if not _upload(fh, data):
            return False  # leave track_id uncached so it retries next poll
        if ring:
            _send(fh, {"ring": {"primary": cols[0], "secondary": cols[1], "mode": 0}})
        last["track_id"] = track_id
        return True
    finally:
        try:
            os.unlink(art)
        except OSError:
            pass


def clear_artstream(fh):
    _send(fh, {"sprite": {"op": "select", "name": ""}})


# ── self-test: render a file to a .rgb565 + print colors (no device) ───────────

if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print("usage: artstream.py <image>  (renders to /tmp/art.rgb565, prints colors)")
        raise SystemExit(1)
    data, cols = _render(src)
    open("/tmp/art.rgb565", "wb").write(data)
    assert len(data) == _DIM * _DIM * 2, f"bad size {len(data)}"
    print(f"OK: {len(data)} bytes -> /tmp/art.rgb565  | primary #{cols[0]:06X} secondary #{cols[1]:06X}")

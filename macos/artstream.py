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

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nowplaying-art")
_DIM = 240                 # device screen
_CHUNK = 512               # raw bytes per sprite chunk
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

def _upload(send_line_fn, data):
    crc = zlib.crc32(data) & 0xFFFFFFFF
    send_line_fn(json.dumps({"sprite": {"op": "begin", "name": _NAME, "size": len(data)}}))
    seq = 0
    for off in range(0, len(data), _CHUNK):
        chunk = data[off:off + _CHUNK]
        send_line_fn(json.dumps({"sprite": {"op": "data", "seq": seq,
                                            "data": base64.b64encode(chunk).decode("ascii")}}))
        seq += 1
        # ~115 KB = 225 chunks; pace lightly so the device's serial RX + LittleFS
        # writes keep up (the whole frame is one upload per track change).
        if seq % 24 == 0:
            time.sleep(0.004)
    send_line_fn(json.dumps({"sprite": {"op": "end", "name": _NAME, "crc32": crc}}))
    send_line_fn(json.dumps({"sprite": {"op": "select", "name": _NAME}}))


# ── public API ─────────────────────────────────────────────────────────────────

def push_artstream(send_line_fn, last={}, *, ring=True):  # noqa: B006 — persistent state
    track_id, art = _now_playing()
    if not track_id or not art:
        return False
    if last.get("track_id") == track_id:
        return False
    try:
        data, cols = _render(art)
        _upload(send_line_fn, data)
        if ring:
            send_line_fn(json.dumps({"ring": {"primary": cols[0], "secondary": cols[1], "mode": 0}}))
        last["track_id"] = track_id
        return True
    finally:
        try:
            os.unlink(art)
        except OSError:
            pass


def clear_artstream(send_line_fn):
    send_line_fn(json.dumps({"sprite": {"op": "select", "name": ""}}))


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

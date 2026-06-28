"""
nowplaying.py — per-player now-playing for the Nano_D++ bridge.

macOS MediaRemote is locked down (macOS 14.4+/26 returns empty for unsigned
apps), so we read each player directly:

  • Music / Spotify  → AppleScript (current track, player position, duration,
                       artwork). Exact cover + live seek.
  • anything else    → best-effort: read the app's window title for "Song —
                       Artist", fetch the cover from the iTunes Search API by
                       name. No reliable seek (the app exposes no position).

get_nowplaying(player) -> dict | None
    player: "Music" | "Spotify" | "<AppName>" (best-effort).
    dict: {track_id, art, position, duration, playing}.  position/duration in
    seconds; position is None when unavailable (best-effort players).

The Mac does all the work; the device just streams the result.
"""

import glob
import hashlib
import json
import os
import ssl
import subprocess
import urllib.parse
import urllib.request

_ART = f"/tmp/nanod-art-{os.getpid()}.dat"

# SSL context for cover downloads: prefer certifi's CA bundle, fall back to an
# unverified context. These are public album-art images (not sensitive data), and
# the stock macOS python3 often lacks a usable CA bundle (CERTIFICATE_VERIFY_FAILED).
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl._create_unverified_context()


def _osa(script, timeout=3):
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


# ── scriptable players (Music / Spotify share these terms) ─────────────────────

def _applescript_track(app, dur_ms=False):
    out = _osa(f'''
        tell application "{app}"
          if it is running and player state is playing then
            set t to current track
            return (name of t) & "\t" & (artist of t) & "\t" & (album of t) & "\t" & (player position) & "\t" & (duration of t)
          end if
        end tell''')
    if not out:
        return None
    p = out.split("\t")
    if len(p) < 5:
        return None
    try:
        pos, dur = float(p[3]), float(p[4])
    except ValueError:
        return None
    if dur_ms:
        dur /= 1000.0
    return p[0], p[1], p[2], pos, dur


def _music_artwork():
    ok = _osa(f'''
        tell application "Music" to set ad to data of artwork 1 of current track
        set f to open for access POSIX file "{_ART}" with write permission
        set eof f to 0
        write ad to f
        close access f
        return "ok"''')
    return ok == "ok" and os.path.exists(_ART) and os.path.getsize(_ART) > 100


def _spotify_artwork():
    url = _osa('tell application "Spotify" to get artwork url of current track')
    return bool(url) and _fetch(url)


# ── best-effort for non-scriptable players (window title + iTunes Search) ──────

def _window_title(app):
    # Many players put "Song — Artist" (or "Song - Artist") in window 1's title.
    t = _osa(f'tell application "System Events" to tell process "{app}" to get name of window 1')
    if not t:
        return None, None
    for sep in (" — ", " – ", " - "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip()   # (title, artist) — order varies, search handles both
    return t.strip(), ""


def _itunes_cover(name, artist):
    try:
        term = urllib.parse.quote(f"{artist} {name}".strip())
        url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=1"
        with urllib.request.urlopen(url, timeout=5) as r:
            res = json.loads(r.read()).get("results")
        if not res:
            return False
        art = res[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
        return bool(art) and _fetch(art)
    except (urllib.error.URLError, ValueError, OSError):
        return False


def _fetch(url):
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            data = r.read()
        if len(data) < 100:
            return False
        with open(_ART, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


# ── Kaset (YouTube Music) ───────────────────────────────────────────────────────
# Kaset keeps its now-playing locked inside WebKit: no AppleScript track terms,
# MediaRemote is locked on macOS 26, its AX tree is empty, and its prefs only save
# on quit (stale during a session). BUT it caches album covers to disk live. We use
# the newest cover-sized (>=400px, ~square) image in its image cache as the cover.
# Cover only — no track name, and no live position, so no seek bar for Kaset.

_KASET_IMGCACHE = os.path.expanduser(
    "~/Library/Containers/com.sertacozercan.Kaset/Data/Library/Caches/com.kaset.imagecache")


def _kaset_cover():
    """Return (path, md5) of Kaset's current cover (newest cover-sized cached image),
    or (None, None) if Kaset isn't running / no cover is cached. The md5 is the
    change key — it flips when the displayed cover changes (i.e. on track change)."""
    try:
        if subprocess.run(["pgrep", "-x", "Kaset"], capture_output=True, timeout=2).returncode != 0:
            return None, None
        from PIL import Image
        files = sorted(((os.path.getmtime(x), x)
                        for x in glob.glob(_KASET_IMGCACHE + "/**/*", recursive=True)
                        if os.path.isfile(x)), reverse=True)
        for _, x in files[:25]:                     # newest first; covers are large squares
            try:
                w, h = Image.open(x).size
            except Exception:
                continue
            if min(w, h) >= 400 and 0.8 < w / h < 1.25:
                return x, hashlib.md5(open(x, "rb").read()).hexdigest()
    except Exception:
        pass
    return None, None


# ── public ─────────────────────────────────────────────────────────────────────

def get_nowplaying(player="Music"):
    if player == "Kaset":
        path, h = _kaset_cover()
        if not path:
            return None
        try:
            with open(path, "rb") as s, open(_ART, "wb") as d:
                d.write(s.read())
        except OSError:
            return None
        return {"track_id": h, "art": _ART, "position": None, "duration": None, "playing": True}

    if player == "Music":
        r = _applescript_track("Music")
        if not r:
            return None
        name, artist, album, pos, dur = r
        if not (_music_artwork() or _itunes_cover(name, artist)):
            return None
        return {"track_id": f"{name}\t{artist}\t{album}", "art": _ART,
                "position": pos, "duration": dur, "playing": True}

    if player == "Spotify":
        r = _applescript_track("Spotify", dur_ms=True)
        if not r:
            return None
        name, artist, album, pos, dur = r
        if not (_spotify_artwork() or _itunes_cover(name, artist)):
            return None
        return {"track_id": f"{name}\t{artist}\t{album}", "art": _ART,
                "position": pos, "duration": dur, "playing": True}

    # best-effort: window title + iTunes cover, no live seek
    name, artist = _window_title(player)
    if not name:
        return None
    if not _itunes_cover(name, artist):
        return None
    return {"track_id": f"{name}\t{artist}", "art": _ART,
            "position": None, "duration": None, "playing": True}


def get_track_meta(player="Music"):
    """Cheap now-playing probe — (track_id, position_s, duration_s) WITHOUT fetching
    artwork; None when nothing is playing. track_id matches get_nowplaying's so the
    caller can detect track changes without the expensive cover export each poll."""
    if player == "Kaset":
        _, h = _kaset_cover()
        return (h, None, None) if h else None
    if player == "Music":
        r = _applescript_track("Music")
    elif player == "Spotify":
        r = _applescript_track("Spotify", dur_ms=True)
    else:
        name, artist = _window_title(player)
        return (f"{name}\t{artist}", None, None) if name else None
    if not r:
        return None
    name, artist, album, pos, dur = r
    return (f"{name}\t{artist}\t{album}", pos, dur)


if __name__ == "__main__":
    import sys
    pl = sys.argv[1] if len(sys.argv) > 1 else "Music"
    info = get_nowplaying(pl)
    if not info:
        print(f"{pl}: nothing playing / no data")
    else:
        sz = os.path.getsize(info["art"]) if os.path.exists(info["art"]) else 0
        pos = f"{info['position']:.0f}/{info['duration']:.0f}s" if info["position"] is not None else "no-seek"
        print(f"{pl}: {info['track_id'].replace(chr(9),' — ')} | {pos} | art {sz}B")

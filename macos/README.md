# nanod-bridge — Nano_D++ macOS Companion

A dependency-free Python 3 daemon (no pip installs) that connects the Nano_D++ haptic knob to macOS over USB-CDC serial. It maps the knob and buttons to system volume and media controls via `osascript`, and is fully driven by a shared JSON config file that the companion app writes.

---

## Requirements

- **Python 3.10+** (ships with macOS 12+; `python3 --version`)
- macOS 12 Ventura or later
- Nano_D++ firmware flashed and connected via USB (`/dev/cu.usbmodem*` present)

No `pip install` step needed — only stdlib modules (`fcntl`, `glob`, `json`, `os`, `subprocess`, `time`).

---

## Quick start (foreground)

```sh
python3 integ/macos/nanod-bridge.py
```

The bridge will:
1. Auto-detect `/dev/cu.usbmodem*` (the Nano_D++ USB-CDC port)
2. Load `~/.config/nanod/bridge.json` (or use defaults if absent)
3. Map knob position `{p}` → macOS output volume (throttled, ~35 ms)
4. Map button events `{kd:N}` → configured action for button index N
5. Reconnect automatically after USB disconnect / re-enumeration

---

## Config file

**Path:** `~/.config/nanod/bridge.json`

The companion app writes this file; the bridge reads it at startup and re-reads it whenever the file's mtime changes (polled each loop — no inotify required).

```json
{
  "buttons": ["playpause", "next", "previous", "mute"],
  "knobVolume": true,
  "artwork": false,
  "player": "Music"
}
```

### Fields

| Field        | Type      | Default                                    | Description |
|--------------|-----------|--------------------------------------------|-------------|
| `buttons`    | `string[]`| `["playpause","next","previous","mute"]`   | Action for each button (index 0–3 = A–D) |
| `knobVolume` | `bool`    | `true`                                     | Knob position drives macOS output volume |
| `artwork`    | `bool`    | `false`                                    | Enable music profile: album art on screen, LED ring in album colors, seek arc. Requires `Pillow` and `numpy`. |
| `player`     | `string`  | `"Music"`                                  | Now-playing source: `"Music"`, `"Spotify"`, or any app name for best-effort window-title mode (no seek). |

### Allowed button action strings

| String              | Effect |
|---------------------|--------|
| `"playpause"`       | AppleScript `playpause` on whichever of Kaset / Spotify / Music is running |
| `"next"`            | `next track` on the running player |
| `"previous"`        | `previous track` on the running player |
| `"mute"`            | Toggle `output muted` |
| `"volup"`           | Output volume +6 (clamped to 100) |
| `"voldown"`         | Output volume −6 (clamped to 0) |
| `"none"`            | Do nothing |
| `"shortcut:<Name>"` | Run a named Shortcut via `shortcuts run "<Name>"` |

**Kaset note:** Kaset supports `playpause`, `next track`, `previous track` via AppleScript. It does NOT expose track-property or artwork access.

If `bridge.json` is absent the bridge runs with defaults (buttons as above, knobVolume on, artwork off) and retries loading the file each cycle.

---

## Install as a Login Agent (auto-start)

### 1. Copy the bridge script to a stable path

```sh
cp integ/macos/nanod-bridge.py /Users/Shared/nanod-bridge.py
chmod +x /Users/Shared/nanod-bridge.py
```

You may place it anywhere permanent; update `ProgramArguments` in the plist to match.

### 2. Install the LaunchAgent

```sh
cp integ/macos/com.nanod.bridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nanod.bridge.plist
```

The agent starts immediately and restarts automatically after crashes or USB disconnects.

### 3. Verify it is running

```sh
launchctl list | grep nanod
tail -f /tmp/nanod-bridge.log
```

### 4. Stop / uninstall

```sh
launchctl unload ~/Library/LaunchAgents/com.nanod.bridge.plist
rm ~/Library/LaunchAgents/com.nanod.bridge.plist
```

---

## Music profile — album art, seek arc, and ring glow

When `artwork` is `true` in `bridge.json`, the bridge runs a music profile that covers three behaviors:

### Album artwork on the display (`artstream.py`)

The Nano_D++ has **no PSRAM**, so the device cannot decode or buffer a full-screen image in RAM. The Mac does all the work: `artstream.py` decodes the current album cover, center-crops it to square, resizes it to 240×240, and converts it to a raw RGB565 frame (115 200 bytes). That frame is uploaded to the device as the sprite `art.rgb565`, which the firmware streams straight from flash to the GC9A01 LCD in strips — the image never occupies device RAM.

**Binary chunk-acked upload protocol** — the upload uses a firmware-side binary mode rather than base64:

1. Send `{"sprite":{"op":"binbegin","name":"art.rgb565","size":<bytes>}}`; wait for `{"ack":"sprite","ok":true}`.
2. For each 4 KB block: send `{"sprite":{"op":"binchunk","len":<n>}}`; send the raw bytes immediately after; wait for `{"binack":{"ok":true}}`.
3. Send `{"sprite":{"op":"end","name":"art.rgb565","crc32":<ieee-crc32>}}`; wait for final `{"ack":"sprite","ok":true}`.
4. Deselect then re-select: `{"sprite":{"op":"select","name":""}}` → `{"sprite":{"op":"select","name":"art.rgb565"}}` so the firmware detects the change and re-streams the frame.

Upload time is approximately 2.5 s. The lock-step (send chunk header, send bytes, wait for `binack`) is required: firing ahead overruns the device's 8 KB CDC RX queue and causes a size-mismatch abort.

Fallback if `artstream.py` or its Python dependencies (`Pillow`, `numpy`) are absent: the bridge skips artwork silently and logs a one-time warning.

### LED ring glow (`{"ring":...}`)

After uploading the frame, `artstream.py` extracts the two dominant vivid colors from the cover and sends them to the LED ring:

```json
{"ring": {"primary": 16730458, "secondary": 4654093, "mode": 0}}
```

Colors are 24-bit RGB integers (`0xRRGGBB`). Near-black and near-white are skipped to keep the glow visually interesting.

### Song progress arc (`{"seek":...}`)

Every poll (every ~2 s by default), `artstream.py` fetches the current player position and sends:

```json
{"seek": {"pos": 0.42}}
```

`pos` is a normalized float `0.0`–`1.0` (`position / duration`). The firmware draws a progress arc on the LED ring. This is sent cheaply without re-fetching artwork; only a track-ID change triggers the full cover upload.

### Now-playing sources (`nowplaying.py`)

`nowplaying.py` provides track metadata and artwork for the players configured in `bridge.json`. macOS MediaRemote is locked down on macOS 14.4+ / macOS 26 (returns empty for unsigned apps), so the bridge reads each player directly via AppleScript.

| Player value | Metadata source | Artwork source | Seek |
|---|---|---|---|
| `"Music"` | AppleScript (`name`, `artist`, `album`, `player position`, `duration`) | `data of artwork 1 of current track` (raw bytes) | yes |
| `"Spotify"` | AppleScript (same terms; duration in ms, converted) | `artwork url of current track` | yes |
| `"<AppName>"` (other) | Window title parsed as `"Song — Artist"` | iTunes Search API by track name (best-effort) | no |

For Music and Spotify, the iTunes Search API is an additional cover fallback when the primary source fails.

**Two entry points** — use the right one to avoid unnecessary artwork exports:

- `get_nowplaying(player)` — fetches track metadata **and** artwork. Returns `{track_id, art, position, duration, playing}`. Call only when the track changes.
- `get_track_meta(player)` — returns `(track_id, position_s, duration_s)` **without** fetching artwork. Call every poll to drive the seek arc and detect track changes cheaply.

### Dependencies

```sh
pip3 install Pillow numpy
```

These are only required when `"artwork": true`. The rest of the bridge (`nanod-bridge.py`) has no pip dependencies.

---

## Real-time volume control (`volctl.swift`)

The bridge spawns a persistent Swift helper (`volctl`) to set macOS output volume via CoreAudio (`AudioObjectSetPropertyData` / `kAudioDevicePropertyVolumeScalar`). The helper reads a target volume (0–100, one integer per line) on stdin and applies it in approximately 1 ms — versus approximately 50–80 ms to spawn a new `osascript` process for every knob tick. This makes the knob feel instantaneous when turning.

**Build:**
```sh
swiftc -O integ/macos/volctl.swift -o integ/macos/volctl
```

The bridge looks for the binary at `integ/macos/volctl` (next to `nanod-bridge.py`). If absent, it falls back to `osascript` with a one-time log warning. The helper handles both devices that expose a master volume scalar and devices that require per-channel (L/R) scalars.

---

## Buttonless flashing (`flash.sh`)

`integ/macos/flash.sh` flashes new firmware without the BOOT+EN button dance.

**How it works:**

1. Sends `{"reboot":"bootloader"}` over serial — the running firmware drops into ROM download mode immediately, no buttons needed.
2. Waits (up to ~20 s) for the device to re-enumerate as a download-mode port and probes it with `esptool`.
3. Flashes with `esptool.py` (`--before no_reset --after no_reset`; esptool cannot reset a native-USB ESP32-S3 board via RTS).
4. Prints a reminder to tap EN once — the device cannot self-reset after flashing.

**Usage:**
```sh
./flash.sh                          # uses fw/.pio/build/nanofoc_d_wifi/firmware.bin
./flash.sh path/to/firmware.bin     # explicit firmware path
```

`esptool.py` is located from the PlatformIO toolchain (`~/.platformio/packages/tool-esptoolpy/`). If the firmware JSON command fails (e.g., device is already unplugged), the script prints the manual fallback: hold BOOT, tap EN, release BOOT.

---

## Artwork agent hook

The bridge imports `artstream.push_artstream` directly when `Pillow` and `numpy` are available. The active serial file handle is stored as `_active_port` in the bridge module namespace and is passed directly into `push_artstream` for lock-step binary writes.

Constraints: `MAX_SPRITE_SIZE` = 64 KB, screen = 240×240 round, raw RGB565 only (GIF decode hangs the LCD task — do not use GIF sprites).

---

## Logs

```sh
tail -f /tmp/nanod-bridge.log
```

Key log prefixes:

| Prefix      | Meaning |
|-------------|---------|
| `[config]`  | Config file loaded or parse error |
| `[serial]`  | Port connect / reconnect events |
| `[raw]`     | Raw button-down lines from device |
| `[button]`  | Dispatched button action |

---

## Protocol reference

See [../SERIAL_API.md](../SERIAL_API.md) for the full Nano_D++ JSON serial protocol.

### Relevant event shapes

```jsonc
// Knob position (0–100 mapped to volume 0–100)
{"p": 73}

// Button down (index 0–3 = A–D)
{"kd": 0}
```

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
  "artwork": false
}
```

### Fields

| Field        | Type      | Default                                    | Description |
|--------------|-----------|--------------------------------------------|-------------|
| `buttons`    | `string[]`| `["playpause","next","previous","mute"]`   | Action for each button (index 0–3 = A–D) |
| `knobVolume` | `bool`    | `true`                                     | Knob position drives macOS output volume |
| `artwork`    | `bool`    | `false`                                    | Push album art to device screen (requires artwork agent) |

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

## Artwork agent hook

The bridge contains a no-op stub `push_artwork(image_path: str)` in `nanod-bridge.py`. The artwork agent imports or monkey-patches this function to push album art to the device screen using the sprite protocol:

```
{"sprite":{"op":"begin","name":<n>,"size":<bytes>}}
N × {"op":"data","seq":<i>,"data":<base64_chunk>}
{"op":"end","name":<n>,"crc32":<uint32>}
{"op":"select","name":<n>}
```

Constraints: `MAX_SPRITE_SIZE` = 64 KB, screen = 240×240 round, PNG/BMP only (GIF is unsafe — hangs LCD task). The active serial file object is exposed as `nanod_bridge._active_port`.

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

# nanod-bridge — Nano_D++ macOS Companion

A dependency-free Node 22 ESM daemon that connects the Nano_D++ haptic knob to macOS. It handles the mutual HMAC-SHA256 TCP handshake, then maps the knob and buttons to system volume and media controls via `osascript`.

---

## Requirements

- **Node.js 22+** (`node --version` should print `v22.x.x` or higher)
- macOS 12 Ventura or later (AppleScript media commands work on Monterey+)
- Nano_D++ firmware built with `WIFI_ENABLED` (the `nanofoc_d_wifi` or `nanofoc_d_full` PlatformIO environment)
- Device connected to the same LAN as your Mac

No `npm install` step is needed. The daemon uses only built-in Node modules (`node:net`, `node:crypto`, `node:child_process`, `node:fs/promises`).

---

## Quick start

```sh
# Minimum: set device IP and pre-shared key, then run
NANOD_IP=192.168.1.42 NANOD_PSK=mysecret node nanod-bridge.mjs
```

The daemon will:
1. Open a TCP connection to `<device-ip>:3333`
2. Receive the device hello with a 16-byte nonce
3. Send `{"auth":{"hmac":"<HMAC-SHA256(psk, deviceNonce)>","nonce":"<clientNonce>"}}` 
4. Verify the device's proof HMAC (mutual auth)
5. Start dispatching knob/button events to macOS actions

Press **Ctrl-C** to shut down cleanly.

---

## Configuration

### Environment variables

| Variable    | Default         | Description                          |
|-------------|-----------------|--------------------------------------|
| `NANOD_IP`  | `192.168.1.42`  | Device IP address                    |
| `NANOD_PSK` | *(empty)*       | Pre-shared key (must match device)   |

### JSON config file

Pass `--config <path>` to load from a JSON file instead:

```sh
node nanod-bridge.mjs --config ~/nanod-config.json
```

`nanod-config.json` example:

```json
{
  "ip":  "192.168.1.42",
  "psk": "mysecret",
  "port": 3333
}
```

All three fields are optional; unset fields fall back to env vars or built-in defaults.

### Setting the PSK on the device

The device PSK is stored in NVS and is set over the USB-CDC serial port (not over TCP, for bootstrap security):

```sh
# Connect to the device serial port at 115200 baud
echo '{"settings":{"netPsk":"mysecret"}}' > /dev/cu.usbmodem*
echo '{"save":true}' > /dev/cu.usbmodem*
```

Or use the Nano_D++ desktop app → Settings → Network → Pre-shared key.

---

## Default mapping

| Input            | macOS action                                   |
|------------------|------------------------------------------------|
| Knob rotate CW   | System volume up                               |
| Knob rotate CCW  | System volume down                             |
| Button A (press) | Play / Pause (targets Music.app)               |
| Button B (press) | Next track (targets Music.app)                |
| Button C (press) | Previous track (targets Music.app)            |
| Button D (press) | Mute / unmute system output                   |

---

## Customising the mapping

Open `nanod-bridge.mjs` and edit the `MAPPING` object near the top of the file. Every action is an async function — you can call `osascript()`, `shortcutsRun()`, or any other async operation.

### Switch to Spotify

Replace the Music.app AppleScript with Spotify equivalents:

```js
MAPPING.keys.onDown[0] = async () => {
  await osascript(`tell application "Spotify" to playpause`);
};
MAPPING.keys.onDown[1] = async () => {
  await osascript(`tell application "Spotify" to next track`);
};
MAPPING.keys.onDown[2] = async () => {
  await osascript(`tell application "Spotify" to previous track`);
};
```

### Use macOS Shortcuts

Any button can trigger a named Shortcut from the Shortcuts app:

```js
MAPPING.keys.onDown[3] = async () => {
  await shortcutsRun("My Custom Shortcut");
};
```

### Change volume sensitivity

Increase or decrease `VOLUME_PER_RAD` in the `MAPPING.knob` object:

```js
// ~1% per 9° of rotation (default)
VOLUME_PER_RAD: 6.37,

// ~1% per 18° of rotation (slower)
VOLUME_PER_RAD: 3.18,
```

---

## Example macOS Shortcuts for music apps

Create these in the Shortcuts app (File → New Shortcut), then reference them by name with `shortcutsRun("...")`.

### "Play Pause"
1. Add action: **Music** → **Play/Pause**
2. Name the shortcut **Play Pause**

### "Next Track"
1. Add action: **Music** → **Next Song**
2. Name the shortcut **Next Track**

### "Spotify Play Pause"
1. Add action: **Scripting** → **Run AppleScript**
2. Script: `tell application "Spotify" to playpause`
3. Name the shortcut **Spotify Play Pause**

### "Focus Music" (open Music.app and play)
1. Add action: **Open App** → Music
2. Add action: **Scripting** → **Run AppleScript** → `tell application "Music" to play`
3. Name the shortcut **Focus Music**

---

## Running as a background service (launchd)

To start the bridge automatically at login, create a launchd plist:

```xml
<!-- ~/Library/LaunchAgents/com.nanod.bridge.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>         <string>com.nanod.bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/node</string>
    <string>/path/to/nanod-bridge.mjs</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NANOD_IP</key>  <string>192.168.1.42</string>
    <key>NANOD_PSK</key> <string>mysecret</string>
  </dict>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardOutPath</key>  <string>/tmp/nanod-bridge.log</string>
  <key>StandardErrorPath</key><string>/tmp/nanod-bridge.log</string>
</dict>
</plist>
```

```sh
launchctl load ~/Library/LaunchAgents/com.nanod.bridge.plist
```

---

## Auto-reconnect behaviour

The daemon reconnects automatically after any disconnect or network error using exponential backoff (starts at 1 s, caps at 30 s). Knob angle tracking is reset on disconnect so the first frame after reconnect does not cause a large volume jump.

---

## Future: menubar app or Tauri wrapper

`nanod-bridge.mjs` is intentionally a self-contained daemon with no UI. Next steps for a polished product:

- **Menubar app (Swift/AppKit)**: Wrap the Node process (or port the TCP logic to Swift/Network.framework) and show current volume / profile in the menu bar. Use `NSStatusItem` + `NSMenu`.
- **Tauri (Rust + WebView)**: Bundle the Node logic as a sidecar or port the TCP client to Rust. Tauri's `system-tray` API gives a cross-platform menubar icon. The JSON protocol is straightforward to implement in Rust with `tokio::net::TcpStream` and `ring`/`hmac`.
- **Electron**: Quick path — require `node:net` and `node:crypto` directly (same code), wrap in `electron.Tray` for the menubar icon. This daemon is the "main process" equivalent already.

---

## Protocol reference

See [../SERIAL_API.md](../SERIAL_API.md) for the full Nano_D++ JSON protocol.

### Handshake summary

```
Client  ──────────────────────────────────────  Device
         TCP connect to <ip>:3333
         <────── {"hello":{"nonce":"<32hex>","proto":1}}
         {"auth":{"hmac":"HMAC-SHA256(psk, hex_decode(deviceNonce))","nonce":"<clientNonce32hex>"}} ──►
         <────── {"auth":{"ok":true,"hmac":"HMAC-SHA256(psk, hex_decode(clientNonce))"}}
         [verify device proof — close if mismatch]
         [connection now authenticated; normal JSON events flow]
```

### Key event indices

| Index | Button | Default action   |
|-------|--------|------------------|
| 0     | A      | Play / Pause     |
| 1     | B      | Next track       |
| 2     | C      | Previous track   |
| 3     | D      | Mute toggle      |

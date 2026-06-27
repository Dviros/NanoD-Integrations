# Nano_D++ Serial API

Authoritative external integration reference for the Binaris Nano_D++ (ESP32-S3 SimpleFOC haptic knob). Covers the serial JSON protocol used by the ZERO/ONE desktop app and available to any third-party integration.

---

## Transport

### Serial (USB-CDC)

The device presents a USB-CDC serial port at 115200 baud, 8N1. The device also enumerates as a USB-HID keyboard/mouse/gamepad and a USB-MIDI device on the same USB connection; only the CDC port carries this JSON protocol.

Connection parameters: **115200 baud, 8 data bits, no parity, 1 stop bit**.

Each JSON message is a single line terminated by `\n`. Newlines within field values must be escaped as `\n`. Partial frames are discarded after a 50 ms read timeout.

### TCP (WiFi build only)

When the firmware is built with the `WIFI_ENABLED` flag (`nanofoc_d_wifi` or `nanofoc_d_full` PlatformIO environments), the device runs a raw TCP JSON server on **port 3333** (`WifiThread::TCP_PORT`). The TCP transport mirrors the serial JSON API exactly: the same newline-delimited JSON messages, commands, events, and ACKs are used. Up to `MAX_TCP_CLIENTS` (4) simultaneous connections are accepted. Inbound lines longer than `MAX_LINE_BYTES` (2048 bytes) are silently dropped.

When a TCP client connects, the device sends an initial greeting:

```json
{ "connected": true, "ip": "192.168.1.42", "device": "Nano_3053f07554dc", "fw": "1.0.0" }
```

All outbound frames (ACKs, events, replies) produced by `com_thread.emit()` are mirrored to every connected TCP client via a FreeRTOS queue that `wifi_thread` drains each loop iteration. ArduinoOTA remains available for over-the-air firmware updates once the device is connected to a station network.

---

## Protocol conventions

- All messages are JSON objects on a single line, terminated by `\n`.
- The host sends **commands** (inbound). The device sends **events**, **responses**, and **ACKs** (outbound).
- Every mutating command produces an ACK. Read-only queries (e.g. `{"profile":"NAME"}`) do not produce an ACK; only a response payload.
- On boot the device emits `{"ack":"boot","ok":true}`.

### ACK shape

Every command that mutates device state produces an ACK:

```json
{ "ack": "<command-key>", "ok": true }
{ "ack": "<command-key>", "ok": false, "error": "human-readable reason" }
```

The `error` field is present only when `ok` is `false`.

---

## Device-to-host messages (outbound)

### Boot ACK

Emitted on startup before the device is ready to accept commands.

```json
{ "ack": "boot", "ok": true }
```

### Error messages

```json
{ "error": "An error occurred." }
{ "error": "Another kind of error.", "msg": "Additional detail." }
```

### Debug messages

```json
{ "debug": "A message for the console." }
```

### Idle messages

Sent once per second when no events have occurred for longer than `idleTimeout` ms. The value is elapsed milliseconds since the last user interaction.

```json
{ "idle": 16233 }
```

### Save confirmation

Sent in addition to the ACK after `{"save":true}` completes:

```json
{ "saved": true }
```

### Profile change event

Emitted when the active profile changes (whether triggered by a button action or a host command):

```json
{ "current": "EXAMPLE PROFILE" }
```

### Key events

Key-down and key-up events are emitted on every button interaction. `ks` is a bitmask of currently held keys (bits 0-3 = keys A-D). `kd` carries the 0-based key index on press; `ku` on release.

```json
{ "kd": 0, "ks": 1 }
{ "ku": 0, "ks": 0 }
```

### Knob telemetry

Emitted on every encoder position change. Legacy field `p` is a raw uint16 encoder position. Fields `a`, `t`, and `v` are the richer telemetry added in the FW3 update.

| Field | Type | Description |
|-------|------|-------------|
| `p` | uint16 | Legacy raw encoder position (kept for back-compat) |
| `a` | float | Shaft angle in radians |
| `t` | int32 | Integer turn count (floor of `a / 2π`) |
| `v` | float | Shaft velocity in rad/s |

```json
{ "p": 42, "a": 3.14159, "t": 0, "v": 0.523 }
```

New integrations should use `a`, `t`, and `v`. `p` is retained for compatibility with older hosts.

### Motor register response

Response from the FOC thread to a `{"R":...}` command:

```json
{ "r": "17=2.0" }
```

---

## Commands (host-to-device)

### Profile commands

#### List all profiles

```json
{ "profiles": "#all" }
```

Response:

```json
{ "profiles": ["DEFAULT PROFILE", "EXAMPLE PROFILE", "BINARIS"], "current": "EXAMPLE PROFILE" }
```

#### Get a single profile

```json
{ "profile": "EXAMPLE PROFILE" }
```

Response includes the full profile object. No ACK is sent for read-only queries.

```json
{
  "profile": {
    "version": 2,
    "name": "EXAMPLE PROFILE",
    "desc": "HELLO WORLD!",
    "profileTag": "DEMO",
    "ledEnable": true,
    "ledBrightness": 100,
    "ledMode": 0,
    "pointer": 16777215,
    "primary": 547180,
    "secondary": 4654093,
    "buttonAIdle": 547180,
    "buttonBIdle": 1715794,
    "buttonCIdle": 2098468,
    "buttonDIdle": 4654093,
    "buttonAPress": 16777215,
    "buttonBPress": 16777215,
    "buttonCPress": 16777215,
    "buttonDPress": 16777215,
    "keys": [
      {
        "pressed": [{ "type": "key", "keyCodes": [17] }],
        "released": [],
        "held": []
      }
    ],
    "knob": [
      {
        "valueMin": 0,
        "valueMax": 127,
        "angleMin": 0,
        "angleMax": 0,
        "wrap": false,
        "step": 0,
        "keyState": 0,
        "haptic": {
          "mode": 0,
          "startPos": 0,
          "endPos": 127,
          "detentCount": 127,
          "vernier": 0,
          "kxForce": false,
          "outputRamp": 0,
          "detentStrength": 0
        },
        "type": "midi",
        "channel": 1,
        "cc": 28
      }
    ],
    "guiEnable": false,
    "audio": { "clickType": "hard", "keyClickType": "clack", "clickLevel": 100 }
  }
}
```

Haptic mode values:

| Value | Name | Description |
|-------|------|-------------|
| 0 | REGULAR | Coarse detents only |
| 1 | VERNIER | Coarse with fine detents between |
| 2 | VISCOSE | Resistive drag while turning |
| 3 | SPRING | Snaps back to center |

Key action `type` values: `key`, `midi`, `mouse`, `gamepad`, `profile`, `next_profile`, `prev_profile`.

#### Update profile fields

```json
{ "profile": "EXAMPLE PROFILE", "updates": { "desc": "LOOK AT ME!", "ledBrightness": 50 } }
```

ACK: `{"ack":"profile","ok":true}`

#### Create a new profile

Send an update with a non-existing profile name. Fields omitted from `updates` receive default values.

```json
{ "profile": "NEW PROFILE", "updates": { "desc": "A NEW PROFILE" } }
```

ACK: `{"ack":"profile","ok":true}`

#### Rename a profile

```json
{ "profile": "EXAMPLE PROFILE", "updates": { "name": "NEW NAME" } }
```

ACK: `{"ack":"profile","ok":true}`

#### Set the active profile

```json
{ "current": "DEFAULT PROFILE" }
```

ACK: `{"ack":"current","ok":true}`

The device also emits a `{"current":"<name>"}` event.

#### Delete or reorder profiles

Send a `profiles` array with the desired final order. Profiles absent from the list are deleted. The array may be a subset of existing profiles.

```json
{ "profiles": ["EXAMPLE PROFILE", "BINARIS", "DEFAULT PROFILE"] }
```

ACK: `{"ack":"profiles","ok":true}`

---

### Motor commands

#### Set SimpleFOC registers

Space-separated `register=value` pairs. Consult the SimpleFOC Commander documentation for register numbers.

```json
{ "R": "17=2.0 19=7.7" }
```

No ACK; the FOC thread may echo register values as `{"r":"..."}` messages.

#### Recalibrate motor

```json
{ "recalibrate": true }
```

ACK: `{"ack":"recalibrate","ok":true}`

---

### System commands

#### Get device settings

```json
{ "settings": "?" }
```

Response (formatted for clarity; arrives on one line):

```json
{
  "settings": {
    "debug": false,
    "ledMaxBrightness": 150,
    "maxVelocity": 10,
    "maxVoltage": 5,
    "deviceOrientation": 1,
    "deviceName": "Nano_3053f07554dc",
    "serialNumber": "3053f07554dc",
    "firmwareVersion": "1.0.0",
    "sysexId": 0,
    "idleTimeout": 10000,
    "pdVoltage": 9.0,
    "activeSprite": "",
    "wifiEnabled": false,
    "wifiSsid": "MyNetwork",
    "wifiPassword": "***",
    "midiUsb": { "in": true, "out": true, "thru": false, "route": false, "nano": true },
    "midi2":  { "in": true, "out": true, "thru": false, "route": false, "nano": true }
  }
}
```

Note: `wifiPassword` is always redacted to `"***"` in the serial response; the plaintext is stored in NVS and never emitted over serial.

#### Update device settings

One or more fields can be set in a single message. Only the fields present in the object are modified.

```json
{ "settings": { "debug": true, "ledMaxBrightness": 170, "idleTimeout": 30000 } }
```

ACK: `{"ack":"settings","ok":true}`

Writable settings fields:

| Field | Type | Description |
|-------|------|-------------|
| `debug` | bool | Enable debug output |
| `ledMaxBrightness` | uint8 | Maximum LED ring brightness (0-255) |
| `maxVelocity` | float | Motor velocity limit |
| `maxVoltage` | float | Motor voltage limit |
| `deviceOrientation` | uint16 | LED ring orientation (0-3) |
| `deviceName` | string | Hostname used by OTA and status endpoint |
| `idleTimeout` | uint32 | Idle timeout in ms (0 = never) |
| `sysexId` | uint8 | MIDI sysex device ID |
| `midiUsb` | object | USB MIDI routing flags (in/out/thru/route/nano) |
| `midi2` | object | Hardware MIDI routing flags |
| `wifiEnabled` | bool | Enable WiFi subsystem |
| `wifiSsid` | string | WiFi SSID (persisted to NVS) |
| `wifiPassword` | string | WiFi password (persisted to NVS, never echoed) |

#### Save settings and profiles to filesystem

```json
{ "save": true }
```

Saves settings to `/device_settings.json` on LittleFS using an atomic write-then-rename pattern. Also saves all haptic profiles to `/haptic_profiles.json`. Current profile name is written to NVS.

ACK: `{"ack":"save","ok":true}` (preceded by `{"saved":true}`)

#### Reload settings and profiles from filesystem

```json
{ "load": true }
```

Replaces all in-memory settings and profiles with the stored copies. On CRC or parse failure the firmware falls back to defaults.

ACK: `{"ack":"load","ok":true}`

---

### Display commands

#### Show a message on the device screen

Displays a transient message screen with a title and body text. The `duration` field is accepted by the protocol but timed-dismiss is reserved for a future firmware version; the message persists until the next LCD command.

```json
{ "message": { "title": "Hey!", "text": "Get some work done.", "duration": 5 } }
```

ACK: `{"ack":"message","ok":true}`

#### Control the device screen (raw layout)

Pushes content to the default LCD layout. All fields are optional; omitted fields render blank.

```json
{ "screen": { "title": "The Title", "data1": "Subtitle", "data2": "Text Content", "data3": "Text Content", "data4": "Text Content" } }
```

No ACK. The screen reverts to the current profile display when a profile command is next dispatched.

---

### WiFi commands

Available in all build environments; the `wifi` command updates `DeviceSettings` and calls `wifi_thread.apply_settings()`. In non-WiFi builds, the settings are stored but the radio is not activated.

```json
{ "wifi": { "ssid": "MyNetwork", "password": "s3cr3t", "enabled": true } }
```

All three fields are optional; only present fields are updated. WiFi credentials are persisted in a separate NVS namespace (`nano_wifi`) and are never returned in plaintext over serial.

ACK: `{"ack":"wifi","ok":true}`

To disable WiFi:

```json
{ "wifi": { "enabled": false } }
```

---

### LED ring commands

#### Set ring colors (music profile glow)

Sets the active LED ring palette. Sent by the music profile after extracting dominant album-cover colors. `primary` and `secondary` are 24-bit RGB integers (`0xRRGGBB`). `mode` is reserved for future use; pass `0`.

```json
{"ring": {"primary": 16730458, "secondary": 4654093, "mode": 0}}
```

ACK: `{"ack":"ring","ok":true}`

---

### Seek arc commands

#### Set song progress arc

Updates the LED ring progress arc for the currently playing track. `pos` is a normalized float in `[0.0, 1.0]` (`position_seconds / duration_seconds`). Sent every poll interval by the music profile.

```json
{"seek": {"pos": 0.42}}
```

ACK: `{"ack":"seek","ok":true}`

---

### System commands

#### Reboot into ROM download mode

Drops the running firmware into the ESP32-S3 ROM download mode. Used by `flash.sh` to avoid the BOOT+EN button combination when reflashing. After flashing, the device cannot self-reset (native USB has no RTS reset wiring); tap EN once to boot the new firmware.

```json
{"reboot": "bootloader"}
```

No ACK is sent; the device reboots immediately.

---

### Sprite commands

Sprites are images stored in LittleFS at `/sprites/<name>`. They are accessible to LVGL via the `L:` filesystem driver letter (e.g. `L:/sprites/myimage.bmp`). Maximum 16 sprites, 64 KB per sprite, 512 KB total.

The sprite protocol supports two upload paths: a **base64 chunked** path (JSON throughout) and a **binary chunk-acked** path (used by the music profile). Both paths end with the same `end` and `select` operations.

#### Binary upload path (recommended for large sprites)

The binary path avoids base64 overhead — a 240×240 RGB565 frame (115 200 bytes) uploads in approximately 2.5 s vs approximately 13 s via base64. Each chunk is acknowledged before the next is sent; the lock-step prevents overrunning the device's 8 KB CDC RX queue.

**Begin binary upload**

```json
{"sprite": {"op": "binbegin", "name": "art.rgb565", "size": 115200}}
```

`size` is the total unencoded byte count. ACK: `{"ack":"sprite","ok":true}`.

**Send a binary chunk**

Send the JSON header line, then write the raw bytes immediately after (no newline between them):

```json
{"sprite": {"op": "binchunk", "len": 4096}}
<4096 raw bytes>
```

Wait for the device to commit the chunk to flash and respond before sending the next chunk:

```json
{"binack": {"ok": true}}
```

`len` must equal the actual byte count sent. A mismatch or timeout aborts the upload.

**End and commit**

Same `end` command as the base64 path — CRC-32 covers the full unencoded file:

```json
{"sprite": {"op": "end", "name": "art.rgb565", "crc32": 3735928559}}
```

ACK: `{"ack":"sprite","ok":true}` or `{"ack":"sprite","ok":false,"error":"CRC mismatch"}`

After committing, deselect then re-select so the firmware detects the new frame:

```json
{"sprite": {"op": "select", "name": ""}}
{"sprite": {"op": "select", "name": "art.rgb565"}}
```

#### Base64 upload path (legacy / small sprites)

The sprite protocol uses a chunked upload state machine: `begin` opens a transfer, one or more `data` chunks carry base64-encoded bytes, and `end` validates the CRC-32 and commits the file.

#### Begin upload

```json
{ "sprite": { "op": "begin", "name": "myimage.bmp", "size": 12345 } }
```

`size` is the total unencoded byte count. A new `begin` aborts any in-progress upload.

ACK: `{"ack":"sprite","ok":true}` or `{"ack":"sprite","ok":false,"error":"reason"}`

#### Send a data chunk

```json
{ "sprite": { "op": "data", "seq": 0, "data": "<base64-encoded bytes>" } }
```

`seq` must be the next expected sequence number (0-based, incrementing by 1 per chunk). A sequence error aborts the upload and removes the partial file.

ACK: `{"ack":"sprite","ok":true}` per chunk.

#### End upload and commit

```json
{ "sprite": { "op": "end", "crc32": 3735928559 } }
```

`crc32` is the IEEE 802.3 CRC-32 of the complete unencoded file. If the CRC does not match or the received byte count differs from the declared `size`, the partial file is removed.

ACK: `{"ack":"sprite","ok":true}` or `{"ack":"sprite","ok":false,"error":"CRC mismatch"}`

#### List sprites

```json
{ "sprite": { "op": "list" } }
```

ACK: `{"ack":"sprite","ok":true,"error":"name1,name2,name3"}` (sprite names are comma-separated in the `error` field of a successful response — this is a known API quirk).

#### Select active sprite

```json
{ "sprite": { "op": "select", "name": "myimage.bmp" } }
```

Sets `DeviceSettings.activeSprite`. Pass an empty `name` or omit it to deselect.

ACK: `{"ack":"sprite","ok":true}`

#### Delete sprite

```json
{ "sprite": { "op": "delete", "name": "myimage.bmp" } }
```

ACK: `{"ack":"sprite","ok":true}` or `{"ack":"sprite","ok":false,"error":"not found"}`

---

## Command summary table

| Command key | Direction | Mutates | ACK key | Notes |
|-------------|-----------|---------|---------|-------|
| `profiles: "#all"` | host→device | no | — | Returns profile list |
| `profiles: [...]` | host→device | yes | `profiles` | Reorder / delete |
| `profile: "<name>"` | host→device | no | — | Returns profile object |
| `profile` + `updates` | host→device | yes | `profile` | Create / update profile |
| `current: "<name>"` | host→device | yes | `current` | Set active profile |
| `R: "..."` | host→device | yes | — | SimpleFOC motor register |
| `recalibrate: true` | host→device | yes | `recalibrate` | Force motor recalibration |
| `settings: "?"` | host→device | no | — | Returns settings object |
| `settings: {...}` | host→device | yes | `settings` | Update settings fields |
| `save: true` | host→device | yes | `save` | Persist to LittleFS |
| `load: true` | host→device | yes | `load` | Reload from LittleFS |
| `message: {...}` | host→device | yes | `message` | Show message screen |
| `screen: {...}` | host→device | no | — | Raw LCD layout update |
| `wifi: {...}` | host→device | yes | `wifi` | WiFi credentials / toggle |
| `sprite: {...}` | host→device | yes | `sprite` | Sprite store operations (base64 and binary paths) |
| `ring: {...}` | host→device | yes | `ring` | Set LED ring palette (music profile) |
| `seek: {"pos":…}` | host→device | yes | `seek` | Set song progress arc (0.0–1.0) |
| `reboot: "bootloader"` | host→device | yes | — | Drop into ROM download mode (no ACK; device reboots) |

---

## Integrations

This section describes how an external application (a script, desktop app, or automation tool) subscribes to events and sends commands.

### Establishing a connection

**Serial:** Open the device's USB-CDC port at 115200 baud 8N1. The device identifies itself by USB VID/PID; known pairs are `239A:8010` and `303A:1001`. On connection the device emits `{"ack":"boot","ok":true}` and immediately dispatches the current settings and profile configuration.

**TCP (WiFi build):** Open a raw TCP connection to `<device-ip>:3333`. On connect the device emits a greeting object. The same newline-delimited JSON messages are used.

### Receiving events

Read newline-delimited lines from the transport. Each line beginning with `{` is a JSON message. Lines that do not begin with `{` are diagnostic output and should be logged, not parsed.

Dispatch by key:

- `ack` — ACK for a prior mutating command
- `error` — error from the device (may or may not be in response to a command)
- `debug` — diagnostic string
- `idle` — idle heartbeat (ms since last interaction)
- `saved` — settings/profiles were written to flash
- `current` — active profile changed
- `kd` — key-down event (also contains `ks`)
- `ku` — key-up event (also contains `ks`)
- `p` / `a` / `t` / `v` — knob telemetry (may appear together on one line)
- `r` — FOC motor register echo
- `profile` — profile object response
- `profiles` — profile list response
- `settings` — settings object response

### Sending commands

Serialize the command as a JSON object and write it as a single line terminated by `\n`. No partial writes; the device discards frames that do not produce a valid JSON object within 50 ms.

Because the device may emit unsolicited events at any time, integrators should match ACKs by the `ack` key value rather than by position in the stream.

### Saving state

The device does not auto-save. Call `{"save":true}` explicitly after any changes that must survive a power cycle. The save uses an atomic write-then-rename on LittleFS to prevent corruption on unexpected power loss.

### Typical integration sequence

```
1. Open port / connect to <device-ip>:3333 (TCP)
2. Receive {"ack":"boot","ok":true}
3. Query profiles: send {"profiles":"#all"}
4. Receive {"profiles":[...],"current":"..."}
5. Listen for kd/ku/a/t/v events
6. On user interaction, send updates (e.g. {"profile":"P","updates":{...}})
7. Receive {"ack":"profile","ok":true}
8. Send {"save":true} to persist
9. Receive {"saved":true} and {"ack":"save","ok":true}
```

### WiFi build: TCP transport architecture

In the `WIFI_ENABLED` build, inbound TCP lines are forwarded to `com_thread` via a FreeRTOS queue (`net_submit()`); `com_thread` parses them identically to serial input. All outbound frames — ACKs, events, and replies — pass through `com_thread.emit()`, which enqueues a heap copy to the net-out queue that `wifi_thread` drains each loop iteration and sends to every connected TCP client. The serial and TCP paths share a single emit point; no frames are missed on either transport.

---

## Power delivery

The firmware uses the STUSB4500 USB-PD controller to negotiate power. On boot, `init_pd()` in `hmi_thread` programs the STUSB4500 NVM with two PDOs (PDO1: 5 V / 3 A; PDO2: 9 V / 3 A), reads back the negotiated PDO from register `0x91` (RDO_REG_STATUS bits [30:28]), and stores the clamped voltage (range [5.0, 9.0] V) in `DeviceSettings.pdVoltage`. The FOC thread reads `pdVoltage` at startup and sets `driver.voltage_power_supply` and `driver.voltage_limit` accordingly. If no STUSB4500 is detected, the firmware defaults to 5.0 V. The negotiated voltage is visible in the `settings` response as `pdVoltage`.

---

## Build environments

| PlatformIO environment | WiFi | Audio | Notes |
|------------------------|------|-------|-------|
| `nanofoc_d` | no | no | Default; safe core only |
| `nanofoc_d_wifi` | yes | no | Adds raw TCP JSON transport on port 3333 |
| `nanofoc_d_audio` | no | yes | Audio click/chime feedback |
| `nanofoc_d_full` | yes | yes | All features |

All environments use LittleFS (`board_build.filesystem = littlefs`) and the dual-OTA partition table in `boards/nano_partitions.csv` (app0/app1 each at 0x140000, plus a data partition).

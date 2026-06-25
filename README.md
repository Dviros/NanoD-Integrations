# Nano_D++ Integrations

Documentation and integration resources for the [Binaris Nano_D++](https://store.binaris.io/products/nano_d-sensory-hid) ESP32-S3 haptic knob.

## Serial API Reference

[SERIAL_API.md](SERIAL_API.md) is the authoritative reference for the JSON protocol used to configure and control the device. It covers:

- Serial (USB-CDC) and WebSocket (WiFi build) transports
- Full command set: profiles, motor, settings, display, WiFi, and sprite commands
- ACK message shapes and error handling
- Event messages: key events, knob telemetry (`a`/`t`/`v`), idle heartbeat
- Integration patterns: connection sequence, event dispatch, saving state
- Power delivery (USB-PD via STUSB4500) and build environments

## ZERO/ONE Configuration Suite

[ZERO/ONE](https://github.com/katbinaris/zeroone) is the official cross-platform desktop application for configuring the Nano_D++. It uses the serial JSON API documented here and is the reference implementation for host-side integration.

## Firmware build environments

| Environment | WiFi | Audio |
|-------------|------|-------|
| `nanofoc_d` | no | no |
| `nanofoc_d_wifi` | yes | no |
| `nanofoc_d_audio` | no | yes |
| `nanofoc_d_full` | yes | yes |

All environments use LittleFS and a dual-OTA partition layout. See `fw/platformio.ini` for details.

## Questions and support

Join the [Binaris Discord](https://discord.gg/mVTvppcfp6) for help and discussion.

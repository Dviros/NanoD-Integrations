#!/usr/bin/env bash
# flash.sh — buttonless(ish) firmware flash for Nano_D++.
#
# Replaces the BOOT+EN dance: sends the firmware's {"reboot":"bootloader"} command
# over serial to drop the device into ROM download mode, waits for the download
# port, then flashes. esptool can't reset this board back out (native USB has no
# RTS->reset wiring), so after the flash you tap EN once to boot.
#
# Usage:  ./flash.sh [path/to/firmware.bin]
#   default firmware: ../../fw/.pio/build/nanofoc_d_wifi/firmware.bin
set -euo pipefail

FW="${1:-$(cd "$(dirname "$0")/../../fw" && pwd)/.pio/build/nanofoc_d_wifi/firmware.bin}"
ESPTOOL="$(ls ~/.platformio/packages/tool-esptoolpy/esptool.py 2>/dev/null | head -1)"
[ -f "$FW" ]      || { echo "firmware not found: $FW"; exit 1; }
[ -n "$ESPTOOL" ] || { echo "esptool not found"; exit 1; }

port_now() { ls /dev/cu.usbmodem* 2>/dev/null | head -1; }

APP_PORT="$(port_now)"
if [ -n "$APP_PORT" ]; then
    echo "[flash] app port: $APP_PORT"
else
    echo "[flash] no device port right now — will wait for auto/manual download mode"
fi

# 1+2) Ask the running firmware to reboot into ROM download mode, wait for the
#      downloader to enumerate. The reboot command sometimes wedges USB entirely
#      (device drops off the bus) — retry up to 3x, then fall back to waiting for
#      a manual BOOT-hold entry.
DL_PORT=""
for attempt in 1 2 3; do
    p="$(port_now)"
    if [ -n "$p" ]; then
        echo "[flash] attempt $attempt: requesting download mode via {\"reboot\":\"bootloader\"} ..."
        python3 - "$p" <<'PY' || true
import serial, sys, time
try:
    s = serial.Serial(sys.argv[1], 115200, timeout=1)
    s.write(b'{"reboot":"bootloader"}\n'); s.flush(); time.sleep(0.2); s.close()
except Exception as e:
    print(f"  (reboot send failed: {e})")
PY
    fi
    for _ in $(seq 1 30); do        # up to ~15 s per attempt
        sleep 0.5
        p="$(port_now)"
        if [ -n "$p" ] && python3 "$ESPTOOL" --chip esp32s3 --port "$p" \
                --before no_reset --after no_reset --connect-attempts 1 chip_id >/dev/null 2>&1; then
            DL_PORT="$p"; break
        fi
    done
    [ -n "$DL_PORT" ] && break
done
if [ -z "$DL_PORT" ]; then
    echo "[flash] auto-entry failed. MANUAL: unplug USB → hold BOOT → plug in while holding → release."
    echo "[flash] waiting up to 60 s for manual download mode ..."
    for _ in $(seq 1 120); do
        sleep 0.5
        p="$(port_now)"
        if [ -n "$p" ] && python3 "$ESPTOOL" --chip esp32s3 --port "$p" \
                --before no_reset --after no_reset --connect-attempts 1 chip_id >/dev/null 2>&1; then
            DL_PORT="$p"; break
        fi
    done
fi
[ -n "$DL_PORT" ] || { echo "[flash] no download port — giving up"; exit 1; }
echo "[flash] download port: $DL_PORT"

# 3) Flash (leave the chip in download mode; it can't self-reset on native USB).
python3 "$ESPTOOL" --chip esp32s3 --port "$DL_PORT" --before no_reset --after no_reset \
    write_flash --flash_mode keep --flash_freq keep --flash_size keep 0x10000 "$FW"

echo
echo "[flash] DONE — tap EN once to boot the new firmware."

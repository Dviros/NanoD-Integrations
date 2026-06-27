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
[ -n "$APP_PORT" ] || { echo "no device port found"; exit 1; }
echo "[flash] app port: $APP_PORT"

# 1) Ask the running firmware to reboot into ROM download mode (no buttons).
#    Use pyserial, not a shell `>` redirect — the redirect opens/closes the tty too
#    fast and the line often never reaches the device.
echo "[flash] requesting download mode via {\"reboot\":\"bootloader\"} ..."
python3 - "$APP_PORT" <<'PY' || true
import serial, sys, time
try:
    s = serial.Serial(sys.argv[1], 115200, timeout=1)
    s.write(b'{"reboot":"bootloader"}\n'); s.flush(); time.sleep(0.2); s.close()
except Exception as e:
    print(f"  (reboot send failed: {e} — hold BOOT, tap EN, release BOOT to enter manually)")
PY

# 2) Wait for the device to re-enumerate as the downloader (port changes/returns).
echo "[flash] waiting for download port ..."
DL_PORT=""
for _ in $(seq 1 40); do            # up to ~20 s
    sleep 0.5
    p="$(port_now)"
    if [ -n "$p" ] && python3 "$ESPTOOL" --chip esp32s3 --port "$p" \
            --before no_reset --after no_reset --connect-attempts 1 chip_id >/dev/null 2>&1; then
        DL_PORT="$p"; break
    fi
done
[ -n "$DL_PORT" ] || { echo "[flash] download port never appeared — hold BOOT, tap EN, release BOOT, retry"; exit 1; }
echo "[flash] download port: $DL_PORT"

# 3) Flash (leave the chip in download mode; it can't self-reset on native USB).
python3 "$ESPTOOL" --chip esp32s3 --port "$DL_PORT" --before no_reset --after no_reset \
    write_flash --flash_mode keep --flash_freq keep --flash_size keep 0x10000 "$FW"

echo
echo "[flash] DONE — tap EN once to boot the new firmware."

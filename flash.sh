#!/usr/bin/env bash
set -euo pipefail

# This Tasmota 15.6 single-channel switch serves state through the web UI's
# status fragment. Its /cm?cmnd endpoint currently closes the HTTP connection.
TASMOTA_URL=${EMONOS_TASMOTA_URL:-http://172.16.0.137}
FLASH_DEVICE=${EMONOS_FLASH_DEVICE:-/dev/disk/by-id/usb-LinuxAut_sdFST_HS-SD_MMC_00048.01903-0:0}
FLASH_MODEL=${EMONOS_FLASH_MODEL:-sdFST_HS-SD_MMC}
FLASH_SERIAL=${EMONOS_FLASH_SERIAL:-00048.01903}
SD_WAIT_SECONDS=${EMONOS_SD_WAIT_SECONDS:-60}
SDMUX_DEVICE=${EMONOS_SDMUX_DEVICE:-/dev/sg0}
SDMUX=${EMONOS_SDMUX:-$(command -v usbsdmux || printf '%s' "$HOME/Dev/usbsdmux/venv/bin/usbsdmux")}
IMAGE=${EMONOS_IMAGE:-output/rpi4/images/emonos-rpi4.img}
IMAGER=${EMONOS_IMAGER:-$(command -v imager || printf '%s' "$HOME/.local/bin/imager")}
power_was_off=0

case "$SD_WAIT_SECONDS" in
    ''|*[!0-9]*) echo 'EMONOS_SD_WAIT_SECONDS must be a positive integer.' >&2; exit 2 ;;
esac
if [ "$SD_WAIT_SECONDS" -lt 1 ]; then
    echo 'EMONOS_SD_WAIT_SECONDS must be greater than zero.' >&2
    exit 2
fi

power_state() {
    local fragment state
    fragment=$(curl -fsS --max-time 5 "${TASMOTA_URL%/}/?m=1")
    state=$(printf '%s' "$fragment" | grep -oE '>(ON|OFF)</td>' | \
        head -n 1 | sed -e 's/^>//' -e 's#</td>$##')
    case "$state" in
        ON|OFF) printf '%s\n' "$state" ;;
        *) echo "Unable to parse Tasmota power state" >&2; return 1 ;;
    esac
}

set_power_state() {
    local desired=$1 current attempt
    case "$desired" in ON|OFF) ;; *) echo 'Expected ON or OFF' >&2; return 2 ;; esac
    current=$(power_state)
    if [ "$current" = "$desired" ]; then
        echo "Tasmota single-channel power is already $desired"
        return 0
    fi
    # Tasmota 15's working UI endpoint is a toggle. Inspect first, send once,
    # and poll status. Never blindly retry a toggle after an HTTP error.
    curl -fsS --max-time 5 --get \
        --data-urlencode 'm=1' --data-urlencode 'o=1' \
        "${TASMOTA_URL%/}/" >/dev/null 2>&1 || true
    for ((attempt = 0; attempt < 20; attempt++)); do
        if current=$(power_state) && [ "$current" = "$desired" ]; then
            echo "Tasmota single-channel power is $desired"
            return 0
        fi
        sleep 0.5
    done
    echo "Power toggle did not reach $desired; inspect the switch before retrying" >&2
    return 1
}

restore_after_failure() {
    local result=$?
    if [ "$power_was_off" -eq 1 ]; then
        echo 'Flash failed; restoring Pi power after returning SD mux to DUT.' >&2
        sudo "$SDMUX" "$SDMUX_DEVICE" dut || true
        set_power_state ON || true
    fi
    exit "$result"
}

wait_for_sd_card() {
    local deadline=$((SECONDS + SD_WAIT_SECONDS)) device_type device_bytes properties model serial
    echo "Waiting up to ${SD_WAIT_SECONDS}s for $FLASH_MODEL (serial $FLASH_SERIAL) as $FLASH_DEVICE..."
    while [ "$SECONDS" -lt "$deadline" ]; do
        if [ -b "$FLASH_DEVICE" ]; then
            if command -v udevadm >/dev/null 2>&1; then
                sudo udevadm settle --timeout=5 || true
            else
                echo 'udevadm is required to verify the USB SD-mux identity.' >&2
                return 1
            fi
            properties=$(udevadm info --query=property --name="$FLASH_DEVICE")
            model=$(printf '%s\n' "$properties" | sed -n 's/^ID_MODEL=//p')
            serial=$(printf '%s\n' "$properties" | sed -n 's/^ID_SERIAL_SHORT=//p')
            if [ "$model" != "$FLASH_MODEL" ] || [ "$serial" != "$FLASH_SERIAL" ]; then
                echo "Refusing unexpected disk: model='$model' serial='$serial'." >&2
                return 1
            fi
            device_type=$(lsblk -dn -o TYPE "$FLASH_DEVICE" | xargs)
            if [ "$device_type" != disk ]; then
                echo "$FLASH_DEVICE appeared, but is type '$device_type', not a whole disk." >&2
                return 1
            fi
            # The USB reader can enumerate as a zero-byte block device before
            # media is inserted. blockdev reports "No medium found" in that
            # state; keep polling rather than leaving set -e through the trap.
            if ! device_bytes=$(sudo blockdev --getsize64 "$FLASH_DEVICE" 2>/dev/null); then
                sleep 0.5
                continue
            fi
            if [ "$device_bytes" -lt "$image_bytes" ]; then
                echo "SD reader is present but media is not ready ($device_bytes bytes; waiting for $image_bytes)." >&2
                sleep 0.5
                continue
            fi
            echo "Detected SD card:"
            lsblk -dn -o NAME,SIZE,MODEL,TRAN "$FLASH_DEVICE"
            FLASH_DEVICE=$(readlink -f "$FLASH_DEVICE")
            return 0
        fi
        sleep 0.5
    done
    echo "Timed out waiting for $FLASH_DEVICE after switching SD mux to host." >&2
    echo "Check the mux, card, and EMONOS_FLASH_DEVICE; power will be restored." >&2
    return 1
}

case "${1:-flash}" in
    status) power_state; exit 0 ;;
    on) set_power_state ON; exit ;;
    off) set_power_state OFF; exit ;;
    flash) ;;
    *) echo "Usage: $0 [status|on|off|flash]" >&2; exit 2 ;;
esac

if [ ! -f "$IMAGE" ]; then echo "Image not found: $IMAGE" >&2; exit 1; fi
if [ ! -x "$SDMUX" ]; then echo "SD mux tool is not executable: $SDMUX" >&2; exit 1; fi
mux_state=$(sudo "$SDMUX" "$SDMUX_DEVICE" get)
case "$mux_state" in
    host) echo "SD mux is already connected to host." ;;
    dut) echo "SD mux is connected to DUT; will switch it to host after power-off." ;;
    *) echo "Unexpected SD mux state: '$mux_state' (expected host or dut)." >&2; exit 1 ;;
esac
if [ ! -x "$IMAGER" ]; then echo "Imager is not executable: $IMAGER" >&2; exit 1; fi
image_bytes=$(stat -c %s "$IMAGE")

echo "Image SHA-256: $(sha256sum "$IMAGE" | awk '{print $1}')"
echo "Writing $IMAGE to $FLASH_DEVICE (this erases the destination card)."
sudo -v
trap restore_after_failure EXIT
power_was_off=1
set_power_state OFF
if [ "$mux_state" = dut ]; then
    sudo "$SDMUX" "$SDMUX_DEVICE" host
fi
wait_for_sd_card
sudo "$IMAGER" --cli "$IMAGE" "$FLASH_DEVICE"
sync
sudo "$SDMUX" "$SDMUX_DEVICE" dut
set_power_state ON
power_was_off=0
trap - EXIT

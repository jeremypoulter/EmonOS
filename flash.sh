#!/usr/bin/env bash
set -euo pipefail

# This Tasmota 15.6 single-channel switch serves state through the web UI's
# status fragment. Its /cm?cmnd endpoint currently closes the HTTP connection.
TASMOTA_URL=${EMONOS_TASMOTA_URL:-http://172.16.0.137}
FLASH_DEVICE=${EMONOS_FLASH_DEVICE:-/dev/disk/by-id/usb-LinuxAut_sdFST_HS-SD_MMC_00048.01903-0:0}
FLASH_MODEL=${EMONOS_FLASH_MODEL:-sdFST_HS-SD_MMC}
FLASH_SERIAL=${EMONOS_FLASH_SERIAL:-00048.01903}
SD_WAIT_SECONDS=${EMONOS_SD_WAIT_SECONDS:-60}
# Pi runtime layout needs the fixed A/B/boot partitions plus 6 GiB of data.
# Keep this aligned with board/genimage partition sizes and the R5 minimum.
MIN_CARD_BYTES=${EMONOS_MIN_CARD_BYTES:-7658835968}
SDMUX_DEVICE=${EMONOS_SDMUX_DEVICE:-}
SDMUX_SCSI_MODEL=${EMONOS_SDMUX_SCSI_MODEL:-sdFST HS-SD/MMC}
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
case "$MIN_CARD_BYTES" in
    ''|*[!0-9]*) echo 'EMONOS_MIN_CARD_BYTES must be a positive integer.' >&2; exit 2 ;;
esac

# Read and normalize the single-channel state from Tasmota's UI status fragment.
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

# Set a requested state idempotently; the available Tasmota UI action is a toggle.
set_power_state() {
    local desired=$1 current attempt
    case "$desired" in ON|OFF) ;; *) echo 'Expected ON or OFF' >&2; return 2 ;; esac
    if ! current=$(power_state); then
        echo 'Unable to read Tasmota power state; refusing to toggle.' >&2
        return 1
    fi
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

# Return the SD mux to DUT and confirm that the hardware reports the new mode.
switch_mux_to_dut() {
    local state
    sudo "$SDMUX" "$SDMUX_DEVICE" dut || return 1
    state=$(sudo "$SDMUX" "$SDMUX_DEVICE" get) || return 1
    if [ "$state" != dut ]; then
        echo "SD mux reports '$state', not DUT." >&2
        return 1
    fi
}

# Best-effort failure recovery; never energize the Pi unless DUT mode is confirmed.
restore_after_failure() {
    local result=$?
    if [ "$power_was_off" -eq 1 ]; then
        echo 'Flash failed; attempting to return the SD mux to DUT.' >&2
        if switch_mux_to_dut; then
            if ! set_power_state ON; then
                echo 'Could not confirm Pi power restored; inspect the switch.' >&2
            fi
        else
            echo 'SD mux did not return to DUT; leaving Pi power off for manual recovery.' >&2
        fi
    fi
    exit "$result"
}

# Resolve the SCSI-generic control node by model; sg numbering is enumeration-dependent.
resolve_sdmux_device() {
    local model_file model candidate matches=0
    if [ -n "$SDMUX_DEVICE" ]; then
        return 0
    fi
    for model_file in /sys/class/scsi_generic/sg*/device/model; do
        [ -r "$model_file" ] || continue
        model=$(sed 's/[[:space:]]*$//' "$model_file")
        if [ "$model" = "$SDMUX_SCSI_MODEL" ]; then
            candidate="/dev/$(basename "$(dirname "$(dirname "$model_file")")")"
            SDMUX_DEVICE=$candidate
            matches=$((matches + 1))
        fi
    done
    case "$matches" in
        1) echo "Using SD mux control node $SDMUX_DEVICE ($SDMUX_SCSI_MODEL)." ;;
        0)
            echo "No SCSI-generic device matched '$SDMUX_SCSI_MODEL'; set EMONOS_SDMUX_DEVICE." >&2
            return 1
            ;;
        *)
            echo "Multiple SCSI devices matched '$SDMUX_SCSI_MODEL'; set EMONOS_SDMUX_DEVICE." >&2
            return 1
            ;;
    esac
}

# Revalidate the by-id target immediately before a destructive write.
verify_flash_device() {
    local mux_state properties model serial device_type device_bytes write_properties write_model write_serial required_bytes
    mux_state=$(sudo "$SDMUX" "$SDMUX_DEVICE" get) || return 1
    if [ "$mux_state" != host ]; then
        echo "SD mux changed to '$mux_state'; expected host before writing." >&2
        return 1
    fi
    [ -b "$FLASH_DEVICE" ] || { echo "SD card is not present at $FLASH_DEVICE." >&2; return 1; }
    sudo udevadm settle --timeout=5 || true
    properties=$(udevadm info --query=property --name="$FLASH_DEVICE") || return 1
    model=$(printf '%s\n' "$properties" | sed -n 's/^ID_MODEL=//p')
    serial=$(printf '%s\n' "$properties" | sed -n 's/^ID_SERIAL_SHORT=//p')
    if [ "$model" != "$FLASH_MODEL" ] || [ "$serial" != "$FLASH_SERIAL" ]; then
        echo "Refusing unexpected disk before write: model='$model' serial='$serial'." >&2
        return 1
    fi
    device_type=$(lsblk -dn -o TYPE "$FLASH_DEVICE" | xargs)
    [ "$device_type" = disk ] || { echo "$FLASH_DEVICE is not a whole disk." >&2; return 1; }
    FLASH_WRITE_DEVICE=$(readlink -e "$FLASH_DEVICE") || return 1
    case "$FLASH_WRITE_DEVICE" in
        /dev/sd*) ;;
        *) echo "Resolved flash target is not a /dev/sd* disk: $FLASH_WRITE_DEVICE." >&2; return 1 ;;
    esac
    write_properties=$(udevadm info --query=property --name="$FLASH_WRITE_DEVICE") || return 1
    write_model=$(printf '%s\n' "$write_properties" | sed -n 's/^ID_MODEL=//p')
    write_serial=$(printf '%s\n' "$write_properties" | sed -n 's/^ID_SERIAL_SHORT=//p')
    if [ "$write_model" != "$FLASH_MODEL" ] || [ "$write_serial" != "$FLASH_SERIAL" ]; then
        echo "Resolved write node changed identity: model='$write_model' serial='$write_serial'." >&2
        return 1
    fi
    if [ "$(readlink -e "$FLASH_DEVICE")" != "$FLASH_WRITE_DEVICE" ]; then
        echo 'Stable by-id path changed while validating the flash target.' >&2
        return 1
    fi
    device_type=$(lsblk -dn -o TYPE "$FLASH_WRITE_DEVICE" | xargs)
    [ "$device_type" = disk ] || { echo "$FLASH_WRITE_DEVICE is not a whole disk." >&2; return 1; }
    device_bytes=$(sudo blockdev --getsize64 "$FLASH_WRITE_DEVICE") || return 1
    required_bytes=$image_bytes
    [ "$required_bytes" -ge "$MIN_CARD_BYTES" ] || required_bytes=$MIN_CARD_BYTES
    [ "$device_bytes" -ge "$required_bytes" ] || {
        echo "$FLASH_DEVICE is too small: need at least $required_bytes bytes (image $image_bytes; runtime layout $MIN_CARD_BYTES)." >&2
        return 1
    }
}

# Wait through reader enumeration and the no-media state until capacity is usable.
wait_for_sd_card() {
    local deadline=$((SECONDS + SD_WAIT_SECONDS)) device_type device_bytes properties model serial required_bytes
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
            required_bytes=$image_bytes
            [ "$required_bytes" -ge "$MIN_CARD_BYTES" ] || required_bytes=$MIN_CARD_BYTES
            if [ "$device_bytes" -lt "$required_bytes" ]; then
                echo "SD reader is present but media is not ready ($device_bytes bytes; waiting for $required_bytes)." >&2
                sleep 0.5
                continue
            fi
            echo "Detected SD card:"
            lsblk -dn -o NAME,SIZE,MODEL,TRAN "$FLASH_DEVICE"
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
    mux-status)
        resolve_sdmux_device
        sudo "$SDMUX" "$SDMUX_DEVICE" get
        exit
        ;;
    on) set_power_state ON; exit ;;
    off) set_power_state OFF; exit ;;
    flash) ;;
    *) echo "Usage: $0 [status|mux-status|on|off|flash]" >&2; exit 2 ;;
esac

if [ ! -f "$IMAGE" ]; then echo "Image not found: $IMAGE" >&2; exit 1; fi
resolve_sdmux_device
if [ ! -x "$SDMUX" ]; then echo "SD mux tool is not executable: $SDMUX" >&2; exit 1; fi
mux_state=$(sudo "$SDMUX" "$SDMUX_DEVICE" get)
case "$mux_state" in
    host) echo "SD mux is already connected to host." ;;
    dut) echo "SD mux is connected to DUT; will switch it to host after power-off." ;;
    *) echo "Unexpected SD mux state: '$mux_state' (expected host or dut)." >&2; exit 1 ;;
esac
if [ ! -x "$IMAGER" ]; then echo "Imager is not executable: $IMAGER" >&2; exit 1; fi
image_bytes=$(stat -c %s "$IMAGE")

echo "Writing $IMAGE to $FLASH_DEVICE (this erases the destination card)."
sudo -v
trap restore_after_failure EXIT
power_was_off=1
set_power_state OFF
if [ "$mux_state" = dut ]; then
    sudo "$SDMUX" "$SDMUX_DEVICE" host
fi
wait_for_sd_card
verify_flash_device
# Imager requires the canonical whole-disk path for this fixed USB reader.
# Keep the stable by-id path for discovery/revalidation; pass its just-checked
# canonical node to Imager without --enable-writing-system-drives.
sudo "$IMAGER" --cli "$IMAGE" "$FLASH_WRITE_DEVICE"
sync
switch_mux_to_dut
set_power_state ON
power_was_off=0
trap - EXIT

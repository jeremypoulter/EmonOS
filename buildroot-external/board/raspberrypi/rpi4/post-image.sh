#!/bin/bash
set -euo pipefail
umask 022

BOARD_DIR=$(dirname "$0")
EXTERNAL_DIR=$(CDPATH='' cd -- "${BOARD_DIR}/../../.." && pwd)
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
ROOTPATH_TMP=$(mktemp -d)
trap 'rm -rf "$ROOTPATH_TMP"' EXIT

mkdir -p "${BINARIES_DIR}/extlinux"
cp "${BOARD_DIR}/extlinux.conf.in" "${BINARIES_DIR}/extlinux/extlinux.conf"

files=("bcm2711-rpi-4-b.dtb" "u-boot.bin" "boot.scr")
for file in "${BINARIES_DIR}"/rpi-firmware/*; do
    name=$(basename "$file")
    cp -aT "$file" "${BINARIES_DIR}/${name}"
    files+=("${name}")
done

# Firmware package install stamps do not track changes to the board config.
# Refresh it on every image assembly, after copying the firmware payload.
cp "${BOARD_DIR}/config.txt" "${BINARIES_DIR}/config.txt"
# The firmware's stock cmdline names the old writable p2 root. Preserve only
# serial diagnostics here; boot.cmd supplies the selected system slot.
printf '%s\n' 'console=ttyS0,115200 earlycon=bcm2835aux,mmio32,0xfe215040 8250.nr_uarts=1' > "${BINARIES_DIR}/cmdline.txt"

"${BUILD_DIR}/uboot-2026.01/tools/mkimage" -A arm64 -T script -C none \
    -d "${BOARD_DIR}/boot.cmd" "${BINARIES_DIR}/boot.scr"

for name in "${files[@]}"; do
    find "${BINARIES_DIR}/${name}" -depth -print0 |
        xargs -0 -r touch -h -d "@$SOURCE_DATE_EPOCH"
done

while IFS= read -r line; do
    if [ "$line" = "#BOOT_FILES#" ]; then
        printf '\t\t\t"%s",\n' "${files[@]}"
    else
        printf '%s\n' "$line"
    fi
done < "${BOARD_DIR}/genimage.cfg.in" > "${BINARIES_DIR}/genimage.cfg"
"${EXTERNAL_DIR}/genimage/prepare-images.sh" "$BOARD_DIR" Image
printf 'BOOT_ORDER=A B\nBOOT_A_LEFT=1\nBOOT_B_LEFT=0\n' > "${BUILD_DIR}/emonos-bootstate.env"
"${BUILD_DIR}/uboot-2026.01/tools/mkenvimage" -s 16384 \
    -o "${BUILD_DIR}/emonos-bootstate.bin" "${BUILD_DIR}/emonos-bootstate.env"
dd if="${BUILD_DIR}/emonos-bootstate.bin" of="${BINARIES_DIR}/bootstate.img" \
    bs=512 conv=notrunc status=none

rm -rf "${GENIMAGE_TMP}"
genimage \
    --rootpath "${ROOTPATH_TMP}" \
    --tmppath "${GENIMAGE_TMP}" \
    --inputpath "${BINARIES_DIR}" \
    --outputpath "${BINARIES_DIR}" \
    --config "${BINARIES_DIR}/genimage.cfg"
"${EXTERNAL_DIR}/ota/bundle.sh" rpi4

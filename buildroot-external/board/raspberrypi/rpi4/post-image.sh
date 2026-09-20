#!/bin/bash
set -euo pipefail

BOARD_DIR=$(dirname "$0")
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
ROOTPATH_TMP=$(mktemp -d)
trap 'rm -rf "$ROOTPATH_TMP"' EXIT

mkdir -p "${BINARIES_DIR}/extlinux"
cp "${BOARD_DIR}/extlinux.conf.in" "${BINARIES_DIR}/extlinux/extlinux.conf"

files=("Image" "bcm2711-rpi-4-b.dtb" "u-boot.bin" "extlinux")
for file in "${BINARIES_DIR}"/rpi-firmware/*; do
    name=$(basename "$file")
    cp -aT "$file" "${BINARIES_DIR}/${name}"
    files+=("${name}")
done

# Firmware package install stamps do not track changes to the board config.
# Refresh it on every image assembly, after copying the firmware payload.
cp "${BOARD_DIR}/config.txt" "${BINARIES_DIR}/config.txt"

while IFS= read -r line; do
    if [ "$line" = "#BOOT_FILES#" ]; then
        printf '\t\t\t"%s",\n' "${files[@]}"
    else
        printf '%s\n' "$line"
    fi
done < "${BOARD_DIR}/genimage.cfg.in" > "${BINARIES_DIR}/genimage.cfg"

rm -rf "${GENIMAGE_TMP}"
genimage \
    --rootpath "${ROOTPATH_TMP}" \
    --tmppath "${GENIMAGE_TMP}" \
    --inputpath "${BINARIES_DIR}" \
    --outputpath "${BINARIES_DIR}" \
    --config "${BINARIES_DIR}/genimage.cfg"

#!/bin/sh
set -eu

BOARD_DIR=$(dirname "$0")
ROOT_UUID=$(dumpe2fs "${BINARIES_DIR}/rootfs.ext2" 2>/dev/null | sed -n 's/^Filesystem UUID: *\(.*\)/\1/p')

if [ -z "$ROOT_UUID" ]; then
    echo "Could not determine the root filesystem UUID" >&2
    exit 1
fi

# Buildroot's GRUB package already created bootx64.efi in this tree. The
# board hook supplies only the runtime configuration and partition image.
test -f "${BINARIES_DIR}/efi-part/EFI/BOOT/bootx64.efi"
sed "s/%ROOT_UUID%/$ROOT_UUID/g" "${BOARD_DIR}/grub.cfg.in" > "${BINARIES_DIR}/efi-part/EFI/BOOT/grub.cfg"
sed "s/%ROOT_UUID%/$ROOT_UUID/g" "${BOARD_DIR}/genimage.cfg.in" > "${BINARIES_DIR}/genimage.cfg"

support/scripts/genimage.sh -c "${BINARIES_DIR}/genimage.cfg"

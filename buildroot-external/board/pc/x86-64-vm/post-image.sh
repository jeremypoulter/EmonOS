#!/bin/sh
set -eu
umask 022

BOARD_DIR=$(dirname "$0")
EXTERNAL_DIR=$(CDPATH='' cd -- "${BOARD_DIR}/../../.." && pwd)

# Buildroot's GRUB package already created bootx64.efi in this tree. The
# board hook supplies only the runtime configuration and partition image.
test -f "${BINARIES_DIR}/efi-part/EFI/BOOT/bootx64.efi"
cp "${BOARD_DIR}/grub.cfg.in" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub.cfg"
"${HOST_DIR}/bin/grub-editenv" "${BINARIES_DIR}/efi-part/EFI/BOOT/grubenv" create
"${HOST_DIR}/bin/grub-editenv" "${BINARIES_DIR}/efi-part/EFI/BOOT/grubenv" set ORDER='A B' A_OK=1 B_OK=0 A_TRY=0 B_TRY=0
find "${BINARIES_DIR}/efi-part/EFI" -depth -print0 |
    xargs -0 -r touch -h -d "@$SOURCE_DATE_EPOCH"
cp "${BOARD_DIR}/genimage.cfg.in" "${BINARIES_DIR}/genimage.cfg"
"${EXTERNAL_DIR}/genimage/prepare-images.sh" "$BOARD_DIR" bzImage

support/scripts/genimage.sh -c "${BINARIES_DIR}/genimage.cfg"
"${EXTERNAL_DIR}/ota/bundle.sh" x86-64-vm

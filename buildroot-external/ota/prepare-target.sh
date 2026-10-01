#!/bin/sh
set -eu
umask 022
target=${1:?missing target directory}
directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
keys=${EMONOS_RAUC_KEY_DIR:?run make rauc_dev_keys and build via the top-level Makefile}
version=${EMONOS_VERSION:?missing EMONOS_VERSION}
case "$version" in
    ''|*[!a-zA-Z0-9._+-]*) echo 'Invalid EmonOS version' >&2; exit 1 ;;
esac
if grep -qx 'BR2_x86_64=y' "$BR2_CONFIG"; then
    board=x86-64-vm
    bootloader=grub
    options='grubenv=/mnt/boot/EFI/BOOT/grubenv'
elif grep -qx 'BR2_aarch64=y' "$BR2_CONFIG"; then
    board=rpi4
    bootloader=uboot
    options='boot-attempts=1\nboot-attempts-primary=1'
else
    echo 'Unsupported RAUC board' >&2
    exit 1
fi
test -s "$keys/dev-cert.pem" && test -s "$keys/dev-key.pem" || {
    echo 'Missing RAUC credentials; run make rauc_dev_keys first' >&2
    exit 1
}
mkdir -p "$target/etc/rauc" "$target/usr/lib/emonos"
install -m 0644 "$keys/dev-cert.pem" "$target/etc/rauc/keyring.pem"
sed -e "s/@COMPATIBLE@/emonos-$board/" -e "s/@BOOTLOADER@/$bootloader/" \
    -e "s|@BOOTLOADER_OPTIONS@|$options|" "$directory/system.conf.in" > "$target/etc/rauc/system.conf"
printf '%s\n' "$version" > "$target/usr/lib/emonos/version"
cat > "$target/usr/lib/os-release" <<EOF
NAME=EmonOS
ID=emonos
VERSION="$version"
VERSION_ID="$version"
PRETTY_NAME="EmonOS $version ($board)"
EOF
ln -sf ../usr/lib/os-release "$target/etc/os-release"
# Consumed by the factory data-image hook only, never copied onto existing
# target data during an update. Both factory slot payloads have this version.
printf '%s\n' "emonos-$board" > "$target/usr/lib/emonos/compatible"

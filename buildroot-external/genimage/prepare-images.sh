#!/bin/sh
set -eu
umask 022

test -n "${1:?missing board directory}"
kernel_file=${2:?missing kernel image name}
images=${BINARIES_DIR:?missing BINARIES_DIR}
: "${SOURCE_DATE_EPOCH:?set a deterministic SOURCE_DATE_EPOCH (the top-level Makefile does this)}"
external_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
kernel_dir=$(mktemp -d)
trap 'rm -rf "$kernel_dir"' EXIT
chmod 755 "$kernel_dir"

cp "$images/$kernel_file" "$kernel_dir/$kernel_file"
# U-Boot's squashfs reader supports gzip but not zstd. The system root remains
# zstd compressed; only the small kernel-slot filesystem uses gzip.
# mksquashfs uses SOURCE_DATE_EPOCH for its creation time and clamps file
# timestamps. Supplying explicit timestamp flags as well is an error.
mksquashfs "$kernel_dir" "$images/kernel.img" -noappend -comp gzip -b 131072 -all-root >/dev/null
test "$(wc -c < "$images/kernel.img")" -lt $((32 * 1024 * 1024))
truncate -s 32M "$images/kernel.img"
test "$(wc -c < "$images/rootfs.squashfs")" -lt $((512 * 1024 * 1024))
cp "$images/rootfs.squashfs" "$images/system.img"
truncate -s 512M "$images/system.img"

truncate -s 8M "$images/bootstate.img"

# The two board hooks write their boot-filesystem recipe and the start of the
# disk recipe; append the same six A/B/data partitions to both.
cat "$external_dir/genimage/partitions-os.cfg.in" >> "$images/genimage.cfg"
printf '}\n' >> "$images/genimage.cfg"

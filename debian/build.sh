#!/bin/sh
# Build the EmonOS x86-64 VM image, slot images and signed RAUC bundle from
# Debian packages with mkosi, reusing the shared files in common/.
#
# Run inside the builder from ci/builder/debian.Dockerfile (via
# `make VARIANT=debian emonos_x86_64_vm`, which does this). Needs --privileged
# for mkosi, and the Docker socket to preload the application images.
#
# Outputs land in output/debian/x86-64-vm/images, the same layout as Buildroot.
set -eu
umask 022

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
src=$(CDPATH='' cd -- "$here/.." && pwd)
common=$src/common
ext=$src/buildroot-external # only the x86-64 board files are used
out=${EMONOS_DEBIAN_OUTPUT:-$src/output/debian/x86-64-vm}
images=$out/images
work=$out/work
root=$work/rootfs
board=x86-64-vm

# Debian main and security come from snapshot.debian.org, and Docker's key is
# pinned by fingerprint. Keep DEBIAN_SNAPSHOT equal to the builder image's.
snapshot=${DEBIAN_SNAPSHOT:-20261120T000000Z}
docker_key_fpr=9DC858229FC7DD38854AE2D88D81803C0EBFCD88

export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$src" log -1 --format=%ct)}
export EMONOS_VERSION=${EMONOS_VERSION:-0.1.0}
export EMONOS_RAUC_KEY_DIR=${EMONOS_RAUC_KEY_DIR:-$src/output/signing}
export EMONOS_VARIANT=debian

rm -rf "$out"
mkdir -p "$work" "$images"
# A checkout may drop the executable bit that mkosi requires on its scripts.
chmod +x "$here/mkosi.postinst.chroot"

# Docker's apt repository. It cannot be snapshotted, so the key fingerprint
# is verified and the package versions float within the stable channel.
sandbox=$work/sandbox
mkdir -p "$sandbox/etc/apt/sources.list.d" "$sandbox/usr/share/keyrings"
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor \
    > "$sandbox/usr/share/keyrings/docker.gpg"
gpg --show-keys --with-colons "$sandbox/usr/share/keyrings/docker.gpg" |
    grep -q "^fpr:::::::::$docker_key_fpr:" || {
    echo 'Docker apt key fingerprint mismatch' >&2
    exit 1
}
printf '%s\n' 'Types: deb' 'URIs: https://download.docker.com/linux/debian' \
    'Suites: trixie' 'Components: stable' 'Architectures: amd64' \
    'Signed-By: /usr/share/keyrings/docker.gpg' \
    > "$sandbox/etc/apt/sources.list.d/docker.sources"

# 1. Debian packages -> root directory. mkosi writes the kernel next to it as
# rootfs.vmlinuz, and mkosi.postinst.chroot leaves an initrd in /usr/lib/emonos.
mkosi -C "$here" --architecture=x86-64 --output-dir="$work" \
    --mirror="https://snapshot.debian.org/archive/debian/$snapshot" \
    --sandbox-tree="$sandbox" --force \
    --environment=EMONOS_INITRD_MODULES=squashfs,virtio_pci,virtio_blk build

# 2. Shared overlay and hooks, as Buildroot's post-build step applies them.
cp -a --remove-destination "$common/app/rootfs-overlay/." \
    "$ext/board/pc/x86-64-vm/rootfs-overlay/." "$root/"
# prepare-target.sh reads the architecture from Buildroot's .config.
printf '%s\n' 'BR2_x86_64=y' > "$work/br2.config"
BR2_CONFIG=$work/br2.config "$common/ota/prepare-target.sh" "$root"
mkdir -p "$root/opt/emonos" "$root/mnt/boot"
cp "$common/app/docker-compose.yml" "$common/app/images.lock" "$root/opt/emonos/"
systemctl --root="$root" enable systemd-networkd systemd-resolved docker \
    mnt-data.mount var-lib-docker.mount var.mount mnt-boot.mount etc-ssh.mount \
    emonos-first-boot.service emonos-preload.service emonos-app.service \
    emonos-persist.service emonos-health.service
# Development only: passwordless root, matching the Buildroot variant.
sed -i 's/^root:[^:]*:/root::/' "$root/etc/shadow"

# 3. Kernel slot: kernel plus an initrd, because squashfs is a module here.
kdir=$work/kernel
mkdir -p "$kdir"
cp "$work/rootfs.vmlinuz" "$kdir/bzImage"
mv "$root/usr/lib/emonos/initrd" "$kdir/initrd"
mksquashfs "$kdir" "$images/kernel.img" -noappend -comp gzip -all-root >/dev/null
test "$(wc -c < "$images/kernel.img")" -lt $((32 * 1024 * 1024))
truncate -s 32M "$images/kernel.img"

# 4. System slot.
mksquashfs "$root" "$images/rootfs.squashfs" -noappend -comp zstd \
    -xattrs-exclude '^system\.posix_acl' >/dev/null
test "$(wc -c < "$images/rootfs.squashfs")" -lt $((512 * 1024 * 1024))
cp "$images/rootfs.squashfs" "$images/system.img"
truncate -s 512M "$images/system.img"

# 5. Signed RAUC bundle. Debian trixie ships RAUC 1.13, which rejects the
# manifest's min-rauc-version=1.15 guard and explicit type=raw (1.13 infers
# raw from .img). The device runs the same 1.13, so drop both from a copy.
# Revisit when Debian ships RAUC 1.15 (forky) or a backport is used.
cp -a "$common/ota" "$work/ota"
sed -i -e '/^min-rauc-version=/d' -e '/^type=raw/d' "$work/ota/manifest.raucm.in"
HOST_DIR=/usr TARGET_DIR=$root BUILD_DIR=$work BINARIES_DIR=$images \
    "$work/ota/bundle.sh" "$board"

# 6. Boot partition: Debian's GRUB with EmonOS's module list and A/B script,
# plus an initrd line per slot.
efi=$work/efi-part/EFI/BOOT
mkdir -p "$efi"
grub-mkimage -O x86_64-efi -p /EFI/BOOT -o "$efi/bootx64.efi" \
    boot linux ext2 fat squash4 part_msdos part_gpt normal efi_gop loadenv \
    test echo serial terminfo
sed -e 's|^\( *\)linux (hd0,gpt\([24]\))/bzImage .*|&\n\1initrd (hd0,gpt\2)/initrd|' \
    "$ext/board/pc/x86-64-vm/grub.cfg.in" > "$efi/grub.cfg"
grub-editenv "$efi/grubenv" create
grub-editenv "$efi/grubenv" set ORDER='A B' A_OK=1 B_OK=0 A_TRY=0 B_TRY=0
find "$work/efi-part/EFI" -depth -print0 | xargs -0 -r touch -h -d "@$SOURCE_DATE_EPOCH"

# 7. Data partition, with the locked application images preloaded as
# common/app/preload-images.sh does for Buildroot.
lock_file=$common/app/images.lock
get() { sed -n "s/^$1=//p" "$lock_file"; }
data=$work/data
mkdir -p "$data/rauc" "$data/preload" "$data/docker" "$data/redis" \
    "$data/emoncms/db" "$data/emoncms/phpfina" "$data/emoncms/phptimeseries"
for slot in kernel.0 rootfs.0 kernel.1 rootfs.1; do
    printf '[slot.%s]\nbundle.compatible=%s\nbundle.version=%s\nstatus=ok\n\n' \
        "$slot" "$(cat "$root/usr/lib/emonos/compatible")" "$EMONOS_VERSION"
done > "$data/rauc/central.raucs"
set --
for service in web db redis mqtt; do
    source=$(get "$service.source")
    tag=$(get "$service.local_tag")
    test -n "$source" && test -n "$tag"
    docker image rm "$source" >/dev/null 2>&1 || true
    docker pull --platform linux/amd64 "$source" >/dev/null
    docker tag "$source" "$tag"
    set -- "$@" "$tag"
done
docker save -o "$data/preload/images.raw.tar" "$@"
python3 "$common/app/canonicalize-docker-archive.py" \
    "$data/preload/images.raw.tar" "$data/preload/images.tar" "$SOURCE_DATE_EPOCH"
rm -f "$data/preload/images.raw.tar"
sha256sum "$data/preload/images.tar" | awk '{print $1}' > "$data/preload/images.tar.sha256"
find "$data" -depth -print0 | xargs -0 -r touch -h -d "@$SOURCE_DATE_EPOCH"
truncate -s 6G "$images/data.ext4"
E2FSPROGS_FAKE_TIME="$SOURCE_DATE_EPOCH" mkfs.ext4 -q -F -L emonos-data \
    -U 8b3f7261-7995-4eac-b12e-8e7356827661 \
    -E root_owner=0:0,hash_seed=8b3f7261-7995-4eac-b12e-8e7356827661,lazy_itable_init=0,lazy_journal_init=0 \
    -d "$data" "$images/data.ext4"

# 8. Disk image, using the shared genimage recipes unchanged.
truncate -s 8M "$images/bootstate.img"
cp -a "$work/efi-part" "$images/efi-part"
cp /usr/share/ovmf/OVMF.fd "$images/OVMF.fd"
{
    cat "$ext/board/pc/x86-64-vm/genimage.cfg.in"
    cat "$common/genimage/partitions-os.cfg.in"
    printf '}\n'
} > "$work/genimage.cfg"
genimage --rootpath "$(mktemp -d)" --tmppath "$work/genimage.tmp" \
    --inputpath "$images" --outputpath "$images" --config "$work/genimage.cfg"
echo "built $images/emonos-$board.img" >&2

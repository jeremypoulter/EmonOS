#!/bin/sh
# Buildroot post-build hook: save locked target images into the data image.
set -eu

target_dir=${1:?Buildroot did not pass TARGET_DIR}
external_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
lock_file="$external_dir/app/images.lock"
data_stage="$BUILD_DIR/emonos-data"
data_image="$BINARIES_DIR/data.ext4"

# Incremental Buildroot output retains files produced by earlier post-build
# hooks. Do not accidentally put the old archive back into a system slot.
rm -f "$target_dir/opt/emonos/preload/images.tar" \
    "$target_dir/opt/emonos/preload/images.tar.sha256"

if grep -qx 'BR2_aarch64=y' "$BR2_CONFIG"; then
    platform=linux/arm64
elif grep -qx 'BR2_x86_64=y' "$BR2_CONFIG"; then
    platform=linux/amd64
else
    echo "unsupported EmonOS preload architecture" >&2
    exit 1
fi

command -v docker >/dev/null || {
    echo "Docker is required on the build host to preload application images" >&2
    exit 1
}

get() {
    sed -n "s/^$1=//p" "$lock_file"
}

rm -rf "$data_stage"
mkdir -p "$data_stage/preload" "$data_stage/docker" \
    "$data_stage/emoncms/db" "$data_stage/emoncms/phpfina" \
    "$data_stage/emoncms/phptimeseries" "$data_stage/redis"
cp "$external_dir/app/docker-compose.yml" "$target_dir/opt/emonos/docker-compose.yml"
cp "$lock_file" "$target_dir/opt/emonos/images.lock"
mkdir -p "$target_dir/etc/systemd/system/multi-user.target.wants"
mkdir -p "$target_dir/etc/systemd/system/local-fs.target.wants"
ln -sf ../mnt-data.mount "$target_dir/etc/systemd/system/local-fs.target.wants/mnt-data.mount"
ln -sf ../var-lib-docker.mount "$target_dir/etc/systemd/system/local-fs.target.wants/var-lib-docker.mount"
ln -sf ../var.mount "$target_dir/etc/systemd/system/local-fs.target.wants/var.mount"
ln -sf ../emonos-first-boot.service "$target_dir/etc/systemd/system/local-fs.target.wants/emonos-first-boot.service"
ln -sf ../etc-ssh.mount "$target_dir/etc/systemd/system/multi-user.target.wants/etc-ssh.mount"
ln -sf ../emonos-preload.service "$target_dir/etc/systemd/system/multi-user.target.wants/emonos-preload.service"
ln -sf ../emonos-app.service "$target_dir/etc/systemd/system/multi-user.target.wants/emonos-app.service"
set --
for service in web db redis mqtt; do
    source=$(get "$service.source")
    tag=$(get "$service.local_tag")
    test -n "$source" && test -n "$tag"
    # Docker's classic image store cannot hold both child platforms under one
    # multi-platform index digest. Remove just this reference before selecting
    # the target child; other local tags remain available to docker save.
    docker image rm "$source" >/dev/null 2>&1 || true
    docker pull --platform "$platform" "$source" >/dev/null
    docker tag "$source" "$tag"
    set -- "$@" "$tag"
done

archive="$data_stage/preload/images.tar"
docker save -o "$archive" "$@"
sha256sum "$archive" | awk '{print $1}' > "$archive.sha256"

# WP4's data partition is deliberately a fixed initial size. It is the final
# partition and emonos-first-boot will grow it to the device's remaining space.
rm -f "$data_image"
truncate -s 6G "$data_image"
mkfs.ext4 -q -F -L emonos-data -d "$data_stage" "$data_image"

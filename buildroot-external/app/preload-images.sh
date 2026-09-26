#!/bin/sh
# Buildroot post-build hook: save the locked target images into the development
# root. WP4 moves this archive to the persistent data partition.
set -eu

target_dir=${1:?Buildroot did not pass TARGET_DIR}
external_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
lock_file="$external_dir/app/images.lock"

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

mkdir -p "$target_dir/opt/emonos/preload"
cp "$external_dir/app/docker-compose.yml" "$target_dir/opt/emonos/docker-compose.yml"
cp "$lock_file" "$target_dir/opt/emonos/images.lock"
mkdir -p "$target_dir/etc/systemd/system/multi-user.target.wants"
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

archive="$target_dir/opt/emonos/preload/images.tar"
docker save -o "$archive" "$@"
sha256sum "$archive" | awk '{print $1}' > "$archive.sha256"

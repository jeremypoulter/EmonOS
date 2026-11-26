#!/bin/sh
set -eu
umask 022
board=${1:?missing board name}
case "$board" in x86-64-vm|rpi4) ;; *) exit 1 ;; esac
directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
keys=${EMONOS_RAUC_KEY_DIR:?missing signing credentials directory}
images=${BINARIES_DIR:?missing image directory}
stage=$(mktemp -d "$BUILD_DIR/emonos-bundle.XXXXXX")
trap 'rm -rf "$stage"' EXIT
version=$(cat "$TARGET_DIR/usr/lib/emonos/version")
compatible=$(cat "$TARGET_DIR/usr/lib/emonos/compatible")
# These are the exact padded bytes used for the two factory disk-image slots.
cp --sparse=always "$images/kernel.img" "$stage/kernel.img"
cp --sparse=always "$images/system.img" "$stage/rootfs.img"
install -m 0755 "$directory/install-check" "$stage/install-check"
sed -e "s/@COMPATIBLE@/$compatible/" -e "s/@VERSION@/$version/" \
    "$directory/manifest.raucm.in" > "$stage/manifest.raucm"
find "$stage" -depth -print0 | xargs -0 -r touch -h -d "@$SOURCE_DATE_EPOCH"
bundle="$images/emonos-$board.raucb"
temporary="$images/.emonos-$board.raucb"
rm -f "$temporary"
"$HOST_DIR/bin/rauc" --cert="$keys/dev-cert.pem" --key="$keys/dev-key.pem" \
    bundle --mksquashfs-args='-comp zstd -all-root' "$stage" "$temporary"
"$HOST_DIR/bin/rauc" --keyring="$TARGET_DIR/etc/rauc/keyring.pem" info "$temporary"
mv "$temporary" "$bundle"
sha256sum "$bundle" > "$bundle.sha256"

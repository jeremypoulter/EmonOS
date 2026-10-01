#!/bin/sh
# Explicitly create local PoC credentials, never as an implicit build side effect.
set -eu
umask 077
directory=${1:?usage: dev-keys.sh <key-directory>}
mkdir -p "$directory"
cert="$directory/dev-cert.pem"
key="$directory/dev-key.pem"
if [ -f "$cert" ] && [ -f "$key" ]; then
    echo "Using existing development credentials in $directory"
    exit 0
fi
if [ -e "$cert" ] || [ -e "$key" ]; then
    echo "Incomplete credentials in $directory; refusing to overwrite" >&2
    exit 1
fi
stage=$(mktemp -d "$directory/.credentials.XXXXXX")
trap 'rm -rf "$stage"' EXIT
openssl req -new -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -subj '/CN=EmonOS PoC development signing/' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,digitalSignature,keyCertSign' \
    -keyout "$stage/key.pem" -out "$stage/cert.pem"
mv "$stage/key.pem" "$key"
mv "$stage/cert.pem" "$cert"
echo "Created development-only credentials in $directory (do not distribute the key)"

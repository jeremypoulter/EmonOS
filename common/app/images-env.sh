#!/bin/sh
# Print Compose variables (EMONOS_<NAME>_IMAGE=<local tag>) from images.lock.
# images.lock is the only place image references are written; Compose and the
# tests derive everything from it.
set -eu
lock_file=${1:?usage: images-env.sh <images.lock>}
sed -n 's/^\([a-z0-9_]*\)\.local_tag=\(.*\)$/\1 \2/p' "$lock_file" |
    while read -r name tag; do
        printf 'EMONOS_%s_IMAGE=%s\n' "$(printf %s "$name" | tr '[:lower:]' '[:upper:]')" "$tag"
    done

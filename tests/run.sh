#!/bin/sh
set -eu

target=${1:?usage: tests/run.sh <target> [pytest arguments]}
shift
test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

case "$target" in
    x86-64-vm)
        ;;
    *)
        echo "Unsupported target: $target" >&2
        exit 2
        ;;
esac

exec python3 -m pytest "$test_dir" "$@"

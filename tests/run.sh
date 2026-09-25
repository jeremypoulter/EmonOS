#!/bin/sh
set -eu

target=${1:?usage: tests/run.sh <x86-64-vm|rpi4> [pytest arguments]}
shift
test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$test_dir/.." && pwd)
if [ -n "${EMONOS_TEST_PYTHON:-}" ]; then
    python=$EMONOS_TEST_PYTHON
elif [ -x "$repo_dir/.venv/bin/python" ]; then
    python=$repo_dir/.venv/bin/python
else
    python=python3
fi

if ! "$python" -c 'import labgrid, pytest' 2>/dev/null; then
    printf 'Test dependencies are missing for %s.\n' "$python" >&2
    printf 'Run: python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt\n' >&2
    exit 2
fi

case "$target" in
    x86-64-vm)
        export LG_QEMU_LAUNCHER="$test_dir/qemu-launch.py"
        export LG_X86_DISK="$repo_dir/output/x86-64-vm/images/emonos-x86-64-vm.img"
        export LG_X86_BIOS="$repo_dir/output/x86-64-vm/images/OVMF.fd"
        export LG_QEMU_MEMORY="${EMONOS_QEMU_MEMORY:-1G}"
        ;;
    rpi4)
        : "${EMONOS_PI_SERIAL:?set EMONOS_PI_SERIAL to the Pi console device, preferably under /dev/serial/by-path/}"
        export LG_SERIAL_DEVICE="$EMONOS_PI_SERIAL"
        ;;
    *)
        printf 'Unsupported target: %s\n' "$target" >&2
        exit 2
        ;;
esac

exec "$python" -m pytest --lg-env "$test_dir/targets/$target.yaml" "$test_dir" "$@"

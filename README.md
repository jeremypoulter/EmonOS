# EmonOS

EmonOS is a minimal, immutable host OS for the emoncms container stack.

## Development status

The repository implements the Buildroot runtime and its shared x86-64 VM / Raspberry Pi 4
boot harness. Docker and Compose run on both targets; a separate, gated harness test proves
the four-image emoncms archive can be loaded into a clean store and started offline. A/B
slots, the persistent data partition, and the emoncms application service are subsequent
milestones documented in `Docs/`.

## Build the x86 VM image

```sh
make emonos_x86_64_vm
```

The image is written to `output/x86-64-vm/images/emonos-x86-64-vm.img`.

## Run the x86 VM

```sh
make run_x86_64_vm
```

The guest uses the serial console. Log in as `root`; the development image has no root
password.

Docker is available after boot. The current writable-root development image can verify the
runtime with:

```sh
docker run --rm hello-world
```

## Test the common runtime

Install the pinned test dependencies, then run the x86 harness. `tests/run.sh` automatically
uses `.venv/bin/python` when that environment exists:

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
tests/run.sh x86-64-vm
```

The QEMU launcher checks available memory and retries only the known `io_uring` allocation
failure. On a development host already under sustained memory pressure, the documented
process-scoped escape hatch is available without changing host sysctls:

```sh
EMONOS_QEMU_DISABLE_IO_URING=1 tests/run.sh x86-64-vm
```

The archive preload test downloads the application images and requires an 8 GB VM. It is
excluded from normal runs:

```sh
EMONOS_QEMU_MEMORY=8G EMONOS_RUN_PRELOAD_TEST=1 \
EMONOS_QEMU_DISABLE_IO_URING=1 tests/run.sh x86-64-vm -k offline_preload
```

The Raspberry Pi uses the same tests over its serial console. Supply the serial device at
runtime rather than a numbered `/dev/ttyUSB*` path, which can change between boots. Use
`/dev/serial/by-id/` if the adapter reports a unique serial number. Cheap adapters such as the
CH340 (`1a86`) do not, so several of them share one `by-id` name that points at whichever
enumerated last. Use the `/dev/serial/by-path/` entry for the USB port instead:

```sh
EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> tests/run.sh rpi4
```

Set `EMONOS_TEST_PYTHON` when the dependencies are installed in a virtual environment using
a non-default Python executable.

## Dependencies

Buildroot downloads and builds its own toolchain. The host needs standard build tools,
Python 3, QEMU/KVM, and enough disk space for Buildroot output. See
`Docs/emonos-poc-implementation-plan.md` for the full PoC scope and prerequisites.

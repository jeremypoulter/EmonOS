# EmonOS

EmonOS is a minimal, immutable host OS for the emoncms container stack.

## Development status

The common runtime and emoncms application stack run on the x86-64 VM and Raspberry Pi 4.
WP4 is introducing a seven-partition A/B layout, read-only squashfs system slots and a
persistent data partition. The x86 VM boots the new layout; the Pi build has not yet been
validated on hardware. See `Docs/emonos-poc-implementation-plan.md` for current status.

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

Docker is available after boot. Verify the runtime with:

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

The test launcher keeps the built disk image unchanged by using a temporary qcow2 overlay,
checks available memory, and retries only the known `io_uring` allocation failure. On a
development host already under sustained memory pressure, the process-scoped escape hatch
is available without changing host sysctls:

```sh
EMONOS_QEMU_DISABLE_IO_URING=1 tests/run.sh x86-64-vm
```

The historical WP0 archive preload test is skipped on the WP4 squashfs images. To verify
that a PHPFina feed survives a guest reboot, run the gated test (the normal VM uses 4 GB):

```sh
EMONOS_RUN_REBOOT_TEST=1 EMONOS_QEMU_DISABLE_IO_URING=1 \
tests/run.sh x86-64-vm -k feed_survives_reboot
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

## SSH development access

OpenSSH host keys are generated on the persistent data partition after first boot, rather
than in the immutable system slot. Password login is disabled. Add a development public key
through the serial console, then connect as root using the Pi's DHCP address:

```sh
mkdir -p /mnt/data/ssh/authorized_keys
cat >> /mnt/data/ssh/authorized_keys/root
# paste one public key, press Enter, then Ctrl-D
chmod 600 /mnt/data/ssh/authorized_keys/root
systemctl restart sshd
```

## Dependencies

Buildroot downloads and builds its own toolchain. The host needs standard build tools,
Python 3, Docker, QEMU/KVM, and enough disk space for Buildroot output. The build embeds an
approximately 1.2 GB Docker archive in a 6 GB data-partition image. Allow at least 20 GB
of free build-output space per target during image assembly. See
`Docs/emonos-poc-implementation-plan.md` for the full PoC scope and prerequisites.

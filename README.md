# EmonOS

EmonOS is a minimal, immutable host OS for the emoncms container stack.

## Development status

The x86-64 VM and Raspberry Pi 4 both boot the seven-partition A/B layout
with read-only squashfs system slots and a persistent data partition. The
emoncms stack, data-partition growth, machine ID and SSH host keys survive
reboot on both targets (WP4, T2/T3). RAUC signed-bundle verification and
slot reporting pass on both targets (WP5, T4). Signed A→B installs with data
survival pass on both targets (WP6, T5); health-based commit and rollback
remain WP7.
See `Docs/emonos-poc-implementation-plan.md` for the PoC scope.

## Build the x86 VM image

```sh
make rauc_dev_keys # once: development-only credentials under output/signing/
make emonos_x86_64_vm
```

The image is written to `output/x86-64-vm/images/emonos-x86-64-vm.img`.

For the Pi, reuse the same signing credentials and run `make emonos_rpi4`.
Its image is `output/rpi4/images/emonos-rpi4.img`.

## RAUC development bundles (WP5)

Each build also writes `output/<target>/images/emonos-<target>.raucb` and
its SHA-256 file. The verity bundle contains the exact padded kernel and
system payloads used in the disk image, plus a compatibility-check hook.
The shared boot files, bootstate and data partitions are not updated.
RAUC metadata lives under `/mnt/data/rauc`.

`make rauc_dev_keys` is explicit and refuses to overwrite partial credentials.
Keep `output/signing/dev-key.pem` private and backed up; only the public
certificate is baked into the images. Set `EMONOS_RAUC_KEY_DIR` to reuse
credentials outside the output tree. CI creates ephemeral credentials for
that job, not keys trusted by local devices. Do not use this PoC signing
setup for production. The device validates certificate lifetime at the
authenticated bundle signing time because its clock may reset on boot;
this is a development policy, not an anti-rollback mechanism.

`EMONOS_VERSION` defaults to `0.1.0`; it sets both `/etc/os-release` and
the bundle version. Repeated disk builds require the same certificate and
version. Signed bundles are not expected to be byte-identical (signing time
and random verity salt); their payload hashes must match the factory images.

To verify the bundle inside the VM without installing it:

```sh
EMONOS_QEMU_RAUC_BUNDLE="$PWD/output/x86-64-vm/images/emonos-x86-64-vm.raucb" \
EMONOS_QEMU_DISABLE_IO_URING=1 tests/run.sh x86-64-vm -k rauc
```

The harness attaches the bundle as a read-only second disk, copies it onto
the disposable VM data partition, then runs `rauc info` with the baked
keyring. On the Pi, copy its bundle onto `/mnt/data` (or mount removable
media there) and set `EMONOS_RAUC_BUNDLE_PATH` to that target-side path when
running the same tests over serial. No WP5 test installs or activates a slot.
Alternatively set `EMONOS_RAUC_BUNDLE_URL` to a reachable local HTTP(S) URL:
the serial-driven guest fetches the bundle, checks its SHA-256 against the
local build, then verifies its signature. Serve only the public bundle, never
the signing directory. This optional transfer path is not required for offline
updates; the target-side file path works without networking.

## WP6 update round trip (opt-in)

`tests/test_rauc.py::test_update_round_trip` **writes the inactive OS slot and
reboots**. Do not set `EMONOS_RUN_UPDATE_TEST=1` on hardware until the VM
round trip is validated. First preserve the v1 x86 disk, then build v2 with
the **same signing key** and a changed version:

```sh
mkdir -p output/wp6
cp --reflink=auto --sparse=always \
  output/x86-64-vm/images/emonos-x86-64-vm.img output/wp6/x86-v1.img
EMONOS_VERSION=0.2.0 make emonos_x86_64_vm
cp output/x86-64-vm/images/emonos-x86-64-vm.raucb output/wp6/x86-v2.raucb
```

The gated VM test boots the v1 copy with a disposable qcow2 overlay and
provides the v2 bundle as a read-only disk:

```sh
EMONOS_RUN_UPDATE_TEST=1 EMONOS_QEMU_DISABLE_IO_URING=1 \
EMONOS_QEMU_BASE_DISK="$PWD/output/wp6/x86-v1.img" \
EMONOS_QEMU_RAUC_BUNDLE="$PWD/output/wp6/x86-v2.raucb" \
EMONOS_V2_BUNDLE_HOST="$PWD/output/wp6/x86-v2.raucb" \
tests/run.sh x86-64-vm -q -k update_round_trip
```

The output tree's default disk will be v2 after that build; keep the v1
copy for repeatable tests. Restore a v1 factory image with a normal
`make emonos_x86_64_vm` (without `EMONOS_VERSION`) after preserving the
v2 bundle. Never point `EMONOS_QEMU_BASE_DISK` at the v2 disk for this test.

For the physical Pi, stage the signed arm64 v2 bundle on `/mnt/data` and set
`EMONOS_V2_BUNDLE_PATH=/mnt/data/wp6-v2.raucb`,
`EMONOS_V2_BUNDLE_HOST` to the local bundle file,
`EMONOS_PI_SERIAL` to the console, and the update gate. The optional
`test_stage_pi_update_bundle` uses `EMONOS_V2_BUNDLE_URL` to copy only that
public bundle from a temporary local server and checks its SHA-256/signature
**without installing** it. The update itself requires no network connection.
WP6 does not mark B good: the first B boot consumes its trial; a later reboot
falls back to A until WP7 implements a health check. The normal factory
`test_ab_layout` and `test_rauc_status` expect A and are not post-update
checks while B is running.

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

To test first-boot data-partition growth without modifying the built image,
give the VM a larger disposable qcow2 overlay:

```sh
EMONOS_QEMU_DISK_SIZE=10G EMONOS_QEMU_DISABLE_IO_URING=1 \
tests/run.sh x86-64-vm -k 'data_partition_growth or persistent_identity_and_ssh'
```

## CI builds

`.github/workflows/ci.yml` lints the tree, builds the x86-64 image on a
GitHub-hosted runner, and runs the shared VM suite with TCG (hosted runners
do not provide KVM). A clean Buildroot toolchain takes about 94 minutes on
that runner. The build needs at least 32 GiB free; CI removes unused SDKs
from its disposable runner before checking capacity. It uploads checksums,
the partition table and Buildroot configuration, not the raw disk image.
Buildroot, container sources, action versions and Python are pinned. CI rebuilds
each image on the same runner and requires an identical SHA-256 before testing.
The build uses a fixed source epoch, UTC, stable partition/filesystem IDs and
root-owned data staging; it does not require any development-harness directory.
Buildroot's experimental reproducible mode only claims byte-identical output
for the same absolute output path. Cross-host byte identity is not assumed.

Use the workflow's manual **build_rpi4** input to build the x86 and Pi images
in parallel on separate hosted runners. The Pi image is not tested in CI:
its bootloader and application tests still require the physical bench.

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

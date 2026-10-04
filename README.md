# EmonOS

EmonOS is a minimal, immutable host OS for the emoncms container stack.

## Development status

The x86-64 VM and Raspberry Pi 4 both boot the seven-partition A/B layout
with read-only squashfs system slots and a persistent data partition. The
emoncms stack, data-partition growth, machine ID and SSH host keys survive
reboot on both targets (WP4, T2/T3). RAUC signed-bundle verification and
slot reporting pass on both targets (WP5, T4). Signed A→B installs with data
survival pass on both targets (WP6, T5). Health-based commit and autonomous
broken-update rollback and interrupted-install recovery pass on both (WP7,
T6/T7). Pi Power1 was verified and used for a five-second mid-install cut;
Power5 was left ON and untouched.
The shared T8 suite and requirement coverage are complete on x86 and Pi.
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
The WP6 test itself does not mark B good. On older WP6 images, its first B
boot consumes the trial and a later reboot falls back to A. New WP7 images
enable the health service, which can commit B autonomously. The normal factory
`test_ab_layout` and `test_rauc_status` expect A and are not post-update
checks while B is running.

## WP7 health, rollback and interruption recovery (T6/T7 validated on both)

New builds enable an autonomous five-minute health monitor. It checks local
HTTP, authenticates the existing PoC `emonospoc` account, validates the feed
list JSON shape, and marks the booted slot good only after those checks pass.
On an empty factory database it provisions the same test account/password;
this is development-only credential handling. Unlike `emonos-app-check`,
the probe does not post samples or modify feed/process data.

Successful commit evidence is `/mnt/data/health/last-good.json`; failure
records are `/mnt/data/health/failures/<boot-id>.json`, written and synced
before reboot. PID1 feeds a 30-second runtime watchdog where supported.
Do not use these development signing/account defaults for a product image.

Build a broken-but-bootable update with
`EMONOS_VERSION=0.2.0-broken EMONOS_APP_FAULT=fail-start make emonos_x86_64_vm`.
The fault overrides only the slot-local app service, not persistent data.
Preserve a WP7 factory v1 disk and the bundles before rebuilding; normal
builds remove the fault. Gated tests in `tests/test_rollback.py` use
`EMONOS_RUN_COMMIT_TEST=1` or `EMONOS_RUN_ROLLBACK_TEST=1`, with
`EMONOS_WP7_BUNDLE_HOST` pointing at the corresponding bundle and the same
file attached through `EMONOS_QEMU_RAUC_BUNDLE`. Rollback tests wait for the
device's own failure reboot—no host reboot command after B is running.
These tests write the inactive slot; hardware testing follows VM validation.
For Pi tests, `test_stage_pi_health_bundle` can fetch only the public bundle
using `EMONOS_WP7_BUNDLE_URL` and verify it without installing. Then set
`EMONOS_WP7_BUNDLE_PATH=/mnt/data/wp7-staged.raucb` for the gated install.
Use `EMONOS_WP7_RESTORE_A=1` with the healthy-update test when you want it
to return explicitly to factory A after proving two committed B boots, so
the broken-B rollback test can follow without reflashing the card.
Both the local x86 VM and physical Pi passed factory commit/watchdog checks, healthy B
commitment through a second reboot, and broken B's autonomous five-minute
rollback to A with data intact. Interrupted-install T7 also passes on both:
QEMU SIGKILL for x86 and a controlled Power1 cut at observed rootfs-B write
progress for Pi. The Pi recovered on A with persistent data intact; Power5
was not operated. An earlier Pi fallback boot exceeded the health deadline once
before recovering on its next boot; that startup-variance issue remains
unexplained and is recorded in the implementation plan.

WP8 is complete: the same runner passes on x86 and Pi, writes JUnit/labgrid
logs, and generates `Docs/emonos-test-coverage.md` from test docstrings. The
Pi run used the provisioned test account's API key from a temporary local
server after its password-auth throttle engaged; no database was reset.

## Interrupted-install VM test (T7)

This opt-in test **kills its own QEMU process during a real inactive-slot
write**, then restarts the same test disk. It creates and retains a fresh
sparse **raw** clone under ignored `output/test-state/`; the normal launcher
continues to use disposable qcow2 overlays. It disables snapshot mode and
does not restore the disk, bootstate or firmware from a saved snapshot.

```sh
EMONOS_RUN_POWER_CUT_TEST=1 EMONOS_QEMU_DISABLE_IO_URING=1 \
EMONOS_QEMU_BASE_DISK="$PWD/output/wp7/x86-v1-health.img" \
EMONOS_QEMU_RAUC_BUNDLE="$PWD/output/wp7/x86-v2-healthy.raucb" \
tests/run.sh x86-64-vm -q -s -k interrupted_install_keeps_active_slot
```

The cut follows observed RAUC rootfs-write progress **and** an incomplete
changed region visible in the raw disk backing, not an arbitrary delay or
userspace percentage alone. A full inactive-slot hash after recovery must
differ from both complete images; the old slot, feed and identity must survive.
`cut-evidence.json` is retained alongside the test disk. Allow several GiB
of project-disk space per retained run; prefer disk-backed `TMPDIR` when a
host's temporary filesystem is RAM-backed and already pressured. This T7
adapter operates only on its own QEMU. Physical Pi T7 uses the serial harness
and guest-side progress watcher to cut only Tasmota Power1; do not run it
unless Power1 is verified to feed only the test Pi.

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

Each runner invocation writes JUnit XML to `output/test-results/<target>/junit.xml`
and labgrid logs under `output/test-results/<target>/labgrid/`. Override the
report root with `EMONOS_TEST_REPORT_DIR`. `Docs/emonos-test-coverage.md` is
generated from pytest function docstrings; regenerate it with
`python3 tests/requirement_coverage.py` after changing acceptance-ID tags.

For an existing provisioned test account that is temporarily rate-limited,
the app check and health probe can use a root-owned, mode-0600
`/mnt/data/health/test-api-key` containing its 32-character write API key.
This is a PoC test credential: do not store it in the repository, labgrid log
directory or CI artifacts. No persistent database reset is needed.

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

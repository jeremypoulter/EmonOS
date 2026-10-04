# EmonOS PoC user guide

This guide covers building, flashing, booting, updating and testing the current
EmonOS proof of concept. The tested targets are x86-64 QEMU and a Raspberry Pi
4. The images are immutable A/B systems with one persistent data partition.

> **PoC, not a production appliance.** The development signing key, test
> application credentials, clock policy and local test network are not
> production security mechanisms. Do not expose the test stack to an untrusted
> network or reuse the development private key for release images.

## 1. Host setup and signing key

Use a Linux build host with Git/submodules, Buildroot's normal compiler and
build prerequisites, Docker, Python 3, QEMU/KVM, and enough space for the
Buildroot output. The top-level `Makefile` pins UTC and the Buildroot source
epoch. See [README.md](../README.md) and `.github/workflows/ci.yml` for the
CI-validated host package list.

Create the local PoC signing credentials once:

```sh
make rauc_dev_keys
```

The private key is `output/signing/dev-key.pem`; it is ignored by Git and must
stay private. Back it up securely if you need to build updates trusted by an
already-flashed device. The public certificate is baked into each image. CI
uses an ephemeral key which local devices do not trust.

## 2. Build images

```sh
make emonos_x86_64_vm
make emonos_rpi4
```

Each command produces a seven-partition disk image and a signed `.raucb`
bundle. The Pi build is a real aarch64 cross-build, not a QEMU-user build.
The bundle contains only the padded kernel and system-slot images; it does
not change the shared boot files, U-Boot/GRUB firmware, bootstate or data.

Default artifacts:

| Target | Disk image | Signed bundle |
|---|---|---|
| x86-64 VM | `output/x86-64-vm/images/emonos-x86-64-vm.img` | `output/x86-64-vm/images/emonos-x86-64-vm.raucb` |
| Raspberry Pi 4 | `output/rpi4/images/emonos-rpi4.img` | `output/rpi4/images/emonos-rpi4.raucb` |

`EMONOS_VERSION` defaults to `0.1.0`. To build an update, retain the v1 disk
and bundles **before** changing that version: the next `make` replaces the
default output images. Use the same `output/signing/` key for every bundle
that must be trusted by the device. Bundle hashes can differ between builds
because RAUC's verity salt/signing metadata is generated; disk image
repeatability is checked at a fixed output path.

## 3. Flash and first boot (Pi)

> **Full-card flash warning:** writing `emonos-rpi4.img` erases the selected
> card, including feeds, database, machine ID, SSH keys and prior update
> state. Back up anything needed first. Never guess the output device.

Identify the card by model and size, unmount its partitions, then write the
whole image to the **disk device**, not a partition. For example, after
carefully replacing `/dev/sdX` with the verified device:

```sh
lsblk -p -o NAME,SIZE,MODEL,TRAN,FSTYPE,MOUNTPOINTS
sha256sum output/rpi4/images/emonos-rpi4.img
sudo umount /dev/sdX?* 2>/dev/null || true
sudo dd if=output/rpi4/images/emonos-rpi4.img of=/dev/sdX \
  bs=4M status=progress conv=fsync
```

Re-run `lsblk` after writing and verify that the device/partition layout is
the intended card. Raspberry Pi Imager can also write the `.img` file.

The image initializes both A/B payloads, with A bootable and B initially
ineligible. The data partition grows to the installed card capacity on first
boot. Allow several minutes for Docker and the application stack. Health
checks and successful slot commits are recorded under `/mnt/data/health/`.

## 4. Console, status and SSH

The Pi serial console is 115200 8N1. Prefer the stable USB-port path, not a
numbered `/dev/ttyUSB*` device:

```sh
ls -l /dev/serial/by-path/
EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> tests/run.sh rpi4 -q
```

Do not open the same serial port in PlatformIO/minicom while labgrid is using
it. Root SSH is key-only. Add a public key through the serial console:

```sh
umask 077
mkdir -p /mnt/data/ssh/authorized_keys
cat >> /mnt/data/ssh/authorized_keys/root
# paste one public key, press Enter, then Ctrl-D
systemctl restart sshd
```

Useful read-only status commands:

```sh
rauc status --detailed
rauc info /mnt/data/<bundle>.raucb
cat /usr/lib/emonos/version
grep -o 'rauc.slot=[AB]' /proc/cmdline
fw_printenv BOOT_ORDER BOOT_A_LEFT BOOT_B_LEFT   # Pi only
systemctl --failed --no-pager
```

The expected development account is `emonospoc` with PoC password
`poc-password-123` if factory provisioning is needed. This is public test
credential material: do not use it as a real user password.

## 5. Shared test suite and reports

Install test dependencies once:

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
```

Run the shared suite on x86:

```sh
tests/run.sh x86-64-vm -q
```

Run it on the Pi using the serial path above. Reports are written to:

```text
output/test-results/<target>/junit.xml
output/test-results/<target>/labgrid/
```

Set `EMONOS_TEST_REPORT_DIR` to choose a different report directory. Labgrid
logs include test commands and console traffic; treat them as sensitive when
using a test API key. The generated acceptance map is
[`emonos-test-coverage.md`](emonos-test-coverage.md); regenerate/check it with
`python3 tests/requirement_coverage.py` and
`python3 tests/requirement_coverage.py --check`.

Optional normal-suite checks:

```sh
# Larger disposable VM disk: verify data-partition growth.
EMONOS_QEMU_DISK_SIZE=10G tests/run.sh x86-64-vm -q \
  -k 'data_partition_growth or persistent_identity_and_ssh'

# Guest software reboot: verify feed, machine ID, SSH key and units.
EMONOS_RUN_REBOOT_TEST=1 tests/run.sh x86-64-vm -q -k feed_survives_reboot
EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> \
  EMONOS_RUN_REBOOT_TEST=1 tests/run.sh rpi4 -q -k feed_survives_reboot
```

On a host whose QEMU cannot use io_uring, add
`EMONOS_QEMU_DISABLE_IO_URING=1`. The launcher retries only that specific QEMU
startup failure; it does not retry guest-test failures.

### Existing test-account API key (optional)

Repeated app checks can trigger emoncms' login throttle. Do not repeatedly try
passwords or reset the persistent DB. If you already have the test user's
32-character write API key, place it in a protected host file named
`write-key` alongside `tests/app-check-with-key.sh` in a temporary directory:

```sh
mkdir -m 700 -p output/test-state/pi-test-api
install -m 755 tests/app-check-with-key.sh output/test-state/pi-test-api/
install -m 600 /secure/location/write-key output/test-state/pi-test-api/write-key
```

Find the wired host address with `ip -4 -brief address`, then in a separate
terminal serve **only this temporary directory** on the isolated bench LAN:

```sh
python3 -m http.server 8767 --bind <wired-host-ip> \
  --directory output/test-state/pi-test-api
```

Run the Pi suite with:

```sh
EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> \
EMONOS_APP_TEST_API_URL=http://<wired-host-ip>:8767 \
tests/run.sh rpi4 -q
```

The guest fetches the script and key over this temporary local HTTP server;
the key is expanded inside the guest and is not placed in pytest command-line
arguments or labgrid logs. Stop the server and remove the temporary key file
when finished. This is a bench-only workaround, not production secret
delivery.

## 6. Signed update tests (opt-in and slot-writing)

The following tests write an inactive OS slot and reboot. Preserve the healthy
v1 image and both v2 bundles before rebuilding. The Pi is tested in place
without a full-card flash; stage bundles under `/mnt/data`.

The healthy update test installs v2 into B, waits for health-based `mark-good`,
reboots B again, and preserves the feed. On Pi, set
`EMONOS_WP7_RESTORE_A=1` if the next test should return to A explicitly.
`EMONOS_WP7_RESTORE_A` is useful when running the healthy and broken update
tests sequentially on one card.

The broken update must still boot B; only the app unit is faulted. Build it
with `EMONOS_VERSION=0.2.0-broken EMONOS_APP_FAULT=fail-start`, then run
`EMONOS_RUN_ROLLBACK_TEST=1`. The guest's own five-minute monitor must record
the failure and reboot to A; the test does not request that reboot.

For the exact x86 v1/v2 bundle preparation and opt-in commands, see the
[WP6 section in README.md](../README.md#wp6-update-round-trip-opt-in) and
[`test_rollback.py`](../tests/test_rollback.py). For the Pi, use
`test_stage_pi_health_bundle` with `EMONOS_WP7_BUNDLE_URL`, then set
`EMONOS_WP7_BUNDLE_PATH` and `EMONOS_WP7_BUNDLE_HOST` for the gated install.

Example x86 invocations, after preserving a v1 disk and building each bundle:

```sh
EMONOS_RUN_COMMIT_TEST=1 EMONOS_QEMU_BASE_DISK="$PWD/output/wp7/x86-v1-health.img" \
EMONOS_QEMU_RAUC_BUNDLE="$PWD/output/wp7/x86-v2-healthy.raucb" \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/x86-v2-healthy.raucb" \
tests/run.sh x86-64-vm -q -k healthy_update_commits_and_reboots

EMONOS_RUN_ROLLBACK_TEST=1 EMONOS_QEMU_BASE_DISK="$PWD/output/wp7/x86-v1-health.img" \
EMONOS_QEMU_RAUC_BUNDLE="$PWD/output/wp7/x86-v2-broken.raucb" \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/x86-v2-broken.raucb" \
tests/run.sh x86-64-vm -q -k broken_update_rolls_back_autonomously
```

For Pi, first stage the selected public bundle from the temporary local server,
then run the corresponding gated test over the serial console:

```sh
EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> \
EMONOS_WP7_BUNDLE_URL=http://<wired-host-ip>:8767/rpi4-v2-healthy.raucb \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-healthy.raucb" \
tests/run.sh rpi4 -q -k stage_pi_health_bundle

EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path> EMONOS_RUN_COMMIT_TEST=1 \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-healthy.raucb" \
EMONOS_WP7_BUNDLE_PATH=/mnt/data/wp7-staged.raucb EMONOS_WP7_RESTORE_A=1 \
tests/run.sh rpi4 -q -k healthy_update_commits_and_reboots
```

Stage the broken Pi bundle to the same data path and run
`EMONOS_RUN_ROLLBACK_TEST=1 ... -k broken_update_rolls_back_autonomously`.
Each gated update overwrites only the inactive OS pair; it does not replace
the data partition.

## 7. Interrupted-install / power-cut test (T7)

The x86 test kills its own QEMU during observed inactive-rootfs writes and
restarts the same raw test disk and firmware state. It writes a fresh sparse
raw test copy under `output/test-state/`; the factory image is not modified.
See [the T7 instructions](../README.md#interrupted-install-vm-test-t7).

The Pi test deliberately turns **Tasmota Power1 OFF** for five seconds after
the guest watcher observes 75–85% global RAUC progress while copying
`rootfs.1`. Before running it, verify that Power1 powers **only this Pi** and
is ON. The test checks Power5 is ON and unchanged, cuts/restores Power1, then
expects the previous active A slot to boot with persistent data intact. It
does not reflash or reset bootstate. The tested bench Tasmota endpoint is
`http://172.16.1.2`; the script controls Power1 only. Do not run the test if
the outlet mapping differs or another device shares Power1.

Both T7 tests are gated by `EMONOS_RUN_POWER_CUT_TEST=1`. Keep the resulting
`cut-evidence.json`; it records the cut phase, changed inactive-slot hash,
relay/QEMU action and recovery assertions. Do not use graceful shutdown or
snapshot restoration for T7.

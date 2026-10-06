# EmonOS live-demo runbook

This runbook is for demonstrating the **already validated Pi 4 PoC**. It
starts with a read-only tour of the running device, then gives optional
signed-update and recovery demonstrations. The Pi is currently healthy on
**slot A, version 0.1.0**. The latest bench check found it at
`172.16.2.248`; DHCP may assign another address.

> **Keep the demo on a trusted LAN.** The PoC serves HTTP without TLS and has
> a public test login. Do not port-forward it or expose it to the internet.
> Never enter a real household password or meter credential.

## 1. Prepare the bench

1. Confirm the Pi is connected to Ethernet, the serial console is attached,
   and the supply/cable used during validation is in place.
2. Do not open another program on the Pi's serial device. Use the stable
   adapter path (replace it with the actual `/dev/serial/by-path/` entry):

   ```sh
   export EMONOS_PI_SERIAL=/dev/serial/by-path/<usb-port-path>
   ```

3. Check the Pi address in the router's DHCP lease list. From the Pi console,
   the address can be checked read-only with:

   ```sh
   ip -4 -o addr show end0
   ```

4. Open `http://<pi-ip>/` from a browser on the same trusted LAN. The tested
   development account is `emonospoc` / `poc-password-123`. These are PoC
   test credentials, not production defaults. If login is throttled, avoid
   repeated password attempts. The API-key fixture is for scripted tests only;
   it is not an interactive web login. See the
   [testing fallback](../README.md#existing-test-account-api-key-optional).

## 2. Five-minute read-only tour

Start at the EmonCMS page. Show that the application is being served by the
Pi, then open the **Feeds** list and select an existing feed/graph. The feeds,
database and Docker store are on the persistent `/mnt/data` partition rather
than the read-only system root.

In a second terminal, run the same shared checks without installing anything:

```sh
tests/run.sh rpi4 -q -k 'boot_runtime or app_stack or ab_layout or persistent_identity_and_ssh or health_commits_factory_slot or runtime_watchdog_is_active'
```

If a pytest environment has not been set up yet:

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
```

The runner writes JUnit and labgrid logs under
`output/test-results/rpi4/`. You can also show status directly over serial:

```sh
rauc status --detailed
cat /usr/lib/emonos/version
grep -o 'rauc.slot=[AB]' /proc/cmdline
findmnt /
df -h /mnt/data
systemctl --failed --no-pager
cat /mnt/data/health/last-good.json
cat /sys/class/watchdog/watchdog0/timeout
cat /sys/class/watchdog/watchdog0/state
```

Expected demo baseline: slot A is booted and marked good, version is `0.1.0`,
the system root is read-only SquashFS, `/mnt/data` is ext4 and expanded to
the card, the watchdog is active at 30 seconds, and there are no failed
units. After the interrupted-install test, slot B is marked bad/ineligible;
that is a safe expected state, not an application failure.

To show that the data survives an ordinary software reboot, with no relay
operation:

```sh
EMONOS_RUN_REBOOT_TEST=1 EMONOS_PI_SERIAL="$EMONOS_PI_SERIAL" \
  tests/run.sh rpi4 -q -k feed_survives_reboot
```

That test writes a small test sample/feed. Do not run it repeatedly against
an account subject to the emoncms login throttle; the test API-key fixture
below avoids password authentication.

## 3. Optional signed A→B update demonstration

This changes the inactive **OS** slot and reboots, but does not reflash the
card or replace the data partition. The health monitor should check the app,
mark B good and keep it selected. Only demonstrate this if the Pi is on A,
the signed healthy v2 bundle is available, and the health probe passes before
the install:

```sh
/usr/libexec/emonos-health-probe
```

The checked-in development bundle, if present on the build host, is
`output/wp7/rpi4-v2-healthy.raucb`. Serve **only that public bundle** on the
trusted wired LAN (use the host's wired IP, not necessarily this example):

```sh
python3 -m http.server 8767 --bind <wired-host-ip> \
  --directory output/wp7
```

In another host terminal, stage and verify it on the Pi without installing:

```sh
EMONOS_PI_SERIAL="$EMONOS_PI_SERIAL" \
EMONOS_WP7_BUNDLE_URL=http://<wired-host-ip>:8767/rpi4-v2-healthy.raucb \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-healthy.raucb" \
  tests/run.sh rpi4 -q -k stage_pi_health_bundle
```

Then run the opt-in update and two-reboot health/commit test:

```sh
EMONOS_PI_SERIAL="$EMONOS_PI_SERIAL" EMONOS_RUN_COMMIT_TEST=1 \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-healthy.raucb" \
EMONOS_WP7_BUNDLE_PATH=/mnt/data/wp7-staged.raucb \
EMONOS_WP7_RESTORE_A=1 \
  tests/run.sh rpi4 -q -k healthy_update_commits_and_reboots
```

`EMONOS_WP7_RESTORE_A=1` explicitly returns the Pi to healthy factory A after
proving B survives a second boot, so the broken-update demo can follow. Stop
the temporary HTTP server when bundle staging finishes; `rauc install` uses
the local copy and does not need a network connection.

### Optional broken-update rollback demo

Only use the deliberately broken bundle on a backed-up PoC card. B still
boots Linux and the data/application stack remains untouched; the app unit
fails, the five-minute health monitor records the reason, and U-Boot should
return to A unattended. This takes at least five minutes:

1. Stage `rpi4-v2-broken.raucb` using the same staging test above.
2. Run:

   ```sh
   EMONOS_PI_SERIAL="$EMONOS_PI_SERIAL" EMONOS_RUN_ROLLBACK_TEST=1 \
   EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-broken.raucb" \
   EMONOS_WP7_BUNDLE_PATH=/mnt/data/wp7-staged.raucb \
     tests/run.sh rpi4 -q -k broken_update_rolls_back_autonomously
   ```

3. Show `/mnt/data/health/failures/<boot-id>.json`, slot A/0.1.0 and the
   unchanged feed. The test itself sends no reboot request after broken B
   boots; rollback must be initiated by the on-device health monitor.

## 4. Optional interrupted-install power-cut demo (T7)

This is a deliberate power interruption, not part of the read-only tour. Only
run it when Tasmota `Power1` at `172.16.1.2` is confirmed to power **only this
Pi**, and both Power1 and Power5 report ON beforehand. The test checks that
Power5 remains ON and controls only Power1. If outlet mapping is uncertain,
do not run it.

Stage the healthy v2 bundle as above, then run:

```sh
EMONOS_PI_SERIAL="$EMONOS_PI_SERIAL" EMONOS_RUN_POWER_CUT_TEST=1 \
EMONOS_WP7_BUNDLE_HOST="$PWD/output/wp7/rpi4-v2-healthy.raucb" \
EMONOS_WP7_BUNDLE_PATH=/mnt/data/wp7-staged.raucb \
  tests/run.sh rpi4 -q -s -k interrupted_install_keeps_active_slot
```

The guest watcher waits for an observed 75–85% RAUC progress line while
copying `rootfs.1`, then commands Power1 OFF. It holds the cut for five
seconds, restores Power1 and verifies A/0.1.0, the feed, machine ID, SSH key
and previous A images. Evidence is written to
`output/test-state/rpi4-t7-evidence.json`. It does not reflash or reset
bootstate. Stop the temporary bundle server after staging.

## 5. After the demo

- Leave the Pi on healthy slot A; do not mark an unhealthy slot good manually.
- Confirm Tasmota reports **Power1 ON and Power5 ON**. No other relay should
  have been operated.
- Keep the development signing key and any API key out of email, screenshots,
  labgrid logs and shared artifacts.
- For a clean PoC data/application reset, plan a deliberate full-card reflash
  and backup first; do not delete or reformat `/mnt/data` casually.

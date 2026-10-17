# EmonOS live-demo runbook

This is a command-by-command guide for the validated Raspberry Pi 4 PoC. It
starts with a safe read-only tour, then shows optional feed persistence,
signed update/rollback and interrupted-install recovery. The tested Pi most
recently booted slot A, version `0.1.0`; DHCP can change its address.

> **PoC security warning:** the application uses development/test credentials,
> plain HTTP, and a development RAUC signing certificate. Keep the demo on a
> trusted LAN. Do not port-forward it or use real household credentials.

## 1. Prepare the demo

1. Confirm Ethernet and power are connected and the Pi has booted. Do not
   start PlatformIO/minicom on the same serial device while using the console.
2. In a host terminal, identify the current console and Pi address:

   ```sh
   ls -l /dev/serial/by-path/
   ```

   With `picocom`, connect at 115200 8N1 (exit with `Ctrl-A`, then `Ctrl-X`):

   ```sh
   picocom -b 115200 /dev/serial/by-path/<usb-port-path>
   ```

   On the Pi console, run:

   ```sh
   ip -4 -o addr show end0
   ```

3. From a browser on the same trusted LAN, open `http://<pi-ip>/`. The demo
    account is `emonospoc` / `poc-password-123` when factory provisioning has
    created it. These are public PoC credentials, not a real-user login.
    Avoid repeated password attempts if emoncms throttles authentication.

If the v2 bundles are not already built on the host, create/reuse the PoC
signing key, save the two bundles outside the changing Buildroot output, then
restore the local output tree to its default version:

```sh
make rauc_dev_keys                         # once per trusted development key
mkdir -pv output/demo-bundles
EMONOS_VERSION=0.2.0 make emonos_rpi4
cp output/rpi4/images/emonos-rpi4.raucb output/demo-bundles/rpi4-v2-healthy.raucb
echo 'Saved healthy v2 bundle.'
EMONOS_VERSION=0.2.0-broken EMONOS_APP_FAULT=fail-start make emonos_rpi4
cp output/rpi4/images/emonos-rpi4.raucb output/demo-bundles/rpi4-v2-broken.raucb
echo 'Saved broken v2 bundle.'
make emonos_rpi4                          # restore local factory-version artifacts
echo 'Restored default factory-version build output.'
```

## 2. Five-minute read-only tour

Use the emoncms page to show the running app and an existing feed graph. The
database, feeds and container store persist on `/mnt/data`; the OS root is
read-only. In a second terminal, connect to the Pi with SSH or its 115200 8N1
serial console and run these commands directly:

```sh
printf 'OS version: '; cat /usr/lib/emonos/version
printf 'Boot slot: '; grep -o 'rauc.slot=[AB]' /proc/cmdline
rauc status --detailed
grep -E ' / | /mnt/data ' /proc/mounts
df -h /mnt/data
docker compose -f /opt/emonos/docker-compose.yml ps
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1/
systemctl is-active emonos-health.service emonos-app.service rauc.service
failed=$(systemctl --failed --no-pager --no-legend --plain)
if [ -z "$failed" ]; then echo 'PASS: no failed systemd units'; else printf '%s\n' "$failed"; fi
jq . /mnt/data/health/last-good.json
cat /sys/class/watchdog/watchdog0/timeout
cat /sys/class/watchdog/watchdog0/state
```

Expected healthy baseline: A is booted/good/primary at `0.1.0`, B is
inactive (it may be marked bad after an interrupted-install demo), `/` is
read-only SquashFS, `/mnt/data` is ext4, HTTP returns `200`, all listed
services are active, no units are failed, and the watchdog state is `active`
with a 30-second timeout.

To demonstrate ordinary reboot persistence without switching power, record
an existing feed and identity before reboot:

```sh
mkdir -pv /mnt/data/demo
feed=$(find /mnt/data/emoncms/phpfina -name '*.dat' | head -1)
test -n "$feed" && printf 'Saving persistence fixture for %s\n' "$feed"
sha256sum "$feed" > /mnt/data/demo/feed.before
echo 'Saved feed checksum.'
cat /etc/machine-id > /mnt/data/demo/machine-id.before
echo 'Saved machine ID.'
sha256sum /mnt/data/ssh/ssh_host_ed25519_key.pub > /mnt/data/demo/ssh-key.before
echo 'Saved SSH host-key checksum.'
sync
echo 'Persistence fixtures synced.'
systemctl reboot
```

After login returns, verify first and avoid running a sample-writing app check
until after comparison:

```sh
sha256sum -c /mnt/data/demo/feed.before
cmp /mnt/data/demo/machine-id.before /etc/machine-id && echo 'PASS: machine ID persisted'
sha256sum -c /mnt/data/demo/ssh-key.before
failed=$(systemctl --failed --no-pager --no-legend --plain)
if [ -z "$failed" ]; then echo 'PASS: no failed systemd units'; else printf '%s\n' "$failed"; fi
```

## 3. Prepare a signed update bundle (host)

Create a development signing key once if needed; retain it for every bundle
the device must trust:

```sh
make rauc_dev_keys
```

The following builds a healthy and a bootable-but-broken v2 bundle. Building
overwrites local output images but does **not** change the already-flashed Pi.
Keep the resulting bundles in a separate directory:

```sh
mkdir -p output/demo-bundles
EMONOS_VERSION=0.2.0 make emonos_rpi4
cp output/rpi4/images/emonos-rpi4.raucb output/demo-bundles/rpi4-v2-healthy.raucb

EMONOS_VERSION=0.2.0-broken EMONOS_APP_FAULT=fail-start make emonos_rpi4
cp output/rpi4/images/emonos-rpi4.raucb output/demo-bundles/rpi4-v2-broken.raucb

# Restore the default factory build tree and ensure the injected fault is gone.
make emonos_rpi4
```

Verify signatures on the host:

```sh
output/rpi4/host/bin/rauc \
  --keyring=output/rpi4/target/etc/rauc/keyring.pem \
  info output/demo-bundles/rpi4-v2-healthy.raucb
output/rpi4/host/bin/rauc \
  --keyring=output/rpi4/target/etc/rauc/keyring.pem \
  info output/demo-bundles/rpi4-v2-broken.raucb
```

Serve **only the public bundles** on the trusted wired LAN. Find the host's
wired IP using `ip -4 -brief address`:

```sh
python3 -m http.server 8767 --bind frenzy.lan \
  --directory output/demo-bundles
```

Leave this terminal running while staging a bundle; stop it afterwards.

## 4. Healthy A→B update demo (Pi shell)

Start with A running and the healthy bundle available from the host. On the
Pi, verify the current slot, capture data checksums, download and verify the
bundle, then install it to the inactive pair:

```sh
grep -q 'rauc.slot=A' /proc/cmdline && echo 'PASS: booted from slot A'
test "$(cat /usr/lib/emonos/version)" = 0.1.0 && echo 'PASS: running v1 0.1.0'
rauc status

curl -fsS --max-time 240 \
  http://frenzy.lan:8767/rpi4-v2-healthy.raucb \
  -o /mnt/data/demo/rpi4-v2-healthy.raucb \
  -w 'Bundle download HTTP %{http_code}\n'
rauc info /mnt/data/demo/rpi4-v2-healthy.raucb
rauc install /mnt/data/demo/rpi4-v2-healthy.raucb
rauc status
sync
systemctl reboot
```

Watch the serial output for `Booting Slot B`, log in, and verify the health
monitor committed the new version:

```sh
grep -q 'rauc.slot=B' /proc/cmdline && echo 'PASS: booted from slot B'
cat /usr/lib/emonos/version                 # 0.2.0
rauc status                      # B booted/good/primary
jq . /mnt/data/health/last-good.json        # slot B, version 0.2.0
systemctl is-active emonos-health.service emonos-app.service
```

The healthy app check can create test data if run separately; compare the
saved feed checksum before running any mutating app test. To return explicitly
to A after demonstrating B, run `rauc status mark-active kernel.0`, confirm its
success message, then:

```sh
sync
systemctl reboot
```

## 5. Broken-update rollback demo (optional, five-minute wait)

Only use the bundle built with `EMONOS_APP_FAULT=fail-start`. This faults the
slot-local application service; it does not modify the persistent Compose
file, database or containers. With A selected and the bundle server running,
on the Pi:

```sh
grep -q 'rauc.slot=A' /proc/cmdline && echo 'PASS: booted from slot A'
rauc status
curl -fsS --max-time 240 \
  http://frenzy.lan:8767/rpi4-v2-broken.raucb \
  -o /mnt/data/demo/rpi4-v2-broken.raucb \
  -w 'Broken bundle download HTTP %{http_code}\n'
rauc info /mnt/data/demo/rpi4-v2-broken.raucb
rauc install /mnt/data/demo/rpi4-v2-broken.raucb
rauc status
sync
systemctl reboot
```

After the serial console shows `Booting Slot B`, **do not issue a reboot**.
The app service should fail while the OS continues running. Wait at least five
minutes for the health monitor to write a failure record and reboot itself.

```sh
watch rauc status
```

On the next boot, expect `Booting Slot A` and then check:

```sh
cat /usr/lib/emonos/version                 # 0.1.0
grep -q 'rauc.slot=A' /proc/cmdline && echo 'PASS: automatically returned to slot A'
ls -lt /mnt/data/health/failures/
rauc status
failed=$(systemctl --failed --no-pager --no-legend --plain)
if [ -z "$failed" ]; then echo 'PASS: no failed systemd units'; else printf '%s\n' "$failed"; fi
```

The health failure record includes the broken version, slot, boot ID, elapsed
time and failure reason. Do not reset `/mnt/data` to make the demo pass.

## 6. Interrupted-install power demo (optional, T7)

This deliberately removes Pi power during an inactive-rootfs write. Only run
it if the single-channel Tasmota at `172.16.0.137` controls **only this Pi**.
First inspect its status fragment; it should report `ON`:

```sh
./flash.sh status
```

Open two terminals: one at the Pi serial console and one on the wired host.

On the Pi, confirm slot A and stage the healthy v2 bundle. Create the guest
watcher; it turns off the single-channel relay only after RAUC logs 75–85%
while copying `rootfs.1`:

```sh
grep -q 'rauc.slot=A' /proc/cmdline && echo 'PASS: slot A is active'
curl -fsS --max-time 240 \
  http://frenzy.lan:8767/rpi4-v2-healthy.raucb \
  -o /mnt/data/demo/rpi4-v2-healthy.raucb \
  -w 'Bundle download HTTP %{http_code}\n'
rauc status
rauc info /mnt/data/demo/rpi4-v2-healthy.raucb
rauc install /mnt/data/demo/rpi4-v2-healthy.raucb 2>&1 | tee "$install_log"
```

On host during the install

```sh
./flash.sh off && sleep 5 && ./flash.sh on
```

The install process/serial session is expected to be interrupted by power
loss. The host monitor restores Power1 after five seconds. Watch the serial
console for `Booting Slot A`, log in, and verify A/0.1.0, health commit and
data checksums as in the previous section. The inactive B system hash should
differ from both its saved v1 hash and the complete v2 bundle image hash.
This does not reflash the card or reset bootstate. Do not run the test if
Power1 is shared with another device; no other relay is involved.

## 7. Finish

- Leave the Pi on healthy, committed A/0.1.0 after the demo.
- Check Tasmota reports Power1 ON. This single-channel switch has no Power5.
- Keep the development signing key and test API key out of screenshots,
  logs and shared artifacts.
- A full-card reflash erases persistent data; back it up before using the
  flash guide. Do not delete or reformat `/mnt/data` casually.

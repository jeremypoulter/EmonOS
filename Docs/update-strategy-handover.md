# emonSD Update Strategy — Analysis and Proposed Direction

**Status:** Discussion document / handover
**Date:** August 2026
**Context:** Review of how emonSD systems are built and kept up to date, and a proposal for a better approach.

---

## 1. Why this document exists

EmonScripts builds the emonSD image and also drives in-field updates. The application
side works well. The problem is everything underneath it: **we do not currently have a
safe way to update the operating system on deployed emonSD systems.** We avoid running
OS updates remotely because a failure leaves the user with a broken system and no
recovery path short of physical access to the SD card.

That caution is reasonable given the current design, but it compounds: deployed systems
drift further behind, and the gap gets harder to close over time.

---

## 2. Current state

### 2.1 Install (image build)

`install/init.sh` clones EmonScripts (`stable`) and runs `install/main.sh`, which reads
`config.ini` (copied from `install/emonsd.config.ini`) and runs component scripts in
sequence:

`apache.sh`, `mysql.sh`, `php.sh`, `redis.sh`, `mosquitto.sh`, `emoncms_core.sh`,
`emoncms_modules.sh`, and for Pi builds `emoncms_emonpi_modules.sh`, `emonhub.sh`,
`firmware.sh`, `emonpilcd.sh`, `docker.sh`, `emonsd.sh`, `network.sh`.

Notable characteristics:

- `apt-get update/upgrade/dist-upgrade` runs **at build time only**
- PHP comes from the third-party **sury** repo (8.1, or 8.0 on ARMv6)
- **phpredis is compiled from git HEAD** — `install/redis.sh:12-22`
- **Mosquitto-PHP is compiled from an OEM fork** — `install/mosquitto.sh:33-43`
- log2ram is installed from an OEM fork
- The image "version" is a marker file `/boot/emonSD-DDMMMYY` generated from the build
  date — `install/emonsd.sh:118-124`
- The image build itself is **manual** (`docs/developer.md`): raspi-config, clear logs,
  disable SSH, `dd`

### 2.2 Update (in-field)

Triggered from the emoncms admin UI via `service-runner`
(`service-runner.py` maps `emoncms-update` to `EmonScripts/update/service-runner-update.sh`).

`update/service-runner-update.sh`:
1. Clears the update log, checks free space
2. Reads `/boot/emonSD-*` and fetches the `safe-update` allowlist from
   `raw.githubusercontent.com/.../master/safe-update` (5 retries)
3. Aborts if the base image is not on the list
4. `git pull` EmonScripts (line 88)
5. Runs the **just-pulled** `update/main.sh`

`update/main.sh`:
- Detects hardware via i2c (`other/i2cdetect.py`)
- `apt-get install python3-pip`, removes pip's `EXTERNALLY-MANAGED` marker, `pip3 install redis`
- `apt-get update` (note: **never `upgrade`**)
- `git pull` of `emonpi`, `RFM2Pi`, `huawei-hilink-status`, `emonPiLCD`
- Runs `update/emonhub.sh`, `update/emoncms.sh`, `update/emonsd.sh`
- Restarts `feedwriter`, `emoncms_mqtt`, `emonhub`, and finally `service-runner`

`update/update_component.sh` is the per-repo worker: refuses to update if there are
local changes, otherwise `fetch`/`checkout`/`pull`, then runs `emoncmsdbupdate.php` and
the module's own `install.sh`.

**Summary: the application layer is a rolling git checkout across ~20 repos; the OS layer
is frozen at image build date.**

### 2.3 Filesystem layout

From `defaults/etc/fstab`:

| Partition | Mount | FS |
|---|---|---|
| `/dev/mmcblk0p1` | `/boot/firmware` | vfat |
| `/dev/mmcblk0p2` | `/` | ext4 |
| `/dev/mmcblk0p3` | `/var/opt/emoncms` | ext2 |

Plus tmpfs for `/tmp` (30M), `/var/tmp` (128M), `/var/lib/php/sessions` (1M), and log2ram
for `/var/log`.

`install/init_resize.sh` sizes partitions on first boot: cards over ~7 GB reserve the
last 10 GB for data, ~8 GB cards reserve 4 GB, otherwise root is capped at 2 GB.

**Relevant to later sections: a lot of write traffic has already been moved off the root
filesystem. That is a meaningful head start.**

---

## 3. Problems with the current model

1. **No OS security patching.** An `emonSD-01Feb24` system runs Bookworm packages from
   December 2023 — apache2, mariadb, openssh, php, kernel. Often LAN-exposed with
   documented default credentials.
2. **No rollback.** A failed update means physical SD access.
3. **Non-reproducible state.** Two devices with the same `/boot/emonSD-*` marker can
   differ arbitrarily: 20 repos pulled at 20 different times, repos with local changes
   silently skipped (`update_component.sh:47` warns to the log only), and PHP extensions
   compiled from whatever HEAD was current on build day.
4. **The update agent updates itself, unpinned.** `service-runner-update.sh:88` pulls
   EmonScripts and immediately executes it. A bad commit reaches every updating device
   at once. No staged rollout.
5. **Not atomic, not verified.** ~20 sequential pulls plus DB migration plus service
   restarts, with no post-update health check. Power loss mid-run leaves undefined state.
6. **`safe-update` gives excluded devices nothing at all** — including security fixes.

### Minor defect found during review

`install/main.sh:92-101` tests for `/usr/lib/python3.11/EXTERNALLY-MANAGED` but removes
`/usr/lib/python3.13/EXTERNALLY-MANAGED`. Commit `3ae3e82` appears to have been only
half applied.

---

## 4. The key reframe

Three different things are all currently called "update". They need different mechanisms
and different risk postures:

| | Cadence | Risk | Today |
|---|---|---|---|
| **(a) Application code** | weekly | moderate | `git pull` x20, no rollback |
| **(b) OS security patches** | continuous | **low** (within a release) | never |
| **(c) Platform moves** (Debian release, PHP major, partitioning) | ~2 years | **high** | manual re-flash |

The reason (b) never happens is that it is being judged with (c)'s risk profile.
`apt upgrade` from `bookworm-security` is not `dist-upgrade` to trixie — Debian works
hard to make the former non-breaking. **Separating these three is the highest-leverage
change available.**

---

## 5. Recommended direction

### Phase 1 — make the current system safe (weeks)

- **`unattended-upgrades`, scoped to the security suite only.** Explicitly exclude sury
  and Docker from the origins pattern — a sury PHP 8.1→8.3 move would break the compiled
  `redis.so`/`mosquitto.so`, and that is the real breakage risk, not Debian itself.
  `Automatic-Reboot "false"`. Surface `/var/run/reboot-required` in the admin UI and on
  the LCD (extend the existing `/tmp/emon_reboot_required` concept).
- **Pre-update snapshot.** Record every component's git SHA, `dpkg --get-selections` and
  config files to `/var/opt/emoncms/update-state/<timestamp>/`. Kilobytes, on the data
  partition. Add `emon-rollback` to restore them.
- **Post-update and boot-time health check.** Services active, apache responding,
  `feed/list.json` returning, mysql reachable, feedwriter advancing. If it fails N boots
  in a row, roll back automatically. **This is what turns "stuck with a broken system"
  into "it recovered by itself".**
- **Interrupted-update marker**, checked at boot.
- **A security-only update path for devices excluded by `safe-update`.**

### Phase 2 — versioned releases instead of "pull latest"

Replace per-repo `git pull` with a **release manifest** pinning exact tags/SHAs for all
components. Devices move *to a release*, not *to whatever is on stable right now*.

Gains: reproducibility, meaningful QA, "roll back to release N-1", channels
(`stable`/`beta`/`dev`) so forum testers absorb breakage first, and one system-version
string for support.

Also: pin EmonScripts itself to the manifest, so the update agent stops self-updating
unpinned (a two-phase run: check out the pinned agent, re-exec, then update everything
else).

Automate the image build here too (pi-gen, or Docker + qemu).

### Phase 3 — separate mutable state from the root filesystem

Prerequisite for everything ambitious, and valuable on its own because it makes
"reflash and restore" a supported five-minute operation.

Currently on root and needing to move:

- `/var/lib/mysql`
- `/var/www/emoncms` and `/opt/*` (the git repos)
- `/etc/emonhub/*`, `settings.ini`, apache/php config
- NetworkManager / wpa_supplicant config, hostname
- `/opt/emoncms/modules/backup/config.cfg`
- user accounts, SSH host keys, `/home/pi`

Then test the export → reflash → import loop every release. **This becomes the official
answer to category (c).** In-place Debian major upgrades should be considered off the
table.

### Phase 4 — A/B root slots

Only if 1–3 prove insufficient. See section 7 for sizing.

---

## 6. What other projects do

| Project | Approach |
|---|---|
| **Home Assistant OS** | Minimal Buildroot host, **A/B partitions**, app layer in Docker containers, all state on a data partition. Closest peer. |
| **OctoPrint / OctoPi** | In-app git/pip updater, OS frozen at build. **This is where we are today.** |
| **OpenWrt** | `sysupgrade` writes a whole new image, preserving an explicit config keep-list. Works because of total state separation. |
| **balenaOS / balenaCloud** | Minimal A/B container host. Open source, usable without the cloud backend. |
| **Mender / RAUC / SWUpdate** | A/B update frameworks; Mender includes a self-hostable deployment server. |
| **openSUSE MicroOS / OSTree / NixOS** | Snapshot-based rollback **without** repartitioning. Worth pricing — could reach existing devices. |

### Home Assistant's update model (the one to copy)

There is **no fleet management server**. The model is:

- A **static version manifest** published per channel; devices poll it
- **Release channels** (stable/beta/dev), user-selected — staged rollout by
  self-selection
- **Opt-in, aggregate, publicly published analytics** — not per-device, not actionable
  remotely

For OEM this matters: many users self-host specifically to avoid anything phoning home.
A central management server would likely be more contentious than the problem it solves.
The recommended middle path is **opt-in health reporting of two fields — release ID and
health-check result** — which gives us the feedback loop ("release 2026-09 is on 400
devices with no health-check failures") without ever holding the ability to push to
someone's device.

---

## 7. HAOS partition sizing (verified)

From `buildroot-external/scripts/hdd-image.sh` in `home-assistant/operating-system`:

```
BOOTSTATE_SIZE=8M
SYSTEM_SIZE=256M
KERNEL_SIZE=24M
OVERLAY_SIZE=96M
```

| Partition | Size |
|---|---|
| `hassos-boot` | FAT, platform-specific |
| `hassos-kernel0` / `kernel1` | 24 MB each |
| `hassos-system0` / `system1` | **256 MB each — SquashFS, read-only** (EROFS on PC/VM) |
| `hassos-bootstate` | 8 MB |
| `hassos-overlay` | 96 MB |
| `hassos-data` | 1280 MB initially, expands to fill disk |

**Total duplicated (the actual cost of A/B): 2 x (256 + 24) = 560 MB.**

Two reasons it is so small:

1. **Read-only compressed rootfs.** Written only during an update, so a slot *cannot
   drift* — stronger than A/B alone.
2. **No application software in the rootfs at all** — Core, Supervisor and add-ons are
   container images on the data partition, which is not duplicated.

### Implication for emonSD

Our rootfs is ~3 GB. Naive A/B would need two ~4 GB slots. Keeping Debian + LAMP in the
slot but moving app and state out gets to ~1.5–2 GB uncompressed; **squashfs would
roughly halve that** to ~700 MB–1 GB per slot, which fits 16 GB cards.

Good news: our low-write design (tmpfs for `/tmp`, `/var/tmp`, PHP sessions; log2ram for
`/var/log`) has **already pushed most write traffic off root**. The remaining blockers to
a read-only root are exactly the Phase 3 state-separation list.

Note HAOS's 96 MB `hassos-overlay`: they did not try to make `/etc` immutable, they gave
it a small persistent partition of its own. Worth copying.

---

## 8. The proposed prototype

**Goal: build and test a replica of the HA approach for emonSD, running a containerised
emoncms.**

### Candidate image: `alexjunk/emoncms`

- **~260 MB, multi-arch** (arm64, arm/v7, amd64) — covers Zero 2 through Pi 5
- **Single container**: apache2, MariaDB, redis, mosquitto, emoncms and all three workers
  (`emoncms_mqtt`, `service-runner`, `feedwriter`), managed by s6-overlay
- **Already packaged as a Home Assistant add-on**
- Data persists via a `/data` volume
- Actively maintained (last push May 2026)
- Separate `alexjunk/emonhub` image for the hardware-facing side
- Tag scheme `alpine3.20_emoncms11.12.3` — **this is already the immutable versioned
  release unit Phase 2 needs**

Source: https://github.com/Open-Building-Management/emoncms
Docs: https://emoncms-docker.github.io
Compose files: https://github.com/Open-Building-Management/compose-files

### Three risks to investigate first

1. **Rolling back a container that contains MariaDB may not be safe.** If a newer tag
   ships a newer MariaDB that upgrades the on-disk format at first start, reverting the
   tag can leave a database the older engine will not open. HA avoids this by keeping the
   database out of Core. **Test this explicitly — it is the one place "just use the
   previous tag" can fail, and rollback is the whole point.**
2. **The module set is smaller than emonSD's.** Listed: graph, dashboard, postprocess,
   backup, sync. Missing: `config`, `setup`, `network`, `device`, `app`, `usefulscripts`
   — i.e. exactly the emonPi-specific, hardware-facing modules. **Where these live
   (host vs emonhub container vs a second container) shapes the whole design and should
   be decided early.**
3. **Supply chain.** We would be shipping hardware depending on a third party's Docker
   Hub account. At minimum pin by digest and mirror; more likely build our own images
   from that Dockerfile. Worth approaching the maintainer about collaborating rather
   than forking.

### Staged plan

**Stage 1 — does the stack work on our hardware?** *(days)*
Plain Raspberry Pi OS Lite, docker compose, container running. Migrate a real emonSD
dataset into `/data`. Measure:
- RAM on a Pi Zero 2 (512 MB, with MariaDB and redis in-container is the tight case)
- CPU under realistic input load
- **SD write amplification through overlay2** — matters a lot given how much effort the
  current image spends on write reduction
- emonhub container access to serial port and GPIO

*If any of this fails, no amount of A/B engineering saves it. Do this before anything else.*

**Stage 2 — update machinery at the container level.** *(weeks)*
Manifest + channels + health check + rollback, where "roll back" is simply the previous
image tag. **This delivers the entire HA app-layer model with zero partitioning work**,
and is most of the practical value.

**Stage 3 — minimal host with A/B.** *(months)*
Base OS options:
- **balenaOS** — already a minimal A/B container host for Pi, open source, usable without
  balenaCloud. **Shortest path to a working replica — it already is this architecture.**
- **Buildroot** — what HA actually does. Most control, steepest climb.
- **Trimmed Raspberry Pi OS + hand-rolled A/B** — most familiar, but we own the mechanism.

Note: `tryboot` bootloader fallback is Pi 4/5 only. Pi 3 / Zero 2 need an initramfs slot
chooser with a boot counter.

---

## 9. Related work item: remove the compiled PHP extensions

This is not a side quest — it is what decouples a release from a specific PHP build, and
it is a prerequisite for both deterministic manifests and a trimmed rootfs.

### phpredis — easy win

`install/redis.sh:12-22` compiles phpredis from git HEAD, but **`php-redis` is packaged**
in Debian, and sury ships versioned `php8.x-redis`. Straight swap to
`apt-get install -y php-redis`. Also removes the untracked `install/phpredis/` build tree
currently sitting in git status.

### Mosquitto-PHP — the hard one

- **No Debian/Ubuntu package exists** (`apt-cache search php-mosquitto` returns nothing)
- Upstream `mgdm/Mosquitto-PHP` is effectively unmaintained (PECL 0.4.0, alpha, ~2016);
  we run an OEM fork
- The `.so` is tied to one PHP minor version, so a sury PHP bump silently kills
  `emoncms_mqtt` — **this is a large part of why the OS has to stay frozen**

**Actual API surface in use** (three real files: `emoncms_mqtt.php`,
`scripts/phpmqtt_input.php`, `Modules/process/process_processlist.php`):

constructor, `connect()`, `loop()`, `disconnect()`, `publish()`, `subscribe()`,
`setCredentials()`, `setTlsCertificates()`, the five `on*()` callbacks, and the
`Mosquitto\Message` object.

**Usage details that reduce the work:**
- **Every publish is QoS 0** (all 2-arg calls) — no outgoing QoS 1/2 state machine needed
- **Subscribe is QoS 2** and emonhub publishes at `qos=2`
  (`EmonHubMqttInterfacer.py:181`), so the *incoming* QoS 2 handshake **is** required
- `cleanSession` is `true` — no persistent session state across reconnects

**Options, ranked:**
1. **Vendor `php-mqtt/client`** into `Lib/` (MIT). Battle-tested, no Composer runtime
   dependency, no deployment change. *Recommended.*
2. **Write our own** — approximately 650 lines for this subset. Feasible, but we own
   every protocol bug (partial reads, dead-connection detection, TLS edge cases). The
   worst failure mode is silent message loss, which surfaces as a gap in a user's energy
   data weeks later.
3. **Composer dependency** — cleanest hygiene, but emoncms currently uses Composer for
   `require-dev` only, so this changes how it deploys.

**Write the compatibility shim first** (~80–120 lines exposing the `Mosquitto\Client`
surface). It makes the choice reversible and means `emoncms_mqtt.php` never has to change.

Note: `composer.json` already lists `ext-mosquitto-php` under `suggest`, not `require` —
treating it as optional is already the stated intent.

---

## 10. Hardware access under a containerised architecture

**The single most important finding: emoncms itself needs no hardware access at all.**
Apache, PHP, MariaDB, redis and mosquitto touch no device nodes. So the largest and most
complex container is fully portable and trivially rollback-able. Only small satellite
containers touch hardware.

### 10.1 What actually touches hardware

| Component | Hardware surface |
|---|---|
| `EmonHubJeeInterfacer` | `/dev/ttyAMA0` serial |
| `EmonHubRF69Interfacer`, `EmonHubRFM69LPLInterfacer` | `spidev` **plus** `RPi.GPIO` |
| `EmonHubPulseCounterInterfacer`, `EmonHubDigitalInputInterfacer` | `RPi.GPIO` |
| `EmonHubDS18B20Interfacer` | `/sys/bus/w1/devices/` |
| USB serial interfacers (MBUS, Modbus, SDM120, VEDirect, ...) | `/dev/ttyUSB*`, `/dev/ttyACM*` |
| `rpi-rfm69` library | `spidev` + `RPi.GPIO` (interrupt + reset pins) |
| emonPi LCD / OLED | `smbus.SMBus(1)` -> `/dev/i2c-1` (see `other/i2cdetect.py`) |
| avrdude firmware upload | serial **plus** GPIO autoreset via the `avrdude-rpi` wrapper |

### 10.2 Passing devices in

**I2C (OLED/LCD) — easiest.**

```yaml
devices:
  - /dev/i2c-1:/dev/i2c-1
```

Gotcha: the node is typically `root:i2c` mode 0660, and inside the container what matters
is the **numeric** gid, not the group name — Alpine may have no `i2c` group at all. Use
`group_add: ["<host i2c gid>"]` rather than assuming a name resolves.

**SPI — easy.** `--device=/dev/spidev0.0`. Requires `dtparam=spi=on` on the host.

**Serial — easy until it hot-plugs.** `/dev/ttyAMA0` maps cleanly. USB serial is the
problem: `devices:` binds the node at container *start*, so unplugging and replugging an
emonTx can change the major/minor and leave the container with a stale node. Docker does
not re-map it. Options:

- `privileged: true` plus a `/dev` bind mount (works, drops isolation)
- `device_cgroup_rules` for the tty char-major plus a `/dev` mount
- a host udev rule that restarts the container on plug events

**This is exactly what the HAOS Supervisor does for you** — it watches udev and re-maps.
Whatever we build will need an equivalent.

### 10.3 Landmine: `RPi.GPIO` does not work on Pi 5

`RPi.GPIO` maps BCM registers directly via `/dev/gpiomem`. Pi 5's RP1 southbridge changed
the hardware, so the library simply does not function there. **Four emonhub interfacers
and the `rpi-rfm69` library all import it.**

This is already a live problem independent of containers. The fix — worth doing either
way — is **`rpi-lgpio`**, a drop-in replacement providing the `RPi.GPIO` API on top of
libgpiod / `/dev/gpiochipN`. It also improves the container story considerably: pass
`/dev/gpiochip*` instead of `/dev/gpiomem`, and privileged mode is no longer needed.

Note that gpiochip **numbering varies** by model and kernel version. Find the chip by
label (`pinctrl-bcm2835` vs `pinctrl-rp1`) rather than hardcoding an index.

### 10.4 What cannot be containerised: device tree overlays

`dtparam=i2c_arm=on`, `dtparam=spi=on`, `dtoverlay=w1-gpio,gpiopin=17`
(`install/emonsd.sh:82`), `dtoverlay=disable-bt` for `ttyAMA0` on Pi 3 — all live in
`/boot/firmware/config.txt` and **can never be set from inside a container**.

Consequence: **the host image and the containers are not fully decoupled.** Supporting a
new sensor type that needs a new overlay requires a host update, not just a container tag
bump. This is the main limit on what the container layer buys us, and it should be stated
plainly in any design.

### 10.5 The architectural consequence: we need a Supervisor equivalent

Three emoncms modules currently reach outside the application:

| Module | What it does | Problem in a container model |
|---|---|---|
| `config` | Edits `/etc/emonhub/emonhub.conf`, restarts emonhub | Cross-container |
| `network` | nmcli, hotspot, wifi-check (see section 11) | Needs the host |
| `admin` + `service-runner` | Runs EmonScripts update shell scripts | Needs the host, and its **entire job changes** |

That last row is the significant one. Today `service-runner` executes update scripts on
the host. In a container model "update" means swapping image tags, which `service-runner`
cannot do from inside a hardware-free container without the Docker socket — and giving a
web application the Docker socket is effectively granting root on the host.

**So a Supervisor equivalent is required**: a small privileged host-side agent owning
container lifecycle, tag swaps, health checks, rollback, network configuration and
firmware upload, exposing an API that emoncms calls. `service-runner` becomes a *client*
of that agent rather than a shell-script runner.

**This is a substantial piece of new software and needs to be scoped explicitly. It is
the largest single item not visible in the original Stage 2 estimate.**

For the `config` module there is a neat dodge: put `emonhub.conf` on the data partition,
bind-mount it into both containers, and have **emonhub watch the file with inotify and
self-reload**. No cross-container control needed.

### 10.6 Suggested container/device split

| Container | Devices |
|---|---|
| **emoncms** | none |
| **emonhub** | `/dev/ttyAMA0`, `/dev/ttyUSB*`, `/dev/spidev0.*`, `/dev/gpiochip*`, `/sys/bus/w1` |
| **emonPiLCD** | `/dev/i2c-1`, `/dev/gpiochip*` (buttons) — or fold into emonhub |
| **supervisor agent** | Docker socket, host network, NET_ADMIN — privileged |
| **host only** | `config.txt` overlays, kernel, NetworkManager |

---

## 11. The network module — the most host-coupled component

`/opt/emoncms/modules/network` (v3.3.0) manages WiFi client connections and the fallback
access point. It is worth examining closely because it is the component least compatible
with containerisation, and because it is on the critical path for **first-boot
provisioning** of a headless device.

### 11.1 Full OS surface

From `network-module/network_model.php`, `install.sh` and `scripts/`:

**Commands invoked:**
- `nmcli` — connect, rescan, `device show`, create/modify/delete connection profiles,
  bring the hotspot up and down
- `iw dev wlan0 interface add ap0 type __ap` — creates the **virtual AP interface**,
  needs NET_ADMIN and the host WiFi driver
- `ip addr show`
- `journalctl -u NetworkManager` (`nm_log.sh`)

**Files read:**
- `/sys/class/net/<iface>/carrier` — link state
- `/etc/NetworkManager/system-connections/` — counted to detect whether any client WiFi
  is configured

**Files written (by `install.sh`, as root):**
- `/etc/NetworkManager/NetworkManager.conf` — **overwritten wholesale**
- `/etc/NetworkManager/dnsmasq.d/redirect.conf` — captive portal DNS redirects
  (gstatic, apple, msftncsi)
- `/etc/sudoers.d/network-sudoers` — grants `www-data` NOPASSWD on five scripts
- `/usr/local/bin/wifi-check` symlink
- **root crontab entry running `wifi-check` every minute**

**Privilege model:** `www-data` has passwordless sudo on `wifi_connect.sh`,
`wifi_rescan.sh`, `startAP.sh`, `stopAP.sh`, `nm_log.sh`. Since those scripts drive
nmcli, the web application effectively has full control of host networking.

**Credential handoff:** PHP writes `/tmp/wifi-config.ini`; `wifi_connect.sh` reads it and
deletes it immediately. The password is validated as a 64-character hex PBKDF2 hash,
which sensibly prevents injection. The SSID is length-checked but not
character-validated (it is passed quoted to nmcli).

**State files:** `/tmp/<iface>_last_state`, `/tmp/<iface>_changed`,
`/tmp/wifi-check-firstboot` — all on tmpfs, so AP-on-boot logic resets each boot by design.

### 11.2 Why this cannot simply be containerised

It is not a matter of passing a device node through. NetworkManager is a host daemon
addressed over D-Bus; creating `ap0` needs the host WiFi driver and NET_ADMIN; the config
files are host config; the cron entry runs on the host. And critically, **the module
configures the very network the container depends on.**

### 11.3 Options

1. **Leave it entirely on the host** as a small separate service. Cleanest separation,
   but splits the user-facing UI across two places.
2. **Run it in a container with `network_mode: host`, `NET_ADMIN`, and the D-Bus system
   socket bind-mounted** (`/var/run/dbus/system_bus_socket`) plus `/etc/NetworkManager`.
   nmcli in a container can talk to the host NetworkManager this way. Works, but that
   container is effectively privileged.
3. **The supervisor agent exposes a network API and the emoncms UI calls it.** This is
   the HA model — HAOS's frontend has a network configuration UI backed by Supervisor,
   which talks to NetworkManager over D-Bus.

**Recommendation: option 3**, consistent with section 10.5. The network module's PHP
views stay in emoncms; its model layer calls the agent API instead of shelling out
through sudo. All five sudoers entries then disappear from the web application.

### 11.4 The provisioning argument

There is a robustness reason to prefer host/supervisor placement beyond tidiness. The
access point and captive portal are how a user gets a **headless emonPi onto their WiFi
in the first place**. That has to work before the emoncms container is necessarily
healthy — and ideally it should still work when emoncms is broken, so a user can reach a
failed device rather than being locked out.

**Putting network provisioning in the same container as the application creates a
circular dependency at exactly the moment recovery matters most.** It belongs in the
layer below.

---

## 12. Suggested first actions

1. Run `alexjunk/emoncms` locally and inspect the `/data` layout, s6 service set, real
   module list, and settings injection
2. **Test a tag-to-tag upgrade and downgrade against a populated MariaDB volume** — the
   highest-risk unknown for the rollback story (section 8)
3. **Scope the supervisor agent** (section 10.5). This is new software that does not
   exist today and is not visible in a naive Stage 2 estimate. It owns container
   lifecycle, tag swaps, health checks, rollback, network configuration and firmware
   upload.
4. Decide the host/container boundary for `config`, `setup`, `network` and the LCD
   (sections 10.6 and 11.3)
5. Then write the Stage 1 hardware test plan with explicit pass/fail criteria

### Independent of the container question

These are worth starting in parallel — they benefit **every device in the field today**,
including ones that will never run containers:

- **Phase 1 safety work**: unattended-upgrades scoped to security, pre-update snapshot,
  health check, rollback (section 5)
- **`php-redis` package swap** — a straight replacement for a compiled extension
  (section 9)
- **Migrate `RPi.GPIO` to `rpi-lgpio`** — Pi 5 does not work today regardless of
  containers (section 10.3)
- **Fix `install/main.sh:92-101`** — python 3.11 check, 3.13 removal (section 3)

---

## 13. Working notes for anyone picking this up

Applies equally to a human colleague and to an AI agent working in the repo.

### 13.1 Working directory topology

Work spans four locations, which is not obvious from EmonScripts alone:

| Path | Contents |
|---|---|
| `/opt/openenergymonitor/EmonScripts` | These build/update scripts (this repo) |
| `/opt/openenergymonitor/*` | emonhub, RFM2Pi, emonPiLCD, rpi-rfm69 and other OEM repos |
| `/var/www/emoncms` | emoncms core and the `Modules/` installed directly into it |
| `/opt/emoncms/modules` | Symlinked modules: backup, sync, postprocess, network, usefulscripts, ... |

Every one is an independent git repository with its own `module.json` version. There are
roughly 20 of them, and that fragmentation is the root of the reproducibility problem in
section 3.

### 13.2 The development machine is not an emonSD

The dev machine used for this analysis is **Ubuntu 26.04 on amd64**. It has emoncms and
the modules installed, but:

- there is no `/boot/emonSD-*` marker, no `/dev/mmcblk*`, no ext2 data partition
- there is no i2c, spi, GPIO or serial hardware
- `emonSD_pi_env` logic and all Pi-specific paths are inert

**Do not infer emonSD behaviour from what this machine does.** Anything hardware-related
must be verified on real Pi hardware.

### 13.3 Destructive operations — read before running anything

Several scripts in this repo make **system-wide, non-reversible changes** and are
designed to run on a fresh Raspberry Pi OS image, not a working machine:

- `install/main.sh` and everything it calls — installs packages, adds third-party apt
  repos (sury, Docker), rewrites config, restarts services
- `install/emonsd.sh` — rewrites `/boot/firmware/config.txt`, installs logrotate and
  rc.local symlinks, installs a firewall
- `/opt/emoncms/modules/network/install.sh` — **overwrites
  `/etc/NetworkManager/NetworkManager.conf` wholesale**, writes sudoers entries, adds a
  root crontab entry
- `other/factoryreset` — drops the emoncms database and deletes all feed data
- `update/main.sh` — restarts services and pulls ~20 repos

**Rule: never run install or update scripts on a development machine.** Use a disposable
Pi, a VM, or a container.

### 13.4 Non-obvious conventions

- **`source load_config.sh`** — nearly every script starts with this; it loads
  `install/config.ini` (copied from `emonsd.config.ini` on first run) and exports the
  path variables. `config.ini` is gitignored, so a fresh clone has no config until
  `main.sh` creates it.
- **Uncommitted changes block updates.** `update/update_component.sh:21-22` refuses to
  update any repo with local modifications, warning only to the log. Leaving edits in a
  module directory will silently stop that module updating on real devices. Commit or
  stash.
- **Two-branch model.** Components use `master` for development and `stable` for release.
  `other/stable_release.sh` performs the release: bump `module.json`, merge master into
  stable, tag, push, create a GitHub release, merge back.
- **`safe-update`** is the allowlist of base images permitted to update, fetched from the
  `master` branch at runtime (see section 2.2).
- **`docs/` feeds a sphinx toctree** (`docs/index.rst`). Do not drop loose markdown files
  there — it will alter the published documentation site. This handover doc deliberately
  sits in the repo root.

### 13.5 Tests and checks

emoncms core has phpunit suites and composer scripts:

```
composer lint      # php-parallel-lint
composer phpcs     # code style
composer test      # lint + phpcs
composer phpunit   # unit tests (tests/phpunit.xml)
composer phpunit-integration
composer phpunit-feature
```

EmonScripts itself has **no test suite** — shell scripts are validated by running them on
real hardware. That is part of why the health check in Phase 1 matters.

### 13.6 Decisions already taken

Recorded so they are not re-litigated. Each can be reopened with new evidence, but not by
default:

- **In-place Debian major upgrades are off the table.** A new image plus a tested
  migration path is the answer to platform moves (section 5, Phase 3).
- **Containers are a Phase 3 destination, not a substitute for Phase 1.** Health checks,
  rollback and security patching are needed either way and are far cheaper.
- **No central fleet-management server.** Opt-in, aggregate, publicly published analytics
  only — following Home Assistant (section 6). Many users self-host specifically to avoid
  anything phoning home.
- **Phase 1 work proceeds regardless of the container decision.** It benefits every device
  already deployed.

### 13.7 Open questions needing a human decision

Do not assume answers to these:

1. **Minimum SD card size** for a new image. A/B needs 32 GB unless the rootfs is trimmed
   and compressed (section 7).
2. **Which Pi models remain supported.** Dropping Pi Zero 2 / 32-bit materially changes
   the container RAM analysis and the `RPi.GPIO` migration scope.
3. **Build our own container images or depend on `alexjunk/*`?** A supply chain question,
   not a technical one (section 8).
4. **Does the supervisor agent get written in-house, or do we adopt balenaOS / Mender and
   inherit theirs?** Largest single scoping decision (section 10.5).
5. **Where the network provisioning UI lives** once emoncms is containerised
   (section 11.3).

---

## References

- HAOS partitioning: https://developers.home-assistant.io/docs/operating-system/partition/
- HAOS build script: https://github.com/home-assistant/operating-system/blob/dev/buildroot-external/scripts/hdd-image.sh
- HAOS add-on hardware access: https://developers.home-assistant.io/docs/add-ons/configuration/
- emoncms Docker image: https://hub.docker.com/r/alexjunk/emoncms
- emoncms Docker source: https://github.com/Open-Building-Management/emoncms
- emoncms Docker docs: https://emoncms-docker.github.io
- emonhub Docker image: https://hub.docker.com/r/alexjunk/emonhub
- php-mqtt/client: https://packagist.org/packages/php-mqtt/client
- rpi-lgpio (RPi.GPIO replacement): https://rpi-lgpio.readthedocs.io/

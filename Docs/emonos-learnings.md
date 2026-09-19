# EmonOS — Consolidated Learnings

**Status:** Working document — findings from reviewing the existing design docs against real source
**Date:** 2026-08-31
**Builds on:** [update-strategy-handover.md](update-strategy-handover.md), [emon-os-initial-design.md](emon-os-initial-design.md), [sd-card-write-load.md](sd-card-write-load.md)
**Feeds into:** [emonos-product-spec.md](emonos-product-spec.md) — this document is the evidence; that one is the specification

---

## Provenance

Following the convention set by [sd-card-write-load.md](sd-card-write-load.md), claims here are marked:

| Marked | Meaning |
|---|---|
| **[verified]** | Read directly from source at the revision named below |
| **[inferred]** | Reasoned from verified facts; sound but not directly observed |
| **[unresolved]** | Could not confirm — listed in §10 |

Revisions read:

- `home-assistant/operating-system` @ `42ea0f607` — `buildroot-external/`, `.github/`, `tests/`
- `openenergymonitor/EmonScripts` @ `3c09925` (master)
- `openenergymonitor/emoncms` @ `28a26313` — `.github/workflows/`, `composer.json`, `Lib/dbschemasetup.php`, `index.php`
- `JeremyPoulter/emoncms-docker` @ `573c00a` (branch `remove_local_emoncms_patches`)

No measurement was taken on Raspberry Pi hardware for this document.

---

## 1. Why this document exists

The three existing docs cover the problem well, but they were written independently and
they disagree with each other in several places. Two of them also contain
recommendations that don't survive contact with the source they cite.

This document records what was verified, what changed as a result, and what still needs
a human decision. It does not restate the existing docs — read those first.

**The single biggest change in position:** [emon-os-initial-design.md](emon-os-initial-design.md) §12 lists multi-board
support among the things to consciously *not* build — *"HAOS's board metadata scheme
handles ~40 targets. You have one. Keep it that way until you don't."*

That is wrong, and it is wrong twice over. We do not have one target, we have at least
four shipping products across at least two boot architectures (§2). And the board
abstraction is what makes a desktop/VM target nearly free rather than a fork (§5) — and
what makes the CI matrix scale (§6.5). It is cheap to build in from the start and
expensive to retrofit.

---

## 2. The product matrix

emonSD is not a single product. It is the base for emonPi 1, emonPi 2, emonPi 3 and
emonBase, and those differ in ways that reach all the way down into the device tree.

| Axis | emonPi 1 | emonPi 2 | emonPi 3 | emonBase |
|---|---|---|---|---|
| MCU | ATmega328 | ATmega328 | **ATSAMD21 (emon32)** | RFM69Pi: ATmega328 / RFM69-SPI: none |
| Firmware upload | avrdude + GPIO autoreset | avrdude + GPIO autoreset | **BOSSA over ttyAMA0** | avrdude / n/a |
| Radio path | via MCU serial | via MCU serial | via MCU serial | **SPI direct** or via MCU |
| LCD + buttons | i2c + GPIO | i2c + GPIO | i2c + GPIO | none |
| One-wire overlay | **conflicts** | required | ? | n/a |
| Pi model | [unresolved] | [unresolved] | [unresolved] | [unresolved] |

### 2.1 One-wire is already a broken build-time variant [verified]

`install/emonsd.sh:78-89` toggles `dtoverlay=w1-gpio,gpiopin=17` on the `enable_onewire`
config flag, carrying this comment:

> *"One wire temperature sensing support for emonPi v2. IMPORTANT: This will likely
> interfere with shutdown button on emonPi v1. It's best to disable onewire if using
> this image with an emonPi v1."*

So emonSD is **already** a build-time variant, and the incompatibility is recorded in a
source comment rather than in any variant mechanism. An A/B design has to make this
explicit or inherit the same footgun with worse consequences — a user who flashes the
wrong image no longer has an easy way to notice or correct it.

### 2.2 emonPi3 firmware upload cannot work on a read-only rootfs [verified]

`update/emonpi3_firmware_upload.sh:19-23` clones BOSSA from GitHub and runs
`make bossac` **on the device, at update time**, then drives the ATSAMD21 bootloader over
`/dev/ttyAMA0`.

This is the same unpinned-compile-from-HEAD pattern the handover doc identifies for
phpredis and mosquitto-php (§9 there), but worse: it needs a compiler present at runtime.
A Buildroot squashfs/EROFS root has neither a toolchain nor anywhere to write.

**Action:** `bossac` becomes a built Buildroot package. Same for `avrdude` and the
`avrdude-rpi` autoreset wrapper, which additionally depends on `RPi.GPIO` — which does
not function on Pi 5 at all (handover §10.3).

### 2.3 The UART is contended three ways [verified]

emonPi 1/2/3 all reach their MCU over `/dev/ttyAMA0`; emonPi3 firmware upload drives the
same port; and [emon-os-initial-design.md](emon-os-initial-design.md) §6.4 puts `console=serial0,115200` on it.

The design doc notes the conflict but treats it as a development convenience trade-off.
For emonPi it is not a trade-off — the serial console cannot ship enabled, which removes
the primary field-diagnostic channel on exactly the products that most need one. Plan for
this: either accept no serial console on emonPi variants, or use `miniuart-bt` on Pi 4.

---

## 3. The structural problem: device tree vs immutability

Handover doc §10.4 establishes that device tree overlays can never be set from inside a
container. The product matrix adds the second half of the problem: **in an A/B design,
`config.txt` lives inside the boot slot and is replaced wholesale by every update.**

So per-product configuration is baked into the update artifact. Three options: [inferred]

| Option | Cost |
|---|---|
| One image per product | 4+ products × 2 Pi families. Release matrix grows badly; a card moved between products gets a subtly wrong device tree |
| One image, all overlays, runtime selection | Does not work — overlay conflicts (§2.1) are resolved at boot, not runtime |
| **One image + persistent variant fragment** | `config.txt` `include`s a fragment held on the data partition; the RAUC install hook copies it into the newly written boot slot |

**Recommendation: option 3.** It keeps one artifact per Pi family, keeps the slot
immutable between updates, and makes "what product am I" persistent state rather than
image identity. Two things it needs: the install hook owns a step that must not fail, and
a defined behaviour for a card moved between products.

**Consequence:** hardware detection has to move earlier. Today `update/main.sh` probes
i2c *during an update*. Product identity must instead be established at first boot and
persisted, because it is an input to boot configuration rather than a runtime discovery.

---

## 4. What HAOS actually does — three corrections [verified]

Read from `home-assistant/operating-system` @ `42ea0f607`. Each of these contradicts
[emon-os-initial-design.md](emon-os-initial-design.md).

**4.1 HAOS uses U-Boot on Pi 4, not tryboot.**
`buildroot-external/board/raspberrypi/rpi4-64/meta` is `BOOTLOADER=uboot`. tryboot appears
only in `rpi5-64/meta`. The design doc §1.2 recommends Pi 4 and §1.3 recommends tryboot
"because it's what HAOS does on Pi 5" — but that *combination* is not one HAOS has
validated. U-Boot also gives a real boot attempt counter, which the design doc itself
concedes tryboot lacks.

**4.2 tryboot works with GPT.**
Design doc §6.1 states GPT is unreliable with `autoboot.txt` and mandates MBR.
`rpi5-64/meta` is `BOOTLOADER=tryboot` **and** `PARTITION_TABLE_TYPE=gpt`. Retest before
committing: choosing MBR forecloses the ESP-based layout and makes a partition scheme
shared with desktop targets much harder.

**4.3 One boot partition, not two.**
The design doc's layout pairs `boot_a`/`boot_b` FAT partitions. HAOS has a **single** ESP
plus separate raw `kernel0`/`kernel1` partitions
(`buildroot-external/genimage/partitions-os-gpt.cfg`). That single-boot-partition design
is precisely what lets one layout work under GRUB, U-Boot *and* tryboot.

These three fit a pattern. Each Pi-specific choice in the design doc is individually
defensible; collectively they hardcode assumptions that make a second target expensive.

### 4.4 The GRUB A/B mechanism is better than tryboot

`buildroot-external/board/pc/grub.cfg` implements slot selection in ~60 lines of GRUB
script over a `grubenv`: `A_OK`/`B_OK` validity flags, `A_TRY`/`B_TRY` attempt counters
with a limit of 3, an `ORDER` variable, and rescue-shell menu entries for both slots.
RAUC drives it through its **native `grub` backend** — no custom bootloader backend
script needed, unlike the tryboot path which requires
`rootfs-overlay/usr/lib/rauc/rpi-tryboot.sh`.

---

## 5. A desktop / VM target is nearly free

HAOS ships `generic_x86_64`, `generic_aarch64` and `ova`. What is shared with the Pi
targets, verbatim: [verified]

- the entire partition layout (`partitions-os-gpt.cfg`)
- the RAUC slot definitions — one templated `ota/system.conf.gtpl` covers every board;
  only the `bootloader=` line varies (`grub` vs `custom` + tryboot handler)
- the RAUC bundle contents — `hook`, `boot.vfat`, `kernel.img`, `rootfs.img`
- the rootfs overlay, systemd units, EROFS, Docker, NetworkManager
- kernel fragments `haos.config`, `docker.config`, `device-support*.config`

The per-board delta is roughly 300 lines:

| Piece | Size | What changes |
|---|---|---|
| `meta` | 10 lines | `BOOTLOADER=grub`, `KERNEL_FILE=bzImage`, `PARTITION_TABLE_TYPE=gpt`, `BOOT_SIZE=32M` |
| defconfig | ~2 structural lines | `BR2_x86_64=y` vs `BR2_aarch64=y`, kernel defconfig, GRUB EFI targets |
| `grub.cfg` | ~60 lines | A/B slot selector |
| `haos-hook.sh` | ~20 lines | Seed `grubenv`, copy `grub.cfg` into the ESP |
| `cmdline.txt` | 1 line | `console=tty0` |
| `kernel.config` | 190 (x86) / 85 (aarch64) | Board drivers |

For a VM target add `qemu-img convert` to vmdk/vhdx/vdi/qcow2 plus an OVA tarball — ~15
lines in `board/pc/ova/haos-hook.sh`.

x86 and aarch64 desktop are the same work: both are GPT + ESP + GRUB EFI. Once one
exists, the other is a defconfig and a driver list.

**The real cost is the hardware matrix, not the code.** The x86 defconfig carries ~70
`BR2_PACKAGE_LINUX_FIRMWARE_*` entries — wifi, Bluetooth, ethernet, GPU — plus PCI
device-support fragments the Pi targets don't use. The `pc` board is the only one needing
an `erofs-compress-hints.txt` to fit that payload into a 256 MB slot.

### 5.1 Why to do it early

The payoff is **testability**, not the NUC use case. See §6.4 — HAOS's entire OS update
and rollback test suite runs against the `ova` image in QEMU. Without an x86 target there
is no CI test target at all.

Handover doc §13.5 notes EmonScripts has no test suite and its shell scripts are
"validated by running them on real hardware"; §13.2 warns the dev machine tells you
nothing about emonSD behaviour. Given the project's own premise — *"a rollback path you
have not tested is not a rollback path"* (design doc §10.2) — being able to test it
automatically is close to the highest-value item available.

It also unblocks parallel work. The supervisor agent, the emoncms container work and the
MariaDB rollback question need no Pi hardware, but are currently gated behind it.

Secondary benefit: it gives a supported answer to self-hosters running emoncms on a
mini-PC, Proxmox or a VM, which is unsupported today.

### 5.2 How it fits the variant scheme

A desktop target has no MCU, no RFM69, no i2c LCD, no GPIO and no device tree — in the §3
model it is a board with an **empty hardware profile**. That makes it a useful forcing
function: if the desktop image builds and boots, the variant abstraction is real rather
than aspirational.

Functionally it is an **emonBase-class** device — emonhub over USB serial to an emonTx,
or MQTT from remote nodes. That makes one problem worse: on a desktop everything is USB,
so USB serial hot-plug re-mapping (handover §10.2, *"exactly what the HAOS Supervisor
does for you"*) moves from edge case to normal path. Another argument for scoping the
supervisor agent early.

---

## 6. CI/CD, dependency updates and testing

The pipeline is not project scaffolding here — it is where several of the design
decisions above become mechanism instead of intent. Signing key handling, release
channels and the tested rollback path all live in CI or they live nowhere.

### 6.1 What OEM has today [verified]

| Repo | CI | Gap |
|---|---|---|
| EmonScripts | **none** — no `.github/workflows` | It is almost entirely shell, and unlinted |
| emoncms | `PHP.yml`, matrix PHP 8.1–8.4 | Runs `composer test`, which is **`lint` + `phpcs` only**. The `phpunit`, `phpunit-integration` and `phpunit-feature` suites exist in `composer.json` and are **never run in CI** |
| both | no dependabot; actions unpinned (`actions/checkout@v4`) | Supply-chain exposure, and no automated bumps |

### 6.2 Dependabot is a small win — Renovate is the right tool

HAOS's entire `.github/dependabot.yml` is **six lines covering `github-actions` and
nothing else.** [verified] That is not an oversight. A Buildroot OS project's real
dependency surface mostly has no dependabot ecosystem:

| Dependency | Where it lives | Dependabot? |
|---|---|---|
| Buildroot itself | git submodule on a release branch | Yes (`gitsubmodule`) |
| Buildroot package versions | version + sha256 in `.mk` / `.hash` files | **No ecosystem** |
| Kernel version | a string in the defconfig | **No ecosystem** |
| Container image digests | `@sha256:` in systemd units | Only inside a Dockerfile / compose file |
| ~20 OEM git repos | the release manifest (handover Phase 2) | **No ecosystem** |

**Recommendation: Renovate for anything that ships.** Its `customManagers` (regex
managers) can be taught to bump an arbitrary version-string-plus-hash pair in any file —
which is exactly the Buildroot `.mk`/`.hash` pattern, the kernel version in a defconfig,
and eventually the release manifest. Dependabot structurally cannot do this.

Either tool is fine for GitHub Actions. **Pin actions by SHA regardless** — HAOS pins
universally (`actions/checkout@3d3c42e5... # v7.0.1`); OEM repos do not.

### 6.3 Auto-merge: the trap is specific

The handover doc's sharpest criticism of the status quo (§3.4) is that *"the update agent
updates itself, unpinned... a bad commit reaches every updating device at once. No staged
rollout."*

**Auto-merging dependency PRs into a branch devices pull from recreates that exact
failure mode with a robot driving it** — faster, and with nobody having read the diff.

The rule that makes it safe maps onto the two-branch model OEM already uses
(handover §13.4):

- **Auto-merge into `master`/`dev` on green CI.** Fine, encouraged, keeps the queue from rotting.
- **Never auto-promote to `stable`.** Channel promotion is a tagged release that has
  passed the QEMU suite — a human action, or at minimum one with a soak period.

HAOS enforces this structurally rather than by policy: `build.yaml` validates the version
against the publish channel before deploying anything, and the release channel is a
build-time option (`hassio_channel_option`) rather than a branch pointer.

### 6.4 Testing — the update round trip is already automated [verified]

The most valuable single finding in this section. HAOS's `tests/` uses **labgrid** +
pytest, driving QEMU over the serial console. `tests/smoke_test/test_os_update.py` runs
the full round trip in CI on every build:

1. Boot, wait for containers up
2. `ha os update` — installs a bundle to the **other slot**, asserts it reaches pending state
3. Reboot, `expect("Booting \`Slot ")`, re-activate the shell driver through login
4. Assert the version changed and nothing is still pending
5. **`ha os boot-slot other`, reboot, assert the other slot boots and reports the other
   version** — the rollback path, tested automatically

`test.yaml` runs this against the `ova` qcow2 built by the same workflow, on a
`ubuntu-22.04` runner with KVM enabled, publishing JUnit reports.

**Why labgrid specifically:** the same tests can drive real hardware over a serial console
plus a network-controlled PDU. The QEMU suite written now becomes the emonPi-on-a-bench
suite later without a rewrite. Given §2's product matrix — four products, MCU firmware
upload, i2c LCD, RFM69 — hardware-in-the-loop is eventually unavoidable, and choosing a
framework that spans both costs nothing now.

### 6.5 Build cost and mechanics [verified]

HAOS runs **14 boards** as a matrix of full Buildroot builds on standard `ubuntu-22.04`
runners. What that takes:

- ccache restored per board (`haos-cc-${board.id}`), **saved only on `dev` branch pushes**
  — PRs read the cache but never write it, avoiding thrash and cache poisoning from
  untrusted PRs
- The runner is cleared to fit: Android NDK and CodeQL toolcache deleted, build moved to `/mnt`
- The build runs inside a `haos-builder` container image, itself cached in ghcr via buildx
  `cache-from` / `cache-to`

For a 4-board EmonOS matrix this is comfortable. Budget **45–90 min cold, 15–25 min warm,
per board.** [inferred]

Note the matrix comes from `.github/workflows/matrix.json`, one entry per board — **the
board abstraction from §5 is also what makes CI scale.** Same decision, second payoff.

### 6.6 Signing keys — copy this pattern exactly [verified]

`build.yaml` takes `RAUC_CERTIFICATE` / `RAUC_PRIVATE_KEY` from repository secrets. If
they are absent it generates a self-signed certificate and emits a `::warning::` that the
build is self-signed. It also explicitly **checks certificate validity** before building.

Both halves matter:

- The fallback means forks and PRs build and test fine without release keys
- The validity check guards a real production hazard: **an expired signing certificate
  means no device in the field can update**, and you find out at the worst possible moment

Design doc §13's gotcha *"keep `key.pem` out of the image and out of git"* is a statement
of intent. This is where it becomes mechanism.

### 6.7 What this changes about the plan

**1. The VM target moves from "should" to "prerequisite."** §5.1 argued it pays for
itself in testability. §6.4 makes it stronger: without an x86 target there is no CI test
target, and the A/B mechanism stays validated only by hand on a bench. Build it first.

**2. Release channels become CI structure, not documentation.** Handover Phase 2's
stable/beta/dev channels, the release manifest, and "devices move to a release, not to
whatever is on stable right now" are enforced by the build/publish pipeline or they are
enforced nowhere.

**3. Artifact hosting is on the critical path earlier than expected.** HAOS publishes dev
builds to S3 (`os-artifacts.home-assistant.io`) and stable to GitHub releases, with a
static `version.home-assistant.io/stable.json` devices poll. Design doc §12 lists this
OTA server as future work — but the test suite fetches from it, so it is needed sooner.

### 6.8 Cheap wins available now

Independent of every other decision in this document:

- **Run the phpunit suites in emoncms CI** — they exist and are skipped (§6.1)
- SHA-pin actions across OEM repos; add dependabot for `github-actions`
- `shellcheck` on EmonScripts — it is ~all shell and has zero CI
- `hadolint` on emoncms-docker
- Buildroot's own `buildroot/utils/check-package` on br2-external packages, once they exist

---

## 7. Updating the application layer

The OS mechanism (§4, §5) covers none of this. Containers live on the data partition;
RAUC never touches them. That separation is deliberate — it is the design doc's §0
premise — and it means **two independent rollback axes**, each needing its own health
check and its own recovery path. Most app rollbacks need no reboot; most OS rollbacks
need no app change.

### 7.1 What a release should be

`emoncms-docker` @ `573c00a` pins nothing: `openenergymonitor/emoncms:latest`,
`mariadb:11.0`, `redis:7.0`, `eclipse-mosquitto:2.0` — all floating tags. [verified]
Design doc §9.1 is right that `:latest` in an appliance means two units flashed a week
apart run different software with no record of why.

The release unit should be a **signed manifest**: release ID, the digest of each image,
and a **minimum-OS-version** field. That is the app-layer equivalent of a RAUC bundle,
and it makes rollback expressible as "re-apply manifest N−1".

### 7.2 The update sequence

1. Fetch and verify the manifest — same PKI as the RAUC bundles
2. **`docker pull` every digest before stopping anything.** A failed pull then costs zero downtime
3. Take the pre-update snapshot (§7.4)
4. Stop cleanly in dependency order — web, workers, db last, with a generous `TimeoutStopSec`
   (design doc §11: MariaDB corrupts if it loses power mid-write)
5. Start the new set
6. Health check
7. On failure: re-apply manifest N−1, restore the snapshot, restart, re-verify
8. On success: record the release good; prune old images after a retention window

The health check must test more than "container is running". The handover doc's Phase 1
list is right — apache responding, `feed/list.json` returning valid JSON, mysql reachable,
feedwriter advancing — **plus inputs still arriving from emonhub.** A stack that serves
pages but has silently stopped ingesting is the failure that costs a user weeks of data
before anyone notices.

### 7.3 Rollback has four layers

**Layer 1 — the images.** Trivial. Previous digests. Free.

**Layer 2 — emoncms schema. Much better than expected.** [verified]

`Lib/dbschemasetup.php` is a *declarative converge-forward* mechanism. `db_schema_setup()`
walks the desired schema and emits only `CREATE TABLE`, `ALTER TABLE … ADD`,
`ALTER TABLE … MODIFY` and `CREATE INDEX`. **It never emits `DROP COLUMN`, `DROP TABLE`
or `DROP INDEX`.**

So a newer release adds columns; an older image run against that database iterates its own
subset schema, finds everything it wants already present, and leaves the extra columns
alone. Old code does not select them. **Image rollback is safe for the additive case,
which is the overwhelming majority of releases.** This is a genuinely good property and
worth not breaking.

Three caveats:

- `dbschemasetup.php:210-223` emits `MODIFY` on a type diff. Widen `int`→`bigint`, roll
  back, and you get a narrowing `MODIFY` that **can truncate**. Any release changing a
  column type is not safely rollback-able without a data restore.
- Operations are applied in a plain loop that breaks on first error
  (`dbschemasetup.php:274-284`), and DDL is not transactional in MariaDB. An interrupted
  migration leaves a **partially-migrated schema with no record of how far it got.**
- `index.php:92-96` runs the convergence **on ordinary web requests** when `dbtest` is
  true. Migration is not a discrete step you control — it happens on first hit. For an
  appliance, turn that off in production and have the updater run it explicitly, so it is
  ordered, logged, and can fail the health check.

**Layer 3 — MariaDB engine format. The real hazard, and it is currently configured to
bite.** [verified]

`docker-compose.yml` sets `MARIADB_AUTO_UPGRADE=1` on a floating `mariadb:11.0`. Pull a
newer 11.0.x, it runs `mariadb-upgrade` against the datadir on start, and reverting the
image can leave a datadir the older engine will not open. This is handover §8 risk 1 and
§12 item 2 — **now confirmed as configured in, not hypothetical.**

Mitigations, in order:

- Pin MariaDB by digest, and treat an engine bump as **its own release**, never bundled
  with an app change. Two unrelated risks; do not correlate them.
- Keep auto-upgrade only on a release that *is* a DB upgrade, gated behind a mandatory
  pre-upgrade dump.
- Accept that **DB engine upgrades are forward-only.** The rollback path is
  restore-from-dump, not image revert. State it rather than discover it.

Good news: `emoncms-docker` already runs **MariaDB as a separate container with its own
volume**. That is the right shape — it decouples the frequent, cheap, safe emoncms
rollback from the rare, expensive, restore-based DB rollback. It is also a concrete
argument against the single-container `alexjunk/emoncms` option in handover §8, which
would fuse the two.

**Layer 4 — feed data.** PHPFina/PHPTimeseries are append-only flat files with a stable
format; rollback does not touch them. But a rollback that restores a DB dump from before
the update **loses feed metadata created in that window**, orphaning the `.dat` files on
disk. Not fatal, but the reconciliation behaviour needs defining.

### 7.4 The pre-update snapshot

Handover Phase 1 already proposes this. In the container world it becomes: a
`mariadb-dump` of the emoncms database (small — metadata, not timeseries), the manifest
currently in force, and `settings.ini` / `emonhub.conf`. **Not** the feed files — too big,
and unaffected.

That is what makes layer 2's `MODIFY` case and all of layer 3 recoverable. Automatic and
pre-update, not a user action; seconds and a few MB. Keep N−1 and N−2.

### 7.5 The cross-layer trap

**An OS rollback does not roll back the application layer.** RAUC swaps the rootfs;
`/mnt/data` is untouched, so containers and database stay where the app update left them.
Roll OS 5 back to 4 and you are running OS 4 with app release 12 — a combination nobody
tested.

That is the price of decoupling, and the fix is cheap: the **minimum-OS-version field in
the manifest** (§7.1), checked at app-update time. Without it the two mechanisms can
construct states neither was tested in.

### 7.6 Testing it

Extends the §6.4 QEMU suite naturally, and none of it needs Pi hardware:

- App update round trip: manifest N → N+1 → health check → assert
- **Forced rollback:** publish a manifest with a deliberately broken emoncms digest;
  assert auto-revert to N and a working stack
- **The schema case:** migrate forward, roll the image back, assert emoncms still serves
  and feeds still write. This is the test that catches the `MODIFY` hazard
- DB engine bump plus restore-from-dump, as a separate, explicitly forward-only test

---

## 8. The supervisor: adopt or build

### 8.1 What the HA Supervisor actually is [verified]

HAOS is four layers, not two:

| Layer | What | Where |
|---|---|---|
| Host services | systemd, Docker, **RAUC as a D-Bus service**, NetworkManager | rootfs |
| **os-agent** | small Go daemon, Apache 2.0, exposes `io.hass.os` on system D-Bus — datadisk, system, apparmor, cgroup, boards, UDisks2 | rootfs |
| Bootstrap | `/usr/sbin/haos-supervisor`, ~130 lines of shell | rootfs |
| **Supervisor** | privileged Python container, self-updating | data partition |

`haos-supervisor.service` declares `Requires=docker.service rauc.service dbus.socket` —
RAUC runs on the host and Supervisor drives OS updates over D-Bus. The launcher creates
the container `--privileged --security-opt apparmor="hassio-supervisor"` with
`docker.sock`, `containerd.sock`, the journal and journal-gatewayd sockets, `/run/dbus`
(ro), `/run/udev` (ro) and `/etc/machine-id`.

**os-agent exists because Supervisor runs in a container.** It is the D-Bus bridge that
lets a container perform privileged host operations.

### 8.2 Adopting it wholesale does not work

It is not a generic container supervisor — it is the Home Assistant application platform.
Its data model is Add-ons + Core + Plugins; the `hassio` Buildroot package preloads six
containers (`supervisor dns audio cli multicast observer core`); it polls
`version.home-assistant.io`; and it carries the add-on store, HA's auth and ingress,
HA-format backups and the `ha` CLI.

Making emoncms an add-on means shipping HA Core beside it on an energy monitor. Apache 2.0
means forking is legally fine, but that means owning a fork of a large, actively-developed
Python codebase whose direction is set entirely by HA's needs.

### 8.3 What is genuinely reusable

**1. The bootstrap script pattern — the best thing here.** `/usr/sbin/haos-supervisor`
contains three ideas worth taking outright:

- **A startup marker for corruption self-healing.** If the marker from the previous run is
  still present, assume the image or container is corrupt: force-remove the container,
  delete *all* images matching that reference, pull fresh.
- **Version resolution with fallback.** Read the pinned version from `updater.json` on the
  data partition; if absent, fetch the channel JSON from the version server.
- **Delete the pin when a pull fails**, so a bad pinned release cannot wedge the device
  permanently. The comment is explicit: *"If the config version is broken, this creates a
  way back (e.g., bad release)."*

**2. The mount list is a free requirements document** for what a container-based
supervisor needs, and therefore what is being granted.

**3. os-agent** — as a template, or possibly a small fork. But see §8.4.

**4. RAUC's D-Bus API** is not HAOS at all — it is RAUC upstream
(`de.pengutronix.rauc.Installer`), used directly either way.

### 8.4 The decision that sizes the work: host daemon or privileged container

**HAOS's shape** — supervisor in a privileged container — buys one thing: the supervisor
updates independently of the OS, on its own cadence. The cost is os-agent, an AppArmor
profile, the bootstrap script, the corruption-recovery logic, and a privileged container
holding the Docker socket.

**A host-native daemon** — a systemd service in the rootfs, updated by the RAUC bundle
like everything else — **makes os-agent unnecessary.** It calls RAUC, NetworkManager,
systemd and Docker directly: no D-Bus bridge, no privileged container, no bootstrap
script, no self-update logic. It also survives a broken container layer, which matters for
handover §11.4 — network provisioning must work when emoncms is down.

The trade is that supervisor updates then require an OS update. For a single-application
appliance on a versioned-release model that is arguably correct: one fewer independently
versioned thing, which is the whole thrust of handover Phase 2.

**Recommendation: host-native.** HAOS's shape is driven by needing to update Supervisor
faster than the OS because the add-on ecosystem moves fast. EmonOS has no add-on
ecosystem.

### 8.5 balenaSupervisor — evaluated, does not fit [verified]

Worth stating in full because it looks like the closest fit and turns out not to be.

**What it is.** balena's on-device agent: Node.js, **Apache 2.0**. Unlike HA's, it *is* a
general-purpose multi-container release manager — it uses balenaEngine's (a Docker fork)
remote API to install, start, stop and monitor OCI containers, and it exposes a local HTTP
API on port 48484: `POST /v1/update`, `GET /v2/applications/state`,
`POST /v2/applications/:appId/restart-service` (and stop/start), `POST .../purge`,
`POST /v1/reboot`, `POST /v1/shutdown`, `GET /v1/healthy`, `GET /v2/state/status`.

**Two disqualifying problems:**

1. **It has no device-side rollback on a failed container update.** The Supervisor API
   documents no rollback mechanism and no automated failure recovery beyond force/cancel
   on the update endpoint. Balena's recovery model is *fleet-side*: mark the release
   invalid in the backend, or pin devices to a known-good release. Both are backend
   operations. **Device-side automatic rollback on a bad release is the single capability
   we most need, and it is the one that is missing.**

2. **It requires a fleet-management backend.** The Supervisor's whole model is polling a
   target state from balenaCloud, or from **openBalena** self-hosted. openBalena is
   **AGPL v3** (not Apache), provides device registration, a VPN, image distribution and
   an API, and explicitly lacks delta updates, a dashboard, multi-user support and remote
   OS updates. Running a registration server and VPN **directly contradicts the recorded
   decision in handover §13.6: "No central fleet-management server."**

**What is worth borrowing anyway:** balenaOS's *OS-level* rollback framework is good and
independently instructive — two mechanisms, `rollback-health` (health-check based) and
`rollback-altboot` (unbootable detection), with a breadcrumb file in the state partition
arming the health service on next boot. That is the same shape as §6.4's RAUC health check
and worth reading before writing ours. Note also that balenaOS does not support manual
downgrade once a host OS update succeeds.

### 8.6 The other option worth pricing: Podman auto-update

The minimal path. Podman can update containers from their registry on a systemd timer and
revert to the previous image when the new unit fails to start, with health checks declared
in the unit — image-level update and rollback with **no supervisor at all**.

It would not cover firmware upload, network provisioning or the DB dump, but it might
cover the container update path well enough that the agent shrinks to only the
emoncms-specific parts. The obstacle is that `emoncms-docker` is Docker, not Podman.
[inferred — not tested]

### 8.7 What must be built regardless

None of this exists in any upstream:

- Release manifest handling and digest pinning (§7.1)
- **emoncms-specific health check** — `feed/list.json` returning, feedwriter advancing,
  inputs still arriving from emonhub (§7.2)
- Pre-update `mariadb-dump` and the rollback sequence, including the engine special case (§7.3, §7.4)
- **MCU firmware upload** — `bossac` for emonPi3, `avrdude` + autoreset for 1/2 (§2.2)
- emonhub config bridging (the inotify dodge from handover §10.5)
- A network API for the emoncms UI to call instead of the five sudoers entries (handover §11.3)
- The `service-runner` client side

That list is most of the work, and it is the same under every option. **The supervisor
question is really "how much scaffolding comes free around a core we are writing anyway"**
— and the answer is: not much, but the good ideas are cheap to copy.

---

## 9. Contradictions between the existing docs

To be resolved rather than left for someone to trip over.

| # | Conflict | Position taken here |
|---|---|---|
| 1 | **Sequencing.** Handover puts A/B at Phase 4, "only if 1–3 prove insufficient"; the design doc starts there | Handover Phase 1 work is independent and benefits deployed devices. Do it in parallel, not instead |
| 2 | **Supervisor.** Design doc §12: "you don't [need one], you have systemd units". Handover §10.5: required, and the largest unscoped item | Handover is right, and §2.2 strengthens it — MCU firmware upload is per-product, needs UART + GPIO, and must work on a read-only root |
| 3 | **Container image.** Handover evaluates `alexjunk/emoncms` (single s6 container); design doc assumes `emoncms/emoncms-docker` multi-container | Leaning multi-container. §7.3 gives a concrete reason: a separate MariaDB container decouples the cheap emoncms rollback from the expensive DB-engine one; a single container fuses them |
| 4 | **Hardware scope.** Design doc's tryboot approach excludes Pi 3 / Zero 2 W | Blocked on §10.1. U-Boot (§4.1) removes the constraint entirely |
| 5 | **RedisBuffer.** Design doc §11 recommends "consider redis buffering for feed writes". SD audit §5 recommends *removing* RedisBuffer — it buys ~2×, one sysctl buys ~20× | SD audit is right and better evidenced. Do not carry RedisBuffer into a new design |
| 6 | **Mount options.** Design doc mounts `/mnt/data` ext4 `noatime`. SD audit warns ext4 without `commit=600` is a ~6× regression vs current ext2 behaviour | Use `noatime,commit=600,errors=remount-ro`. The SD audit's warning is explicit: *"`commit=600` is not optional"* |

---

## 10. Open questions

Ordered by how much they block. The first is new and now dominant.

**10.1 Which Raspberry Pi model ships in each product?** [unresolved]
Not stated in the emonPi3 overview, the emonPi2 hardware page, the emonBase docs or the
shop listing — all say "Raspberry Pi" without a model. This decides the bootloader
(tryboot needs Pi 4B/CM4+), the `RPi.GPIO`→`rpi-lgpio` migration scope, and whether the
container RAM analysis holds. **Get it from the BOM.**

**10.2 How is the MariaDB engine upgrade handled?** (§7.3)
Narrowed, not open. `MARIADB_AUTO_UPGRADE=1` on a floating tag means engine upgrades are
**forward-only** and image revert can fail. The remaining decision is whether to accept
restore-from-dump as the only rollback path for engine bumps, and how to gate them into
their own release class. *emoncms schema* rollback is no longer a risk — see §7.3 layer 2.

**10.3 Supervisor: host-native daemon or privileged container?** (§8.4)
Narrowed. Adopting HA's Supervisor is out (§8.2) and balenaSupervisor is out (§8.5). The
open part is host-native vs containerised, and whether Podman auto-update (§8.6) shrinks
the core enough to matter. Answerable in days, not a standing unknown.

**10.4 Where do release artifacts live?** (§6.7)
GitHub releases alone, or object storage plus a signed version manifest? Determines what
the update client polls, and the test suite depends on it.

**10.5 Minimum SD card size**, and whether the data partition should default to USB/NVMe
where available (SD audit §4).

**10.6 Build our own container images or depend on `alexjunk/*`?** Supply chain.

**10.7 Where does the network provisioning UI live** once emoncms is containerised?
(handover §11.3)

**10.8 Desktop target: UEFI-only, or also legacy BIOS?** UEFI-only is simplest and is what
HAOS does.

**10.9 Self-hosted CI runners?** Fine at 4 boards on GitHub-hosted runners; revisit if the
matrix grows or build times bite (§6.5).

**10.10 What fraction of block-layer writes are actually feed data?** (SD audit §6.4)
"Three numbers, one afternoon" — worth doing before optimising anything in that area.

---

## 11. Recommended additions to the plan

Beyond what the handover doc already lists.

**Do early, cheap:**

- Build the **board/variant abstraction** from the first commit — one `meta` file per
  target, no Pi-isms outside `board/`. This is what makes §5 and §6.5 free.
- Stand up the **x86-64 or aarch64 VM target first**, before the Pi target, and wire the
  A/B round trip into CI (§5.1, §6.4).
- Adopt **labgrid + pytest** as the test framework from the start, so the QEMU suite
  becomes the hardware suite later without a rewrite (§6.4).
- Package `bossac`, `avrdude` and the autoreset wrapper as Buildroot packages (§2.2).
- Move product detection to **first boot, persisted to the data partition** (§3).
- **Pin every container by digest and define the release manifest format** — including
  the minimum-OS-version field, without which OS and app rollback can construct untested
  combinations (§7.1, §7.5).
- **Split MariaDB engine bumps into their own release class**, gated behind a mandatory
  pre-upgrade dump (§7.3).
- Turn off request-time schema convergence (`dbtest`) in production and have the updater
  run migrations explicitly, so they are ordered, logged and can fail the health check (§7.3).
- Set up **RAUC signing secrets with a self-signed fallback and a certificate expiry
  check** before the first bundle is built (§6.6).

**Correct in the existing docs:**

- Design doc §1.2/§1.3/§6.1/§12 per §4 above.
- Design doc §11's RedisBuffer recommendation per §9.5 above.
- Design doc §8.1 mount options per §9.6 above.
- `install/main.sh:92-101` — tests for python 3.11, removes 3.13 (handover §3).

**Housekeeping:** [emon-os-initial-design.md](emon-os-initial-design.md) contains the same document twice — a copy
truncated mid-sentence at §10.1 (line 736), then a complete copy. Only lines 737–1652 are
current.

---

## 12. Decisions carried forward

From handover §13.6, unchanged:

- In-place Debian major upgrades are off the table
- Containers are a Phase 3 destination, not a substitute for Phase 1
- No central fleet-management server — opt-in, aggregate, publicly published analytics only
- Phase 1 safety work proceeds regardless of the container decision

Added here:

- **Multi-board support is in scope from day one.** Reverses design doc §12 (§1, §5, §6.5)
- **The desktop/VM target is a first-class build target**, justified by CI testability
  rather than by end users (§5.1, §6.4)
- **RedisBuffer is not carried into the new design** (§9.5)
- **Auto-merge never promotes to `stable`.** Dependency PRs may auto-merge to the
  development branch on green CI; shipping is a tagged release that passed the QEMU
  suite (§6.3)
- **MariaDB stays a separate container** from emoncms, so the two rollback classes stay
  decoupled (§7.3)
- **DB engine upgrades are forward-only**; the rollback path is restore-from-dump, not
  image revert (§7.3)
- **balenaSupervisor is not adopted.** No device-side rollback on a failed container
  update, and it requires a fleet-management backend, contradicting handover §13.6 (§8.5)
- **HA Supervisor is not adopted or forked**; its bootstrap ideas are copied instead (§8.2, §8.3)

---

## References

- HAOS source read for §4, §5 and §6 — `github.com/home-assistant/operating-system` @ `42ea0f607`
- RAUC GRUB backend — `rauc.readthedocs.io/en/latest/integration.html`
- labgrid (embedded test framework, QEMU and real hardware) — `labgrid.readthedocs.io`
- HA os-agent — `github.com/home-assistant/os-agent` (Apache 2.0)
- balenaSupervisor — `github.com/balena-os/balena-supervisor` (Apache 2.0)
- balena Supervisor API — `docs.balena.io/reference/supervisor/supervisor-api`
- balenaOS rollback framework — `docs.balena.io/reference/os/updates/rollbacks`
- openBalena — `github.com/balena-io/open-balena` (AGPL v3)
- Renovate custom regex managers — `docs.renovatebot.com/modules/manager/regex/`
- emon32 firmware (emonPi3 / emonTx6) — `github.com/openenergymonitor/emon32-fw`
- BOSSA (ATSAMD21 flashing) — `github.com/shumatech/BOSSA`
- rpi-lgpio (`RPi.GPIO` replacement) — `rpi-lgpio.readthedocs.io`
- Product docs — [links.md](links.md)

# EmonOS — Product Specification

**Version:** 0.2 — draft for review
**Date:** 2026-08-31
**Status:** Normative where marked. Open decisions in §12.

This document is **self-contained**. Every requirement's rationale is stated here, and
every factual claim is cited to primary source — a repository, a file, a line range —
rather than to a companion document. Appendix A holds the evidence.

---

## 0. How to read this document

Requirements use RFC 2119 keywords — **MUST**, **MUST NOT**, **SHOULD**, **MAY** — and
carry stable IDs (`OS-1`, `APP-4`, …). **IDs are frozen from v0.2 onward**: new
requirements take the next free number for their prefix, and a withdrawn requirement's ID
is retired rather than reused. (v0.1 was a review draft and was renumbered once, in v0.2,
to seat the additions in document order. That will not happen again.)

**Test cases cite requirement IDs.** A requirement with no test is not implemented, and
the conformance list in §11 is the checklist.

Anything marked **[OPEN-n]** is an unresolved decision, described in full in §12.
Requirements are written to be independent of an open decision's outcome wherever
possible; where that is not possible, the dependency is marked.

Claims of fact carry an evidence reference **[A-n]** pointing into Appendix A, which names
the repository and revision it was read from. Where this document says something is
*verified*, that means someone read the source, not that it was recalled.

---

## 1. What EmonOS is

EmonOS is the operating system for OpenEnergyMonitor appliances: a minimal, immutable
Linux host whose sole job is to run the emoncms application stack reliably and to update
itself without ever leaving a user with a device they cannot recover.

It replaces emonSD.

### 1.1 The problem being solved

emonSD is a Raspberry Pi OS image with an application installed on top of it. It has four
compounding faults:

**No OS security patching.** The image build runs `apt-get update/upgrade` once, at build
time. The in-field update path never runs `upgrade` at all **[A-1]**. A device flashed
from an image built in early 2024 is still running that vintage of apache, mariadb,
openssh, php and kernel — typically on a LAN, often with documented default credentials.

**No rollback.** A failed update leaves the user with a broken device recoverable only by
physically removing the SD card. This is why OS updates are avoided, which is why the
first fault persists.

**No reproducibility.** The application layer is a rolling `git pull` across roughly twenty
independent repositories. Two devices reporting the same image version can differ
arbitrarily: repositories pulled at different times, repositories with local modifications
silently skipped, and PHP extensions compiled from whatever upstream HEAD was current on
build day.

**An unpinned self-updating agent.** The update script pulls its own repository and
immediately executes what it pulled. A bad commit reaches every updating device at once,
with no staged rollout.

### 1.2 The defining property

> **A user must never be left with an unrecoverable device as a result of an update.**

Every requirement in this document exists to serve that sentence. Where a trade-off
arises, this is the tie-breaker.

### 1.3 The core idea

Three changes, taken together:

1. **The OS becomes an image, not an installation.** Two redundant slots hold a read-only
   compressed root filesystem. An update writes the inactive slot and reboots into it. If
   it does not come up healthy, the device returns to the slot that was working.
2. **The application becomes a set of pinned container images**, described by a signed
   manifest, updated and rolled back independently of the OS.
3. **All mutable state lives on a third partition** that neither mechanism writes.

---

## 2. Goals and non-goals

### 2.1 Goals

| | Goal |
|---|---|
| G1 | OS updates are atomic, signed, verified and automatically rolled back on failure |
| G2 | OS security patching happens routinely, without a maintainer weighing each one |
| G3 | Application updates are versioned, reproducible and independently rollback-able |
| G4 | A given release ID describes a device's software exactly — no per-device drift |
| G5 | One codebase serves every OEM product and a desktop/VM target |
| G6 | The full update and rollback path is exercised automatically in CI |
| G7 | User data survives every update, rollback and re-flash |

### 2.2 Non-goals

Recorded so they are not re-litigated. Each may be reopened with new evidence, but not by
default.

| | Non-goal | Why |
|---|---|---|
| N1 | **No central fleet-management server** | Many users self-host specifically to avoid anything phoning home. A management server would likely be more contentious than the problem it solves |
| N2 | **No remote push to devices.** Devices poll; the vendor never initiates | Follows from N1. It also means a compromised vendor account cannot reach devices |
| N3 | **No add-on ecosystem or third-party app store** | EmonOS runs one application stack. This is the single largest simplification against comparable systems, and it is what makes a small supervisor viable (§8) |
| N4 | **No in-place major-version OS upgrades** | Superseded by whole-image A/B. A new image plus a tested migration path is the answer to platform moves |
| N5 | **Not a general-purpose Linux host.** Users do not install packages onto EmonOS | The root filesystem is read-only by design (ARCH-3) |
| N6 | **Not a drop-in emonSD replacement** preserving local customisation | Migration is export/import (§10), not in-place conversion. Preserving arbitrary local modification is incompatible with G4 |

---

## 3. Supported products

EmonOS is a **multi-target** product from the first release.

| Target | Class | Hardware profile |
|---|---|---|
| emonPi 3 | appliance | ATSAMD21 (emon32) MCU, RFM69, i2c LCD, buttons |
| emonPi 2 | appliance | ATmega328 MCU, RFM69, i2c LCD, buttons, one-wire |
| emonPi 1 | appliance | ATmega328 MCU, RFM69, i2c LCD, buttons, **no one-wire** |
| emonBase (RFM69-SPI) | base station | RFM69 over SPI + GPIO, no MCU, no display |
| emonBase (RFM69Pi) | base station | ATmega328 over serial, no display |
| `generic-x86-64` | desktop / VM | **empty hardware profile** |
| `generic-aarch64` | desktop / VM | **empty hardware profile** |

### 3.1 Why multi-target from day one

It is tempting to build for one board and generalise later. That is the wrong call here,
for three reasons.

**The products already differ below the application layer.** They use different
microcontrollers with different flashing tools, different radio paths, and different
device tree overlays. One difference is already actively harmful: the current image
enables a one-wire overlay that conflicts with the shutdown button on emonPi 1, and the
conflict is recorded only in a source comment **[A-2]**. An immutable image makes that
worse, because a user who flashes the wrong variant has no easy way to notice or correct
it.

**A second target is cheap if planned, expensive if retrofitted.** In a comparable system
built the same way, the entire delta between a Raspberry Pi target and a desktop target is
around 300 lines: a board metadata file, a defconfig, a bootloader configuration, a kernel
fragment and a hook script. The partition layout, the update mechanism, the slot
definitions and the whole userspace are shared verbatim **[A-7]**.

**The desktop target is the test target.** Without it there is nowhere to run the
conformance suite in §11 except a physical bench, and the rollback path stays unverified
in exactly the way §1.1 describes. This is the strongest argument of the three.

### 3.2 Requirements

**HW-1** &nbsp;Each target **MUST** be described by a board metadata file declaring
bootloader, kernel image name, partition table type, boot partition size, architecture and
hardware profile.

**HW-2** &nbsp;No target-specific logic **MUST** exist outside that target's board
directory.

**HW-3** &nbsp;The build **MUST** produce every target from one source tree and one command
per target.

**HW-4** &nbsp;A desktop/VM target **MUST** be a first-class supported build, not a
development convenience.

**HW-5** &nbsp;The set of targets built by CI **MUST** be derived from the board metadata,
not maintained separately.

**[OPEN-1]** &nbsp;Which Raspberry Pi model each appliance ships with is unconfirmed. This
determines the bootloader per target (§4.3) and is the most blocking unknown in this
document.

---

## 4. Architecture

### 4.1 Layers

```
┌──────────────────────────────────────────────────────────┐
│  Application containers   emoncms · emonhub · mariadb    │  ← app release manifest
│                           redis · mosquitto              │
├──────────────────────────────────────────────────────────┤
│  Supervisor               lifecycle · health · rollback  │  ← ships with the OS
│                           network · MCU firmware         │
├──────────────────────────────────────────────────────────┤
│  Host OS (read-only)      systemd · container runtime    │  ← signed bundle, A/B slots
│                           update agent · network · kernel│
├──────────────────────────────────────────────────────────┤
│  Data partition           feeds · database · config      │  ← never replaced
└──────────────────────────────────────────────────────────┘
```

**ARCH-1** &nbsp;The OS layer and the application layer **MUST** update through separate,
independent mechanisms.

**ARCH-2** &nbsp;The data partition **MUST NOT** be written by either update mechanism
except through the explicit snapshot and migration paths in §7.4 and §10.

**ARCH-3** &nbsp;The root filesystem **MUST** be mounted read-only, and **MUST** be a
compressed image written only by an update.

**ARCH-4** &nbsp;No application software **MUST** be present in the root filesystem. The
application ships as container images on the data partition.

> **Why ARCH-3 and ARCH-4 together.** A read-only root cannot drift, which is a stronger
> guarantee than A/B alone: a slot is either exactly what was installed or it is broken,
> with no third state. Keeping the application out of the root filesystem is what keeps a
> slot small enough for two copies to be affordable, and what allows the application to be
> updated without touching the OS.

### 4.2 Storage layout

**OS-1** &nbsp;The disk **MUST** carry two OS slots (kernel + root filesystem), a boot
partition, a persistent OS-configuration partition, and a data partition.

**OS-2** &nbsp;Boot slot and root filesystem slot **MUST** be versioned and installed
together, and **MUST NOT** be independently selectable.

**OS-3** &nbsp;Both slots **MUST** be sized for the largest anticipated future image, since
they cannot be resized in the field without repartitioning.

**OS-4** &nbsp;The data partition **MUST** be identified by label, not by device path, so
it can later be relocated to USB or NVMe without changing the OS image.

**OS-5** &nbsp;On first boot the data partition **MUST** expand to fill the available
device.

**OS-6** &nbsp;A single boot partition shared by both slots **SHOULD** be preferred over
paired per-slot boot partitions, since it is what allows one partition layout to work
across different bootloaders **[A-7]**.

**[OPEN-5]** &nbsp;Slot sizing, and therefore minimum media size, is unresolved.

### 4.3 Boot and slot selection

**OS-7** &nbsp;The bootloader **MUST** provide, per slot: a validity flag, a boot attempt
counter, and automatic fall-through to the other slot when attempts are exhausted.

**OS-8** &nbsp;A slot **MUST NOT** be marked good by the act of booting. It is marked good
only by the health check in §6.3.

**OS-9** &nbsp;A rescue path **MUST** exist that boots a slot to a diagnostic shell without
starting the application stack.

**OS-10** &nbsp;Where the bootloader cannot provide a real attempt counter, a hardware
watchdog **MUST** be enabled — it is then the only fallback for a slot that hangs after
boot but before the health check.

> **Why OS-10 exists.** Some Raspberry Pi bootloader mechanisms provide a one-shot "try
> this other slot" flag rather than a counter. That handles a slot that fails to boot, but
> not a slot that boots and then hangs before anything can mark it good. On such a target
> the watchdog is not a refinement; it is the recovery mechanism. Which targets this
> applies to depends on **[OPEN-1]**.

---

## 5. OS update

**OS-11** &nbsp;An OS update **MUST** be delivered as a single signed bundle covering
kernel, root filesystem and boot files together.

**OS-12** &nbsp;The device **MUST** verify the bundle signature against a keyring baked
into the running root filesystem, and **MUST** refuse to install an unverifiable bundle.

**OS-13** &nbsp;The bundle **MUST** be written to the inactive slot. The running slot
**MUST NOT** be modified.

**OS-14** &nbsp;An update interrupted at any point — including power loss mid-write —
**MUST** leave the device booting the previously running slot.

**OS-15** &nbsp;After a successful install the device **MUST** arm the new slot for one
attempt and **MUST NOT** mark it good until §6.3 passes.

**OS-16** &nbsp;The installer **MUST** copy the persistent board variant fragment (SUP-8)
into the newly written boot slot as part of the install.

**OS-17** &nbsp;Application containers **MUST** be stopped cleanly, in dependency order,
before a reboot into a new slot.

> **Why OS-16 is not an implementation detail.** Device tree overlays live in the boot
> partition, which is inside the slot and replaced wholesale by every update. Without this
> step, per-product hardware configuration is lost on the first update. See SUP-8.

---

## 6. Health checking and recovery

### 6.1 What "healthy" means

**HC-1** &nbsp;A health check **MUST** verify the application is serving its purpose, not
merely that the init system reached its target. At minimum:

| | Check |
|---|---|
| a | The emoncms web interface responds |
| b | `feed/list.json` returns valid JSON |
| c | The database is reachable |
| d | The feed writer is advancing |
| e | **Inputs are still arriving from emonhub** |

> **Why (e) is not optional.** A stack that serves pages but has silently stopped ingesting
> looks healthy to every simpler check. The user discovers it weeks later as a gap in their
> energy data, by which time the data is unrecoverable. This is the failure mode the whole
> health check exists to catch.

**HC-2** &nbsp;The health check **MUST** have a bounded timeout and **MUST** treat timeout
as failure.

**HC-3** &nbsp;Health check results **MUST** be recorded to persistent storage so a failure
is diagnosable after the rollback that follows it.

### 6.2 Recovery

**HC-4** &nbsp;On health check failure after an OS update, the device **MUST** reboot into
the previously running slot without user action.

**HC-5** &nbsp;On health check failure after an application update, the supervisor **MUST**
restore the previous release manifest and the pre-update snapshot without user action.

**HC-6** &nbsp;After any automatic rollback the device **MUST** surface, in the emoncms UI,
what was attempted, that it failed, and that it recovered.

**HC-7** &nbsp;The device **MUST NOT** automatically retry a release that has already
failed its health check on that device.

**HC-8** &nbsp;A device **MUST** be able to complete first-boot provisioning and network
configuration with no working application stack.

### 6.3 Commit

**HC-9** &nbsp;A new OS slot **MUST** be marked good only after the health check passes,
and that action **MUST** be what makes the slot the default for subsequent boots.

---

## 7. Application update

### 7.1 The release manifest

**APP-1** &nbsp;An application release **MUST** be described by a signed manifest carrying
a release ID, the **digest** of every container image, and a minimum OS version.

**APP-2** &nbsp;Container images **MUST** be referenced by digest. Floating tags such as
`:latest` **MUST NOT** appear in a shipped manifest.

**APP-3** &nbsp;The supervisor **MUST** refuse a manifest whose minimum OS version exceeds
the running OS version.

> **Why APP-2.** The current container stack pins nothing — every image is a floating tag
> **[A-6]**. Two units flashed a week apart then run different software with no record of
> why, which defeats G4 directly.
>
> **Why APP-3.** An OS rollback does not roll back the application layer: the root
> filesystem is swapped but the data partition is untouched, so containers stay where the
> application update left them. Rolling the OS back from release 5 to 4 while the
> application sits at release 12 produces a combination nobody tested. The minimum-OS
> field is the cheapest possible guard against that.

### 7.2 Update sequence

**APP-4** &nbsp;All images **MUST** be pulled and verified before any running container is
stopped. A failed pull **MUST** abort the update with no downtime.

**APP-5** &nbsp;A pre-update snapshot (§7.4) **MUST** be taken before any container is
stopped.

**APP-6** &nbsp;Containers **MUST** be stopped in dependency order — web, then workers,
then database last — with a stop timeout generous enough for a clean database shutdown.

**APP-7** &nbsp;After the new set starts, the §6.1 health check **MUST** run before the
release is recorded as good.

**APP-8** &nbsp;Images belonging to release N−1 **MUST** be retained until release N has
passed its health check, and **SHOULD** be retained for at least one further release.

### 7.3 Database handling

This is the one area where rollback can genuinely fail, so the requirements are specific.

**APP-9** &nbsp;The database **MUST** run as a container separate from emoncms, so that an
emoncms rollback does not imply a database engine rollback.

**APP-10** &nbsp;A database engine version change **MUST** be shipped as its own release,
never bundled with an application change.

**APP-11** &nbsp;A release that changes the database engine version **MUST** take a full
database dump before starting the new engine, and **MUST** be marked in the manifest as
**not rollback-able by image revert**.

**APP-12** &nbsp;Schema migration **MUST** be run explicitly by the supervisor as an
ordered, logged step whose failure fails the health check. Request-time schema convergence
**MUST** be disabled in production.

**APP-13** &nbsp;A release changing a column *type* **MUST** be marked in the manifest as
requiring snapshot restore to roll back. Additive schema changes need no such marking.

> **Why these four, and why not more.** emoncms's schema mechanism is *declarative and
> additive-only*: it emits `CREATE TABLE`, `ADD`, `MODIFY` and `CREATE INDEX`, and never
> drops a column, table or index **[A-4]**. So a newer release adds columns, and an older
> image run against that database finds everything it needs and ignores the extras.
> **Ordinary application rollback is therefore safe**, which is a better position than it
> first appears.
>
> Three exceptions remain, and APP-11 to APP-13 address them:
> - The database *engine* auto-upgrades its on-disk format on start, and the current
>   configuration has this enabled on a floating tag **[A-6]**. Reverting the image can
>   then leave a datadir the older engine will not open. Engine upgrades are
>   **forward-only**; the rollback path is restore-from-dump.
> - A column *type* change is emitted as `MODIFY`, so rolling back issues the narrowing
>   `MODIFY` in reverse, which can truncate data.
> - Migrations run in a plain loop that stops at the first error, and DDL is not
>   transactional, so an interrupted migration leaves a partially-migrated schema with no
>   record of how far it got **[A-4]**. Running it as an explicit, logged step whose
>   failure is visible is the mitigation.
>
> APP-12's second sentence exists because the schema currently converges on ordinary web
> requests **[A-5]** — migration is not a discrete step anyone controls. For an appliance
> it must be.

### 7.4 Snapshots

**APP-14** &nbsp;A pre-update snapshot **MUST** contain: a database dump, the manifest in
force, and application and emonhub configuration.

**APP-15** &nbsp;A snapshot **MUST NOT** contain feed data files. They are append-only,
unaffected by rollback, and too large.

**APP-16** &nbsp;At least the two most recent snapshots **MUST** be retained.

**APP-17** &nbsp;Restoring a snapshot **MUST** define its behaviour for feed data files
created after the snapshot was taken, and **MUST NOT** delete them silently.

> **Why APP-17.** Feed *data* is unaffected by rollback, but feed *metadata* lives in the
> database. Restoring a dump from before the update therefore orphans any data files whose
> metadata was created in that window. Not fatal, but it must be defined behaviour rather
> than a surprise.

---

## 8. The supervisor

A privileged agent owning everything the application cannot do for itself.

### 8.1 Why this is new software

Two existing supervisors were evaluated and neither fits **[A-10]**:

- The **Home Assistant Supervisor** is not a generic container supervisor; it is that
  project's application platform, with an add-on store, its own authentication and
  ingress, and a fixed set of companion containers. Adopting it means shipping that
  project's application stack alongside emoncms. Forking it means owning a large,
  actively-developed codebase whose direction is set by someone else's product.
- **balenaSupervisor** *is* a general-purpose multi-container release manager, but it has
  **no device-side rollback on a failed container update** — recovery is a backend
  operation, invalidating a release or pinning devices from a server — and it requires a
  fleet-management backend, which N1 rules out.

What both do provide is a design to learn from, particularly around self-healing
bootstrap (SUP-6).

### 8.2 Responsibilities

**SUP-1** &nbsp;The supervisor **MUST** own: container lifecycle, application update and
rollback, health checking, OS update triggering, network configuration, MCU firmware
upload, and snapshot creation and restore.

**SUP-2** &nbsp;The supervisor **MUST** expose an authenticated local API, and emoncms
**MUST** use it rather than shelling out.

**SUP-3** &nbsp;The emoncms container **MUST NOT** have access to the container runtime
socket, and **MUST NOT** hold `sudo` rights on the host.

**SUP-4** &nbsp;The supervisor **MUST** remain functional when the application stack is
unhealthy or absent.

**SUP-5** &nbsp;The supervisor **MUST NOT** be managed by itself.

**SUP-6** &nbsp;If a supervisor component is delivered as a container image, its launcher
**MUST** detect a corrupt image or container and re-pull, and **MUST** discard a pinned
version that cannot be fetched, so a bad pin cannot permanently wedge the device.

> **Why SUP-3.** Today the emoncms web application holds passwordless `sudo` on five
> network scripts and drives host networking through them. Granting a web application the
> container runtime socket in a containerised design would be strictly worse — it is
> equivalent to root on the host. The API boundary in SUP-2 is what removes both.
>
> **Why SUP-4.** The access point and captive portal are how a user gets a headless device
> onto their network in the first place, and ideally they still work when the application
> is broken, so a user can reach a failed device rather than being locked out. Placing
> provisioning inside the application container creates a circular dependency at exactly
> the moment recovery matters most.

**[OPEN-3]** &nbsp;Host-native daemon or privileged container. SUP-6 applies only if the
containerised option is chosen.

### 8.3 Hardware and board configuration

**SUP-7** &nbsp;Product identity **MUST** be established at first boot and persisted to the
data partition. It **MUST NOT** be re-derived during an update.

**SUP-8** &nbsp;Board-specific boot configuration — device tree overlays in particular —
**MUST** be held as a persistent fragment on the data partition and included by the boot
configuration, not baked into the OS image.

**SUP-9** &nbsp;Behaviour **MUST** be defined for media moved between products, and the
system **MUST NOT** silently apply the wrong device tree.

**SUP-10** &nbsp;MCU firmware upload tooling **MUST** be present in the OS image as built
packages. Nothing **MUST** be compiled on the device at update time.

> **Why SUP-8.** Device tree overlays cannot be set from inside a container, and in an A/B
> design the file that holds them is replaced by every update. The options are one image
> per product (a release matrix that grows badly, and a card moved between products gets a
> subtly wrong device tree), all overlays present with runtime selection (does not work —
> overlay conflicts are resolved at boot, and one such conflict already exists **[A-2]**),
> or one image plus a persistent fragment. Only the third scales.
>
> **Why SUP-10.** The current emonPi 3 firmware update clones a flashing tool from GitHub
> and compiles it on the device at update time **[A-3]**. A read-only root filesystem has
> neither a toolchain nor anywhere to write, so this must become a built package. The same
> applies to the tooling for the older ATmega-based products.

### 8.4 Network

**SUP-11** &nbsp;The supervisor **MUST** provide WiFi client configuration, access-point
fallback and captive portal, exposed through its API.

**SUP-12** &nbsp;Network provisioning **MUST** work on a device whose application stack has
never started.

**[OPEN-7]** &nbsp;Where the provisioning user interface lives.

---

## 9. Releases, channels and security

### 9.1 Versioning and channels

**REL-1** &nbsp;A device **MUST** report a single OS release ID and a single application
release ID, and those two **MUST** fully determine its software.

**REL-2** &nbsp;Devices **MUST** move between releases, never to "whatever is current".

**REL-3** &nbsp;Three channels **MUST** exist — stable, beta and development — and the
channel **MUST** be user-selectable.

**REL-4** &nbsp;Devices **MUST** poll for updates. The vendor **MUST NOT** be able to
initiate an update on a device.

**REL-5** &nbsp;Update checking and installation **MUST** be separately controllable, and
the user **MUST** be able to decline or defer an update.

> **Why channels rather than a rollout system.** N1 rules out a server that knows which
> devices exist, so staged rollout by percentage is not available. User-selected channels
> achieve the same end by self-selection: testers absorb breakage first, and a bad release
> is caught before it reaches stable. It requires no infrastructure beyond a static file
> per channel.

**[OPEN-4]** &nbsp;Where release artefacts are hosted.

### 9.2 Signing

**SEC-1** &nbsp;OS bundles and application manifests **MUST** be signed, and devices
**MUST** reject unverifiable artefacts.

**SEC-2** &nbsp;Signing keys **MUST NOT** be present in any shipped image or in version
control.

**SEC-3** &nbsp;The build **MUST** fall back to a self-signed certificate when release keys
are unavailable, and **MUST** mark such builds visibly as unsigned-for-release.

**SEC-4** &nbsp;The build **MUST** fail or warn on an expired or near-expiry signing
certificate.

> **Why SEC-3 and SEC-4.** SEC-3 lets forks and pull requests build and be tested without
> access to release keys — without it, CI cannot test what it builds. SEC-4 guards a real
> production hazard: an expired signing certificate means no device in the field can
> update, and it is discovered at the worst possible moment. This pattern is used in
> comparable projects **[A-9]**.

### 9.3 Telemetry

**SEC-5** &nbsp;Any health or version reporting **MUST** be opt-in, **MUST** be aggregate
and anonymous, and **MUST NOT** create a channel by which a device can be addressed
individually.

> The value being sought is one sentence: *"release 2026-09 is on 400 devices with no
> health-check failures."* That does not require knowing which devices, and must not
> create the ability to reach them.

### 9.4 Host posture

**SEC-6** &nbsp;The root filesystem **MUST** be read-only in normal operation.

**SEC-7** &nbsp;Default credentials **MUST NOT** grant access from outside the device
without a forced change on first use.

**SEC-8** &nbsp;OS security updates **MUST** reach devices on the stable channel without
requiring a maintainer to assess each one individually.

---

## 10. Data, migration and backup

**DATA-1** &nbsp;Feed data, the database, application settings, emonhub configuration,
network configuration, user accounts and host keys **MUST** live on the data partition.

**DATA-2** &nbsp;A device **MUST** survive a full OS re-flash with all of DATA-1 intact,
provided the data partition is preserved.

**DATA-3** &nbsp;The data partition **MUST** be mounted with options chosen for power-loss
resilience as well as write reduction, and **MUST** remount read-only on filesystem error
rather than continue writing.

**DATA-4** &nbsp;An export/import path from an existing emonSD system **MUST** exist and
**MUST** be tested on every release. This is the supported migration route (N6).

**DATA-5** &nbsp;Scheduled local backup **MUST** be available, and the user **MUST** be
able to retrieve a backup off the device.

**DATA-6** &nbsp;Application-level write buffering of feed data (the existing "RedisBuffer"
feed engine) **MUST NOT** be carried into EmonOS.

> **Why DATA-3 is phrased as a trade, not just write reduction.** The historic emonSD
> design optimised hard for reducing bytes written, on the reasoning that SD cards fail
> from write wear. Analysis of that work found the mitigations optimised a metric that does
> not correspond to flash wear, and that together they produce close to the worst possible
> posture for the more likely failure mode — power-loss corruption on a mains-powered,
> headless device **[A-11]**. Remounting read-only on error, rather than continuing to
> write to a filesystem that has already reported corruption, turns a recoverable fault
> into a recoverable fault.
>
> **Why DATA-6.** The same analysis found the buffer reduces write cycles by roughly 2×,
> where a single kernel writeback setting achieves roughly 20× and covers every writer on
> the system rather than only feed data. The buffer also carries a live correctness defect
> — the most recent interval is silently missing from averaged queries and CSV exports —
> and holds up to a minute of every feed in volatile memory **[A-11]**.

---

## 11. Conformance

**TEST-1** &nbsp;The OS update round trip — install, reboot, verify new slot — **MUST** run
automatically in CI on every build.

**TEST-2** &nbsp;Automatic rollback **MUST** be tested by deliberately publishing a build
that fails its health check, asserting the device recovers unattended.

**TEST-3** &nbsp;The application update round trip and its rollback **MUST** be tested the
same way.

**TEST-4** &nbsp;Schema-forward-then-image-back **MUST** be tested: migrate forward, roll
the image back, assert the stack still serves and feeds still write.

**TEST-5** &nbsp;Power loss during install **MUST** be tested against OS-14.

**TEST-6** &nbsp;The emonSD export/import path **MUST** be tested per release (DATA-4).

**TEST-7** &nbsp;Tests **MUST** be written against a framework capable of driving both a
virtual machine and real hardware over a serial console, so the CI suite becomes the
hardware suite without a rewrite.

**TEST-8** &nbsp;Every requirement in this document **MUST** map to at least one test or be
explicitly recorded as untested.

**TEST-9** &nbsp;A build **MUST NOT** be promoted to the stable channel without a green run
of TEST-1 through TEST-6.

**TEST-10** &nbsp;Automated dependency updates **MAY** merge to the development branch on
green CI, and **MUST NOT** promote to stable automatically.

**TEST-11** &nbsp;All build and test dependencies **MUST** be pinned, including CI action
versions.

> **Why TEST-1 to TEST-5 are requirements and not aspirations.** The starting position is
> no CI at all on the build/update scripts, and an application test suite that is defined
> but never run **[A-12]** — so this section is establishing a practice, not tightening
> one. It is testable in a virtual machine, on standard CI runners, with no hardware: a
> comparable project runs exactly this suite, including booting the other slot and
> asserting it comes up, on every build **[A-8]**. There is no good reason for EmonOS's
> rollback path to be less tested than its build.
>
> **Why TEST-10.** Auto-merging dependency updates into a branch that devices track would
> recreate the unpinned self-updating agent described in §1.1, with a robot driving it.
> Merging to a development branch is fine; promotion to stable is a tagged release that
> passed TEST-9.

---

## 12. Open decisions

Each blocks or shapes requirements above. None should be resolved silently.

**OPEN-1 — Which Raspberry Pi model ships in each product?**
Not stated in the published product documentation, which says "Raspberry Pi" without a
model. Determines the bootloader per target, which determines whether OS-7's attempt
counter is available natively or whether the OS-10 watchdog fallback is load-bearing. Also
determines the scope of migrating GPIO libraries, since the library four hardware
interfacers depend on does not function on the newest Pi. **Critical path. Owner:
hardware / BOM.**

**OPEN-2 — Database engine upgrade policy.**
APP-10 and APP-11 make engine upgrades forward-only with restore-from-dump as the rollback
path. The decision is whether that is acceptable, or whether a stronger guarantee is
needed. **Owner: software.**

**OPEN-3 — Supervisor: host-native daemon or privileged container?**
A host-native daemon in the OS image removes the need for a privileged container, a
D-Bus bridge and a self-healing bootstrap, and survives a broken container layer. The cost
is that supervisor updates require an OS update. A containerised supervisor updates
independently but needs all of the above, and SUP-6 then applies. **Owner: architecture.**

**OPEN-4 — Where are release artefacts hosted?**
Releases on a code-hosting platform alone, or object storage plus a signed version
manifest per channel? Determines what the update client polls, and the conformance suite
depends on it. **Owner: infrastructure.**

**OPEN-5 — Slot sizing and minimum media size.**
Both slots must hold the largest future image (OS-3) and cannot be resized in the field.
Sizing generously costs media; sizing tightly risks a dead end. **Owner: product.**

**OPEN-6 — Container image provenance.**
Build our own images, or depend on a third party's registry account? A supply-chain
question rather than a technical one. If third-party, images must at minimum be pinned by
digest (APP-2) and mirrored. **Owner: product.**

**OPEN-7 — Where does the network provisioning UI live?**
The supervisor owns the capability (SUP-11), but the user interface could stay in emoncms
calling the API, or move to the supervisor so it is reachable when the application is
down. SUP-12 constrains but does not settle this. **Owner: architecture.**

**OPEN-8 — Desktop target: UEFI-only, or also legacy BIOS?**
UEFI-only is simpler and matches comparable projects. **Owner: architecture.**

---

## 13. Delivery order

Each stage is a working, testable system. The ordering is deliberate: it front-loads the
things that make everything after them verifiable.

| Stage | Deliverable | Unlocks |
|---|---|---|
| 1 | Board abstraction + one target booting (HW-1, HW-2, HW-3) | Everything |
| 2 | **Desktop/VM target** and CI building it (HW-4, HW-5) | The test target |
| 3 | Application stack running on a writable root | Proves the app works before immutability is added |
| 4 | A/B layout, read-only root, data partition (OS-1 – OS-6, ARCH-3) | — |
| 5 | Signed bundles, slot switching (OS-11 – OS-14, SEC-1 – SEC-4) | — |
| 6 | Health check and automatic rollback (HC-1 – HC-9) | **TEST-1, TEST-2** |
| 7 | Supervisor: lifecycle, manifests, app rollback (SUP-1 – SUP-6, APP-1 – APP-17) | **TEST-3, TEST-4** |
| 8 | Hardware profiles, MCU firmware, network (SUP-7 – SUP-12) | Appliance targets |
| 9 | Channels, artefact hosting, migration (REL-1 – REL-5, DATA-4) | **TEST-6**, release |

Stage 2 before stage 3 is the load-bearing choice. Building the desktop target early costs
little and means every subsequent stage is verified automatically rather than by hand.

---

## 14. Out of scope for v1

Deferred deliberately, recorded so the boundary is visible.

- Delta updates. Full-slot writes are acceptable at this image size
- Data partition on USB/NVMe as a shipped default (OS-4's label indirection keeps it open)
- Multi-user or multi-tenant emoncms deployments
- Any device-addressable remote support channel (N1, N2, SEC-5)
- Automated migration of local user modifications from emonSD (N6)
- Hardware-in-the-loop CI. TEST-7 requires the framework to support it; standing it up is later

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **A/B slots** | Two complete copies of the OS. One runs; the other receives updates. Roles swap on a successful update |
| **Slot** | One kernel + root filesystem pair, versioned and installed as a unit (OS-2) |
| **Bundle** | A single signed artefact containing everything an OS update replaces |
| **Manifest** | A signed description of an application release: release ID, image digests, minimum OS version |
| **Digest** | A content hash identifying a container image exactly, unlike a tag which can be repointed |
| **Data partition** | The partition holding all mutable state; never written by an update mechanism |
| **Supervisor** | The privileged agent owning container lifecycle, updates, health and hardware access (§8) |
| **Health check** | The test that decides whether a newly booted slot or newly started release is good (§6.1) |
| **Commit / mark good** | Recording a slot as known-working, making it the default for later boots |
| **Channel** | A published release stream — stable, beta or development — selected by the user |
| **Board metadata** | The per-target file describing bootloader, kernel, partitioning and hardware profile |
| **Hardware profile** | The set of devices a target has: MCU, radio, display, one-wire. May be empty |
| **emonhub** | The component that talks to energy monitoring hardware and delivers readings to emoncms |
| **Feed** | A time series of measurements. Data is flat files; metadata is in the database |

---

## Appendix A — Evidence

Findings this specification depends on, each read from source at the revision named. This
appendix exists so the document can be audited without reference to any other document.

Sources read:

- `openenergymonitor/EmonScripts` @ `3c09925` (master)
- `openenergymonitor/emoncms` @ `28a26313`
- `emoncms-docker` @ `573c00a`
- `home-assistant/operating-system` @ `42ea0f607`

No measurement was taken on Raspberry Pi hardware. Claims about comparable projects are
about how those projects are built, verified by reading them; they are not endorsements of
the same choices.

| Ref | Finding | Source |
|---|---|---|
| **A-1** | The in-field update path runs `apt-get update` but never `upgrade`; `upgrade`/`dist-upgrade` run only at image build time | `EmonScripts` `update/main.sh`, `install/main.sh` |
| **A-2** | A one-wire device tree overlay is enabled by a build-time flag, with a source comment recording that it conflicts with the shutdown button on emonPi 1 | `EmonScripts` `install/emonsd.sh:78-89` |
| **A-3** | emonPi 3 firmware update clones a flashing tool from GitHub and compiles it **on the device at update time**, then drives the MCU bootloader over the serial port | `EmonScripts` `update/emonpi3_firmware_upload.sh:19-23` |
| **A-4** | emoncms schema migration is declarative and additive-only: it emits `CREATE TABLE`, `ALTER TABLE ADD`, `ALTER TABLE MODIFY` and `CREATE INDEX`, and never `DROP`. Operations run in a plain loop that stops at the first error | `emoncms` `Lib/dbschemasetup.php` — `db_schema_setup()` and `db_schema_update_column()` |
| **A-5** | Schema convergence runs on ordinary web requests when a settings flag is enabled, rather than as a discrete migration step | `emoncms` `index.php:92-96` |
| **A-6** | The container stack pins no image by digest — all floating tags — and enables automatic database engine upgrade on start | `emoncms-docker` `docker-compose.yml` |
| **A-7** | In a comparable Buildroot-based A/B system, the partition layout, update-agent slot configuration and bundle contents are shared verbatim across all targets; only board metadata, defconfig, bootloader config and a kernel fragment differ. A single shared boot partition (rather than paired per-slot ones) is what allows one layout to work under three different bootloaders | `operating-system` `buildroot-external/genimage/`, `buildroot-external/ota/`, `buildroot-external/board/` |
| **A-8** | The same project tests the full OS update round trip in a virtual machine in CI on every build: install to the other slot, reboot, assert the version changed, then switch back to the other slot and assert it boots | `operating-system` `tests/smoke_test/test_os_update.py` |
| **A-9** | The same project's build takes signing keys from CI secrets, falls back to a self-signed certificate with a visible warning when they are absent, and checks certificate validity before building | `operating-system` `.github/workflows/build.yaml` |
| **A-10** | The Home Assistant Supervisor is that project's application platform, not a general container supervisor: its build package preloads six of its own containers and it carries an add-on store, its own auth and CLI. balenaSupervisor is general-purpose and Apache-2.0, but its API documents no device-side rollback on a failed container update, and its model requires polling target state from a backend (hosted or self-hosted) | `operating-system` `buildroot-external/package/hassio/`, `rootfs-overlay/usr/sbin/haos-supervisor`; balena Supervisor API documentation |
| **A-11** | Analysis of emonSD write-load mitigations found the application-level feed buffer reduces flash page programs by roughly 2×, where a single kernel writeback setting achieves roughly 20× across all writers; that the buffer silently omits the most recent interval from averaged queries and CSV exports; and that the combined mitigations worsen power-loss resilience | Write-load audit, 2026-08-27, against `emoncms` `Modules/feed/engine/`, `scripts/feedwriter.php` |
| **A-12** | `EmonScripts` has no CI at all. `emoncms` CI runs lint and code style only; its unit, integration and feature test suites are defined but never invoked | `EmonScripts` (no `.github/workflows`); `emoncms` `.github/workflows/PHP.yml`, `composer.json` |

### Further reading

These informed the specification but are **not** required to read or apply it. They may
not persist.

- `Docs/emonos-learnings.md` — the full analysis behind Appendix A
- `Docs/update-strategy-handover.md` — the original problem statement and phased plan
- `Docs/emon-os-initial-design.md` — a build tutorial for the A/B approach
- `Docs/sd-card-write-load.md` — the write-load audit summarised in A-11

External references, expected to persist:

- RAUC (A/B update framework) — `rauc.readthedocs.io`
- Buildroot manual, `br2-external` chapter — `buildroot.org/downloads/manual/manual.html`
- labgrid (test framework spanning VM and hardware) — `labgrid.readthedocs.io`
- Home Assistant OS — `github.com/home-assistant/operating-system`
- balenaSupervisor — `github.com/balena-os/balena-supervisor`

---

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.2 | 2026-08-31 | Made standalone: rationale inlined, Appendix A added citing primary sources, glossary and delivery order added. OS-6, HC-8, HW-5, TEST-11 added; OS section renumbered from OS-6 onward |
| 0.1 | 2026-08-31 | First draft |

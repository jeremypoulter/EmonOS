# EmonOS — Proof of Concept Specification

**Version:** 1.1
**Date:** 2026-09-17
**Parent:** [emonos-product-spec.md](emonos-product-spec.md) — requirement IDs below refer to that document
**Status:** Scope definition. Everything not listed in §2 is out of scope.

---

## 1. The question this PoC answers

> **Can we build one OS image tree that boots the emoncms container stack on two
> targets, update it atomically, and have a bad update recover itself without
> intervention?**

Nothing else. If the answer is yes, the architecture in the parent spec is sound and the
remaining work is scope rather than risk. If it is no, we learn that in weeks rather than
months.

This maps to **stages 1–6** of the parent spec §13. Stages 7–9 (supervisor, hardware
profiles, channels) are explicitly not attempted.

---

## 2. Scope

### 2.1 In scope

| Area | PoC delivers | Parent requirements demonstrated |
|---|---|---|
| Board abstraction | One source tree, two targets, per-target metadata file | HW-1, HW-2, HW-3, HW-4 |
| Partitioning | GPT (hybrid MBR alias on Pi), single shared boot partition, bootstate partition, A/B kernel + rootfs, data partition | OS-1 (reduced), OS-2, OS-6 |
| Read-only root | squashfs, mounted `ro` | ARCH-3, SEC-6 |
| Application layer | Docker + a compose file on the data partition, started by systemd | ARCH-4 |
| State separation | All emoncms data on the data partition, survives update | ARCH-2, DATA-1, DATA-2 |
| OS update | RAUC bundle of kernel + rootfs, written to inactive slot, signed with a dev key; shared boot files remain factory-installed | OS-11 (reduced), OS-12, OS-13 |
| Slot arming | New slot gets a bounded number of boot attempts | OS-7, OS-15 |
| Health check | Minimal: emoncms answers on `:80` | HC-1 (reduced — see §7) |
| Commit | Slot marked good only by the health check | OS-8, HC-9 |
| Rollback | Failed health check returns to the previous slot unattended | HC-4 |
| Interrupted update | Power cut mid-write leaves the previous slot booting | OS-14 |
| Test harness | One script drives both targets | TEST-7 (seed) |

### 2.2 Explicitly out of scope

Deferring these is the point — they are what makes the PoC a PoC.

| Deferred | Why it can wait |
|---|---|
| **The supervisor** (SUP-1 – SUP-12) | systemd + compose is enough to prove the OS layer. The supervisor is the largest single piece of new software and it does not gate this question |
| **Application update and rollback** (APP-1 – APP-17) | A separate mechanism from the OS update. Prove one at a time |
| **Signed app manifests** (APP-1, APP-3) | The compose file is checked into the image tree for the PoC |
| **Release channels, artefact hosting** (REL-1 – REL-5) | Bundles are copied to the device by hand or attached as a disk (§9.1) |
| **Hardware profiles, device tree fragments** (SUP-7 – SUP-10, OS-16) | No appliance target in the PoC. `generic` profiles only |
| **Network provisioning, AP, captive portal** (SUP-11, SUP-12, HC-8) | Targets get DHCP and that is all |
| **Release signing infrastructure** (SEC-2 – SEC-4) | One development key generated outside version control; its public certificate is embedded in the image and clearly marked as development-only |
| **emonhub and any hardware** | No MCU, no radio, no i2c. HC-1(e) is therefore not testable and is excluded from the PoC health check |
| **emonSD migration** (DATA-4) | Fresh installs only |
| **Data partition expansion** (OS-5) | Fixed-size data partition, sized generously |
| **CI** (TEST-1 – TEST-11) | The test harness must be *runnable* by CI, but wiring it up is the next step |
| **`rpi4-qemu`** | Deferred: QEMU does not model the Pi firmware stage or GENET Ethernet faithfully enough to justify a PoC target. Its validated launch recipe is retained in the implementation plan appendix for a later CI smoke target. |

---

## 3. Building blocks

Named once here so there is no ambiguity about what the PoC is made of. Each is an
existing, maintained project; the PoC writes configuration and glue, not update machinery.

| Component | Role in the PoC | Why this one |
|---|---|---|
| **Buildroot** | Builds the whole image from one tree, via a `br2-external` layer | The tool used by the comparable system in parent Appendix A-7; small images, one command per target |
| **RAUC** | The A/B update agent: slot layout, bundle format, signature verification, install to the inactive slot, boot-slot marking | Native backends for U-Boot and GRUB avoid a custom RAUC backend; each board still supplies its boot-selection script. Its D-Bus API is what the future supervisor will drive |
| **U-Boot** (Pi 4) / **GRUB-EFI** (x86) | Slot selection with a real attempt counter | Both supported by RAUC out of the box (§4.1) |
| **squashfs** | Read-only compressed root filesystem per slot | Simplest read-only option in Buildroot; EROFS is a drop-in swap later if wanted |
| **Docker + Compose** | Runs the emoncms stack from the data partition | Matches `emoncms-docker` as it exists today |
| **labgrid + pytest** | The test harness driving the VM and real hardware over serial | Spans QEMU and real hardware with the same tests (parent TEST-7) |

RAUC in particular is load-bearing: **T4–T7 in §8 are RAUC behaviour** — slot status,
install, mark-good, and recovery from an interrupted write. The PoC does not implement any
of that; it configures RAUC and proves the configuration is right on both targets.

---

## 4. Targets

Two targets, one source tree.

| ID | Runs on | Bootloader | Purpose |
|---|---|---|---|
| `x86-64-vm` | QEMU `q35` + KVM, UEFI (OVMF) | GRUB (EFI) | Fast iteration; the future CI target |
| `rpi4` | Raspberry Pi 4B, real hardware | U-Boot | Proves it works on the actual product family |
The `rpi4-qemu` target is deferred. The physical Pi is required for the U-Boot A/B and
power-interruption evidence in this PoC. The implementation plan retains the validated
emulator launch recipe so it can be added later as a CI smoke target.

### 4.1 Why U-Boot on Pi 4 and not tryboot

Two reasons, both from the parent spec's evidence:

- U-Boot provides a real boot **attempt counter** (OS-7). Tryboot provides a one-shot flag,
  which would make the watchdog fallback (OS-10) load-bearing on day one.
- RAUC has **native backends** for both U-Boot and GRUB. Tryboot needs a custom RAUC
  bootloader backend. The PoC adapts standard U-Boot and GRUB slot-selection scripts but
  writes no custom RAUC backend.

This also means the PoC does not depend on **[OPEN-1]** (which Pi model ships in which
product). Pi 4 is chosen for the PoC because it is available, not because it is decided.

---

## 5. Partition layout

One logical layout for both targets — GPT on x86 and GPT with a hybrid MBR alias on Pi 4,
using a single shared boot partition.

| # | Label | Contents | Size |
|---|---|---|---|
| 1 | `emonos-boot` | ESP / firmware, bootloader, its environment | 64 MB |
| 2 | `emonos-kernel0` | Kernel, slot A | 32 MB |
| 3 | `emonos-rootfs0` | squashfs, slot A | 512 MB |
| 4 | `emonos-kernel1` | Kernel, slot B | 32 MB |
| 5 | `emonos-rootfs1` | squashfs, slot B | 512 MB |
| 6 | `emonos-bootstate` | U-Boot redundant environment / boot attempt state | 8 MB |
| 7 | `emonos-data` | Docker, emoncms data, config | remainder |

Sizes are PoC values, not a decision on **[OPEN-5]**. Measure the actual rootfs and
revisit.

The layout intentionally omits the product's separate persistent OS-configuration partition:
for the PoC, host configuration is either immutable, volatile, or explicitly held below the
data partition. OS-1 is therefore only partially demonstrated.

`emonos-data` is mounted by **label**, not device path (OS-4) — this costs nothing now and
keeps the USB/NVMe option open later.

---

## 6. Application layer

Deliberately the simplest thing that works.

- A `docker-compose.yml` derived from `emoncms-docker`, placed on the data partition at
  first boot.
- One systemd unit runs `docker compose up`, ordered after the data mount and Docker.
- Images **pinned by digest** — this is free, and without it the PoC cannot tell whether a
  failure came from the OS change or from an image that moved underneath it.
- Container images preloaded into the data partition at build time (`docker save` at build,
  `docker load` on first boot) so the PoC works without a registry.
- The database runs as its own container (APP-9), because that decision is cheap to honour
  now and expensive to reverse.

No supervisor, no manifest, no application-level update. Changing the application in the
PoC means building a new OS image.

---

## 7. Health check

The PoC health check is the parent spec's HC-1 reduced to what is testable without
hardware:

| Check | In PoC? |
|---|---|
| a — emoncms web interface responds | **Yes** |
| b — `feed/list.json` returns valid JSON | **Yes** |
| c — database reachable | Yes, implied by (b) |
| d — feed writer advancing | No — needs data flowing |
| e — inputs arriving from emonhub | **No — no hardware** |

Behaviour: poll for up to 5 minutes after boot. On success, mark the slot good. On
failure, reboot — the slot was never marked good, so the bootloader returns to the
previous one.

**HC-1(e) being untestable in the PoC is a known gap**, not an oversight. It is the check
that matters most in production and it needs the appliance target.

---

## 8. Exit criteria

The PoC is complete when all eight pass on both targets. These are the tests; nothing
else counts as done.

| | Test | Proves |
|---|---|---|
| **T1** | Both targets build from one tree, one command each | HW-3 |
| **T2** | Each target boots and serves emoncms on `:80` | ARCH-4 |
| **T3** | Create a feed, reboot, feed still there | DATA-2 |
| **T4** | `rauc status` reports both slots correctly | OS-1, OS-2 |
| **T5** | Install v2, reboot → running v2, feed intact | OS-11, OS-13, OS-15 |
| **T6** | Install a **deliberately broken** v2 → device returns to v1 unattended, feed intact | **HC-4, OS-8, HC-9** |
| **T7** | Kill power mid-`rauc install` → device still boots the previous slot | OS-14 |
| **T8** | One test script runs T2–T7 against any target, selected by argument | TEST-7 |

**T6 is the PoC.** Everything else is setup for it.

v1 and v2 must differ visibly — a version string in `/etc/os-release` is enough — so the
tests can assert which slot is running rather than inferring it.

The "broken" v2 for T6 should fail the *health check*, not the boot. A build that fails to
boot at all is an easier case and tests less.

---

## 9. Risks to retire first

In order. Each is a day or less, and each can invalidate the plan.

| | Risk | Retire by |
|---|---|---|
| **R1** | QEMU `raspi4b` may not boot a U-Boot + real-firmware chain faithfully enough to be a useful proxy | Boot a stock aarch64 kernel on `raspi4b` before building anything |
| **R2** | QEMU `raspi4b` networking (GENET) is recent and may be incomplete | Test networking early; §9.1 removes the dependency for the core tests |
| **R3** | Docker on a read-only root may need writable paths not yet identified | Run stage 3 (app on a writable root) before making it read-only |
| **R4** | 512 MB slots may not fit a rootfs carrying Docker | Measure after the first build; adjust before writing the partition layout into tests |
| **R5** | Preloaded container images may not fit a sensibly sized data partition | Measure `docker save` output early |

### 9.1 Update delivery in the PoC

**Bundles are not fetched over the network.** They are attached as a second disk image
(QEMU) or copied to the data partition (hardware), then installed with a local
`rauc install`.

This is deliberate: it removes R2 from the critical path and separates "does A/B update
work" from "does download work". Network delivery arrives with channels and artefact
hosting, which are out of scope (§2.2).

---

## 10. Repository skeleton

```
emonos/
├── buildroot/                        # submodule, pinned to a release branch
├── buildroot-external/
│   ├── configs/
│   │   ├── emonos_x86_64_vm_defconfig
│   │   └── emonos_rpi4_defconfig
│   ├── board/
│   │   ├── x86-64-vm/{meta,grub.cfg,cmdline.txt,post-image.sh}
│   │   └── rpi4/{meta,uboot.config,boot.cmd,post-image.sh}
│   ├── genimage/                     # shared partition layout
│   ├── ota/                          # RAUC system.conf + manifest templates
│   ├── rootfs-overlay/               # systemd units, health check, compose file
│   └── keys/dev-cert.pem             # DEV KEY — clearly marked, never a release key
├── tests/                            # pytest + labgrid
├── Makefile
└── README.md
```

Two rules to hold from the first commit, because they are cheap now and expensive later:

- **HW-2 — nothing target-specific outside `board/`.** The PoC has two targets
  specifically to make violations visible immediately.
- **Tests cite requirement IDs**, so the parent spec's §11 checklist can be filled in
  rather than reconstructed.

---

## 11. Rough sizing

| Stage | Work |
|---|---|
| R1–R5 risk retirement | ~1 week |
| One target booting to a shell | ~1 week |
| Docker + emoncms on a writable root, all targets | ~1 week |
| A/B layout, squashfs, data partition | ~1–2 weeks |
| RAUC, slot switching, T4–T5 | ~1–2 weeks |
| Health check, commit, rollback, T6–T7 | ~1–2 weeks |
| Test harness, T8 | ~1 week |

Call it **6–9 weeks** for one person, with the caveat that stage 4 onward is where
unknowns live. The first three stages are well-trodden.

---

## 12. What the PoC does not tell us

Stated so the results are not over-read:

- Nothing about **appliance hardware** — MCU firmware, radio, i2c display, device tree
  variants. The `rpi4` target is a bare Pi 4, not an emonPi.
- Nothing about **application update or rollback**, including the MariaDB engine question
  (**[OPEN-2]**).
- Nothing about **supervisor design** (**[OPEN-3]**) beyond confirming that systemd alone
  is insufficient for anything past the PoC.
- Nothing about **update delivery, channels or hosting** (**[OPEN-4]**).
- Nothing about **SD card endurance or power-loss resilience in the field** — T7 tests one
  interruption, not sustained behaviour.

A successful PoC de-risks the OS layer. The supervisor and the application layer remain
the largest unknowns, and the parent spec §12 open decisions remain open.

# EmonOS PoC — Implementation Plan

**Version:** 1.4
**Date:** 2026-09-19 (v1.3 2026-09-17, v1.2 2026-09-16, v1.1 2026-09-16, v1.0 2026-09-14)
**Changes in 1.4:** The PoC database baseline moves from the obsolete `mariadb:11.0-jammy`
image to upstream `mariadb:11.8-noble`, pinned by digest. It remains fixed across v1, v2 and
broken-v2; this is a fresh-install compatibility check, not a database-engine-upgrade test.
Revert to the prior pinned 11.0 image only if WP3 identifies a concrete emoncms compatibility
failure, and record the evidence (D23).
**Changes in 1.3:** Consolidates the earlier standalone planning document, which has been
removed. The plan now freezes
shared bootloader/firmware files during PoC updates rather than claiming their replacement
is atomic (D9); the broken-v2 fault is slot-local and cannot corrupt persistent Compose or
application data (D19); health-check, offline-image, and power-cut evidence is strengthened
(D18, D21); OS-15 uses one trial boot (D20); and data uses durable ext4 defaults rather than
the unmeasured `commit=600` tuning (D17). **Buildroot remains cross-compiled on x86.** The
web image is split into cross-compiled PHP-extension artifacts and QEMU-safe multi-platform
layer assembly; native arm64 GitHub capacity is a fallback only if that experiment fails
(F1, D16, WP0.4).
**Changes in 1.2:** **`rpi4-qemu` is dropped — the PoC has two targets, `x86-64-vm` and
`rpi4`** (D14, decided 2026-09-16). F4 rewritten as that decision; F1/F3/F7, WP2, WP3, WP5,
WP7, WP8 and the exit criteria de-scoped to two targets; **WP9 (real hardware) moves onto the
critical path** and gains task 0.7 (confirm the bench); **R12 added** — the bench is a single
point of failure and the U-Boot A/B path is now demonstrated nowhere else; R11 marked moot,
R2 moot as well as retired; §7 gains the U-Boot/CI caveat. The verified `raspi4b` recipe is
kept in Appendix B so the target can be re-added cheaply.
**Changes in 1.1:** WP0's QEMU probes were run. **R1 and R2 retired**; F4 rewritten with the
verified `raspi4b` launch recipe and its five constraints; **F5's diagnosis corrected** (host
memory pressure, not `RLIMIT_MEMLOCK`); **F7 added** (`rpi4-qemu` has no ethernet, which
forces in-guest health checks); R7 rewritten; R10, R11, D15, D16 added; Appendix B
re-verified. §8 item 4 resolved.
**Parent:** [emonos-poc-spec.md](emonos-poc-spec.md) — that document owns *scope*; this one owns *how*
**Also references:** [emonos-product-spec.md](emonos-product-spec.md) (requirement IDs), [emonos-learnings.md](emonos-learnings.md) (evidence), [emon-os-initial-design.md](emon-os-initial-design.md) (build tutorial, partly superseded)
**Status:** Proposed. §1 must be read before any code is written — it changes five things in the PoC spec, including its target list (§4, §8: `rpi4-qemu` is dropped).

---

## 0. What this document is

The PoC spec says what the PoC must prove (T1–T8) and what it must not attempt. It does not
say which files to write, in what order, or which of the remaining technical choices to
make. This plan does that, and it resolves every decision needed to start.

Following the convention of the other docs, claims are marked:

| Marked | Meaning |
|---|---|
| **[verified]** | Read from source or observed on this machine, today |
| **[inferred]** | Reasoned from a verified fact |
| **[unresolved]** | Needs a probe or a human decision — listed in §5 or §8 |

**Primary source read for this plan:** `home-assistant/operating-system` @ `42ea0f607` — the
same revision cited in the parent specs' Appendix A. Every `buildroot-external/…` path
quoted below was read at that revision. This matters because the PoC's whole premise is
"configure existing machinery, write no update logic" (PoC spec §3), and the reference
implementation is the cheapest available answer to "what does that configuration look
like".

---

## 1. Findings that change the PoC spec

Seven things surfaced while grounding the plan: four change the PoC spec's *content*, one
(F4) changes its *target list*, one is an environment condition, and F7 reshapes the test
harness. All are cheap to absorb now and expensive later.

**Probe results, 2026-09-16.** WP0's three QEMU tasks have been run. **R1 and R2 are
retired** — see F4 and F7 — and their results led to `rpi4-qemu` being dropped from the PoC
(D14), leaving two targets. The `x86-64-vm` launch path is verified end to end (q35 + KVM +
`-cpu host`, serial on `ttyS0` with no earlycon needed, virtio disk enumerating all GPT
partitions, `eth0` up, aarch64/amd64 userspace reached on both targets). F5's diagnosis was
corrected in the process.

### F1 — `openenergymonitor/emoncms` is amd64-only. This blocks T2 on the Pi. [verified]

```
$ docker manifest inspect --verbose openenergymonitor/emoncms:latest
  → single manifest, platform: { architecture: "amd64", os: "linux" }
```

Not a manifest list. There is no arm64 image. The other three are multi-arch (`mariadb:11.0`,
`redis:7.0`, `eclipse-mosquitto:2.0` all publish `arm64`). [verified]

So **T2 ("each target boots and serves emoncms on :80") cannot pass on `rpi4` with the
published image**, and neither can T3, T5, T6 or T7 there, since all of them assert a
working stack. This is not in the PoC spec's risk list (R1–R5), and with `rpi4-qemu` dropped
it is the only thing standing between the build and half the exit criteria — the Pi is now
the sole aarch64 target, so nothing routes around it.

It is fixable: `emoncms-docker`'s `web/Dockerfile` builds `FROM php:8.4-apache` (multi-arch),
takes a `TARGETPLATFORM` arg, and pulls s6-overlay by architecture. [verified] So
`docker buildx build --platform linux/arm64` should work — but it compiles `phpredis` and
`mosquitto-php` from source (`install_redis.sh`, `install_mosquitto.sh`) [verified], which
under QEMU user-mode emulation is slow and is exactly the unpinned-compile pattern the
learnings doc §2.2 flags.

**Plan:** new risk **R6**, retired in WP0 (§4). Publish from the maintainer's
`JeremyPoulter/emoncms-docker` fork to its GitHub Container Registry package. Buildroot
cross-compiles the host OS on x86 as normal. For the web image, first cross-compile the PHP
extension artifacts for the exact arm64 PHP ABI against an arm64 sysroot, then use Buildx/QEMU
only for the remaining target-architecture image assembly: package installation, downloads,
file copies, configuration, and layer export. Do not run PHP extension compilation under QEMU
user-mode emulation.

The current Dockerfile compiles `mysqli`, `gettext`, `phpredis`, and Mosquitto-PHP. It must
therefore be split into a cross-build stage and a runtime assembly stage before QEMU-only
assembly is a valid claim. If the PHP extension cross-build cannot reliably reproduce the
official PHP image's ABI, use GitHub-provided native arm64 capacity as the fallback; do not
repurpose the Pi acceptance bench as a routine image builder.

The fork's package is the PoC image source, not an untrusted third-party registry dependency.
Record its source commit, package name, platform, manifest digest, image-config digest, and
SBOM/provenance if GitHub Actions produces them. The resulting image must be pulled once on
each target architecture, saved, loaded offline, and started through its exact digest with
registry access disabled before it is accepted for the PoC.

**Cross-build investigation, 2026-09-17.** The current fork compiles more than the two
third-party modules: `docker-php-ext-install mysqli gettext` also compiles PHP core extension
sources. `phpredis` and Mosquitto-PHP use the standard `phpize` -> `configure` -> `make`
shared-extension flow. PHP documents selection of the target installation through
`--with-php-config`; this makes an arm64 cross-build feasible in principle, provided the
following inputs are all locked and supplied:

- The multi-architecture `php:8.4-apache` **index digest**, exact PHP version, PHP API and
  Zend API. The current amd64 tag resolves to PHP 8.4.25, PHP API 20240924 and Zend API
  20240924; a tag must not be relied upon to retain that ABI.
- Exported arm64 PHP headers and a target-aware `php-config` wrapper. The wrapper must report
  paths in an arm64 sysroot rather than the x86 build container's `/usr/local` paths.
- `aarch64-linux-gnu-gcc`/binutils, a target sysroot, and `--host=aarch64-linux-gnu` for
  Autoconf. Mosquitto-PHP additionally needs `libmosquitto-dev:arm64` and target-aware
  `pkg-config` metadata.
- The exact PHP 8.4.25 source for `mysqli` and `gettext`, plus the target image's generated
  PHP configuration headers. These are core-extension inputs, not independently versioned
  PECL modules.

WP0.4 is an experiment, not a presumed implementation: build the four `.so` files, verify
their ELF architecture and dynamic dependencies, copy them to a clean arm64 PHP image, and
verify `php -m` plus a real stack start on the Pi. Pin every Git source currently cloned by
the Dockerfile (`emoncms`, its modules, EmonScripts, phpredis and Mosquitto-PHP) to a commit
and verify its content before the experiment. If any ABI check fails, use a GitHub-provided
native arm64 runner for the compiler stages; the Buildx/QEMU assembly path still applies to
non-compiling layers.

**Local Buildx probe, 2026-09-18.** The current Docker engine can pull the arm64 variant of
`php:8.4-apache`, but cannot execute it: `docker run --platform linux/arm64 ...` terminates
with `exec format error`, and the default Buildx builder advertises only amd64/386 platforms.
Before QEMU assembly can be used, WP0 must create a `docker-container` Buildx builder and
register a fixed `binfmt_misc` emulator image, or select a remote builder. Treat this as a
host-tool prerequisite, not evidence that QEMU assembly itself has been validated.

**Resolved 2026-09-24 — the cross-build works; R6 is retired.** Implemented in the fork as
[jeremypoulter/emoncms-docker#3](https://github.com/jeremypoulter/emoncms-docker/pull/3) (branch
`cross-compile-php-extensions`, open, CI green):

- CI resolves the `php:8.4-apache-trixie` index digest once. A matrix job then extracts the
  target PHP headers and `php-config` without executing the target image and builds the four
  extensions in an amd64 `debian:trixie` container. The arm64 build uses
  `aarch64-linux-gnu-gcc` and `libmosquitto-dev:arm64`. Each `.so` is checked for its ELF
  machine type, a `get_module` symbol, the absence of an RPATH, and the libmosquitto soname.
- phpredis is pinned at 6.3.0 (`df4fab2de7fc`) and Mosquitto-PHP at `426a08afc452`.
- The runtime image copies only the prebuilt `.so` files and installs `libmosquitto1`, not
  `-dev`. `install_redis.sh`, `install_mosquitto.sh` and `docker-php-ext-install` are gone.
  QEMU runs only the image-assembly steps and the load checks.
- No ABI check failed, so the native arm64 runner fallback (D16) was not needed.
- **Publication:** pull requests build without pushing. Pushes to `master` publish (added in
  fork PR #3 at `815e0f8`; before that, publishing was manual only). Manual `workflow_dispatch` runs
  also publish. A manual dispatch of the fork published
  `ghcr.io/jeremypoulter/emoncms` (amd64 + arm64, public, index digest
  `sha256:2686f3c0631ecc2e9951a69a6b601beb53547688c03f5013e388f57b786fd099`). `:latest`
  now moves with every `master` push, so EmonOS must keep pinning by digest and bump the pin
  deliberately.
- **Validated on the Pi 4:** pulled by digest in ~90 s. The container reports Debian 13,
  `aarch64` and PHP 8.4.25. `mysqli`, `gettext`, `redis` 6.3.0 and `mosquitto` all load with
  no missing libraries. The full four-container stack then ran on the Pi (WP3 findings
  below).

Not covered by R6: the offline `docker save`/`load` path (WP0.5) and pinning the Git
sources the Dockerfile still clones (`emoncms`, its modules and EmonScripts).

### F2 — The original PoC 6-partition layout was missing the partitions that make it work. [verified]

PoC spec v1.0 §5 listed six partitions. The reference layout
(`buildroot-external/genimage/partitions-os-gpt.cfg`) has eight:

| Reference partition | PoC spec has it? | What it is for |
|---|---|---|
| `hassos-boot` (ESP) | yes | bootloader, grubenv, `rauc.db` status file |
| `hassos-kernel0` | yes | |
| `hassos-system0` | yes | |
| `hassos-kernel1` | yes | |
| `hassos-system1` | yes | |
| **`hassos-bootstate`** | **no** | **raw U-Boot environment — where `BOOT_A_LEFT` lives** |
| **`hassos-overlay`** | **no** | **writable `/etc`** |
| `hassos-data` | yes | |

Both omissions are load-bearing:

- **`bootstate`.** On the U-Boot targets the boot attempt counter is a raw 16 KB env blob
  written by `fw_setenv` — `/etc/fw_env.config` is generated as
  `/dev/disk/by-partlabel/hassos-bootstate  0x0000  ${BOOT_ENV_SIZE}`
  (`buildroot-external/scripts/rauc.sh:54`). [verified] Without it, U-Boot has nowhere
  durable to keep the counter, and **OS-7 — the whole reason the PoC chose U-Boot over
  tryboot (PoC spec §4.1) — has no storage.** On GRUB the counter lives in `grubenv` on the
  ESP (`system.conf.gtpl`: `grubenv=/mnt/boot/EFI/BOOT/grubenv`) [verified], so the
  partition is unused there — but the layout is meant to be shared, so it should exist on
  both.
- **`overlay`.** squashfs root means `/etc` is read-only. The reference dedicates a
  96 MB ext4 partition to an early `/etc` overlay.

**Plan:** add `emonos-bootstate` (8 MB) to the layout — non-negotiable, it is where OS-7
lives. Do **not** add an overlay partition; instead take the cheaper route in §2 D7
(read-only `/etc`, tmpfs `/var`, persistence on the data partition). Seven partitions.

### F3 — The Pi 4 target needs a hybrid MBR/GPT table, not pure GPT. [verified]

`buildroot-external/board/raspberrypi/rpi4-64/meta` is `PARTITION_TABLE_TYPE=hybrid`;
only `pc/generic-x86-64/meta` is `PARTITION_TABLE_TYPE=gpt`. [verified] The partition
*contents* are identical — `partitions-os-hybrid.cfg` and `partitions-os-gpt.cfg` are the
same file with a different `hdimage` stanza [verified] — so the PoC spec §5 claim of "one
layout for all targets" survives, but "GPT" needs to become "GPT, with a hybrid MBR
alias on the Pi target", parameterised by the board `meta` file exactly as the reference
does.

This also settles learnings §4.2: tryboot works with GPT (`rpi5-64/meta` is
`BOOTLOADER=tryboot` + `PARTITION_TABLE_TYPE=gpt` [verified]), but the *U-Boot* Pi 4 path
that the PoC actually chose uses hybrid. Both facts are true; they apply to different
targets.

### F4 — `rpi4-qemu` is dropped: it works, but its fidelity gaps outweigh its value. [verified]

> **Decision (2026-09-16): the PoC has two targets, `x86-64-vm` and `rpi4`.** The emulated
> Pi target is deferred, not abandoned — everything needed to re-add it is recorded here and
> in Appendix B, so it is a day's work if the argument changes. See D14 for the consequences.
>
> This was a *scope* change, so the PoC spec was updated in v1.1: §1, §2.1, §4, §8 and §10
> now use the two-target scope, with `rpi4-qemu` explicitly deferred. This plan is written to
> that scope.

R1 was retired first, so this is a judgement about value rather than feasibility: `raspi4b`
**does** work. The stock Raspberry Pi 4 kernel (`kernel8.img`, 6.18.52-v8+, fetched from
`raspberrypi/firmware`) boots on QEMU 10.2.1 `raspi4b`, reports `Machine model: Raspberry Pi
4 Model B`, brings up **all 4 CPUs at EL2**, reaches the VFS root handoff, and runs a real
aarch64 userspace to a clean power-down (an arm64 busybox initramfs printed
`arch=aarch64 cpus=4` and halted the machine).

But what it emulates diverges from a real Pi 4 in five ways at once, listed below. A pass on
`rpi4-qemu` would therefore be weak evidence about the actual product family, while the
divergences cost real harness complexity — a bespoke `earlycon`, no host-side network probe,
a different block-device name, a third of the RAM, and TCG speeds. Two targets still exercise
the whole board abstraction that matters: **two architectures, two bootloaders (GRUB and
U-Boot), and two partition-table types.** That is what HW-2 and HW-3 are about; a third
target was belt-and-braces.

QEMU does not implement the VideoCore ROM/`start4.elf` stage, so `config.txt` — including
`kernel=u-boot.bin`, which is how a real Pi loads U-Boot — is never read. The emulated
target must therefore be launched directly:

```
qemu-system-aarch64 -M raspi4b -m 2G \
  -kernel u-boot.bin -dtb bcm2711-rpi-4-b.dtb \
  -drive file=emonos.img,format=raw,if=sd \
  -append "earlycon=pl011,0xfe201000 console=ttyAMA0,115200 ..." \
  -display none -serial mon:stdio
```

That recipe *would* have satisfied PoC spec §4's goal ("same image, differing only in how it
is launched") — the disk image is byte-identical — which is why it is recorded rather than
deleted. But five constraints come with it, each verified, and each one a trap:

| Constraint | Consequence |
|---|---|
| **`earlycon=pl011,0xfe201000` is mandatory** | Without it there is **no serial output whatsoever** — `console=ttyAMA0,115200` alone yields a silent boot, because QEMU's `bcm2835-aux-uart` fails to probe (`error -22`) and the console never binds. A harness would see nothing and report a timeout with no clue why. Put it in the rpi4 `cmdline.txt`; it is harmless on real hardware |
| **The SD image appears as `mmcblk1`, not `mmcblk0`** | On a real Pi 4 it is `mmcblk0`. Any hardcoded device path breaks on one target or the other. This independently vindicates OS-4 (mount by label) and `root=PARTUUID=` — the reference's `/dev/disk/by-partlabel/` convention is not stylistic, it is what makes one image work on both |
| **`-m` must be exactly `2G`** | QEMU rejects any other size for `raspi4b` |
| **…yet the guest sees only ~960 MB** | Without the firmware to patch the DTB's memory node, the stock DTB declares 0x3c000000. Feeds into R4 sizing: `rpi4-qemu` has less RAM for the Docker stack than a real 4/8 GB Pi 4 |
| **QEMU disables 5 DT nodes** | It prints them on startup: `bcm2711-pcie`, `bcm2711-rng200`, `bcm2711-thermal`, `bcm2711-genet-v5` (→ F7), and the aux UART fails to probe. So no PCIe, no hardware RNG, no thermal, no ethernet |

Partition detection itself is fine — a 4-partition GPT image was enumerated as
`mmcblk1p1`–`p4`. Both `-drive if=sd` and `-device sd-card,drive=…` work identically.

**The cost of dropping it, stated plainly.** `rpi4-qemu` was the only way to test the Pi path
without a bench, and the Pi path is where the interesting half of the PoC lives: U-Boot's boot
attempt counter is OS-7, which is *why* U-Boot was chosen over tryboot (PoC spec §4.1). With
the emulated target gone, **T4–T7 on `rpi4` can only be demonstrated on physical hardware**,
so WP9 moves from "overlaps, a few days" onto the critical path, and it needs a bench with
switchable power for T7. `x86-64-vm` still gives CI a target (learnings §5.1), and it
exercises the same RAUC configuration over the GRUB backend — but not the U-Boot one. That
gap is now R12.

### F5 — QEMU intermittently fails to start under host memory pressure. [verified]

> **Corrected 2026-09-16.** Plan v1.0 recorded this as a `RLIMIT_MEMLOCK` problem and
> prescribed a `limits.d` drop-in. That diagnosis was wrong. The rlimit is irrelevant; the
> cause is host memory exhaustion. The fix is different, and the wrong fix would have
> changed nothing.

The symptom is a misleading error — QEMU reports an io_uring failure, but the real
condition is that the kernel cannot satisfy the allocation:

```
$ qemu-system-x86_64 -machine q35 -nographic -nic none
qemu-system-x86_64: Failed to initialize io_uring: Cannot allocate memory
```

What the evidence actually shows [all verified]:

- `strace` shows QEMU calling `io_uring_setup` **twice**. The first succeeds; the second
  returns `ENOMEM`. QEMU treats that as fatal and exits.
- It is **not** the rlimit. The same command **passes** as an unprivileged user with
  `memlock` at its default 8192 KB — including 10/10 sequential starts and 5/5 with
  `-accel kvm`. Raising the limit to 64 MB or `unlimited` changes nothing either way.
  (A `setpriv` control confirmed `CapEff: 0`, so the passing runs were genuinely
  unprivileged.)
- It **is** memory pressure. At the time of failure the host had 26.7 GB of 31 GB resident,
  1 GB free and **30 GB of swap in use** — an interactive desktop workload (Cursor, Chrome,
  VS Code, opencode). Failures recur intermittently under that load, and concurrency
  degrades with it: 8/8 QEMU instances start, but only 13/16 and 15/32 do, the excess
  aborting.

**The fix — deny io_uring rather than chase memory.** QEMU has an epoll/poll fallback
compiled in (`fdmon_poll_ops`, `fdmon_epoll_setup`) but no CLI or environment switch to
select it, and it treats `ENOMEM` as fatal. It does, however, **degrade gracefully when
io_uring is refused with `EPERM`** — verified 3/3 passes with
`sysctl kernel.io_uring_disabled=2`. So denying io_uring makes QEMU immune to this failure
mode entirely, at the cost of io_uring for every unprivileged process on the host.

**Decision (2026-09-16): mitigate in the harness; make no change to the host.** See D15.
The two host-level options below were verified to work and are kept as escape hatches if
flakiness proves intolerable once CI runs the suite repeatedly:

- `/etc/sysctl.d/90-no-iouring.conf` → `kernel.io_uring_disabled=2` — deterministic and
  durable, but removes io_uring from every unprivileged process on the workstation.
- Run QEMU in a container — Docker's default seccomp profile already blocks
  `io_uring_setup`, giving the same fallback, scoped.

The harness mitigation has two parts, both proven during this probe session:

1. **Precondition.** Assert `MemAvailable` ≥ (2 × guest RAM + 1 GB) before launching, and
   fail with that number in the message. A suite that reports "no console output" when the
   real cause is a host that could not start QEMU will waste hours — this is the single
   highest-value line in the strategy.
2. **Retry.** Match `Failed to initialize io_uring` on stderr and relaunch, up to 3 attempts
   with a short backoff. Every `raspi4b` boot in this session used exactly this and it was
   sufficient. Retry *only* on that string — never blanket-retry a target that failed to
   boot, or T6/T7 would silently paper over a genuine rollback failure.

### F6 — The kernel partition is a squashfs, not a raw kernel. [verified]

`genimage/images-os.cfg` builds `kernel.img` as a **squashfs filesystem containing the
kernel file**, and the bootloaders read it as a filesystem:
`grub.cfg` does `linux (${boothd},gpt2)/bzImage`; the U-Boot script does
`load ${devtype} ${devnum}:2 ${kernel_addr_r} Image`. [verified] Hence
`CONFIG_FS_SQUASHFS=y` and `CONFIG_CMD_SQUASHFS=y` in
`buildroot-external/bootloader/uboot.config`, and GRUB needs its `squash4` module.
[verified]

Worth knowing before writing genimage config, because "kernel partition, type raw, 32 MB"
in PoC spec §5 reads as `dd` of a bare `Image` and that is not how either bootloader finds
it.

### F7 — Health checks belong inside the guest, over the serial console. [verified]

The finding that drove this was the sharpest of the `raspi4b` divergences, and it is the main
reason F4 came out the way it did. QEMU actively disables the GENET node
(`warning: bcm2711 dtc: brcm,bcm2711-genet-v5 has been disabled!`) and the guest sees **only
loopback**: `NET IFACES: [lo]`. PoC spec R2 anticipated networking being "recent and may be
incomplete" — it is not incomplete, it is **absent**, and no flag adds it (`raspi4b` has no
PCIe, so a virtio NIC cannot be attached either).

With `rpi4-qemu` dropped, both remaining targets *do* have networking — `x86-64-vm` brings up
`eth0` via `-nic user,model=virtio-net-pci` [verified], and a real Pi 4 has GENET. **So the
constraint is gone, but the conclusion should stand anyway** (D18), for three reasons that
have nothing to do with QEMU:

- It is what the on-device health check does in production (HC-1). Probing from the host
  tests a host-side approximation of the thing we actually ship.
- On real hardware, host-side probing means discovering the DUT's DHCP address from inside a
  test. labgrid is already attached to the serial console; `curl localhost` over that console
  needs no address at all.
- It keeps one test body working against both targets, which is what T8 asks for.

Settle it in WP3, before T2 is written; retrofitting it means rewriting every test (R10).

Independently, this confirms PoC spec §9.1 (bundles delivered as a disk, never fetched over
the network) — which remains the right call for `rpi4` regardless, since it separates "does
A/B update work" from "does download work".

---

## 2. Decisions resolved

Everything needed to start. Each row is a decision this plan takes so implementation is not
blocked; each is reversible, and the ones worth your explicit sign-off are repeated in §8.

| | Decision | Choice | Why |
|---|---|---|---|
| D1 | Buildroot version | submodule pinned to tag **`2026.02.3`** | `2026.02.x` is the long-term-maintained series; `2026.08` is current but unmaintained-after-next. Pin a tag, not a branch — reproducibility over freshness (design doc §13). `2026.02.3` carries `rauc`, `docker-engine`, `docker-cli` and `docker-compose` 2.38.2 [verified] |
| D2 | Compose vs. systemd `docker run` units | **compose**, as PoC spec §6 says | Buildroot 2026.02.3 installs compose v2.38.2 as a CLI plugin at `/usr/lib/docker/cli-plugins/docker-compose` [verified], so `docker compose up` works. It also keeps the PoC's app description close to `emoncms-docker`, which is the point of ARCH-4 |
| D3 | Templating for `system.conf` / `manifest.raucm` | **`envsubst`**, not `tempio` | The reference uses a Go-template binary (`tempio`) because its templates have conditionals. Ours have two variants; `envsubst` plus one `if` in shell is less machinery. genimage already does `${VAR}` substitution natively |
| D4 | Partition table | GPT, **hybrid MBR alias on Pi targets**, driven by `PARTITION_TABLE_TYPE` in board `meta` | F3 |
| D5 | Partition count | **7** — add `emonos-bootstate` (8 MB); no overlay partition | F2 |
| D6 | Slot sizes | start at PoC spec values (kernel 32 MB, rootfs 512 MB); **measure at end of WP4 and fix the number before WP8 writes it into tests** | R4. Reference uses 24 MB / 256 MB with EROFS and no Docker; we carry ~200 MB of Go binaries, so 512 MB is plausible but unproven |
| D7 | Writable paths on a read-only root | **No overlay partition.** `/var` on tmpfs; `/etc` read-only; SSH host keys and everything else persistent explicitly on `/mnt/data`; `/var/lib/docker` bind-mounted from `/mnt/data/docker` | Keeps the PoC at 7 partitions and forces persistent state onto the data partition, which is DATA-1 anyway. The forcing function is deliberate (design doc §6.2). Cost: no persistent host OS settings — acceptable for a PoC, revisit for v1 |
| D8 | `machine-id` | Persist it in the bootloader environment and pass `systemd.machine_id=` on the cmdline, with `systemd.condition-first-boot=true` when empty | Exactly what the reference does in both `grub.cfg` and `uboot-boot64.ush` [verified], ~20 lines, and it proves userspace can write the boot env — which RAUC needs regardless |
| D9 | Shared boot partition update | **Freeze shared firmware, bootloader, boot configuration and `grubenv` during PoC updates.** RAUC bundles update only inactive kernel/rootfs slot pairs. Retain an install-hook prototype only as reference for the later OS-16 variant-fragment work. | Replacing shared boot assets is not independently atomic merely because selected files are preserved. A power failure in that replacement can break both slots. This PoC proves slot updates, not safe shared-bootloader updates; report OS-11 coverage as partial. |
| D10 | rootfs compression | squashfs + **zstd** | PoC spec §3. Reference has moved to EROFS; squashfs is the simpler Buildroot option and a later swap is a defconfig change |
| D11 | Container image delivery | Pull each source by its pinned index digest at OS-image build time, tag it with a target-local immutable preload name, then `docker save` those tags into the data partition image. On first boot, verify the archive SHA-256, `docker load`, then start Compose with `pull_policy: never` using the local names. Delete the tar after a successful load. | **Revised 2026-09-25 by WP0.5 Pi evidence.** `docker save registry/name@sha256:…` followed by `docker load` loses both `RepoDigests` and tags, so a Compose service using the source digest cannot find it offline. Source-digest provenance belongs in `images.lock`; target-local tags and the verified archive SHA provide the offline binding. Peak space includes the archive plus the expanded Docker store. |
| D12 | Signing | one dev key, generated into the build dir if absent, cert appended to `/etc/rauc/keyring.pem`, key `.gitignore`d and the cert clearly marked | PoC spec §2.2 and SEC-3. Mirrors `scripts/rauc.sh` + `scripts/generate-signing-key.sh` |
| D13 | Test harness timing | **thin harness in WP1** (boot → login → assert), grown each WP | The PoC spec §11 schedules the harness last. Learnings §5.1/§6.7 argue the opposite and are right: a harness written after the fact validates nothing that was already hand-checked. Writing it thin and early costs ~a day and makes every later WP self-verifying |
| D14 | Targets and build order | **Two targets: `x86-64-vm` → `rpi4`.** `rpi4-qemu` deferred | Decided 2026-09-16 (F4). Two targets still cover two architectures, two bootloaders and two partition-table types, which is what HW-2/HW-3 need. Cost: the U-Boot A/B path is only ever demonstrated on hardware, so WP9 joins the critical path (R12) |
| D15 | QEMU's io_uring failure under memory pressure | **Mitigate in the harness — precondition + targeted retry. No host change** | Decided 2026-09-16. Keeps the workstation and any CI runner unmodified, so the suite is portable; the host-level fixes (F5) are recorded as escape hatches. Cost: occasional reruns while the box is thrashing |
| D16 | ARM container builds | **Cross-compile PHP extension artifacts on x86; use Buildx/QEMU only for non-compiling arm64 image assembly and publish the fork's multi-platform image to GitHub Container Registry.** | The official PHP base is already multi-arch, but its helpers compile extensions in the target container. Separate those operations explicitly. If cross-build ABI validation fails, use GitHub-provided native arm64 capacity; do not rely on the Pi acceptance bench for routine builds. Buildroot remains independently cross-compiled on x86. |
| D22 | Common image bases | Prefer **Debian 13 Trixie** when an existing official multi-arch variant supports the required service: use `php:8.4-apache-trixie` for web and `redis:8.10-trixie` for Redis. Keep `mariadb:11.8-noble` and `eclipse-mosquitto:2.0-openssl` as upstream exceptions. Revised 2026-09-24 from Bookworm. | Image inspection (2026-09-24) verifies Trixie amd64/arm64 variants for PHP and Redis. Trixie is the current Debian stable with the longer support window, and it is what the untagged `php:8.4-apache` already resolves to. Redis Trixie variants start at 8.4, so Redis moves off the end-of-life 7.0 line to 8.10 (8.10.2 at time of writing). Official MariaDB uses Ubuntu (or UBI) and Mosquitto 2.0 is Alpine-only. Rebuilding database or broker images just to standardise a base adds ownership without a PoC benefit. Standardise immutable pins, provenance, runtime policy and multi-architecture support across all four. |
| D17 | Data-partition durability defaults | ext4 mounted `noatime,errors=remount-ro` with normal kernel/ext4 writeback defaults; RedisBuffer disabled | The write-load audit's `commit=600` recommendation is modelled and extends data-loss exposure. Endurance tuning is outside this PoC. The PoC should prefer the more durable default while testing OS-update recovery. |
| D18 | Where health checks execute | **Autonomous health service runs inside the guest. Tests invoke equivalent checks through the serial console, never host-side HTTP.** | This exercises the shipped HC-1 path, avoids DHCP discovery on the Pi, and preserves one target-agnostic test body. The health service must not depend on Compose starting successfully. |
| D19 | Broken-v2 injection | A **slot-local systemd unit fault** prevents the application from starting while leaving persisted Compose, preloaded images, data, RAUC and the health monitor intact | Changing the data-resident Compose file would survive OS rollback and falsely make recovery fail. A boot failure is also too easy; the guest must boot, time out in health checking, and select v1 itself. |
| D20 | Trial boot count | **One** attempt per newly installed slot | Product OS-15 requires one trial boot. The bootloader still records validity and remaining attempts, but an uncommitted trial must fall back at the next reset. |
| D21 | Interrupted-install evidence | Trigger cuts from observed inactive-slot write progress; preserve disk, boot-state and firmware-variable state; record cut phase and verify the active payload | An arbitrary sleep can miss the write entirely. Snapshot restoration or graceful shutdown would invalidate T7. |
| D23 | MariaDB PoC baseline | **`mariadb:11.8-noble`**, pinned by immutable multi-architecture digest and held fixed for all PoC OS variants | The official image is Ubuntu 24.04 and transfers at about 102 MB compressed on arm64. This is a fresh-install compatibility choice, not an engine upgrade. If WP3 identifies a concrete emoncms compatibility failure, revert only to the prior pinned `mariadb:11.0-jammy` image and record the failure and new digest. |

---

## 3. Repository layout

Per PoC spec §10, with F2/F3/D5 folded in. `board/` is two levels deep so the two Pi
targets can share files, as the reference does.

```
emonos/
├── .gitignore                          # output/, *.pem key material, buildroot/dl
├── Makefile                            # make emonos_x86_64_vm  → defconfig + build
├── README.md
├── buildroot/                          # submodule, tag 2026.02.3            (D1)
├── buildroot-external/
│   ├── external.desc                   # name: EMONOS
│   ├── external.mk
│   ├── Config.in
│   ├── meta                            # VERSION_MAJOR/MINOR/SUFFIX, EMONOS_ID
│   ├── configs/
│   │   ├── emonos_x86_64_vm_defconfig
│   │   └── emonos_rpi4_defconfig
│   ├── board/
│   │   ├── pc/
│   │   │   ├── grub.cfg                # A/B selector over grubenv          (~60 lines)
│   │   │   └── x86-64-vm/{meta,cmdline.txt,kernel.config,emonos-hook.sh}
│   │   └── raspberrypi/
│   │       ├── config.txt              # kernel=u-boot.bin, enable_uart=1
│   │       ├── uboot-boot64.ush        # A/B selector over the bootstate env
│   │       ├── uboot.config
│   │       └── rpi4/{meta,kernel.config,emonos-hook.sh}
│   ├── genimage/                       # shared layout, one file per variant  (D4, D5)
│   │   ├── genimage.cfg
│   │   ├── images-os.cfg
│   │   ├── hdimage-gpt.cfg  hdimage-hybrid.cfg
│   │   └── partitions-os.cfg
│   ├── ota/
│   │   ├── system.conf.in              # envsubst                            (D3)
│   │   ├── manifest.raucm.in
│   │   └── rauc-hook                   # retained for later shared-boot work (D9)
│   ├── rootfs-overlay/
│   │   └── usr/
│   │       ├── lib/systemd/system/     # mnt-data.mount, var-lib-docker.mount,
│   │       │                           # emonos-first-boot, emonos-app,
│   │       │                           # emonos-health, emonos-persist
│   │       └── libexec/emonos/         # first-boot, health-check, persist scripts
│   ├── scripts/
│   │   ├── post-build.sh  post-image.sh
│   │   ├── hdd-image.sh                # genimage driver + sizes
│   │   ├── rauc.sh                     # keys, keyring, system.conf, fw_env.config
│   │   ├── boards.sh                   # enumerate board/*/*/meta            (HW-5)
│   │   └── generate-signing-key.sh
│   └── keys/README.md                  # "DEV KEY ONLY" — key.pem is gitignored
├── app/
│   ├── docker-compose.yml              # digest-pinned                       (F1)
│   ├── images.lock                     # image → digest, both architectures
│   └── publish-emoncms.sh              # cross-build artifacts + Buildx GHCR publish (D16)
└── tests/
    ├── requirements.txt                # labgrid, pytest-dependency, pytest-timeout
    ├── run.sh                          # ./run.sh <target> [pytest args]     (T8)
    ├── targets/{x86-64-vm,rpi4}.yaml     # rpi4-qemu.yaml if it is ever re-added
    ├── conftest.py  strategy.py
    └── test_{boot,data,slots,update,rollback,powercut}.py
```

**Two rules from the first commit** (PoC spec §10), both mechanically checkable:

- **HW-2 — nothing target-specific outside `board/`.** Add a CI-able grep in
  `scripts/check-hw2.sh` for `rpi`, `bcm`, `x86`, `grub`, `uboot` outside `board/` and
  `configs/`. The two targets differ in architecture, bootloader and partition table, so a
  violation shows up immediately; a script makes it show up on the same day. With only two
  targets this check matters *more*, not less — it is now the main thing standing between the
  tree and a hidden x86 assumption.
- **Tests cite requirement IDs** in their docstrings, so product spec §11 can be filled in
  rather than reconstructed. `pytest --collect-only` then generates the coverage table.

---

## 4. Work packages

Each WP ends in a system that boots and a test that passes. Verification is stated as a
command, not an intention.

### WP0 — Retire the risks that can invalidate the plan (~1 week)

Nothing is built here. Every task either passes or changes the plan.

| Task | Retires | Status |
|---|---|---|
| 0.1 Get QEMU starting reliably | **F5** | **DONE, diagnosis corrected.** Not an rlimit; host memory pressure. Mitigation settled in the harness, host untouched (D15, §8 item 4) |
| 0.2 Boot a stock aarch64 kernel on `raspi4b` — `-kernel` + `-dtb`, serial to stdio | **R1** | **DONE — PASS, then target dropped.** Real Pi 4 kernel, 4 CPUs at EL2, aarch64 userspace, clean shutdown. Feasible but too divergent to be worth carrying (F4) |
| 0.3 Probe `raspi4b` networking (GENET) | **R2** | **DONE — no ethernet exists.** The decisive divergence; also the origin of D18 (F7) |
| 0.4 Create a multi-platform Buildx builder and register pinned binfmt emulators; cross-build arm64 PHP extension artifacts against the exact official PHP 8.4 ABI; assemble/publish the fork's multi-platform image to GitHub Container Registry; validate on Pi | **R6** | **DONE 2026-09-24.** Extensions are cross-compiled in CI and the multi-platform image is published to GHCR by manual dispatch. `php -m`, `ldd` and a full stack start pass on the Pi (F1 resolution note). Work continues on the fork (PR #3); the upstream PR (emoncms/emoncms-docker#58) was closed 2026-09-24 and will be re-proposed once settled. Neither blocks the PoC. |
| 0.5 Pull by digest, `docker save`, `docker load` into a clean target store, then start Compose with registry access disabled for all images on both architectures | **R5** | **DONE 2026-09-25 — design corrected.** Pulling source index digests then saving them directly produces a 1.2 GB arm64 tar, but `docker load` restores untagged image IDs, not source digest references. After applying target-local immutable tags, the Pi tar loaded into a fresh store in 93 s and the full four-service stack became healthy with the Pi's default route removed and `pull_policy: never`. Archive SHA-256: `7081961c41164c9d85d3945dedd4995a7b0e85b28a0a514a5ab8729bb13e2878`. The gated `test_offline_preload` repeated the source-pull → local-tag → archive → empty-store → load → no-route Compose test for all four amd64 images in `x86-64-vm`; it passes with `EMONOS_QEMU_MEMORY=8G`. Store: 1.26 GB images; archive plus store peak: at least 2.46 GB before volumes. **Set the WP4 PoC data-partition minimum to 4 GB**; it provides ~1.4 GB headroom above the 2.62 GB archive/store/fresh-volume peak. |
| 0.6 Clone the reference tree at `42ea0f607`; read the 12 files in Appendix A | — | **DONE.** Tree read; Appendix A is the map |
| **0.7 Confirm the Pi 4 bench** — board, SD cards, USB-serial on the GPIO UART, scriptable switchable power | **R12** | **Partial.** The board, SD card, USB-serial console and Ethernet (DHCP, DNS and registry access) all work. Two findings: the host has three identical CH340 adapters, so the console must be addressed by `/dev/serial/by-path/`, not `by-id` (Appendix B); and the Pi logs `Undervoltage detected!` under load, so the supply needs replacing. **Switchable power is still outstanding.** |

0.2 and 0.3 both completed; the emulated Pi target was then dropped on value grounds rather
than feasibility (F4, D14). **WP0 is no longer a gate on starting WP1** — 0.4 and 0.5 are
measurements that inform sizing (R4, R5) and the arm64 image (R6), and both must complete
before WP3 depends on them.

0.7 is new, and it is the one WP0 task with a lead time measured in shipping rather than
hours: with no emulated Pi, T1 and T4–T7 on `rpi4` cannot be demonstrated without a bench, so
it needs ordering now rather than discovering in WP9 (R12). Switchable power is the part most
easily forgotten — T7 is a power cut, and it has to be scriptable.

### WP1 — Skeleton, board abstraction, one target to a shell (~1 week) → **T1 (partial)**

1. `git init`; Buildroot submodule at `2026.02.3`; `external.desc`, `external.mk`,
   `Config.in`, top-level `Makefile` so `make emonos_x86_64_vm` works.
2. `board/pc/x86-64-vm/meta` with exactly the HW-1 fields:
   `BOARD_ID, BOARD_NAME, ARCH, BOOTLOADER, KERNEL_FILE, PARTITION_TABLE_TYPE, BOOT_SIZE, HW_PROFILE`.
3. `emonos_x86_64_vm_defconfig`: `BR2_x86_64`, systemd, glibc, GRUB-EFI, `BR2_TARGET_ROOTFS_EXT2`
   (squashfs comes in WP4 — one change at a time), dropbear or openssh, `bzImage`.
4. `scripts/boards.sh` enumerating `board/*/*/meta` → JSON. HW-5 satisfied on day one,
   costs ten lines, and is what a CI matrix will consume later.
5. **Thin labgrid harness** (D13): `tests/targets/x86-64-vm.yaml` with `QEMUDriver` +
   `ShellDriver` + a `QEMUShellStrategy` — copy the pattern from the reference's
   `tests/qemu_shell_strategy.py` and `tests/qemu-strategy.yaml` [verified]. The QEMU
   arguments for `x86-64-vm` are already verified and recorded in Appendix B — use them
   rather than rediscovering them. Add a free-memory precondition and an
   `ENOMEM` retry to the strategy from the start (R7). One test: boots, logs in, `uname -a`.

**Verify:** `make emonos_x86_64_vm && tests/run.sh x86-64-vm -k test_boot` — green.

### WP2 — Docker on a writable root, both targets (~1 week) → **T1**

1. Add `docker-engine`, `docker-cli`, `containerd`, `runc`, `docker-compose`, `libseccomp`,
   `iptables`, `ca-certificates`, `e2fsprogs`, `dosfstools`, `util-linux`.
2. Kernel fragment per target: namespaces, cgroup v2, overlayfs, netfilter, bridge, veth,
   seccomp. Keep it as `board/*/kernel.config` so HW-2 holds.
3. Add `board/raspberrypi/rpi4/` + `emonos_rpi4_defconfig`, cross-compiled on the x86 host
   and verified on real hardware.
4. Extend the harness: `docker run --rm hello-world` on each target.

**Verify:** two builds from one tree, one command each; `hello-world` passes on both.
That is **T1**, and it is the first real test of HW-3.

> Do not skip the `hello-world` check. A broken Docker is far harder to diagnose after WP4
> makes the root read-only and compressed (design doc §5).

### WP3 — emoncms stack on a writable root (~1 week)

1. `app/docker-compose.yml` derived from `emoncms-docker`'s, with `pull_policy: never` and
   target-local immutable preload names. `app/images.lock` records every source index
   **digest** for both architectures, its target-local tag and the verified preload archive
   SHA-256 (D11). `MARIADB_AUTO_UPGRADE` is **removed**
   (learnings §7.3 layer 3 — do not carry a known hazard into a new design), MariaDB as its
   own container (APP-9). Use the fork's GHCR web image from `php:8.4-apache-trixie` and
   the official `redis:8.10-trixie` variant (D22). Use the fresh-install baseline
   `mariadb:11.8-noble`, held fixed across every OS test artifact (D23), and
   `eclipse-mosquitto:2.0-openssl` as documented D22 exceptions rather than rebuilding them
   onto a forced common distro base.
2. `emonos-app.service`: `docker compose up`, `After=` the data mount and `docker.service`,
   generous `TimeoutStopSec` for a clean database shutdown (design doc §11).
3. First-boot `docker load` of the preloaded tars (D11).
4. Bootstrap a local PoC account/API key, create a known feed and samples, and define an
   authenticated feed-list success response. Confirm that it reaches MariaDB rather than
   accepting a login page, an error-shaped JSON response, or a cache-only response.
5. Harness: execute equivalent `curl` checks **inside the guest over the serial console**,
   not from the host (D18). Include bounded per-request timeouts and run with registry access
   disabled after the preload test; retrofitting this after T2 is written means rewriting
   every test (R10).

**Verify:** the health-check content of PoC spec §7 (a) and (b) passes on both targets,
on a *writable* root. **This retires R3 before immutability is added** — the sequencing the
PoC spec §9 asks for, and product spec §13 stage 3.

**Pi 4 stack trial, 2026-09-24.** A hand-written compose file ran on the `rpi4` image, with
`/var/lib/docker` on a temporary 5 GB tmpfs. The stack was the four images D22 selects, with
index digests as below; `app/images.lock` should start from these.

| Service | Image | Index digest |
|---|---|---|
| web | `ghcr.io/jeremypoulter/emoncms` | `sha256:2686f3c0631ecc2e9951a69a6b601beb53547688c03f5013e388f57b786fd099` |
| db | `mariadb:11.8-noble` | `sha256:79d59758afc91b89b120b0a8904d637f5a3b3e1c4900f29b740d6d46c72fef68` |
| redis | `redis:8.10-trixie` | `sha256:718f745deb7dfefeac6eed7041fc7ec9476b50e61b247932682457c41adafa0e` |
| mqtt | `eclipse-mosquitto:2.0-openssl` | `sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21` |

The web pin is **held at the Pi-tested `2686f3c0…`**, which is emoncms 11.18.0 on PHP 8.4.25.
The first `master` publish after fork PR #3 merged (2026-09-24) produced
`sha256:1ded4d5dcc40159d531487b403fda64bccde2ec19487c4351bb6cda51b63c921`, which is emoncms
11.19.2 on PHP 8.4.26, built from `88f1ed2`. It also drops the fork's local `service-runner.py` in favour of upstream's (fork PR #2). Both architectures load all four extensions under local emulation,
but it has not run on the Pi. Decide whether to move the pin when WP3 writes
`app/images.lock`, and re-run the Pi stack trial before adopting it.

All four containers became healthy. emoncms created its schema in MariaDB 11.8.9 on first
start, and `GET /` returned 200. Register, login, HTTP input and feed creation all worked.
An input processlist was set to log to a PHPFina feed. Values published over MQTT with
php-mosquitto then passed through `emoncms_mqtt`, the Redis buffer and `feedwriter` into
`phpfina/1.dat`. Findings that step 1–5 must absorb:

- **MariaDB health check.** The shared env file sets `MYSQL_HOST=db`. MariaDB's
  `healthcheck.sh` reads that and then fails with `Access denied for user 'healthcheck'`.
  The db service must set `MYSQL_HOST=localhost`, as upstream's compose file does, or use
  separate env files for db and web.
- **No `curl` on the host OS.** The step 5 checks need `curl` in the Buildroot image (it
  also brings a real `ip`; BusyBox's lacks `-br`), or they must run with
  `docker compose exec web curl …`. The second option breaks D18's rule that health checks
  must not depend on the stack, so add `curl`.
- **PHP output in JSON responses.** Under PHP 8.4, `user/register.json` prepends a
  `Deprecated: strlen()` notice and header warnings from
  `Modules/user/user_model.php:1284` to its JSON. Step 4's parsing must tolerate this, and
  `display_errors` should be off in the image's `php.ini`.
- **Processlist API (emoncms 11.18.0).** `input/process/set.json` takes `inputid` in the
  query string and `processlist` as a POST body in JSON form, e.g.
  `[{"fn":"process__log_to_feed","args":[<feedid>]}]`. The legacy `1:<feedid>` string is
  rejected. Getting this wrong only returns `false`.
- **Disk.** The current 512 MB root cannot hold the 823 MB unpacked web image. Every trial
  before WP4 needs `/var/lib/docker` on tmpfs or a scratch partition (R4, R5).

### WP4 — A/B layout, read-only root, data partition (~1–2 weeks) → **T2, T3**

1. `genimage/partitions-os.cfg` — the seven partitions of D5/F2, sizes from `meta` +
   `hdd-image.sh`. `kernel.img` built as a squashfs (F6).
2. `BR2_TARGET_ROOTFS_SQUASHFS` + zstd; drop ext2.
3. D7/D17 in full: tmpfs `/var`, `mnt-data.mount` **by label** (OS-4) with
   `noatime,errors=remount-ro` and normal writeback defaults, `var-lib-docker.mount` bind,
   `emonos-first-boot.service` creating the
   directory skeleton, SSH host keys on `/mnt/data`.
4. `emonos-persist.service` for D8 (`machine-id` into the boot env).
5. Both rootfs slots populated at flash time, but only slot A marked OK in the boot env —
   the reference's `haos-hook.sh` seeds `A_OK=1` and leaves `B_OK` unset [verified].
6. **Measure the rootfs.** Fix the slot size now (D6, R4).

**Verify:** **T2** (each target boots and serves `:80` from a read-only squashfs root) and
**T3** (create a feed via the API, reboot, feed still there). This is partial evidence for
DATA-2; it does not prove state survives a full re-flash with the data partition retained.

### WP5 — RAUC configuration and bundles (~1–2 weeks) → **T4**

1. Packages: `rauc`, `rauc-service` (D-Bus — the future supervisor's interface), host `rauc`.
2. `scripts/rauc.sh`: generate the dev key if absent, append the cert to
   `/etc/rauc/keyring.pem`, render `system.conf` from `ota/system.conf.in`, and on U-Boot
   targets write `/etc/fw_env.config` pointing at `emonos-bootstate` (F2).
3. `system.conf` slot map, mirroring the reference structure [verified]:
   `boot.0` (vfat, shared, `allow-mounted=true`), `kernel.0`/`kernel.1` (raw, `bootname=A`/`B`),
   `rootfs.0`/`rootfs.1` (raw, `parent=kernel.0`/`kernel.1`). `bootname` is on the *kernel*
   slot, not the rootfs — the rootfs hangs off it. `statusfile` on the boot partition.
4. Bootloader glue, both native RAUC backends, no custom script (PoC spec §3):
   - **x86:** `bootloader=grub`, `grubenv=/mnt/boot/EFI/BOOT/grubenv`; `board/pc/grub.cfg`
      implements `ORDER`/`A_OK`/`A_TRY` with one trial attempt, plus two rescue entries (OS-9 for free).
   - **rpi4:** `bootloader=uboot`; `uboot-boot64.ush` reads/writes the raw bootstate env and
     decrements `BOOT_A_LEFT`/`BOOT_B_LEFT`.
5. `ota/manifest.raucm.in` — `format=verity`, images `kernel.img` and `rootfs.img` only;
   `install-check` hook asserting `compatible`. Keep shared `boot.vfat` factory-installed and
   outside bundle updates (D9).
6. `post-image.sh` builds the bundle **from the same artefacts as the disk image**. If the
   two ever diverge, a shipped update behaves differently from a fresh flash.

**Verify:** **T4** — `rauc status` reports both slots with correct `bootname`, state and
version on both targets. `rauc info` on the bundle verifies against the baked keyring.

> With `rpi4-qemu` gone, the `uboot` half of step 4 is only ever exercised on the bench
> (R12). Write it in WP5 with the rest, but treat it as unverified until WP9 runs.

### WP6 — The update round trip (~1 week) → **T5**

1. Build v2 with a visibly different `/etc/os-release` version (PoC spec §8).
2. Deliver it as a second disk / a file on the data partition (PoC spec §9.1) — no network.
3. `rauc install` → reboot → assert the other slot and the new version, feed intact.
4. Harness: `expect` on the bootloader's slot announcement so the test knows which slot came
   up rather than inferring it. The reference expects a literal `Booting \`Slot ` string
   [verified] — print an equivalent from both bootloader configs.

**Verify:** **T5**.

### WP7 — Health check, commit, rollback (~1–2 weeks) → **T6, T7**

1. `emonos-health.service` → `/usr/libexec/emonos/health-check`: start independently of a
   successful Compose start and poll for no more than five monotonic minutes. Require bounded
   HTTP success plus the authenticated, expected-shape feed-list response from WP3; reject
   login pages, error JSON and cache-only success. On success, `rauc status mark-good`; treat
   a mark-good error as failure. On failure, persist version, slot, elapsed time and reason to
   `/mnt/data` (HC-3), then reboot.
2. Watchdog (`RuntimeWatchdogSec=`) for the slot that hangs before the check runs (OS-10).
3. **The broken v2**: fail the *health check*, not the boot — the PoC spec is explicit that a
   build which cannot boot is the easier case and tests less. Use the slot-local application
   service fault defined by D19; do not alter the persisted Compose file, images or database.
4. Power-cut test: begin `rauc install`, detect inactive-slot write progress, then cut power
   and restart the same disk, boot-state and firmware-variable state — `kill -9` on QEMU for
   `x86-64-vm`, the switchable PDU or relay for `rpi4` (WP0.7). Do not use graceful shutdown or
   snapshot restore. Record the interrupted phase and verify the previously active payload,
   slot selection and durable feed fixture after recovery (D21).

**Verify:** **T6** (broken v2 → returns to v1 unattended, feed intact) and **T7**
(power cut mid-install → still boots the previous slot). **T6 is the PoC** (PoC spec §8).

### WP8 — One harness, all targets, all tests (~1 week) → **T8**

1. `tests/run.sh <target>` selecting `tests/targets/<target>.yaml`; T2–T7 target-agnostic.
2. JUnit output and `--lg-log`, so CI can consume it without changes (TEST-7 seed).
3. Requirement-ID coverage table generated from test docstrings.

**Verify:** **T8** — `tests/run.sh x86-64-vm` and `tests/run.sh rpi4` both green, same tests.
Two targets is a weaker test of target-agnosticism than three, but it is still the test:
different architecture, different bootloader, different partition table, one test body.

### WP9 — Real hardware (~1 week, on the critical path from WP2 onward)

`tests/targets/rpi4.yaml` swaps `QEMUDriver` for a serial console plus switchable power
(a network-controlled PDU or a relay) for the power-cut test. This is the payoff for choosing
labgrid (learnings §6.4).

**D14 changed this work package's character.** In plan v1.1, with `rpi4-qemu` carrying the
Pi path in emulation, WP9 was a few days of confirmation that overlapped everything else.
It is now the *only* place the aarch64 build, the U-Boot A/B path, the hybrid MBR table and
the Pi's power-cut behaviour are ever demonstrated — **T1 and T4–T7 on `rpi4` do not exist
without it** (R12). Consequences:

- The bench must be procured and working before WP2 needs it, not before WP9 — hence
  task 0.7.
- Every WP from 2 onward now has a hardware leg that cannot be run from a laptop. Budget for
  the Pi build lagging the x86 build by a cycle rather than tracking it.
- WP9 also closes the fidelity gaps F4 lists — firmware stage, `config.txt`, EEPROM boot
  order — which the emulated target never covered anyway. That part is unchanged.

---

## 5. Risk register

R1–R5 are the PoC spec's; R6 onward are new. R1 and R2 were retired by WP0's probes; R2 and
R11 are additionally moot under D14, since both were properties of a target the PoC no longer
builds.

| | Risk | Severity | Retire in | Action |
|---|---|---|---|---|
| ~~**R1**~~ | ~~`raspi4b` too unfaithful to be a proxy~~ | — | — | **RETIRED 2026-09-16.** Boots a real Pi 4 kernel to aarch64 userspace on 4 CPUs. Fidelity limits are known and bounded (F4) |
| ~~**R2**~~ | ~~`raspi4b` GENET networking incomplete~~ | — | — | **RETIRED 2026-09-16 — worse than assumed: no ethernet at all.** Both remaining targets have working networking, so it is now moot as well as retired; its legacy is D18 |
| **R3** | Docker needs writable paths not yet identified | rework in WP4 | WP3 | Run the stack on a writable root first |
| **R4** | 512 MB slots may not fit Docker | repartition | WP4 | Measure; fix the number before WP8. **2026-09-24:** the `rpi4` root with Docker installed uses 355 MB of 488 MB, leaving 97 MB. Images must live on the data partition, never on the slot |
| ~~**R5**~~ | ~~Preloaded images may not fit the data partition~~ | — | — | **RETIRED 2026-09-25.** Pi and x86 clean-store offline preload tests pass using D11's local immutable tags. Docker archive 1.2 GB, expanded image store 1.26 GB, first-start volumes 162 MB: ~2.62 GB peak. Docker must be data-partition resident; WP4's PoC minimum is 4 GB. Do not assume digest names survive `docker save` |
| ~~**R6**~~ | ~~No arm64 `emoncms` image~~ | — | — | **RETIRED 2026-09-24.** Cross-compiled extensions and a multi-platform image in GHCR, validated on the Pi 4 (F1 resolution note). The native arm64 runner fallback was not needed |
| **R7** | **QEMU intermittently fails to start under host memory pressure** [verified] | flaky CI; misleading failures | WP1 (harness) | **Harness-side only, by decision (D15): no host change.** A `MemAvailable` precondition plus a retry matched to the literal `Failed to initialize io_uring`, max 3 attempts. Never blanket-retry a boot failure |
| **R8** | Read-only `/etc` without an overlay (D7) may break something not yet found — sshd, systemd, Docker | rework, adds a partition | WP4 | If it bites, add the overlay partition the reference has. Known escape hatch, not a dead end |
| **R9** | Compose v2 under Buildroot on aarch64 is not a path the reference exercises (it uses systemd units per container) | fall back to `docker run` units | WP2 | `docker compose version` on both targets in WP2, before WP3 depends on it. Note this is now an aarch64 claim testable **only on the bench** (R12). **2026-09-24:** Docker 28.3.3 and Compose 2.38.2 on `rpi4` ran the four-service stack with health-gated `depends_on` and `up --wait` |
| **R10** | Host-side vs in-guest health checks diverge between targets, so T2–T7 are not actually one test suite | breaks T8; late rework | WP3 | Settle on in-guest checks over the serial console before T2 is written (D18) |
| ~~**R11**~~ | ~~`rpi4-qemu` sees only ~960 MB and cannot use KVM~~ | — | — | **MOOT 2026-09-16 (D14).** A real Pi 4 has 4–8 GB and runs at native speed. The 5-minute health-check budget of PoC spec §7 is no longer under pressure from emulation |
| **R12** | **The Pi bench is a single point of failure.** With `rpi4-qemu` dropped, one board, one serial adapter and one switchable outlet are the only route to T1 and T4–T7 on `rpi4`, and the U-Boot A/B path is never exercised anywhere else — including in CI once the harness is wired up | **T1, T4–T7 on `rpi4` cannot be demonstrated at all**; U-Boot bugs surface late, on the target that matters most | WP0.7, then WP9 | Order the bench in WP0 (0.7), not WP9. Buy two boards and two SD cards — the marginal cost is trivial against a week of blocked work. Keep the verified `raspi4b` recipe in Appendix B so the emulated target can be re-added as a CI smoke test if the bench becomes a bottleneck |

---

## 6. Schedule

| WP | Work | Cumulative |
|---|---|---|
| WP0 | Risk retirement — **QEMU probes done (R1, R2, F5 corrected); R5 retired by offline preload tests; R6 retired 2026-09-24. Only the bench PSU and switchable power part of 0.7 remain** | hardware lead time |
| WP1 | Skeleton, board abstraction, x86 to a shell, thin harness | 2 wk |
| WP2 | Docker, both targets — **T1** | 3 wk |
| WP3 | emoncms on a writable root | 4 wk |
| WP4 | A/B, squashfs, data partition — **T2, T3** | 5.5 wk |
| WP5 | RAUC, bundles — **T4** | 7 wk |
| WP6 | Update round trip — **T5** | 8 wk |
| WP7 | Health check, rollback, power cut — **T6, T7** | 9.5 wk |
| WP8 | Harness — **T8** | 10 wk |
| WP9 | Real hardware — the `rpi4` half of T1 and T4–T7 | runs alongside WP2–WP8, no longer additive |

**8–10 weeks for one person**, against the PoC spec's 6–9. The difference is WP0 (which the
spec allows for) and R6 — an arm64 image nobody has built yet. WP4 onward is where the
unknowns live; WP1–WP3 are well-trodden.

**Dropping `rpi4-qemu` does not shorten this.** It removes one build configuration and one
harness target file — under half a week of the total — but it moves WP9 from a comfortable
overlap to a hard dependency running in parallel with WP2 onward. The realistic effect is
that the range stays 8–10 weeks and its *shape* changes: less emulator work, more bench work,
and a schedule that now depends on hardware arriving on time (R12). If the bench slips, the
critical path slips with it, which was not true in v1.1.

---

## 7. What this plan does not resolve

Beyond PoC spec §12, and stated so the plan is not over-read:

- **Whether the reference's choices are right for EmonOS.** They are a working existence
  proof at one revision, and copying a validated combination is cheaper than deriving one.
  Where we diverge — squashfs over EROFS (D10), compose over per-container units (D2), no
  overlay partition (D7) — the divergence is deliberate and each is reversible.
- **Nothing about `[OPEN-1]`.** Pi 4 is used because it is available. If the BOM says Pi 5,
  the bootloader becomes tryboot and PoC spec §4.1's reasoning has to be re-run — but the
  board abstraction is what makes that a new `board/` directory rather than a fork.
- **Nothing about CI.** The harness is built to be CI-runnable (TEST-7); wiring it up is the
  next step (PoC spec §2.2).
- **Whether the U-Boot A/B path works anywhere but on the bench.** With `rpi4-qemu` dropped
  (D14), `bootloader=uboot`, the raw bootstate environment, the attempt counter and the hybrid
  MBR table are demonstrated on one physical Pi 4 and nowhere else. The PoC will show that
  they *work*; it will not show that they work unattended, repeatedly, in a pipeline with no
  human near the board. That is a CI question, and CI is the next step — but the answer now
  requires either hardware in the loop or re-adding the emulated target (R12).

---

## 8. Decisions needed from you

Two architectural choices remain plus the hardware inputs. The image source is resolved by
the maintainer: use the `JeremyPoulter/emoncms-docker` fork and publish its ARM image to its
GitHub Container Registry package.

1. ~~**D16 / R6 — PHP extension cross-build outcome.**~~ **Resolved 2026-09-24: the
   cross-build passes.** The arm64 `.so` files match the pinned `php:8.4-apache-trixie`
   ABI and load on the Pi, so no native arm64 runner is needed (F1 resolution note).
2. **D7 / R8 — no `/etc` overlay partition.** Accept losing persistent host OS settings in
   the PoC (cheaper, forces state onto the data partition where DATA-1 wants it), or add the
   eighth partition now and match the reference exactly?
3. **D1 — Buildroot `2026.02.3` (maintained LTS series) vs `2026.08` (newest packages).**
   This plan takes the LTS.
4. ~~**F5 / R7 — how to immunise QEMU against the io_uring failure.**~~ **Resolved
   2026-09-16: handle it in the harness, no host change** (D15, F5). The host is untouched;
   the sysctl and container routes stay documented as escape hatches. Independently of the
   PoC: 30 GB of swap in use suggests the editor/browser set is worth a restart.
5. **Repo location.** `Docs/` is currently not under version control. Does `emonos/` become a
   new repository, or a directory inside an existing one with these docs alongside?
6. **The Pi bench — needed now, not in WP9 (new, from D14/R12).** With `rpi4-qemu` dropped,
   what exists today? A Pi 4B and SD cards, a USB-serial adapter on the GPIO UART, and
   *switchable power* — a network PDU, a smart plug with a local API, or a relay — because
   T7 is a power cut and it must be scriptable. Two boards rather than one if the budget is
   indifferent. This is the one dependency the plan cannot work around by writing code.

---

## Appendix A — Reference file map

`home-assistant/operating-system` @ `42ea0f607`. These are the files to read before writing
the corresponding EmonOS file; each was read for this plan.

| Reference path | Answers |
|---|---|
| `buildroot-external/board/pc/generic-x86-64/meta` | The HW-1 field set |
| `buildroot-external/board/raspberrypi/rpi4-64/meta` | `BOOTLOADER=uboot`, `PARTITION_TABLE_TYPE=hybrid`, `BOOT_ENV_SIZE` |
| `buildroot-external/genimage/partitions-os-gpt.cfg` | The eight partitions, and their fixed partition UUIDs |
| `buildroot-external/genimage/images-os.cfg` | `kernel.img` is a squashfs (F6) |
| `buildroot-external/genimage/hdimage-{gpt,hybrid}.cfg` | How one layout serves two table types |
| `buildroot-external/board/pc/grub.cfg` | The GRUB A/B selector: `ORDER`, `*_OK`, `*_TRY`, rescue entries and `MACHINE_ID`; adapt its trial limit to D20's one attempt |
| `buildroot-external/board/raspberrypi/uboot-boot64.ush` | The U-Boot equivalent: raw bootstate read/write and `BOOT_*_LEFT` decrement; adapt its default/reset count to D20's one attempt |
| `buildroot-external/bootloader/uboot.config` | `CONFIG_ENV_IS_NOWHERE`, `CONFIG_FS_SQUASHFS`, `CONFIG_CMD_FILEENV` |
| `buildroot-external/ota/system.conf.gtpl` | The slot map; `bootname` on the kernel slot; `grubenv=` path |
| `buildroot-external/ota/manifest.raucm.gtpl` | Bundle contents and which slots carry hooks |
| `buildroot-external/ota/rauc-hook` | Reference `install_boot()` preserves `*.txt` + `grubenv`; retain as input to later OS-16 work, but do not invoke it in PoC bundles (D9) |
| `buildroot-external/scripts/rauc.sh` | Key generation, keyring assembly, `fw_env.config` (F2) |
| `buildroot-external/scripts/hdd-image.sh` | genimage invocation, size defaults, `qemu-img` conversions |
| `buildroot-external/board/pc/generic-x86-64/haos-hook.sh` | Seeding `grubenv` with `A_OK=1` and `B_OK` unset |
| `tests/qemu-strategy.yaml`, `tests/qemu_shell_strategy.py` | The labgrid target definition and strategy to copy |
| `tests/smoke_test/test_os_update.py` | The round-trip test shape: install → expect the boot banner → re-activate the shell driver through login → assert the version |

## Appendix B — Verified environment (this machine, probed 2026-09-14 and 2026-09-16)

| | |
|---|---|
| OS / CPU / RAM / disk | Ubuntu 26.04.1 LTS, 8 cores / 16 threads, 30 GB RAM + 48 GB swap, 419 GB free |
| QEMU | 10.2.1 (`1:10.2.1+ds-1ubuntu3.2`); `q35` and `raspi4b` both present |
| KVM | `/dev/kvm` present and usable — **granted by a POSIX ACL (`user:jpoulter:rw-`), not by `kvm` group membership.** A CI runner or new user will not inherit that |
| OVMF | `/usr/share/ovmf/OVMF.fd` (combined — the form the reference's labgrid config expects) and split `OVMF_{CODE,VARS}_4M.fd`. **OVMF emits nothing on `-serial`**; only the kernel console does, so `console=` in `cmdline.txt` is what the harness depends on |
| Docker | 29.7.2. The default builder exposes only amd64/386. **Since 2026-09-23** a `docker-container` Buildx builder `emonos-multiarch` exists and arm64 binfmt is registered via `tonistiigi/binfmt`. That registration does not survive a reboot and was found missing once; re-run `docker run --privileged --rm tonistiigi/binfmt --install arm64` before any local arm64 `docker run` |
| Pi 4 bench (2026-09-24) | Console at 115200 on the CH340 at `/dev/serial/by-path/pci-0000:07:00.0-usb-0:2.3.2.3.4:1.0-port0` (`ttyUSB2` today). The host has three identical CH340s with no serial numbers, so `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` points at whichever enumerated last. Do not use it. Ethernet `end0` gets DHCP on 172.16.0.0/22 and reaches the internet. The host has no route to it, so all access goes through the serial console (D18). 8 GB RAM. `Undervoltage detected!` logged under container load. No switchable power yet |
| `ulimit -l` | 8192 KB soft and hard. **Irrelevant to QEMU** — see the F5 correction |
| `kernel.io_uring_disabled` | `0`, and **left that way** (D15). Setting it to `2` does make QEMU fall back to epoll and immunise it against F5 — verified 3/3 with a temporary, restored value — but no host change is being made, so the harness handles it |
| Verified launch — `x86-64-vm` | `-machine q35 -accel kvm -cpu host -m 1G -serial mon:stdio`, `console=ttyS0,115200`; virtio disk → `vda` + all GPT partitions; `-nic user,model=virtio-net-pci` → `eth0` |
| Verified launch — `rpi4-qemu` **(target deferred by D14; recipe retained)** | `-M raspi4b -m 2G -kernel … -dtb bcm2711-rpi-4-b.dtb -drive file=…,format=raw,if=sd -serial mon:stdio`, **`earlycon=pl011,0xfe201000 console=ttyAMA0,115200`**; SD → `mmcblk1` + all GPT partitions; TCG only, no KVM. Kept deliberately: it is the cheapest way to re-add an aarch64 CI smoke test if the Pi bench becomes a bottleneck (R12) |
| Test artefacts kept | `$CLAUDE_JOB_DIR/tmp/r1/` (Pi `Image`, `bcm2711-rpi-4-b.dtb`, arm64 initramfs, GPT `sd.img`, boot logs) and `…/tmp/x86/`. Reproducible from the recipes above; not durable storage |
| Local checkouts | `../emoncms-docker` on `cross-compile-php-extensions` (fork PR #3), `../../OpenEnergyMonitor/{EmonScripts,emoncms}`; reference tree at `42ea0f607` |
| Not present | `rauc` on the host (built by Buildroot as a host tool); `labgrid` (host pip, not a Buildroot package [verified]) |

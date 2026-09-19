# Building a HAOS-style OS for Emoncms

**A learning-oriented tutorial: Buildroot + A/B partitions + RAUC + Docker on Raspberry Pi**

---

## 0. What you're actually replicating

Home Assistant OS is four ideas stacked together. It's worth naming them separately, because you will implement them one at a time:

| Idea | HAOS implementation | Your equivalent |
|---|---|---|
| Minimal, purpose-built Linux | Buildroot via `br2-external`, forked upstream | Buildroot `br2-external`, unforked |
| Immutable, redundant OS | 2× kernel + 2× rootfs (squashfs) partitions, read-only | Same |
| Atomic, verified OS update | RAUC bundles (`.raucb`), X.509 signed | Same |
| Mutable application layer | Docker on `/mnt/data`, orchestrated by Supervisor | Docker on `/mnt/data`, orchestrated by systemd |

The critical architectural insight, and the one people miss: **the OS and the application update through completely separate mechanisms.** The OS is a signed disk image swapped wholesale. Emoncms is a container pulled by tag/digest. They never touch each other. Your feed data lives on a third partition that neither mechanism writes to.

### Target partition layout

```
MBR (not GPT — see §6.1)

p1  autoboot     FAT16    16 MB    autoboot.txt only, the slot selector
p2  boot_a       FAT32   128 MB    config.txt, cmdline.txt, kernel, dtbs, firmware
p3  boot_b       FAT32   128 MB    (same, slot B)
p4  extended     ----     ----     container for logical partitions below
p5  rootfs_a     squashfs 512 MB   read-only OS slot A
p6  rootfs_b     squashfs 512 MB   read-only OS slot B
p7  data         ext4     rest     /mnt/data — Docker, emoncms feeds, MariaDB, config
```

HAOS also carries a separate small `overlay` partition for OS-level settings (network config etc.). For a first concept, fold that into `data` and add it later if you want the separation.

### Boot flow

```
EEPROM bootloader
      │
      ├─ reads p1:/autoboot.txt
      │     [all]     tryboot_a_b=1, boot_partition=2   ← the committed slot
      │     [tryboot]               boot_partition=3    ← the slot being tested
      │
      ├─ tryboot flag set?  ── yes ──▶ boot p3
      │                      └─ no ──▶ boot p2
      │
      ▼
config.txt + kernel from chosen boot partition
      │
      ▼
cmdline.txt: root=/dev/mmcblk0p5 ro rootfstype=squashfs   (or p6 for slot B)
      │
      ▼
systemd ──▶ mount /mnt/data ──▶ dockerd ──▶ emoncms stack
                                     │
                                     ▼
                            health check passes
                                     │
                                     ▼
                        rauc status mark-good  ──▶ autoboot.txt [all] rewritten to 3
```

Note that boot slot and rootfs slot are **paired**. The Pi firmware only knows about the FAT partition; the kernel and its `cmdline.txt` inside it point at the matching rootfs. You test kernel+rootfs together, never independently.

---

## 1. Decide these three things first

### 1.1 Fork HAOS, or start from vanilla Buildroot?

**Start vanilla.** The HAOS repo is excellent reference material but it's deeply entangled with the Supervisor, the HA CLI, their PKI, their board metadata scheme, and ~40 board configs. Stripping it out is more work than building up, and you'll learn less.

Instead: clone HAOS alongside your project and read it constantly. These files are the ones you'll return to:

```
buildroot-external/configs/rpi4_64_defconfig     # what a real config looks like
buildroot-external/board/raspberrypi/            # genimage, boot files, post-image
buildroot-external/ota/manifest.raucm.gtpl       # RAUC manifest template
buildroot-external/scripts/rauc.sh               # key/cert generation
buildroot-external/rootfs-overlay/usr/lib/systemd/system/   # unit patterns
```

```shell
git clone https://github.com/home-assistant/operating-system/ haos-reference
cd haos-reference && git submodule update --init
```

### 1.2 Which Pi?

**Pi 4 (4 GB or 8 GB) or CM4.** Reasons:

- The `autoboot.txt` + `tryboot` A/B mechanism lives in the EEPROM bootloader, which exists on Pi 4B and later, CM4 and later, and the Pi 400. It is **not** available on Pi 3 or Zero 2 W, which boot from `bootcode.bin` — those need U-Boot instead.
- Pi 5 works too and is what HAOS uses tryboot on, but has fewer worked examples and the NVMe/PCIe path adds variables.
- Emoncms with MariaDB wants RAM and, ideally, not an SD card (see §11).

Before anything else, update the EEPROM to a recent release. Old bootloaders have `tryboot` bugs.

### 1.3 Bootloader strategy: tryboot vs U-Boot

| | RPi firmware + tryboot | U-Boot |
|---|---|---|
| Extra bootloader to build | No | Yes |
| RAUC backend | Custom (script you write) | Built-in, mature |
| Boot attempt counter | Effectively 1 | Configurable (e.g. 3 tries) |
| Recovers from unbootable slot | No — only from a reboot | Yes |
| Works on Pi 3 / Zero 2 W | No | Yes |

**Use tryboot for the concept.** It's fewer moving parts and it's what HAOS does on Pi 5. Understand its limitation clearly: tryboot is a one-shot flag, not a counter. If the new slot boots but then hangs, a watchdog reboot returns you to the old slot (good). If the new slot never gets far enough to clear the flag and the system doesn't reboot, you're stuck (bad). A hardware watchdog is therefore not optional here — it *is* your fallback mechanism.

Graduate to U-Boot later if you want real attempt counting.

---

## 2. Prerequisites

A Linux host (Debian/Ubuntu is smoothest), Docker with privileged container support, `sudo`, and ~30 GB free disk. Buildroot builds are CPU-bound; expect 45–90 minutes for the first build and a few minutes for rebuilds.

Like HAOS, do the build inside a container so your host distro doesn't leak into the build. HAOS's `scripts/enter.sh` is a good template — borrow it. The reason theirs runs privileged is that the image assembly step mounts loopback filesystems, which rootless containers can't do. Yours will need the same.

```shell
sudo apt install -y git build-essential wget cpio unzip rsync bc \
    libssl-dev file python3 docker.io
```

---

## 3. Repository skeleton

```
emonos/
├── buildroot/                       # git submodule, upstream Buildroot
├── buildroot-external/
│   ├── external.desc
│   ├── external.mk
│   ├── Config.in
│   ├── configs/
│   │   └── emonos_rpi4_defconfig
│   ├── board/rpi4/
│   │   ├── genimage.cfg
│   │   ├── boot/
│   │   │   ├── autoboot.txt
│   │   │   ├── config.txt
│   │   │   ├── cmdline_a.txt
│   │   │   └── cmdline_b.txt
│   │   ├── post-build.sh
│   │   └── post-image.sh
│   ├── ota/
│   │   └── manifest.raucm.in
│   ├── rootfs-overlay/
│   │   ├── etc/rauc/system.conf
│   │   ├── usr/lib/rauc/rpi-bootloader-backend
│   │   └── usr/lib/systemd/system/*.service
│   └── scripts/
│       └── keys.sh
├── Makefile
└── scripts/enter.sh
```

Add Buildroot as a submodule pinned to a stable release:

```shell
mkdir emonos && cd emonos && git init
git submodule add -b 2025.02.x https://gitlab.com/buildroot.org/buildroot.git buildroot
```

**`buildroot-external/external.desc`**

```
name: EMONOS
desc: Emoncms Operating System
```

**`buildroot-external/Config.in`** — empty is fine to start; this is where custom package definitions get sourced.

**`buildroot-external/external.mk`**

```makefile
include $(sort $(wildcard $(BR2_EXTERNAL_EMONOS_PATH)/package/*/*.mk))
```

**Top-level `Makefile`** (mirrors HAOS's, so `make emonos_rpi4` works):

```makefile
BUILDROOT_DIR = $(CURDIR)/buildroot
EXTERNAL_DIR  = $(CURDIR)/buildroot-external
O ?= $(CURDIR)/output

%:
	@echo "=== Using $@_defconfig ==="
	$(MAKE) -C $(BUILDROOT_DIR) O=$(O) BR2_EXTERNAL=$(EXTERNAL_DIR) $@_defconfig
	@echo "=== Building $@ ==="
	$(MAKE) -C $(O)
```

---

## 4. Milestone 1 — a plain bootable image

Do not attempt A/B yet. Get a single-slot Pi image that boots to a shell. Everything afterward is a modification of a known-working system.

`buildroot-external/configs/emonos_rpi4_defconfig`, first pass:

```
BR2_aarch64=y
BR2_cortex_a72=y
BR2_ARM_FPU_VFPV4=y

BR2_TOOLCHAIN_BUILDROOT_GLIBC=y
BR2_TOOLCHAIN_BUILDROOT_CXX=y

BR2_INIT_SYSTEMD=y
BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_EUDEV=y
BR2_SYSTEM_DHCP="eth0"
BR2_TARGET_GENERIC_HOSTNAME="emonos"

# Kernel — Raspberry Pi fork
BR2_LINUX_KERNEL=y
BR2_LINUX_KERNEL_CUSTOM_TARBALL=y
BR2_LINUX_KERNEL_CUSTOM_TARBALL_LOCATION="$(call github,raspberrypi,linux,<PIN_A_COMMIT>)/linux-<PIN_A_COMMIT>.tar.gz"
BR2_LINUX_KERNEL_DEFCONFIG="bcm2711"
BR2_LINUX_KERNEL_IMAGE=y
BR2_LINUX_KERNEL_DTS_SUPPORT=y
BR2_LINUX_KERNEL_INTREE_DTS_NAME="broadcom/bcm2711-rpi-4-b"

# Firmware
BR2_PACKAGE_RPI_FIRMWARE=y
BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI4=y
BR2_PACKAGE_RPI_FIRMWARE_BOOT_OVERLAYS=y

BR2_TARGET_ROOTFS_EXT2=y
BR2_TARGET_ROOTFS_EXT2_4=y
```

Pin the kernel to a specific commit rather than a branch. Reproducibility matters more than freshness here, and a moving `rpi-6.x.y` branch will bite you three weeks in when a rebuild produces a different kernel.

```shell
make emonos_rpi4
```

Flash `output/images/sdcard.img` (Buildroot's default `genimage-raspberrypi4-64.cfg` produces one), confirm it boots, confirm serial console works. Get a UART cable — you will need it, and debugging boot slot switching without one is miserable.

---

## 5. Milestone 2 — Docker on the rootfs

Add to the defconfig:

```
BR2_PACKAGE_DOCKER_ENGINE=y
BR2_PACKAGE_DOCKER_CLI=y
BR2_PACKAGE_CONTAINERD=y
BR2_PACKAGE_RUNC=y
BR2_PACKAGE_LIBSECCOMP=y
BR2_PACKAGE_IPTABLES=y
BR2_PACKAGE_CA_CERTIFICATES=y
BR2_PACKAGE_E2FSPROGS=y
BR2_PACKAGE_DOSFSTOOLS=y
BR2_PACKAGE_DOSFSTOOLS_MKFS_FAT=y
BR2_PACKAGE_UTIL_LINUX_BINARIES=y
```

Docker needs a long list of kernel options that `bcm2711_defconfig` doesn't all set: namespaces, cgroup v2, overlayfs, netfilter, bridge, veth, seccomp. Run `make linux-menuconfig` once, or better, keep a fragment file:

```
BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="$(BR2_EXTERNAL_EMONOS_PATH)/board/rpi4/linux-docker.fragment"
```

Buildroot prints the required options in `make menuconfig` help for `docker-engine` — copy from there. `dockerd` will also tell you what's missing at startup; check `journalctl -u docker`.

**Do not skip this step's verification.** Boot it and run `docker run --rm hello-world` on the Pi. A rootfs with a broken Docker is much harder to diagnose once it's read-only and squashfs-compressed.

---

## 6. Milestone 3 — A/B partition layout

### 6.1 Why MBR, not GPT

HAOS prefers GPT. On the Pi with `autoboot.txt` + `tryboot`, GPT is unreliable — there are open reports of the bootloader failing to find the boot partition on tryboot with GPT partition tables, and of `boot_partition=` being ignored. Use MBR with an extended partition. You get 3 primaries for FAT plus logicals for everything else, which is exactly enough.

### 6.2 Switch rootfs to squashfs

```
# BR2_TARGET_ROOTFS_EXT2 is not set
BR2_TARGET_ROOTFS_SQUASHFS=y
BR2_TARGET_ROOTFS_SQUASHFS4_ZSTD=y
```

A read-only rootfs means `/etc`, `/var`, and `/root` need to be writable some other way. The simplest concept-stage approach is tmpfs for volatile paths plus an overlay for `/etc` backed by the data partition:

`rootfs-overlay/usr/lib/systemd/system/etc-overlay.service` — or, cleaner, do the overlay mount in an initramfs before systemd starts. HAOS uses a dedicated overlay partition and mounts `/etc` as an overlayfs early. For a first pass, tmpfs-only `/etc` overlay is acceptable and loses nothing but persistent OS settings across reboots — which is a useful forcing function, since it makes you put real configuration on the data partition where it belongs.

### 6.3 genimage

`buildroot-external/board/rpi4/genimage.cfg`:

```
image autoboot.vfat {
  vfat { label = "AUTOBOOT" }
  file autoboot.txt { image = "autoboot.txt" }
  size = 16M
}

image boot_a.vfat {
  vfat { label = "BOOT_A" }
  file config.txt   { image = "config.txt" }
  file cmdline.txt  { image = "cmdline_a.txt" }
  file Image        { image = "Image" }
  file bcm2711-rpi-4-b.dtb { image = "bcm2711-rpi-4-b.dtb" }
  file start4.elf   { image = "rpi-firmware/start4.elf" }
  file fixup4.dat   { image = "rpi-firmware/fixup4.dat" }
  file overlays     { image = "rpi-firmware/overlays" }
  size = 128M
}

image boot_b.vfat {
  vfat { label = "BOOT_B" }
  file config.txt   { image = "config.txt" }
  file cmdline.txt  { image = "cmdline_b.txt" }
  file Image        { image = "Image" }
  file bcm2711-rpi-4-b.dtb { image = "bcm2711-rpi-4-b.dtb" }
  file start4.elf   { image = "rpi-firmware/start4.elf" }
  file fixup4.dat   { image = "rpi-firmware/fixup4.dat" }
  file overlays     { image = "rpi-firmware/overlays" }
  size = 128M
}

image data.ext4 {
  ext4 { label = "emonos-data" }
  size = 2G
}

image sdcard.img {
  hdimage {
    partition-table-type = "mbr"
    extended-partition = 4
  }

  partition autoboot { image = "autoboot.vfat"; partition-type = 0xC; bootable = "true" }
  partition boot_a   { image = "boot_a.vfat";   partition-type = 0xC }
  partition boot_b   { image = "boot_b.vfat";   partition-type = 0xC }
  partition rootfs_a { image = "rootfs.squashfs"; partition-type = 0x83; size = 512M }
  partition rootfs_b { image = "rootfs.squashfs"; partition-type = 0x83; size = 512M }
  partition data     { image = "data.ext4";       partition-type = 0x83 }
}
```

Note the freshly-flashed card has **both** rootfs slots populated with the same image. HAOS ships only slot A populated; either works, but writing both means slot B is a valid fallback from the very first boot.

### 6.4 Boot files

`autoboot.txt` — this is the entire A/B mechanism, 4 lines:

```
[all]
tryboot_a_b=1
boot_partition=2

[tryboot]
boot_partition=3
```

`config.txt`:

```
arm_64bit=1
kernel=Image
enable_uart=1
dtoverlay=disable-bt
```

`dtoverlay=disable-bt` frees `/dev/ttyAMA0` for emonHub to talk to an emonPi/emonTx board. Keep `enable_uart=1` for the console during development, but then the console and emonHub fight over the port — on a Pi 4 use `miniuart-bt` instead, or accept that you drop the serial console once emonHub goes in.

`cmdline_a.txt`:

```
root=/dev/mmcblk0p5 rootfstype=squashfs ro rootwait console=serial0,115200 cgroup_enable=memory
```

`cmdline_b.txt` is identical but `root=/dev/mmcblk0p6`.

### 6.5 Verify manually before automating

Flash this, boot it, then from the Pi:

```sh
# reboot '0 tryboot'      → should come up on p3/p6
cat /proc/device-tree/chosen/bootloader/partition | xxd   # which partition booted
# reboot                  → back to p2/p5
```

If manual tryboot switching doesn't work, RAUC will not save you. Fix it here.

---

## 7. Milestone 4 — RAUC

### 7.1 Packages

```
BR2_PACKAGE_RAUC=y
BR2_PACKAGE_RAUC_NETWORK=y
BR2_PACKAGE_RAUC_SERVICE=y
BR2_PACKAGE_HOST_RAUC=y
BR2_PACKAGE_DTC=y
BR2_PACKAGE_DTC_PROGRAMS=y
BR2_PACKAGE_RPI_USERLAND=y
```

`dtc` gives you `fdtget` for reading the booted partition; `rpi-userland` gives you `vcmailbox` for setting the tryboot flag.

### 7.2 Keys

Generate a dev CA once per build directory and embed the cert in the image, exactly as HAOS does. Their `buildroot-external/scripts/rauc.sh` is worth copying wholesale — the pattern is: if `key.pem`/`cert.pem` don't exist in the output dir, create a self-signed pair; always append `cert.pem` to the image's `/etc/rauc/keyring.pem`.

```shell
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
  -keyout key.pem -out cert.pem -subj "/O=emonOS/CN=emonOS dev-1"
```

Keep `key.pem` out of the image and out of git. For production you'd want a real two-tier PKI with an offline root; for the concept, a single self-signed cert is fine — just build in the habit of treating it as a secret now.

### 7.3 `/etc/rauc/system.conf`

```ini
[system]
compatible=emonos-rpi4
bootloader=custom
data-directory=/mnt/data/rauc

[keyring]
path=/etc/rauc/keyring.pem

[handlers]
bootloader-custom-backend=/usr/lib/rauc/rpi-bootloader-backend

[slot.boot.0]
device=/dev/mmcblk0p2
type=raw
bootname=A

[slot.rootfs.0]
device=/dev/mmcblk0p5
type=raw
parent=boot.0

[slot.boot.1]
device=/dev/mmcblk0p3
type=raw
bootname=B

[slot.rootfs.1]
device=/dev/mmcblk0p6
type=raw
parent=boot.1
```

`bootname` goes on the slot the *bootloader* selects — the FAT partition — and the rootfs is declared as its child via `parent=`. This mirrors HAOS, where `kernel.0` carries `bootname: A` and `rootfs.0` hangs off it.

Slots are `raw` so RAUC just `dd`s a complete filesystem image in. Simple and robust; the cost is that the bundle carries the full partition image rather than a delta.

### 7.4 The custom bootloader backend

RAUC's custom backend is a script called with four verbs: `get-primary`, `set-primary <bootname>`, `get-state <bootname>`, `set-state <bootname> good|bad`. This is where you translate RAUC's model onto `autoboot.txt`.

`rootfs-overlay/usr/lib/rauc/rpi-bootloader-backend`:

```sh
#!/bin/sh
# Illustrative — harden before trusting it. No locking, no atomic rename.
set -e

AUTOBOOT_DEV=/dev/mmcblk0p1
MNT=/run/autoboot

mount_ab()   { mkdir -p "$MNT"; mount -t vfat "$AUTOBOOT_DEV" "$MNT"; }
umount_ab()  { sync; umount "$MNT"; }

# bootname <-> partition number
part_of()  { case "$1" in A) echo 2 ;; B) echo 3 ;; esac; }
name_of()  { case "$1" in 2) echo A ;; 3) echo B ;; esac; }

write_autoboot() {  # $1 = committed partition, $2 = tryboot partition
    printf '[all]\ntryboot_a_b=1\nboot_partition=%s\n\n[tryboot]\nboot_partition=%s\n' "$1" "$2" \
        > "$MNT/autoboot.txt"
}

committed_part() { sed -n '/^\[all\]/,/^\[/s/^boot_partition=//p' "$MNT/autoboot.txt" | head -1; }

booted_part() { fdtget -t i /sys/firmware/fdt /chosen/bootloader partition; }

case "$1" in
  get-primary)
      mount_ab; name_of "$(committed_part)"; umount_ab ;;

  set-primary)
      # Stage the new slot as the tryboot target; do NOT commit it yet.
      mount_ab
      new=$(part_of "$2"); cur=$(committed_part)
      write_autoboot "$cur" "$new"
      umount_ab
      # Arm the one-shot flag so the *next* reboot uses [tryboot].
      vcmailbox 0x00038064 4 4 1 >/dev/null ;;

  get-state)
      # RPi firmware has no boot counter; report good and let the
      # health-check service be the real arbiter.
      echo "good" ;;

  set-state)
      mount_ab
      case "$3" in
        good) p=$(part_of "$2")
              other=$([ "$p" = 2 ] && echo 3 || echo 2)
              write_autoboot "$p" "$other" ;;      # commit
        bad)  p=$(part_of "$2")
              other=$([ "$p" = 2 ] && echo 3 || echo 2)
              write_autoboot "$other" "$p" ;;      # commit the other one
      esac
      umount_ab ;;

  *) echo "unknown command: $1" >&2; exit 1 ;;
esac
```

The `vcmailbox` tag for the tryboot flag needs verifying against current firmware — check `vcmailbox` docs and the Rtone reference implementation below rather than trusting the constant above. `reboot '0 tryboot'` is the more portable way to arm it, but it reboots immediately, so it belongs in a separate helper the user invokes rather than inside `set-primary`.

**Two implementations worth reading before writing your own:**
- `Rtone/raspberrypi-firmware-rauc-bootloader-backend` on GitHub — a complete, production-minded version of exactly this script
- RAUC PR #1599 — upstream work adding native Pi firmware bootchooser support, which may make your script unnecessary

### 7.5 Bundle manifest

`buildroot-external/ota/manifest.raucm.in`:

```ini
[update]
compatible=emonos-rpi4
version=@VERSION@

[bundle]
format=verity

[image.boot]
filename=boot.vfat

[image.rootfs]
filename=rootfs.squashfs
```

The `[image.<class>]` sections name slot *classes*, not slots — RAUC picks the inactive slot of each class. `format=verity` gives you dm-verity protection of the bundle contents.

### 7.6 post-image script

Extend `post-image.sh` to build the bundle from the same artifacts that went into the disk image — this is the point HAOS makes explicitly, and it matters: if the bundle and the image are built from different inputs, you will eventually ship an update that behaves differently from a fresh flash.

```sh
BUNDLE_DIR=$BINARIES_DIR/bundle
mkdir -p "$BUNDLE_DIR"
cp "$BINARIES_DIR/boot_a.vfat"     "$BUNDLE_DIR/boot.vfat"
cp "$BINARIES_DIR/rootfs.squashfs" "$BUNDLE_DIR/rootfs.squashfs"
sed "s/@VERSION@/$EMONOS_VERSION/" "$EXTERNAL/ota/manifest.raucm.in" \
    > "$BUNDLE_DIR/manifest.raucm"

$HOST_DIR/bin/rauc bundle \
    --cert="$BINARIES_DIR/../cert.pem" \
    --key="$BINARIES_DIR/../key.pem" \
    "$BUNDLE_DIR" "$BINARIES_DIR/emonos-rpi4-$EMONOS_VERSION.raucb"
```

---

## 8. Milestone 5 — the data partition and Docker

### 8.1 Mounting

`rootfs-overlay/usr/lib/systemd/system/mnt-data.mount`:

```ini
[Unit]
Description=Emoncms data partition
Before=docker.service

[Mount]
What=/dev/disk/by-label/emonos-data
Where=/mnt/data
Type=ext4
Options=noatime

[Install]
WantedBy=local-fs.target
```

Mount by **label**, not device path. HAOS does this deliberately (`hassos-data`) and it's what makes their data-disk migration feature possible — you can move the data partition to an SSD later by relabelling, without touching the OS image.

Then bind Docker's state onto it:

```ini
# var-lib-docker.mount
[Unit]
Requires=mnt-data.mount
After=mnt-data.mount
Before=docker.service

[Mount]
What=/mnt/data/docker
Where=/var/lib/docker
Type=none
Options=bind
```

### 8.2 First-boot setup

The flashed data partition is a fixed 2 GB. A oneshot service should grow it to fill the card and create the directory skeleton:

```ini
[Unit]
ConditionPathExists=!/mnt/data/.provisioned
Requires=mnt-data.mount
After=mnt-data.mount
Before=docker.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/emonos-first-boot
RemainAfterExit=yes
```

The script: `sfdisk` to extend the last partition, `resize2fs`, `mkdir -p /mnt/data/{docker,emoncms/{data,db},rauc}`, then touch the sentinel.

### 8.3 Preloading images

Pulling ~500 MB of containers on first boot over a customer's network is a bad first impression. HAOS ships the Supervisor and plugins pre-installed on the data partition. Do the same: `docker save` your images at build time into `data.ext4`, and have first-boot `docker load` them. Costs image size, buys offline provisioning.

For the concept stage, network-pull is fine — just know it's a stopgap.

---

## 9. Milestone 6 — the Emoncms stack

### 9.1 Orchestration

HAOS has the Supervisor for this. You don't, and shouldn't build one. Two reasonable options:

**Option A — systemd units (recommended to start).** One unit per container, `Requires`/`After` chained, a shared Docker network. No extra packages, and `journalctl` gives you a single log stream. Verbose, but transparent.

**Option B — docker-compose.** Check whether your Buildroot version packages a compose you can live with (`BR2_PACKAGE_DOCKER_COMPOSE`). Fewer files, but another moving part in the rootfs.

A representative unit:

```ini
# emoncms.service
[Unit]
Description=Emoncms
Requires=docker.service emoncms-db.service emoncms-redis.service
After=docker.service emoncms-db.service emoncms-redis.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f emoncms
ExecStart=/usr/bin/docker run --rm --name emoncms \
    --network emoncms \
    -p 80:80 \
    -v /mnt/data/emoncms/data:/var/opt/emoncms \
    -v /mnt/data/emoncms/config:/etc/emoncms \
    openenergymonitor/emoncms@sha256:<DIGEST>
ExecStop=/usr/bin/docker stop emoncms

[Install]
WantedBy=multi-user.target
```

The stack you need, from `github.com/emoncms/emoncms-docker`: emoncms web, MariaDB, Redis, Mosquitto, and emonHub if you're reading from emon hardware. Read their `docker-compose.yml` and translate it.

**Pin by digest, not tag.** `:latest` in an appliance image means two units flashed a week apart run different software with no record of why.

### 9.2 emonHub and serial hardware

If this replaces an emonPi/emonBase, emonHub needs the UART:

```
--device=/dev/ttyAMA0:/dev/ttyAMA0
```

and the GPIO serial console must be off in `cmdline.txt`, or emonHub and getty will both read the port and both get corrupted data. This is a classic silent failure — data arrives intermittently mangled rather than not at all.

### 9.3 What lives where

| Path | Content | Survives OS update? |
|---|---|---|
| `/mnt/data/emoncms/data` | PHPFina/PHPTimeseries feed files | Yes |
| `/mnt/data/emoncms/db` | MariaDB — inputs, feeds, users, dashboards | Yes |
| `/mnt/data/emoncms/config` | `settings.ini`, emonhub.conf | Yes |
| `/mnt/data/docker` | Images and container state | Yes |
| `/` (squashfs) | Kernel, systemd, Docker binaries, unit files | No — replaced wholesale |

Write this table down and check every new file you add against it. The single most common way to ruin an A/B design is to let something important accumulate on the rootfs.

---

## 10. Milestone 7 — the update round trip

### 10.1 Health check and commit

Nothing about tryboot is safe until something decides the new slot is actually good. That decision is yours to define, and it should test the thing you care about — Emoncms serving requests — not merely that systemd reached `multi-user.target`.

```ini
# emonos-health.service
[Unit]
Description=Confirm boot slot health
After=emoncms.service
Requires=emoncms.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/emonos-health-check
RemainAfterExit=yes
```

```sh
#!/bin/sh
# Poll for up to 5 minutes, then commit or bail.
for i in $(seq 60); do
    if curl -fsS -o /dev/null http://localhost/ ; then
        rauc status mark-good
        logger -t emonos "boot slot committed"
        exit 0
    fi
    sleep 5
done
logger -t emonos "health check FAILED — rebooting to previous slot"# Building a HAOS-style OS for Emoncms

**A learning-oriented tutorial: Buildroot + A/B partitions + RAUC + Docker on Raspberry Pi**

---

## 0. What you're actually replicating

Home Assistant OS is four ideas stacked together. It's worth naming them separately, because you will implement them one at a time:

| Idea | HAOS implementation | Your equivalent |
|---|---|---|
| Minimal, purpose-built Linux | Buildroot via `br2-external`, forked upstream | Buildroot `br2-external`, unforked |
| Immutable, redundant OS | 2× kernel + 2× rootfs (squashfs) partitions, read-only | Same |
| Atomic, verified OS update | RAUC bundles (`.raucb`), X.509 signed | Same |
| Mutable application layer | Docker on `/mnt/data`, orchestrated by Supervisor | Docker on `/mnt/data`, orchestrated by systemd |

The critical architectural insight, and the one people miss: **the OS and the application update through completely separate mechanisms.** The OS is a signed disk image swapped wholesale. Emoncms is a container pulled by tag/digest. They never touch each other. Your feed data lives on a third partition that neither mechanism writes to.

### Target partition layout

```
MBR (not GPT — see §6.1)

p1  autoboot     FAT16    16 MB    autoboot.txt only, the slot selector
p2  boot_a       FAT32   128 MB    config.txt, cmdline.txt, kernel, dtbs, firmware
p3  boot_b       FAT32   128 MB    (same, slot B)
p4  extended     ----     ----     container for logical partitions below
p5  rootfs_a     squashfs 512 MB   read-only OS slot A
p6  rootfs_b     squashfs 512 MB   read-only OS slot B
p7  data         ext4     rest     /mnt/data — Docker, emoncms feeds, MariaDB, config
```

HAOS also carries a separate small `overlay` partition for OS-level settings (network config etc.). For a first concept, fold that into `data` and add it later if you want the separation.

### Boot flow

```
EEPROM bootloader
      │
      ├─ reads p1:/autoboot.txt
      │     [all]     tryboot_a_b=1, boot_partition=2   ← the committed slot
      │     [tryboot]               boot_partition=3    ← the slot being tested
      │
      ├─ tryboot flag set?  ── yes ──▶ boot p3
      │                      └─ no ──▶ boot p2
      │
      ▼
config.txt + kernel from chosen boot partition
      │
      ▼
cmdline.txt: root=/dev/mmcblk0p5 ro rootfstype=squashfs   (or p6 for slot B)
      │
      ▼
systemd ──▶ mount /mnt/data ──▶ dockerd ──▶ emoncms stack
                                     │
                                     ▼
                            health check passes
                                     │
                                     ▼
                        rauc status mark-good  ──▶ autoboot.txt [all] rewritten to 3
```

Note that boot slot and rootfs slot are **paired**. The Pi firmware only knows about the FAT partition; the kernel and its `cmdline.txt` inside it point at the matching rootfs. You test kernel+rootfs together, never independently.

---

## 1. Decide these three things first

### 1.1 Fork HAOS, or start from vanilla Buildroot?

**Start vanilla.** The HAOS repo is excellent reference material but it's deeply entangled with the Supervisor, the HA CLI, their PKI, their board metadata scheme, and ~40 board configs. Stripping it out is more work than building up, and you'll learn less.

Instead: clone HAOS alongside your project and read it constantly. These files are the ones you'll return to:

```
buildroot-external/configs/rpi4_64_defconfig     # what a real config looks like
buildroot-external/board/raspberrypi/            # genimage, boot files, post-image
buildroot-external/ota/manifest.raucm.gtpl       # RAUC manifest template
buildroot-external/scripts/rauc.sh               # key/cert generation
buildroot-external/rootfs-overlay/usr/lib/systemd/system/   # unit patterns
```

```shell
git clone https://github.com/home-assistant/operating-system/ haos-reference
cd haos-reference && git submodule update --init
```

### 1.2 Which Pi?

**Pi 4 (4 GB or 8 GB) or CM4.** Reasons:

- The `autoboot.txt` + `tryboot` A/B mechanism lives in the EEPROM bootloader, which exists on Pi 4B and later, CM4 and later, and the Pi 400. It is **not** available on Pi 3 or Zero 2 W, which boot from `bootcode.bin` — those need U-Boot instead.
- Pi 5 works too and is what HAOS uses tryboot on, but has fewer worked examples and the NVMe/PCIe path adds variables.
- Emoncms with MariaDB wants RAM and, ideally, not an SD card (see §11).

Before anything else, update the EEPROM to a recent release. Old bootloaders have `tryboot` bugs.

### 1.3 Bootloader strategy: tryboot vs U-Boot

| | RPi firmware + tryboot | U-Boot |
|---|---|---|
| Extra bootloader to build | No | Yes |
| RAUC backend | Custom (script you write) | Built-in, mature |
| Boot attempt counter | Effectively 1 | Configurable (e.g. 3 tries) |
| Recovers from unbootable slot | No — only from a reboot | Yes |
| Works on Pi 3 / Zero 2 W | No | Yes |

**Use tryboot for the concept.** It's fewer moving parts and it's what HAOS does on Pi 5. Understand its limitation clearly: tryboot is a one-shot flag, not a counter. If the new slot boots but then hangs, a watchdog reboot returns you to the old slot (good). If the new slot never gets far enough to clear the flag and the system doesn't reboot, you're stuck (bad). A hardware watchdog is therefore not optional here — it *is* your fallback mechanism.

Graduate to U-Boot later if you want real attempt counting.

---

## 2. Prerequisites

A Linux host (Debian/Ubuntu is smoothest), Docker with privileged container support, `sudo`, and ~30 GB free disk. Buildroot builds are CPU-bound; expect 45–90 minutes for the first build and a few minutes for rebuilds.

Like HAOS, do the build inside a container so your host distro doesn't leak into the build. HAOS's `scripts/enter.sh` is a good template — borrow it. The reason theirs runs privileged is that the image assembly step mounts loopback filesystems, which rootless containers can't do. Yours will need the same.

```shell
sudo apt install -y git build-essential wget cpio unzip rsync bc \
    libssl-dev file python3 docker.io
```

---

## 3. Repository skeleton

```
emonos/
├── buildroot/                       # git submodule, upstream Buildroot
├── buildroot-external/
│   ├── external.desc
│   ├── external.mk
│   ├── Config.in
│   ├── configs/
│   │   └── emonos_rpi4_defconfig
│   ├── board/rpi4/
│   │   ├── genimage.cfg
│   │   ├── boot/
│   │   │   ├── autoboot.txt
│   │   │   ├── config.txt
│   │   │   ├── cmdline_a.txt
│   │   │   └── cmdline_b.txt
│   │   ├── post-build.sh
│   │   └── post-image.sh
│   ├── ota/
│   │   └── manifest.raucm.in
│   ├── rootfs-overlay/
│   │   ├── etc/rauc/system.conf
│   │   ├── usr/lib/rauc/rpi-bootloader-backend
│   │   └── usr/lib/systemd/system/*.service
│   └── scripts/
│       └── keys.sh
├── Makefile
└── scripts/enter.sh
```

Add Buildroot as a submodule pinned to a stable release:

```shell
mkdir emonos && cd emonos && git init
git submodule add -b 2025.02.x https://gitlab.com/buildroot.org/buildroot.git buildroot
```

**`buildroot-external/external.desc`**

```
name: EMONOS
desc: Emoncms Operating System
```

**`buildroot-external/Config.in`** — empty is fine to start; this is where custom package definitions get sourced.

**`buildroot-external/external.mk`**

```makefile
include $(sort $(wildcard $(BR2_EXTERNAL_EMONOS_PATH)/package/*/*.mk))
```

**Top-level `Makefile`** (mirrors HAOS's, so `make emonos_rpi4` works):

```makefile
BUILDROOT_DIR = $(CURDIR)/buildroot
EXTERNAL_DIR  = $(CURDIR)/buildroot-external
O ?= $(CURDIR)/output

%:
	@echo "=== Using $@_defconfig ==="
	$(MAKE) -C $(BUILDROOT_DIR) O=$(O) BR2_EXTERNAL=$(EXTERNAL_DIR) $@_defconfig
	@echo "=== Building $@ ==="
	$(MAKE) -C $(O)
```

---

## 4. Milestone 1 — a plain bootable image

Do not attempt A/B yet. Get a single-slot Pi image that boots to a shell. Everything afterward is a modification of a known-working system.

`buildroot-external/configs/emonos_rpi4_defconfig`, first pass:

```
BR2_aarch64=y
BR2_cortex_a72=y
BR2_ARM_FPU_VFPV4=y

BR2_TOOLCHAIN_BUILDROOT_GLIBC=y
BR2_TOOLCHAIN_BUILDROOT_CXX=y

BR2_INIT_SYSTEMD=y
BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_EUDEV=y
BR2_SYSTEM_DHCP="eth0"
BR2_TARGET_GENERIC_HOSTNAME="emonos"

# Kernel — Raspberry Pi fork
BR2_LINUX_KERNEL=y
BR2_LINUX_KERNEL_CUSTOM_TARBALL=y
BR2_LINUX_KERNEL_CUSTOM_TARBALL_LOCATION="$(call github,raspberrypi,linux,<PIN_A_COMMIT>)/linux-<PIN_A_COMMIT>.tar.gz"
BR2_LINUX_KERNEL_DEFCONFIG="bcm2711"
BR2_LINUX_KERNEL_IMAGE=y
BR2_LINUX_KERNEL_DTS_SUPPORT=y
BR2_LINUX_KERNEL_INTREE_DTS_NAME="broadcom/bcm2711-rpi-4-b"

# Firmware
BR2_PACKAGE_RPI_FIRMWARE=y
BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI4=y
BR2_PACKAGE_RPI_FIRMWARE_BOOT_OVERLAYS=y

BR2_TARGET_ROOTFS_EXT2=y
BR2_TARGET_ROOTFS_EXT2_4=y
```

Pin the kernel to a specific commit rather than a branch. Reproducibility matters more than freshness here, and a moving `rpi-6.x.y` branch will bite you three weeks in when a rebuild produces a different kernel.

```shell
make emonos_rpi4
```

Flash `output/images/sdcard.img` (Buildroot's default `genimage-raspberrypi4-64.cfg` produces one), confirm it boots, confirm serial console works. Get a UART cable — you will need it, and debugging boot slot switching without one is miserable.

---

## 5. Milestone 2 — Docker on the rootfs

Add to the defconfig:

```
BR2_PACKAGE_DOCKER_ENGINE=y
BR2_PACKAGE_DOCKER_CLI=y
BR2_PACKAGE_CONTAINERD=y
BR2_PACKAGE_RUNC=y
BR2_PACKAGE_LIBSECCOMP=y
BR2_PACKAGE_IPTABLES=y
BR2_PACKAGE_CA_CERTIFICATES=y
BR2_PACKAGE_E2FSPROGS=y
BR2_PACKAGE_DOSFSTOOLS=y
BR2_PACKAGE_DOSFSTOOLS_MKFS_FAT=y
BR2_PACKAGE_UTIL_LINUX_BINARIES=y
```

Docker needs a long list of kernel options that `bcm2711_defconfig` doesn't all set: namespaces, cgroup v2, overlayfs, netfilter, bridge, veth, seccomp. Run `make linux-menuconfig` once, or better, keep a fragment file:

```
BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="$(BR2_EXTERNAL_EMONOS_PATH)/board/rpi4/linux-docker.fragment"
```

Buildroot prints the required options in `make menuconfig` help for `docker-engine` — copy from there. `dockerd` will also tell you what's missing at startup; check `journalctl -u docker`.

**Do not skip this step's verification.** Boot it and run `docker run --rm hello-world` on the Pi. A rootfs with a broken Docker is much harder to diagnose once it's read-only and squashfs-compressed.

---

## 6. Milestone 3 — A/B partition layout

### 6.1 Why MBR, not GPT

HAOS prefers GPT. On the Pi with `autoboot.txt` + `tryboot`, GPT is unreliable — there are open reports of the bootloader failing to find the boot partition on tryboot with GPT partition tables, and of `boot_partition=` being ignored. Use MBR with an extended partition. You get 3 primaries for FAT plus logicals for everything else, which is exactly enough.

### 6.2 Switch rootfs to squashfs

```
# BR2_TARGET_ROOTFS_EXT2 is not set
BR2_TARGET_ROOTFS_SQUASHFS=y
BR2_TARGET_ROOTFS_SQUASHFS4_ZSTD=y
```

A read-only rootfs means `/etc`, `/var`, and `/root` need to be writable some other way. The simplest concept-stage approach is tmpfs for volatile paths plus an overlay for `/etc` backed by the data partition:

`rootfs-overlay/usr/lib/systemd/system/etc-overlay.service` — or, cleaner, do the overlay mount in an initramfs before systemd starts. HAOS uses a dedicated overlay partition and mounts `/etc` as an overlayfs early. For a first pass, tmpfs-only `/etc` overlay is acceptable and loses nothing but persistent OS settings across reboots — which is a useful forcing function, since it makes you put real configuration on the data partition where it belongs.

### 6.3 genimage

`buildroot-external/board/rpi4/genimage.cfg`:

```
image autoboot.vfat {
  vfat { label = "AUTOBOOT" }
  file autoboot.txt { image = "autoboot.txt" }
  size = 16M
}

image boot_a.vfat {
  vfat { label = "BOOT_A" }
  file config.txt   { image = "config.txt" }
  file cmdline.txt  { image = "cmdline_a.txt" }
  file Image        { image = "Image" }
  file bcm2711-rpi-4-b.dtb { image = "bcm2711-rpi-4-b.dtb" }
  file start4.elf   { image = "rpi-firmware/start4.elf" }
  file fixup4.dat   { image = "rpi-firmware/fixup4.dat" }
  file overlays     { image = "rpi-firmware/overlays" }
  size = 128M
}

image boot_b.vfat {
  vfat { label = "BOOT_B" }
  file config.txt   { image = "config.txt" }
  file cmdline.txt  { image = "cmdline_b.txt" }
  file Image        { image = "Image" }
  file bcm2711-rpi-4-b.dtb { image = "bcm2711-rpi-4-b.dtb" }
  file start4.elf   { image = "rpi-firmware/start4.elf" }
  file fixup4.dat   { image = "rpi-firmware/fixup4.dat" }
  file overlays     { image = "rpi-firmware/overlays" }
  size = 128M
}

image data.ext4 {
  ext4 { label = "emonos-data" }
  size = 2G
}

image sdcard.img {
  hdimage {
    partition-table-type = "mbr"
    extended-partition = 4
  }

  partition autoboot { image = "autoboot.vfat"; partition-type = 0xC; bootable = "true" }
  partition boot_a   { image = "boot_a.vfat";   partition-type = 0xC }
  partition boot_b   { image = "boot_b.vfat";   partition-type = 0xC }
  partition rootfs_a { image = "rootfs.squashfs"; partition-type = 0x83; size = 512M }
  partition rootfs_b { image = "rootfs.squashfs"; partition-type = 0x83; size = 512M }
  partition data     { image = "data.ext4";       partition-type = 0x83 }
}
```

Note the freshly-flashed card has **both** rootfs slots populated with the same image. HAOS ships only slot A populated; either works, but writing both means slot B is a valid fallback from the very first boot.

### 6.4 Boot files

`autoboot.txt` — this is the entire A/B mechanism, 4 lines:

```
[all]
tryboot_a_b=1
boot_partition=2

[tryboot]
boot_partition=3
```

`config.txt`:

```
arm_64bit=1
kernel=Image
enable_uart=1
dtoverlay=disable-bt
```

`dtoverlay=disable-bt` frees `/dev/ttyAMA0` for emonHub to talk to an emonPi/emonTx board. Keep `enable_uart=1` for the console during development, but then the console and emonHub fight over the port — on a Pi 4 use `miniuart-bt` instead, or accept that you drop the serial console once emonHub goes in.

`cmdline_a.txt`:

```
root=/dev/mmcblk0p5 rootfstype=squashfs ro rootwait console=serial0,115200 cgroup_enable=memory
```

`cmdline_b.txt` is identical but `root=/dev/mmcblk0p6`.

### 6.5 Verify manually before automating

Flash this, boot it, then from the Pi:

```sh
# reboot '0 tryboot'      → should come up on p3/p6
cat /proc/device-tree/chosen/bootloader/partition | xxd   # which partition booted
# reboot                  → back to p2/p5
```

If manual tryboot switching doesn't work, RAUC will not save you. Fix it here.

---

## 7. Milestone 4 — RAUC

### 7.1 Packages

```
BR2_PACKAGE_RAUC=y
BR2_PACKAGE_RAUC_NETWORK=y
BR2_PACKAGE_RAUC_SERVICE=y
BR2_PACKAGE_HOST_RAUC=y
BR2_PACKAGE_DTC=y
BR2_PACKAGE_DTC_PROGRAMS=y
BR2_PACKAGE_RPI_USERLAND=y
```

`dtc` gives you `fdtget` for reading the booted partition; `rpi-userland` gives you `vcmailbox` for setting the tryboot flag.

### 7.2 Keys

Generate a dev CA once per build directory and embed the cert in the image, exactly as HAOS does. Their `buildroot-external/scripts/rauc.sh` is worth copying wholesale — the pattern is: if `key.pem`/`cert.pem` don't exist in the output dir, create a self-signed pair; always append `cert.pem` to the image's `/etc/rauc/keyring.pem`.

```shell
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
  -keyout key.pem -out cert.pem -subj "/O=emonOS/CN=emonOS dev-1"
```

Keep `key.pem` out of the image and out of git. For production you'd want a real two-tier PKI with an offline root; for the concept, a single self-signed cert is fine — just build in the habit of treating it as a secret now.

### 7.3 `/etc/rauc/system.conf`

```ini
[system]
compatible=emonos-rpi4
bootloader=custom
data-directory=/mnt/data/rauc

[keyring]
path=/etc/rauc/keyring.pem

[handlers]
bootloader-custom-backend=/usr/lib/rauc/rpi-bootloader-backend

[slot.boot.0]
device=/dev/mmcblk0p2
type=raw
bootname=A

[slot.rootfs.0]
device=/dev/mmcblk0p5
type=raw
parent=boot.0

[slot.boot.1]
device=/dev/mmcblk0p3
type=raw
bootname=B

[slot.rootfs.1]
device=/dev/mmcblk0p6
type=raw
parent=boot.1
```

`bootname` goes on the slot the *bootloader* selects — the FAT partition — and the rootfs is declared as its child via `parent=`. This mirrors HAOS, where `kernel.0` carries `bootname: A` and `rootfs.0` hangs off it.

Slots are `raw` so RAUC just `dd`s a complete filesystem image in. Simple and robust; the cost is that the bundle carries the full partition image rather than a delta.

### 7.4 The custom bootloader backend

RAUC's custom backend is a script called with four verbs: `get-primary`, `set-primary <bootname>`, `get-state <bootname>`, `set-state <bootname> good|bad`. This is where you translate RAUC's model onto `autoboot.txt`.

`rootfs-overlay/usr/lib/rauc/rpi-bootloader-backend`:

```sh
#!/bin/sh
# Illustrative — harden before trusting it. No locking, no atomic rename.
set -e

AUTOBOOT_DEV=/dev/mmcblk0p1
MNT=/run/autoboot

mount_ab()   { mkdir -p "$MNT"; mount -t vfat "$AUTOBOOT_DEV" "$MNT"; }
umount_ab()  { sync; umount "$MNT"; }

# bootname <-> partition number
part_of()  { case "$1" in A) echo 2 ;; B) echo 3 ;; esac; }
name_of()  { case "$1" in 2) echo A ;; 3) echo B ;; esac; }

write_autoboot() {  # $1 = committed partition, $2 = tryboot partition
    printf '[all]\ntryboot_a_b=1\nboot_partition=%s\n\n[tryboot]\nboot_partition=%s\n' "$1" "$2" \
        > "$MNT/autoboot.txt"
}

committed_part() { sed -n '/^\[all\]/,/^\[/s/^boot_partition=//p' "$MNT/autoboot.txt" | head -1; }

booted_part() { fdtget -t i /sys/firmware/fdt /chosen/bootloader partition; }

case "$1" in
  get-primary)
      mount_ab; name_of "$(committed_part)"; umount_ab ;;

  set-primary)
      # Stage the new slot as the tryboot target; do NOT commit it yet.
      mount_ab
      new=$(part_of "$2"); cur=$(committed_part)
      write_autoboot "$cur" "$new"
      umount_ab
      # Arm the one-shot flag so the *next* reboot uses [tryboot].
      vcmailbox 0x00038064 4 4 1 >/dev/null ;;

  get-state)
      # RPi firmware has no boot counter; report good and let the
      # health-check service be the real arbiter.
      echo "good" ;;

  set-state)
      mount_ab
      case "$3" in
        good) p=$(part_of "$2")
              other=$([ "$p" = 2 ] && echo 3 || echo 2)
              write_autoboot "$p" "$other" ;;      # commit
        bad)  p=$(part_of "$2")
              other=$([ "$p" = 2 ] && echo 3 || echo 2)
              write_autoboot "$other" "$p" ;;      # commit the other one
      esac
      umount_ab ;;

  *) echo "unknown command: $1" >&2; exit 1 ;;
esac
```

The `vcmailbox` tag for the tryboot flag needs verifying against current firmware — check `vcmailbox` docs and the Rtone reference implementation below rather than trusting the constant above. `reboot '0 tryboot'` is the more portable way to arm it, but it reboots immediately, so it belongs in a separate helper the user invokes rather than inside `set-primary`.

**Two implementations worth reading before writing your own:**
- `Rtone/raspberrypi-firmware-rauc-bootloader-backend` on GitHub — a complete, production-minded version of exactly this script
- RAUC PR #1599 — upstream work adding native Pi firmware bootchooser support, which may make your script unnecessary

### 7.5 Bundle manifest

`buildroot-external/ota/manifest.raucm.in`:

```ini
[update]
compatible=emonos-rpi4
version=@VERSION@

[bundle]
format=verity

[image.boot]
filename=boot.vfat

[image.rootfs]
filename=rootfs.squashfs
```

The `[image.<class>]` sections name slot *classes*, not slots — RAUC picks the inactive slot of each class. `format=verity` gives you dm-verity protection of the bundle contents.

### 7.6 post-image script

Extend `post-image.sh` to build the bundle from the same artifacts that went into the disk image — this is the point HAOS makes explicitly, and it matters: if the bundle and the image are built from different inputs, you will eventually ship an update that behaves differently from a fresh flash.

```sh
BUNDLE_DIR=$BINARIES_DIR/bundle
mkdir -p "$BUNDLE_DIR"
cp "$BINARIES_DIR/boot_a.vfat"     "$BUNDLE_DIR/boot.vfat"
cp "$BINARIES_DIR/rootfs.squashfs" "$BUNDLE_DIR/rootfs.squashfs"
sed "s/@VERSION@/$EMONOS_VERSION/" "$EXTERNAL/ota/manifest.raucm.in" \
    > "$BUNDLE_DIR/manifest.raucm"

$HOST_DIR/bin/rauc bundle \
    --cert="$BINARIES_DIR/../cert.pem" \
    --key="$BINARIES_DIR/../key.pem" \
    "$BUNDLE_DIR" "$BINARIES_DIR/emonos-rpi4-$EMONOS_VERSION.raucb"
```

---

## 8. Milestone 5 — the data partition and Docker

### 8.1 Mounting

`rootfs-overlay/usr/lib/systemd/system/mnt-data.mount`:

```ini
[Unit]
Description=Emoncms data partition
Before=docker.service

[Mount]
What=/dev/disk/by-label/emonos-data
Where=/mnt/data
Type=ext4
Options=noatime

[Install]
WantedBy=local-fs.target
```

Mount by **label**, not device path. HAOS does this deliberately (`hassos-data`) and it's what makes their data-disk migration feature possible — you can move the data partition to an SSD later by relabelling, without touching the OS image.

Then bind Docker's state onto it:

```ini
# var-lib-docker.mount
[Unit]
Requires=mnt-data.mount
After=mnt-data.mount
Before=docker.service

[Mount]
What=/mnt/data/docker
Where=/var/lib/docker
Type=none
Options=bind
```

### 8.2 First-boot setup

The flashed data partition is a fixed 2 GB. A oneshot service should grow it to fill the card and create the directory skeleton:

```ini
[Unit]
ConditionPathExists=!/mnt/data/.provisioned
Requires=mnt-data.mount
After=mnt-data.mount
Before=docker.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/emonos-first-boot
RemainAfterExit=yes
```

The script: `sfdisk` to extend the last partition, `resize2fs`, `mkdir -p /mnt/data/{docker,emoncms/{data,db},rauc}`, then touch the sentinel.

### 8.3 Preloading images

Pulling ~500 MB of containers on first boot over a customer's network is a bad first impression. HAOS ships the Supervisor and plugins pre-installed on the data partition. Do the same: `docker save` your images at build time into `data.ext4`, and have first-boot `docker load` them. Costs image size, buys offline provisioning.

For the concept stage, network-pull is fine — just know it's a stopgap.

---

## 9. Milestone 6 — the Emoncms stack

### 9.1 Orchestration

HAOS has the Supervisor for this. You don't, and shouldn't build one. Two reasonable options:

**Option A — systemd units (recommended to start).** One unit per container, `Requires`/`After` chained, a shared Docker network. No extra packages, and `journalctl` gives you a single log stream. Verbose, but transparent.

**Option B — docker-compose.** Check whether your Buildroot version packages a compose you can live with (`BR2_PACKAGE_DOCKER_COMPOSE`). Fewer files, but another moving part in the rootfs.

A representative unit:

```ini
# emoncms.service
[Unit]
Description=Emoncms
Requires=docker.service emoncms-db.service emoncms-redis.service
After=docker.service emoncms-db.service emoncms-redis.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f emoncms
ExecStart=/usr/bin/docker run --rm --name emoncms \
    --network emoncms \
    -p 80:80 \
    -v /mnt/data/emoncms/data:/var/opt/emoncms \
    -v /mnt/data/emoncms/config:/etc/emoncms \
    openenergymonitor/emoncms@sha256:<DIGEST>
ExecStop=/usr/bin/docker stop emoncms

[Install]
WantedBy=multi-user.target
```

The stack you need, from `github.com/emoncms/emoncms-docker`: emoncms web, MariaDB, Redis, Mosquitto, and emonHub if you're reading from emon hardware. Read their `docker-compose.yml` and translate it.

**Pin by digest, not tag.** `:latest` in an appliance image means two units flashed a week apart run different software with no record of why.

### 9.2 emonHub and serial hardware

If this replaces an emonPi/emonBase, emonHub needs the UART:

```
--device=/dev/ttyAMA0:/dev/ttyAMA0
```

and the GPIO serial console must be off in `cmdline.txt`, or emonHub and getty will both read the port and both get corrupted data. This is a classic silent failure — data arrives intermittently mangled rather than not at all.

### 9.3 What lives where

| Path | Content | Survives OS update? |
|---|---|---|
| `/mnt/data/emoncms/data` | PHPFina/PHPTimeseries feed files | Yes |
| `/mnt/data/emoncms/db` | MariaDB — inputs, feeds, users, dashboards | Yes |
| `/mnt/data/emoncms/config` | `settings.ini`, emonhub.conf | Yes |
| `/mnt/data/docker` | Images and container state | Yes |
| `/` (squashfs) | Kernel, systemd, Docker binaries, unit files | No — replaced wholesale |

Write this table down and check every new file you add against it. The single most common way to ruin an A/B design is to let something important accumulate on the rootfs.

---

## 10. Milestone 7 — the update round trip

### 10.1 Health check and commit

Nothing about tryboot is safe until something decides the new slot is actually good. That decision is yours to define, and it should test the thing you care about — Emoncms serving requests — not merely that systemd reached `multi-user.target`.

```ini
# emonos-health.service
[Unit]
Description=Confirm boot slot health
After=emoncms.service
Requires=emoncms.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/emonos-health-check
RemainAfterExit=yes
```

```sh
#!/bin/sh
# Poll for up to 5 minutes, then commit or bail.
for i in $(seq 60); do
    if curl -fsS -o /dev/null http://localhost/ ; then
        rauc status mark-good
        logger -t emonos "boot slot committed"
        exit 0
    fi
    sleep 5
done
logger -t emonos "health check FAILED — rebooting to previous slot"
systemctl reboot
```

The `systemctl reboot` at the end is the fallback: the tryboot flag was one-shot and is now clear, so a plain reboot returns to the committed slot.

Add a hardware watchdog (`BR2_PACKAGE_WATCHDOGD` or systemd's `RuntimeWatchdogSec=`) to cover the case where the new slot hangs before this service ever runs.

### 10.2 Testing the round trip

```sh
# on the Pi, running v1 from slot A
rauc status                         # confirm: booted from boot.0 (A)

curl -LO http://yourhost/emonos-rpi4-2.raucb
rauc install emonos-rpi4-2.raucb    # writes p3 + p6, arms tryboot
systemctl reboot

# after reboot
rauc status                         # booted from boot.1 (B)
cat /mnt/data/emoncms/...           # data still there
```

Then deliberately break it. Build a bundle whose `emoncms.service` cannot start, install it, reboot, and confirm the Pi comes back on slot A with your feeds intact. **A rollback path you have not tested is not a rollback path.** Test at minimum: app fails to start, kernel panics on boot, and power loss during `rauc install`.

### 10.3 Signature behaviour

RAUC rejects bundles it can't verify. Your locally-built image will trust your dev cert because you baked it into `keyring.pem`, so local bundles install fine. When you eventually have a release CA and want to test an upgrade path from a dev build to a signed release, the HAOS approach works — bind-mount an augmented keyring over `/etc/rauc/` and restart the service.

---

## 11. Emoncms-specific concerns HAOS doesn't have

**Write volume.** Emoncms writes continuously to feed files; MariaDB adds its own. SD cards die from this. The mitigations, roughly in order of value: put the data partition on a USB SSD or NVMe (CM4/Pi 5); mount `noatime`; set `commit=600` on the data ext4; keep `/var/log` on tmpfs with a small journal; consider `redis` buffering for feed writes, which Emoncms supports.

**Database in a container.** MariaDB corrupts if it loses power mid-write. Your update process must stop containers cleanly before reboot — add `ExecStop` with a generous `TimeoutStopSec` and make sure `rauc install` isn't racing a checkpoint.

**Backups.** A/B protects the OS, not the data. Emoncms has export tooling; a scheduled dump to `/mnt/data/backup` plus a way to pull it off the device is not optional in an appliance.

**Time.** Feed data is timestamped. If the Pi boots without RTC or NTP, you get feeds in 1970. Add a real RTC or make emonHub wait for time sync.

---

## 12. Where this diverges from HAOS, deliberately

Things HAOS has that you are consciously not building:

- **Supervisor** — container lifecycle, app store, backups, an update API. You have systemd units. That's the right trade for a single-purpose appliance, but it means app updates are manual until you build something.
- **OTA server** — HAOS publishes bundles as GitHub releases and the Supervisor polls a version JSON. You'll want an equivalent eventually. Look at hawkBit (RAUC integrates with it natively) or, more simply, a signed JSON manifest on a static web host plus a systemd timer.
- **Multi-board support** — HAOS's board metadata scheme handles ~40 targets. You have one. Keep it that way until you don't.
- **A CLI** — `ha` wraps everything for users. You have `rauc status` and ssh.

---

## 13. Gotcha checklist

- Pin the kernel commit, not a branch
- EEPROM bootloader must be current, or tryboot misbehaves
- MBR, not GPT, when using `autoboot.txt` + tryboot
- `autoboot.txt` is limited to 512 bytes
- Boot slot and rootfs slot are paired and tested together — never version them independently
- Sizing: both rootfs slots must be big enough for the *largest future* image, and you can't resize them in the field without repartitioning. Budget generously — 512 MB for a ~250 MB image
- `rauc install` writes the whole partition; the bundle is roughly the size of your rootfs. Plan bandwidth
- Anything the user configures must land on `/mnt/data`, or it dies on the next OS update
- Serial console and emonHub cannot share the UART
- Keep `key.pem` out of the image and out of git

---

## 14. Suggested order of work

Each of these is a working, testable system. Resist collapsing them.

1. Plain Buildroot image boots on the Pi, serial console works
2. Docker runs on it, `hello-world` passes
3. Emoncms stack runs from a normal read-write rootfs — prove the app works before adding immutability
4. Repartition to A/B, rootfs squashfs read-only, data partition mounted
5. Manual `reboot '0 tryboot'` switches slots
6. RAUC installed, `rauc status` shows both slots correctly
7. Custom bootloader backend, `rauc install` + reboot switches slots
8. Health check commits the slot; deliberately broken build rolls back
9. Bundle hosting and an update-check mechanism

Steps 1–3 are a weekend. Steps 4–8 are where the learning is, and where you'll spend most of the time. Step 9 is a project in itself.

---

## References

- HAOS OS development docs — `developers.home-assistant.io/docs/operating-system/`, particularly the Partitions and Update system pages
- HAOS source — `github.com/home-assistant/operating-system`
- RAUC docs — `rauc.readthedocs.io`
- Buildroot manual, br2-external chapter — `buildroot.org/downloads/manual/manual.html`
- Raspberry Pi `autoboot.txt` documentation — `raspberrypi.com/documentation/computers/config_txt.html#autoboot-txt`
- Rtone RPi firmware RAUC backend — `github.com/Rtone/raspberrypi-firmware-rauc-bootloader-backend`
- Bootlin, "Safe updates using RAUC on Raspberry Pi 5" — a detailed walkthrough of the same tryboot approach
- Emoncms Docker — `github.com/emoncms/emoncms-docker`
systemctl reboot
```

The `systemctl reboot` at the end is the fallback: the tryboot flag was one-shot and is now clear, so a plain reboot returns to the committed slot.

Add a hardware watchdog (`BR2_PACKAGE_WATCHDOGD` or systemd's `RuntimeWatchdogSec=`) to cover the case where the new slot hangs before this service ever runs.

### 10.2 Testing the round trip

```sh
# on the Pi, running v1 from slot A
rauc status                         # confirm: booted from boot.0 (A)

curl -LO http://yourhost/emonos-rpi4-2.raucb
rauc install emonos-rpi4-2.raucb    # writes p3 + p6, arms tryboot
systemctl reboot

# after reboot
rauc status                         # booted from boot.1 (B)
cat /mnt/data/emoncms/...           # data still there
```

Then deliberately break it. Build a bundle whose `emoncms.service` cannot start, install it, reboot, and confirm the Pi comes back on slot A with your feeds intact. **A rollback path you have not tested is not a rollback path.** Test at minimum: app fails to start, kernel panics on boot, and power loss during `rauc install`.

### 10.3 Signature behaviour

RAUC rejects bundles it can't verify. Your locally-built image will trust your dev cert because you baked it into `keyring.pem`, so local bundles install fine. When you eventually have a release CA and want to test an upgrade path from a dev build to a signed release, the HAOS approach works — bind-mount an augmented keyring over `/etc/rauc/` and restart the service.

---

## 11. Emoncms-specific concerns HAOS doesn't have

**Write volume.** Emoncms writes continuously to feed files; MariaDB adds its own. SD cards die from this. The mitigations, roughly in order of value: put the data partition on a USB SSD or NVMe (CM4/Pi 5); mount `noatime`; set `commit=600` on the data ext4; keep `/var/log` on tmpfs with a small journal; consider `redis` buffering for feed writes, which Emoncms supports.

**Database in a container.** MariaDB corrupts if it loses power mid-write. Your update process must stop containers cleanly before reboot — add `ExecStop` with a generous `TimeoutStopSec` and make sure `rauc install` isn't racing a checkpoint.

**Backups.** A/B protects the OS, not the data. Emoncms has export tooling; a scheduled dump to `/mnt/data/backup` plus a way to pull it off the device is not optional in an appliance.

**Time.** Feed data is timestamped. If the Pi boots without RTC or NTP, you get feeds in 1970. Add a real RTC or make emonHub wait for time sync.

---

## 12. Where this diverges from HAOS, deliberately

Things HAOS has that you are consciously not building:

- **Supervisor** — container lifecycle, app store, backups, an update API. You have systemd units. That's the right trade for a single-purpose appliance, but it means app updates are manual until you build something.
- **OTA server** — HAOS publishes bundles as GitHub releases and the Supervisor polls a version JSON. You'll want an equivalent eventually. Look at hawkBit (RAUC integrates with it natively) or, more simply, a signed JSON manifest on a static web host plus a systemd timer.
- **Multi-board support** — HAOS's board metadata scheme handles ~40 targets. You have one. Keep it that way until you don't.
- **A CLI** — `ha` wraps everything for users. You have `rauc status` and ssh.

---

## 13. Gotcha checklist

- Pin the kernel commit, not a branch
- EEPROM bootloader must be current, or tryboot misbehaves
- MBR, not GPT, when using `autoboot.txt` + tryboot
- `autoboot.txt` is limited to 512 bytes
- Boot slot and rootfs slot are paired and tested together — never version them independently
- Sizing: both rootfs slots must be big enough for the *largest future* image, and you can't resize them in the field without repartitioning. Budget generously — 512 MB for a ~250 MB image
- `rauc install` writes the whole partition; the bundle is roughly the size of your rootfs. Plan bandwidth
- Anything the user configures must land on `/mnt/data`, or it dies on the next OS update
- Serial console and emonHub cannot share the UART
- Keep `key.pem` out of the image and out of git

---

## 14. Suggested order of work

Each of these is a working, testable system. Resist collapsing them.

1. Plain Buildroot image boots on the Pi, serial console works
2. Docker runs on it, `hello-world` passes
3. Emoncms stack runs from a normal read-write rootfs — prove the app works before adding immutability
4. Repartition to A/B, rootfs squashfs read-only, data partition mounted
5. Manual `reboot '0 tryboot'` switches slots
6. RAUC installed, `rauc status` shows both slots correctly
7. Custom bootloader backend, `rauc install` + reboot switches slots
8. Health check commits the slot; deliberately broken build rolls back
9. Bundle hosting and an update-check mechanism

Steps 1–3 are a weekend. Steps 4–8 are where the learning is, and where you'll spend most of the time. Step 9 is a project in itself.

---

## References

- HAOS OS development docs — `developers.home-assistant.io/docs/operating-system/`, particularly the Partitions and Update system pages
- HAOS source — `github.com/home-assistant/operating-system`
- RAUC docs — `rauc.readthedocs.io`
- Buildroot manual, br2-external chapter — `buildroot.org/downloads/manual/manual.html`
- Raspberry Pi `autoboot.txt` documentation — `raspberrypi.com/documentation/computers/config_txt.html#autoboot-txt`
- Rtone RPi firmware RAUC backend — `github.com/Rtone/raspberrypi-firmware-rauc-bootloader-backend`
- Bootlin, "Safe updates using RAUC on Raspberry Pi 5" — a detailed walkthrough of the same tryboot approach
- Emoncms Docker — `github.com/emoncms/emoncms-docker`
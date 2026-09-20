# Pi 4 boot and serial debugging

## Current status — 2026-09-20

Hardware observations supplied by the maintainer:

- Removing `miniuart-bt` and `disable_commandline_tags=1` restored firmware/U-Boot
  output. These settings were removed together; neither has been individually identified
  as the cause of the earlier silence.
- Linux early output works with `console=ttyS0,115200` and
  `earlycon=bcm2835aux,mmio32,0xfe215040`.
- Extlinux no longer specifies `fdt`: U-Boot uses the firmware-provided device tree,
  preserving its UART configuration and board fixups.
- Linux then reported `unable to register 8250 port`. The kernel has
  `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`; extlinux now explicitly supplies
  `8250.nr_uarts=1`.

**Hardware retest succeeded:** the maintainer reached an interactive root shell after the
latest rebuild. `uname -a` reports Linux `6.12.61-v8`, `aarch64`; `/etc/os-release` reports
Buildroot `2026.02.3`. This confirms the serial-login foundation is usable.

The maintainer also confirmed:

- `/dev/root` is mounted at `/` as ext4, read/write (`rw,relatime`).
- `df -h` reports 487.6 MB filesystem capacity, 80.0 MB used and 371.7 MB available.
- cgroup v2 is mounted at `/sys/fs/cgroup`, and BPF at `/sys/fs/bpf`.
- The active serial-getty credential mount is for `serial-getty@ttyS0.service`.
- `systemctl --failed --no-pager` reports zero failed units.

Live serial checks subsequently confirmed:

- `/proc/cmdline` is `root=/dev/mmcblk0p2 rootwait rw 8250.nr_uarts=1
  console=ttyS0,115200 earlycon=bcm2835aux,mmio32,0xfe215040`.
- No `cgroup_disable=memory` argument is present.
- `cgroup.controllers` contains `cpuset cpu io memory pids`; memory cgroups are available
  for the future Docker runtime.
- Docker is not yet installed on the Pi image.
- `systemctl --failed --no-pager` continues to report zero failed units.

`findmnt` is absent from the minimal image. Use `cat /proc/mounts` or `mount` to inspect
mounts until the util-linux diagnostic tools are included. Docker is not yet built into
the Pi image; its runtime verification remains the next milestone.

## Build-side corrections

The Pi image was rebuilt with the handover changes. The image-assembly hook now copies
board `config.txt` directly on every run, avoiding stale firmware package staging. Directory
copies use `cp -aT` to avoid nesting `overlays/overlays` on repeated assembly.

Changing the getty setting also required refreshing systemd's installation in the existing
build output:

```sh
PATH="$PWD/tools/bin:$PATH" make -C output/rpi4 systemd-reinstall
make emonos_rpi4
```

The rebuilt ext4 filesystem was inspected with `debugfs`: the enabled serial getty is
`serial-getty@ttyS0.service`, and `buildroot-console.conf` specifies `DefaultInstance=ttyS0`.
This is artifact inspection, not hardware validation.

## Retest

Flash `output/rpi4/images/emonos-rpi4.img` to include both the boot configuration and getty
change. Boot-partition-only edits cannot update the existing root filesystem's getty.

Capture a fresh power-on log at 115200 8N1. Expect firmware diagnostics, U-Boot, then Linux
on the mini UART and an `emonos login:` prompt. Log in as `root` and record:

```sh
cat /proc/cmdline
uname -a
systemctl status serial-getty@ttyS0.service --no-pager
systemctl --failed --no-pager
```

## Bench connection details

- Serial adapter: `/dev/ttyUSB2` (115200 8N1).
- Tasmota host: `172.16.1.2`, power output **5** (`Power5`).
- Power API authentication and automated control have not yet been tested. Only output 5
  should be operated by this target's eventual test profile.

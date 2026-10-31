#!/usr/bin/env python3
"""Validate the Pi factory image's redundant U-Boot/Linux boot-state contract."""

import json
from pathlib import Path
import subprocess
import zlib


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "output/rpi4"
ENV_SIZE = 0x4000
COMPATIBLE = "emonos-rpi4-bootstate-v2"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def config_value(config: str, key: str) -> str:
    prefix = f"{key}="
    for line in config.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise SystemExit(f"missing {key} from U-Boot .config")


def main() -> None:
    image = OUTPUT / "images/emonos-rpi4.img"
    bootstate_image = OUTPUT / "images/bootstate.img"
    uboot_config = OUTPUT / "build/uboot-2026.01/.config"
    for path in (image, bootstate_image, uboot_config):
        require(path.is_file(), f"missing Pi build artifact: {path}")

    partition_table = json.loads(subprocess.check_output(
        ["sfdisk", "--json", str(image)], text=True,
    ))["partitiontable"]
    sector_size = partition_table["sectorsize"]
    bootstate = next(
        partition for partition in partition_table["partitions"]
        if partition.get("name") == "bootstate"
    )
    first_offset = bootstate["start"] * sector_size
    redundant_offset = first_offset + ENV_SIZE

    config = uboot_config.read_text()
    expected = {
        "CONFIG_ENV_IS_IN_MMC": "y",
        "CONFIG_ENV_REDUNDANT": "y",
        "CONFIG_ENV_APPEND": "y",
        "CONFIG_ENV_MMC_DEVICE_INDEX": "0",
        "CONFIG_ENV_OFFSET": hex(first_offset),
        "CONFIG_ENV_OFFSET_REDUND": hex(redundant_offset),
        "CONFIG_ENV_SIZE": hex(ENV_SIZE),
        "CONFIG_CMD_SAVEENV": "y",
    }
    for key, value in expected.items():
        require(config_value(config, key).lower() == value.lower(),
                f"{key} does not match expected value {value}")

    fw_env = (ROOT / "buildroot-external/board/raspberrypi/rpi4/rootfs-overlay"
              "/etc/fw_env.config").read_text().splitlines()
    require(fw_env == [
        f"/dev/disk/by-partlabel/bootstate 0x0000 {hex(ENV_SIZE)} 0x200",
        f"/dev/disk/by-partlabel/bootstate {hex(ENV_SIZE)} {hex(ENV_SIZE)} 0x200",
    ], "Linux fw_env.config does not match the two raw U-Boot copies")

    data = bootstate_image.read_bytes()
    copies = [data[:ENV_SIZE], data[ENV_SIZE:2 * ENV_SIZE]]
    require(all(len(copy) == ENV_SIZE for copy in copies),
            "bootstate image does not contain two full environment copies")
    require(copies[0] == copies[1], "factory bootstate copies differ")
    for index, copy in enumerate(copies):
        expected_crc = int.from_bytes(copy[:4], "little")
        require(zlib.crc32(copy[5:]) == expected_crc,
                f"bootstate copy {index} has an invalid CRC")
        require(copy[4] == 1, f"bootstate copy {index} has an unexpected initial flag")
        for entry in (b"BOOT_ORDER=A B", b"BOOT_A_LEFT=1", b"BOOT_B_LEFT=0"):
            require(entry in copy, f"bootstate copy {index} lacks {entry.decode()}")

    system = (OUTPUT / "target/etc/rauc/system.conf").read_text()
    require(f"compatible={COMPATIBLE}" in system,
            "factory RAUC compatibility identity is not bootstate-v2")
    info = json.loads(subprocess.check_output([
        str(OUTPUT / "host/bin/rauc"),
        f"--keyring={OUTPUT / 'target/etc/rauc/keyring.pem'}",
        "info", "--output-format=json", str(OUTPUT / "images/emonos-rpi4.raucb"),
    ], text=True))
    require(info["compatible"] == COMPATIBLE,
            "Pi update bundle does not use the bootstate-v2 compatibility identity")

    print("Pi redundant boot-state image and RAUC compatibility checks passed")


if __name__ == "__main__":
    main()

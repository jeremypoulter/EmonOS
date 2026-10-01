"""Host-only OTA preparation checks, independent of Buildroot output."""

import os
import subprocess
from pathlib import Path

import pytest


OTA = Path(__file__).parents[1] / "buildroot-external/ota"


def test_grub_embeds_slot_selection_commands():
    config = (OTA.parent / "configs/emonos_x86_64_vm_defconfig").read_text()
    line = next(line for line in config.splitlines()
                if line.startswith("BR2_TARGET_GRUB2_BUILTIN_MODULES_EFI="))
    modules = line.split('"')[1].split()
    assert {"test", "echo", "serial", "terminfo", "loadenv", "linux", "squash4"} <= set(modules)
    grub = (OTA.parent / "board/pc/x86-64-vm/grub.cfg.in").read_text()
    assert "terminal_output serial" in grub
    assert 'echo "Booting Slot B"' in grub


@pytest.mark.parametrize("architecture,board,backend", [
    ("x86_64", "x86-64-vm", "grub"), ("aarch64", "rpi4", "uboot"),
])
def test_target_config_and_public_only_keyring(tmp_path, architecture, board, backend):
    target = tmp_path / "target"
    (target / "etc").mkdir(parents=True)
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "dev-cert.pem").write_text("PUBLIC CERTIFICATE")
    (keys / "dev-key.pem").write_text("PRIVATE KEY")
    config = tmp_path / "config"
    config.write_text(f"BR2_{architecture}=y\n")
    environment = {
        **os.environ, "EMONOS_RAUC_KEY_DIR": str(keys),
        "EMONOS_VERSION": "0.2.0-test", "BR2_CONFIG": str(config),
    }
    subprocess.run([str(OTA / "prepare-target.sh"), str(target)], env=environment, check=True)
    content = (target / "etc/rauc/system.conf").read_text()
    assert f"compatible=emonos-{board}" in content
    assert f"bootloader={backend}" in content
    assert "@" not in content
    assert "readonly=true" in content
    assert "bundle-formats=verity" in content
    if backend == "uboot":
        assert "boot-attempts=1\nboot-attempts-primary=1" in content
    assert (target / "etc/rauc/keyring.pem").read_text() == "PUBLIC CERTIFICATE"
    assert not list(target.rglob("dev-key.pem"))
    assert 'VERSION_ID="0.2.0-test"' in (target / "etc/os-release").read_text()


def test_dev_keys_refuse_incomplete_credentials(tmp_path):
    key = tmp_path / "dev-key.pem"
    key.write_text("do not overwrite")
    result = subprocess.run([str(OTA / "dev-keys.sh"), str(tmp_path)], capture_output=True)
    assert result.returncode != 0
    assert key.read_text() == "do not overwrite"
    assert not (tmp_path / "dev-cert.pem").exists()


@pytest.mark.parametrize("system,bundle,expected", [
    ("emonos-rpi4", "emonos-rpi4", 0),
    ("emonos-rpi4", "emonos-x86-64-vm", 10),
    ("", "", 10),
])
def test_install_check_rejects_mismatch(system, bundle, expected):
    result = subprocess.run(
        [str(OTA / "install-check"), "install-check"], capture_output=True,
        env={**os.environ, "RAUC_SYSTEM_COMPATIBLE": system, "RAUC_MF_COMPATIBLE": bundle},
    )
    assert result.returncode == expected

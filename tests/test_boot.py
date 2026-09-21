"""Boot and common runtime coverage for HW-3 and TEST-7."""

from pathlib import Path

import pytest


REQUIRED_CGROUP_CONTROLLERS = {"cpu", "cpuset", "io", "memory", "pids"}


def test_target_image_is_present(target_name: str, repo_root: Path) -> None:
    """HW-3: the selected target build produces its documented disk image."""
    images = {
        "x86-64-vm": "output/x86-64-vm/images/emonos-x86-64-vm.img",
        "rpi4": "output/rpi4/images/emonos-rpi4.img",
    }
    image = repo_root / images[target_name]
    assert image.is_file(), f"build {target_name} first; missing {image}"


def test_targets_use_common_runtime(repo_root: Path) -> None:
    """HW-3: both targets select the common EmonOS runtime and kernel policy."""
    for target in ("emonos_x86_64_vm", "emonos_rpi4"):
        content = (repo_root / f"buildroot-external/configs/{target}_defconfig").read_text()
        assert "BR2_EMONOS_RUNTIME=y" in content
        assert "board/common/kernel-container.config" in content


@pytest.mark.timeout(360)
def test_boot_runtime(command, expected_architecture: str) -> None:
    """HW-3, TEST-7: exercise the common runtime through the target serial shell."""
    assert command.run_check("uname -s") == ["Linux"]
    assert command.run_check("uname -m") == [expected_architecture]
    assert command.run_check("hostname") == ["emonos"]
    command.run_check("test -z \"$(systemctl --failed --no-legend --plain)\"")

    controllers = set(command.run_check("cat /sys/fs/cgroup/cgroup.controllers")[0].split())
    assert REQUIRED_CGROUP_CONTROLLERS <= controllers

    assert command.poll_until_success(
        "docker info >/dev/null 2>&1", tries=60, timeout=90.0, sleepduration=1
    )
    command.run_check("docker version >/dev/null")
    command.run_check("docker compose version")
    output = command.run_check("docker run --rm hello-world", timeout=120)
    assert "Hello from Docker!" in output

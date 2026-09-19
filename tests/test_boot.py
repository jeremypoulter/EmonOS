"""Initial boot smoke test coverage for HW-3 and TEST-7."""

from pathlib import Path


def test_x86_image_is_present() -> None:
    """HW-3: the x86 target build produces the documented disk image."""
    image = Path(__file__).parents[1] / "output/x86-64-vm/images/emonos-x86-64-vm.img"
    assert image.is_file(), f"build the x86 target first; missing {image}"


def test_x86_defconfig_enables_docker() -> None:
    """ARCH-4 seed: the x86 build includes the Docker runtime and Compose CLI."""
    defconfig = Path(__file__).parents[1] / "buildroot-external/configs/emonos_x86_64_vm_defconfig"
    content = defconfig.read_text()
    assert "BR2_PACKAGE_DOCKER_ENGINE=y" in content
    assert "BR2_PACKAGE_DOCKER_CLI=y" in content
    assert "BR2_PACKAGE_DOCKER_COMPOSE=y" in content

"""Initial boot smoke test coverage for HW-3 and TEST-7."""

from pathlib import Path


def test_x86_image_is_present() -> None:
    """HW-3: the x86 target build produces the documented disk image."""
    image = Path(__file__).parents[1] / "output/x86-64-vm/images/emonos-x86-64-vm.img"
    assert image.is_file(), f"build the x86 target first; missing {image}"

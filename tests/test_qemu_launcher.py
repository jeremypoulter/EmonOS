"""Host-side checks for the QEMU launcher; no guest or KVM is needed."""

import runpy
from pathlib import Path


def test_overlay_directory_falls_back_on_clean_runner(monkeypatch, tmp_path: Path) -> None:
    launcher = runpy.run_path(str(Path(__file__).with_name("qemu-launch.py")))
    directory = launcher["overlay_directory"]
    monkeypatch.setenv("EMONOS_QEMU_TMPDIR", str(tmp_path / "not-created"))
    assert directory() is None
    monkeypatch.setenv("EMONOS_QEMU_TMPDIR", str(tmp_path))
    assert directory() == str(tmp_path)

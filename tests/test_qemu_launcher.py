"""Host-side checks for the QEMU launcher; no guest or KVM is needed."""

import runpy
import tempfile
from pathlib import Path


def test_overlay_directory_uses_standard_tempfile_default(monkeypatch, tmp_path: Path) -> None:
    launcher = runpy.run_path(str(Path(__file__).with_name("qemu-launch.py")))
    directory = launcher["overlay_directory"]
    monkeypatch.delenv("EMONOS_QEMU_TMPDIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert directory() is None
    with tempfile.TemporaryDirectory(prefix="emonos-qemu-", dir=directory()) as overlay:
        assert Path(overlay).parent == tmp_path
    monkeypatch.setenv("EMONOS_QEMU_TMPDIR", str(tmp_path))
    assert directory() == str(tmp_path)

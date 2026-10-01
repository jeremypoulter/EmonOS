"""Host-side checks for the QEMU launcher; no guest or KVM is needed."""

import runpy
import struct
import tempfile
import sys
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


def test_disposable_overlay_can_expose_extra_disk_space(monkeypatch, tmp_path: Path) -> None:
    launcher = runpy.run_path(str(Path(__file__).with_name("qemu-launch.py")))
    image_command = launcher["run_image_command"]
    # Exercise the same process-scoped io_uring fallback used on stressed hosts.
    monkeypatch.setenv("EMONOS_QEMU_DISABLE_IO_URING", "1")
    backing = tmp_path / "base.raw"
    backing.write_bytes(b"\0" * 1024)
    overlay = tmp_path / "overlay.qcow2"
    image_command([
        "create", "-q", "-f", "qcow2", "-F", "raw", "-b", str(backing), str(overlay),
    ])
    image_command(["resize", "-q", "-f", "qcow2", str(overlay), "2M"])
    # qcow2 stores its virtual size as a big-endian u64 at offset 24. Read
    # it directly so this assertion does not start a third qemu-img process.
    with overlay.open("rb") as image:
        image.seek(24)
        size = struct.unpack(">Q", image.read(8))[0]
    assert size == 2 * 1024 * 1024
    assert backing.read_bytes() == b"\0" * 1024


def test_bundle_transport_is_read_only_and_separate(monkeypatch, tmp_path: Path) -> None:
    launcher = runpy.run_path(str(Path(__file__).with_name("qemu-launch.py")))
    main = launcher["main"]
    commands = []
    monkeypatch.setattr(sys, "argv", ["qemu-launch.py", "-drive", "file=base.img,if=virtio"])
    monkeypatch.setenv("EMONOS_QEMU_RAUC_BUNDLE", str(tmp_path / "test.raucb"))
    monkeypatch.delenv("EMONOS_QEMU_DISABLE_IO_URING", raising=False)
    monkeypatch.setitem(main.__globals__, "available_memory_mb", lambda: 100000)
    monkeypatch.setitem(main.__globals__, "run_image_command", lambda _: None)
    monkeypatch.setitem(main.__globals__, "run_once", lambda command: (commands.append(command) or 0, b""))
    assert main() == 0
    arguments = commands[0]
    assert "virtio-blk-pci,drive=vmdisk" in arguments
    assert "virtio-blk-pci,drive=bundledisk" in arguments
    assert any("node-name=bundlefile,read-only=on" in item for item in arguments)
    assert "driver=raw,file=bundlefile,node-name=bundledisk,read-only=on" in arguments

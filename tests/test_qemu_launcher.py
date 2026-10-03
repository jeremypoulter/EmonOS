"""Host-side checks for the QEMU launcher; no guest or KVM is needed."""

import runpy
import struct
import tempfile
import sys
import signal
import pytest
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
    monkeypatch.delenv("EMONOS_QEMU_STATE_DIR", raising=False)
    monkeypatch.setitem(main.__globals__, "available_memory_mb", lambda: 100000)
    monkeypatch.setitem(main.__globals__, "run_image_command", lambda _: None)
    monkeypatch.setitem(main.__globals__, "run_once", lambda command: (commands.append(command) or 0, b""))
    assert main() == 0
    arguments = commands[0]
    assert "virtio-blk-pci,drive=vmdisk" in arguments
    assert "virtio-blk-pci,drive=bundledisk" in arguments
    assert any("node-name=bundlefile,read-only=on" in item for item in arguments)
    assert "driver=raw,file=bundlefile,node-name=bundledisk,read-only=on" in arguments


def test_crash_state_is_reused_without_snapshot_or_disk_reset(monkeypatch, tmp_path: Path) -> None:
    launcher = runpy.run_path(str(Path(__file__).with_name("qemu-launch.py")))
    main = launcher["main"]
    base = tmp_path / "base.raw"
    base.write_bytes(bytes(1024))
    state = tmp_path / "state"
    state.mkdir()
    commands, image_commands = [], []
    original = launcher["run_image_command"]
    monkeypatch.setattr(sys, "argv", ["qemu-launch.py", "-snapshot", "-drive", f"file={base},if=virtio"])
    monkeypatch.setenv("EMONOS_QEMU_STATE_DIR", str(state))
    monkeypatch.setenv("EMONOS_QEMU_DISABLE_IO_URING", "1")
    monkeypatch.setenv("EMONOS_QEMU_DISK_SIZE", "2M")
    monkeypatch.setitem(main.__globals__, "available_memory_mb", lambda: 100000)
    monkeypatch.setitem(main.__globals__, "run_image_command",
                        lambda args: (image_commands.append(args), original(args)))
    monkeypatch.setitem(main.__globals__, "run_once", lambda args: (commands.append(args) or 0, b""))
    assert main() == 0
    overlay = state / "disk.raw"
    original_inode = overlay.stat().st_ino
    # Represent guest-modified state without resetting it on the next launch.
    with overlay.open("r+b") as guest:
        guest.seek(256)
        guest.write(b"guest-write")
    (state / "evidence.json").write_text('{"cut":"rootfs.1"}')
    assert main() == 0
    assert len(image_commands) == 1  # raw clone resize happens only once
    assert overlay.stat().st_ino == original_inode
    with overlay.open("rb") as guest:
        guest.seek(256)
        assert guest.read(11) == b"guest-write"
    assert base.read_bytes() == bytes(1024)
    assert (state / "evidence.json").read_text() == '{"cut":"rootfs.1"}'
    assert all("-snapshot" not in args for args in commands)
    assert all("virtio-blk-pci,drive=limiteddisk" in args for args in commands)
    assert all("driver=raw,file=vmfile,node-name=vmdisk" in args for args in commands)
    monkeypatch.setitem(main.__globals__, "run_once", lambda _: (-signal.SIGKILL, b""))
    monkeypatch.setitem(main.__globals__, "unblock_serial_accept",
                        lambda _: pytest.fail("intentional cut must not enqueue a dummy serial connection"))
    assert main() == 128 + signal.SIGKILL
    monkeypatch.setenv("EMONOS_QEMU_DISK_SIZE", "3M")
    with pytest.raises(RuntimeError, match="does not match"):
        main()
    assert overlay.stat().st_ino == original_inode

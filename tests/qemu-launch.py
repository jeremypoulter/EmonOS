#!/usr/bin/env python3
"""Launch QEMU with EmonOS-specific resource checks and targeted retries."""

import os
import json
from contextlib import nullcontext
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


IO_URING_ERROR = b"Failed to initialize io_uring"
MAX_ATTEMPTS = 3


def available_memory_mb() -> int:
    with open("/proc/meminfo", encoding="ascii") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable is missing from /proc/meminfo")


def overlay_directory() -> str | None:
    # An optional explicit override; otherwise tempfile uses the standard
    # TMPDIR/TEMP/TMP lookup. Never assume a harness-specific host path.
    return os.environ.get("EMONOS_QEMU_TMPDIR") or None


def run_image_command(arguments: list[str]) -> None:
    command = ["qemu-img", *arguments]
    if os.environ.get("EMONOS_QEMU_DISABLE_IO_URING") == "1":
        command = [
            "strace", "-f", "-qq", "-e", "trace=io_uring_setup", "-e",
            "inject=io_uring_setup:error=EPERM", *command,
        ]
    subprocess.run(command, check=True)


def replace_virtio_drive(arguments: list[str], overlay_path: str | None = None,
                         throttle: bool = False, overlay_format: str = "qcow2") -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(arguments):
        if index + 1 < len(arguments) and arguments[index] == "-drive":
            options = dict(
                field.split("=", 1) for field in arguments[index + 1].split(",") if "=" in field
            )
            if options.get("if") == "virtio" and "file" in options:
                aio = options.get("aio", "threads")
                filename = overlay_path or options["file"]
                disk_format = overlay_format if overlay_path else options.get("format", "raw")
                result.extend(
                    [
                        "-blockdev",
                        f"driver=file,filename={filename},aio={aio},node-name=vmfile",
                        "-blockdev",
                        f"driver={disk_format},file=vmfile,node-name=vmdisk",
                    ]
                )
                if throttle:
                    result.extend([
                        "-object", "throttle-group,id=emonos-iolimit",
                        "-blockdev", "driver=throttle,throttle-group=emonos-iolimit,file=vmdisk,node-name=limiteddisk",
                    ])
                result.extend(["-device", f"virtio-blk-pci,drive={'limiteddisk' if throttle else 'vmdisk'}"])
                index += 2
                continue
        result.append(arguments[index])
        index += 1
    return result


def run_once(command: list[str]) -> tuple[int, bytes]:
    process = subprocess.Popen(command, stderr=subprocess.PIPE)

    def forward_signal(signum, _frame):
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    stderr = bytearray()

    def copy_stderr() -> None:
        assert process.stderr is not None
        for chunk in iter(lambda: process.stderr.read(4096), b""):
            stderr.extend(chunk)
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()

    reader = threading.Thread(target=copy_stderr)
    reader.start()
    returncode = process.wait()
    reader.join()
    return returncode, bytes(stderr)


def unblock_serial_accept(arguments: list[str]) -> None:
    for index, argument in enumerate(arguments[:-1]):
        if argument != "-chardev":
            continue
        options = dict(
            field.split("=", 1) for field in arguments[index + 1].split(",") if "=" in field
        )
        path = options.get("path")
        if options.get("id") == "serialsocket" and path:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as serial:
                    serial.connect(path)
            except OSError:
                pass
            return


def main() -> int:
    qemu = os.environ.get("EMONOS_QEMU_BIN", "qemu-system-x86_64")
    arguments = sys.argv[1:]
    if "-version" in arguments or "--version" in arguments:
        return subprocess.call([qemu, *arguments])

    minimum_mb = int(os.environ.get("EMONOS_QEMU_MIN_AVAILABLE_MB", "3072"))
    available_mb = available_memory_mb()
    if available_mb < minimum_mb:
        print(
            f"QEMU requires {minimum_mb} MB MemAvailable; host has {available_mb} MB",
            file=sys.stderr,
        )
        return 1

    state_dir = os.environ.get("EMONOS_QEMU_STATE_DIR")
    context = nullcontext(state_dir) if state_dir else tempfile.TemporaryDirectory(
        prefix="emonos-qemu-", dir=overlay_directory(),
    )
    with context as tmpdir:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            overlay = f"{tmpdir}/disk.raw" if state_dir else f"{tmpdir}/disk.qcow2"
            for index, argument in enumerate(arguments[:-1]):
                if argument != "-drive":
                    continue
                options = dict(
                    field.split("=", 1) for field in arguments[index + 1].split(",") if "=" in field
                )
                if options.get("if") == "virtio" and "file" in options:
                    metadata = {"backing": os.path.realpath(options["file"]),
                                "size": os.environ.get("EMONOS_QEMU_DISK_SIZE"),
                                "format": "raw" if state_dir else "qcow2"}
                    state_file = f"{tmpdir}/state.json"
                    if state_dir and os.path.exists(overlay):
                        with open(state_file, encoding="utf-8") as saved:
                            if json.load(saved) != metadata:
                                raise RuntimeError("persistent QEMU state does not match backing disk/size")
                    else:
                        if state_dir:
                            if os.path.exists(state_file):
                                raise RuntimeError("persistent state metadata exists without its disk")
                            # A raw test clone models direct slot writes, not
                            # qcow2 mapping-cache commits lost with QEMU RAM.
                            subprocess.run(["cp", "--reflink=auto", "--sparse=always",
                                            options["file"], overlay], check=True)
                        else:
                            run_image_command([
                                "create", "-q", "-f", "qcow2", "-F", "raw", "-b", options["file"], overlay,
                            ])
                        if disk_size := metadata["size"]:
                            run_image_command(["resize", "-q", "-f", metadata["format"], overlay, disk_size])
                        if state_dir:
                            with open(state_file, "w", encoding="utf-8") as saved:
                                json.dump(metadata, saved)
                    break
            else:
                raise RuntimeError("no virtio disk to protect with a qcow2 overlay")

            # Persistent state is test-scoped for a real kill/restart. Never
            # allow -snapshot to silently make crash recovery discard writes.
            launch_args = [item for item in arguments if item != "-snapshot"] if state_dir else arguments
            command = [qemu, *replace_virtio_drive(launch_args, overlay, throttle=bool(state_dir),
                                                  overlay_format="raw" if state_dir else "qcow2")]
            if state_dir:
                command.extend(["-pidfile", f"{state_dir}/qemu.pid"])
            command.extend(["-device", "i6300esb", "-watchdog-action", "reset"])
            # Optional transport for T4: expose the host bundle read-only as
            # the second virtio disk. This is not an update/install operation.
            if bundle := os.environ.get("EMONOS_QEMU_RAUC_BUNDLE"):
                command.extend([
                    "-blockdev",
                    f"driver=file,filename={bundle},aio=threads,node-name=bundlefile,read-only=on",
                    "-blockdev",
                    "driver=raw,file=bundlefile,node-name=bundledisk,read-only=on",
                    "-device", "virtio-blk-pci,drive=bundledisk",
                ])
            if os.environ.get("EMONOS_QEMU_DISABLE_IO_URING") == "1":
                command = [
                    "strace", "-f", "-qq", "-e", "trace=io_uring_setup", "-e",
                    "inject=io_uring_setup:error=EPERM", *command,
                ]
            returncode, stderr = run_once(command)
            if returncode == 0:
                return 0
            if state_dir and returncode in (-signal.SIGKILL, 128 + signal.SIGKILL):
                # T7 intentionally killed a running QEMU. Do not enqueue a
                # dummy serial connection which would poison the next boot.
                return 128 + signal.SIGKILL
            if IO_URING_ERROR not in stderr or attempt == MAX_ATTEMPTS:
                unblock_serial_accept(arguments)
                return returncode
            if not state_dir:
                os.unlink(overlay)
            print(f"retrying QEMU after io_uring failure ({attempt}/{MAX_ATTEMPTS})", file=sys.stderr)
            time.sleep(attempt)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

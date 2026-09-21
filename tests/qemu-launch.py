#!/usr/bin/env python3
"""Launch QEMU with EmonOS-specific resource checks and targeted retries."""

import os
import signal
import socket
import subprocess
import sys
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


def replace_virtio_drive(arguments: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(arguments):
        if index + 1 < len(arguments) and arguments[index] == "-drive":
            options = dict(
                field.split("=", 1) for field in arguments[index + 1].split(",") if "=" in field
            )
            if options.get("if") == "virtio" and "file" in options:
                aio = options.get("aio", "threads")
                result.extend(
                    [
                        "-blockdev",
                        f"driver=file,filename={options['file']},aio={aio},node-name=vmfile",
                        "-blockdev",
                        f"driver={options.get('format', 'raw')},file=vmfile,node-name=vmdisk",
                        "-device",
                        "virtio-blk-pci,drive=vmdisk",
                    ]
                )
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

    command = [qemu, *replace_virtio_drive(arguments)]
    if os.environ.get("EMONOS_QEMU_DISABLE_IO_URING") == "1":
        command = [
            "strace",
            "-f",
            "-qq",
            "-e",
            "trace=io_uring_setup",
            "-e",
            "inject=io_uring_setup:error=EPERM",
            *command,
        ]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        returncode, stderr = run_once(command)
        if returncode == 0:
            return returncode
        if IO_URING_ERROR not in stderr or attempt == MAX_ATTEMPTS:
            unblock_serial_accept(arguments)
            return returncode
        print(f"retrying QEMU after io_uring failure ({attempt}/{MAX_ATTEMPTS})", file=sys.stderr)
        time.sleep(attempt)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

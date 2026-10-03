"""T7 host power adapter: kill only this labgrid driver's actual QEMU."""

import os
import json
import hashlib
import signal
import time
import urllib.parse
import urllib.request
from pathlib import Path

from labgrid.util import PtxExpect


TASMOTA = "http://172.16.1.2/cm?cmnd="


def tasmota(command: str) -> dict:
    request = urllib.request.Request(TASMOTA + urllib.parse.quote(command))
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def power1_state() -> tuple[str, str]:
    status = tasmota("Status 11")["StatusSTS"]
    return status["POWER1"], status["POWER5"]


def cut_pi_power1() -> dict:
    before = power1_state()
    assert before == ("ON", "ON"), f"unexpected relay state: {before}"
    tasmota("Power1 OFF")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = power1_state()
        assert state[1] == before[1], f"Power5 changed during Power1 cut: {state}"
        if state[0] == "OFF":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("Power1 did not turn off")
    time.sleep(5)
    tasmota("Power1 ON")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = power1_state()
        assert state[1] == before[1], f"Power5 changed during Power1 restore: {state}"
        if state[0] == "ON":
            return {"relay": "Power1", "before": before, "after": state,
                    "off_seconds": 5, "command": "Power1 OFF/ON"}
        time.sleep(0.1)
    raise AssertionError("Power1 did not turn back on")


def complete_guest_triggered_power_cut() -> dict:
    """Observe Power1 already OFF from the guest watcher, then restore it."""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        before = power1_state()
        assert before[1] == "ON", f"Power5 changed during Power1 cut: {before}"
        if before[0] == "OFF":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("guest did not turn off Power1 after write progress")
    time.sleep(5)
    tasmota("Power1 ON")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = power1_state()
        assert state[1] == before[1], f"Power5 changed during Power1 restore: {state}"
        if state[0] == "ON":
            return {"relay": "Power1", "before": before, "after": state,
                    "off_seconds": 5, "trigger": "guest-observed rootfs.1 write progress"}
        time.sleep(0.1)
    raise AssertionError("Power1 did not restore on")


def cut_qemu_power(target, command, state_dir: Path) -> dict:
    qemu = target.get_driver("QEMUDriver")
    pid = int((state_dir / "qemu.pid").read_text())
    process = qemu._child
    assert process is not None and process.poll() is None
    # A pidfd pins the exact process, avoiding PID reuse between checks/kill.
    descriptor = os.pidfd_open(pid)
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert Path(os.fsdecode(arguments[0])).name.startswith("qemu-system-")
        assert os.fsencode(str(state_dir / "qemu.pid")) in arguments
        ancestor = pid
        for _ in range(32):
            if ancestor == process.pid:
                break
            status = Path(f"/proc/{ancestor}/status").read_text()
            ancestor = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
            assert ancestor > 1
        else:
            raise AssertionError("PID file is not this driver's QEMU descendant")
        target.deactivate(command)
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    finally:
        os.close(descriptor)
    returncode = process.wait(timeout=30)
    process.communicate(timeout=5)
    # QEMUDriver.off() sends a graceful QMP quit and cannot handle an already
    # dead monitor. Clear just its process/socket bookkeeping, not disk state.
    qemu._child = None
    qemu.status = 0
    qemu._clientsocket.close()
    qemu._clientsocket = None
    qemu._expect = PtxExpect(qemu)
    assert returncode in (-signal.SIGKILL, 128 + signal.SIGKILL), returncode
    return {"qemu_pid": pid, "signal": "SIGKILL", "returncode": returncode}


def restore_qemu_power(target, command) -> None:
    qemu = target.get_driver("QEMUDriver")
    qemu.on()
    command.console.expect("Booting Slot A", timeout=180)
    command.console.expect("emonos login: ", timeout=180)
    command.console.sendline("")
    target.activate(command)

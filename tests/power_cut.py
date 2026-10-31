"""T7 power adapters for disposable QEMU and physical Raspberry Pi targets."""

import os
import signal
import subprocess
import time
from pathlib import Path

from labgrid.util import PtxExpect


FLASH_SCRIPT = Path(__file__).parents[1] / "flash.sh"


def pi_power_state() -> str:
    result = subprocess.run(
        [str(FLASH_SCRIPT), "status"], check=True, capture_output=True, text=True,
        env=os.environ.copy(), timeout=15,
    )
    state = result.stdout.strip()
    if state not in {"ON", "OFF"}:
        raise RuntimeError(f"unexpected Pi relay state: {state!r}")
    return state


def set_pi_power(state: str) -> None:
    if state not in {"ON", "OFF"}:
        raise ValueError(f"unexpected Pi relay state: {state!r}")
    subprocess.run(
        [str(FLASH_SCRIPT), state.lower()], check=True, capture_output=True,
        text=True, env=os.environ.copy(), timeout=30,
    )
    actual = pi_power_state()
    if actual != state:
        raise RuntimeError(f"Pi relay requested {state}, reports {actual}")


def cut_pi_power1() -> dict:
    before = pi_power_state()
    assert before == "ON", f"Pi power must be ON before the cut, reports {before}"
    try:
        set_pi_power("OFF")
        time.sleep(5)
    finally:
        set_pi_power("ON")
    return {"relay": "single-channel Pi Power1", "before": before,
            "after": pi_power_state(), "off_seconds": 5,
            "command": "flash.sh off/on"}


def complete_guest_triggered_power_cut() -> dict:
    """Cut Pi power only after the guest reports inactive-rootfs write progress."""
    before = pi_power_state()
    if before != "ON":
        raise RuntimeError(f"Pi power must be ON before the cut, reports {before}")
    try:
        set_pi_power("OFF")
        time.sleep(5)
    finally:
        set_pi_power("ON")
    after = pi_power_state()
    return {"relay": "single-channel Pi Power1", "before": before,
            "after": after, "off_seconds": 5,
            "trigger": "guest-observed rootfs.1 write progress",
            "command": "flash.sh off/on"}


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

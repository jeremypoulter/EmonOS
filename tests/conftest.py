"""Shared labgrid fixtures for EmonOS targets."""

from pathlib import Path
import os
import tempfile

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).parents[1]


@pytest.fixture(scope="session")
def target_name(env) -> str:
    return env.config.get_option("target")


@pytest.fixture(scope="session")
def expected_architecture(env) -> str:
    return env.config.get_option("architecture")


@pytest.fixture(scope="session")
def command(target, target_name: str, repo_root: Path):
    old_state = os.environ.get("EMONOS_QEMU_STATE_DIR")
    if target_name == "x86-64-vm" and os.environ.get("EMONOS_RUN_POWER_CUT_TEST") == "1":
        if old_state:
            pytest.fail("T7 requires a fresh test-owned state directory, not a supplied state path")
        # Retained multi-GiB crash state belongs on the project disk, not a
        # possibly RAM-backed /tmp that can stall a healthy factory bootstrap.
        states = repo_root / "output/test-state"
        states.mkdir(parents=True, exist_ok=True)
        os.environ["EMONOS_QEMU_STATE_DIR"] = tempfile.mkdtemp(prefix="t7-", dir=states)
    if target_name == "x86-64-vm":
        qemu = target.get_driver("QEMUDriver")
        qemu.on()

    try:
        yield target.get_driver("CommandProtocol")
    finally:
        try:
            target.cleanup()
        finally:
            if old_state is None:
                os.environ.pop("EMONOS_QEMU_STATE_DIR", None)
            else:
                os.environ["EMONOS_QEMU_STATE_DIR"] = old_state

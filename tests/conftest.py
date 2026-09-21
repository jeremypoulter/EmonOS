"""Shared labgrid fixtures for EmonOS targets."""

from pathlib import Path

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
def command(target, target_name: str):
    if target_name == "x86-64-vm":
        qemu = target.get_driver("QEMUDriver")
        qemu.on()

    try:
        yield target.get_driver("CommandProtocol")
    finally:
        target.cleanup()

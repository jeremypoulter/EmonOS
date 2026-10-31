"""Host-side safeguards for the Pi power-cut relay adapter."""

from types import SimpleNamespace

import pytest

import power_cut


def test_guest_observed_cut_uses_flash_adapter_and_restores_power(monkeypatch):
    states = iter(("ON\n", "OFF\n", "ON\n", "ON\n"))
    calls = []

    def run(arguments, **_kwargs):
        calls.append(arguments[1])
        if arguments[1] == "status":
            return SimpleNamespace(stdout=next(states))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(power_cut.subprocess, "run", run)
    monkeypatch.setattr(power_cut.time, "sleep", lambda seconds: calls.append(seconds))

    evidence = power_cut.complete_guest_triggered_power_cut()

    assert calls == ["status", "off", "status", 5, "on", "status", "status"]
    assert evidence == {
        "relay": "single-channel Pi Power1",
        "before": "ON",
        "after": "ON",
        "off_seconds": 5,
        "trigger": "guest-observed rootfs.1 write progress",
        "command": "flash.sh off/on",
    }


def test_guest_observed_cut_refuses_to_toggle_power_already_off(monkeypatch):
    monkeypatch.setattr(
        power_cut.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="OFF\n"),
    )
    with pytest.raises(RuntimeError, match="must be ON"):
        power_cut.complete_guest_triggered_power_cut()

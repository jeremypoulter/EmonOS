"""WP7: autonomous commit and rollback over the common serial harness."""

import hashlib
import json
import os
import shlex
from pathlib import Path

import pytest

from test_rauc import active_slot_hashes, target_file_hash, target_rauc_json
from common import app_check_command


def read_record(command, path: str) -> dict:
    lines = command.run_check(f"jq -c . {shlex.quote(path)}")
    records = [json.loads(line) for line in lines if line.startswith("{")]
    assert len(records) == 1, lines
    return records[0]


def wait_for_commit(command) -> dict:
    command.run_check("command -v timeout >/dev/null && command -v jq >/dev/null")
    assert command.poll_until_success(
        "systemctl is-active --quiet emonos-health.service", tries=80,
        timeout=315.0, sleepduration=4,
    ), command.run_check("journalctl -u emonos-health.service --no-pager -n 40")
    record = read_record(command, "/mnt/data/health/last-good.json")
    assert record["boot_id"] == command.run_check("cat /proc/sys/kernel/random/boot_id")[0]
    assert record["outcome"] == "good" and record["elapsed_seconds"] <= 300
    return record


def reboot_into(command, target, slot: str, *, autonomous: bool = False) -> None:
    if not autonomous:
        command.run_check("sync")
        command.console.sendline("systemctl reboot")
    target.deactivate(command)
    command.console.expect(f"Booting Slot {slot}", timeout=420 if autonomous else 180)
    command.console.expect("emonos login: ", timeout=180)
    # Redraw login and let ShellDriver handle it. Manual root login bypasses
    # its post-login kernel-console silencing and prompt-settle path.
    command.console.sendline("")
    target.activate(command)


def prepare_update(command, target_name: str, host_path: str, version: str) -> dict:
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    assert wait_for_commit(command)["slot"] == "A"
    command.run_check(app_check_command(), timeout=120)
    feed = command.run_check("find /mnt/data/emoncms/phpfina -name '*.dat' | head -1")[0]
    original = {
        "feed": feed, "feed_hash": target_file_hash(command, feed),
        "id": command.run_check("cat /etc/machine-id")[0],
        "key": target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub"),
        "a_images": active_slot_hashes(command),
    }
    bundle = Path(host_path)
    with bundle.open("rb") as source:
        expected = hashlib.file_digest(source, "sha256").hexdigest()
    path = "/mnt/data/wp7-update.raucb"
    if target_name == "x86-64-vm":
        assert os.environ.get("EMONOS_QEMU_RAUC_BUNDLE") == str(bundle)
        command.run_check(f"head -c {bundle.stat().st_size} /dev/vdb > {path}", timeout=120)
    else:
        source = os.environ["EMONOS_WP7_BUNDLE_PATH"]
        if source != path:
            command.run_check(f"cp {shlex.quote(source)} {path}", timeout=120)
    assert target_file_hash(command, path).split()[0] == expected
    info = target_rauc_json(command, f"info --output-format=json {path}")
    assert info["compatible"] == f"emonos-{target_name}" and info["version"] == version
    _, _, status = command.run(f"rauc install {path} >/mnt/data/wp7-install.log 2>&1", timeout=360)
    assert status == 0, command.run_check("tail -80 /mnt/data/wp7-install.log")
    assert target_rauc_json(command, "status --output-format=json")["boot_primary"] == "kernel.1"
    return original


def assert_preserved(command, original: dict) -> None:
    assert target_file_hash(command, original["feed"]) == original["feed_hash"]
    assert command.run_check("cat /etc/machine-id") == [original["id"]]
    assert target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub") == original["key"]
    assert active_slot_hashes(command) == original["a_images"]
    command.run_check('test -z "$(systemctl --failed --no-pager --no-legend --plain)"')


@pytest.mark.timeout(400)
def test_health_commits_factory_slot(command) -> None:
    """HC-1: factory app passes strict authenticated checks and mark-good."""
    record = wait_for_commit(command)
    assert record["slot"] == "A" and record["version"] == "0.1.0"
    status = target_rauc_json(command, "status --output-format=json")
    assert status["booted"] == "A" and status["boot_primary"] == "kernel.0"


def test_runtime_watchdog_is_active(command) -> None:
    """OS-10: PID1 feeds a real watchdog driver on each target."""
    command.run_check("test -e /dev/watchdog0")
    assert command.run_check("cat /sys/class/watchdog/watchdog0/state") == ["active"]
    command.run_check("systemctl show -p RuntimeWatchdogUSec | grep -q '30s'")


@pytest.mark.timeout(320)
def test_stage_pi_health_bundle(command, target_name: str) -> None:
    """WP7 setup: stage a public bundle for a later separately gated install."""
    if target_name != "rpi4" or not (url := os.environ.get("EMONOS_WP7_BUNDLE_URL")):
        pytest.skip("provide EMONOS_WP7_BUNDLE_URL on the Pi to stage a health-test bundle")
    source = Path(os.environ["EMONOS_WP7_BUNDLE_HOST"])
    with source.open("rb") as bundle:
        expected = hashlib.file_digest(bundle, "sha256").hexdigest()
    path = "/mnt/data/wp7-staged.raucb"
    command.run_check(
        f"curl -fsS --max-time 240 --proto '=http,https' {shlex.quote(url)} "
        f"-o {path}.part && mv {path}.part {path}", timeout=260,
    )
    assert target_file_hash(command, path).split()[0] == expected
    info = target_rauc_json(command, f"info --output-format=json {path}")
    assert info["compatible"] == "emonos-rpi4"
    assert info["version"] in ("0.2.0", "0.2.0-broken")


@pytest.mark.timeout(1500)
def test_healthy_update_commits_and_reboots(command, target, target_name: str) -> None:
    """T5, HC-1: healthy B marks itself good and survives a second boot."""
    if os.environ.get("EMONOS_RUN_COMMIT_TEST") != "1":
        pytest.skip("set EMONOS_RUN_COMMIT_TEST=1 for a healthy update and two reboots")
    original = prepare_update(command, target_name, os.environ["EMONOS_WP7_BUNDLE_HOST"], "0.2.0")
    reboot_into(command, target, "B")
    record = wait_for_commit(command)
    assert record["slot"] == "B" and record["version"] == "0.2.0"
    assert target_rauc_json(command, "status --output-format=json")["boot_primary"] == "kernel.1"
    assert_preserved(command, original)
    reboot_into(command, target, "B")
    assert wait_for_commit(command)["slot"] == "B"
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.2.0"]
    assert_preserved(command, original)
    command.run_check(app_check_command(), timeout=120)
    # Physical tests share one card. Explicitly return to healthy factory A
    # when requested so the broken-B test can follow without a reflash.
    if os.environ.get("EMONOS_WP7_RESTORE_A") == "1":
        command.run_check("rauc status mark-active kernel.0")
        reboot_into(command, target, "A")
        assert wait_for_commit(command)["slot"] == "A"
        assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
        assert_preserved(command, original)


@pytest.mark.timeout(1500)
def test_broken_update_rolls_back_autonomously(command, target, target_name: str) -> None:
    """T6, HC-4, OS-8, HC-9: broken B times out and autonomously returns to A."""
    if os.environ.get("EMONOS_RUN_ROLLBACK_TEST") != "1":
        pytest.skip("set EMONOS_RUN_ROLLBACK_TEST=1 to install the broken app bundle")
    original = prepare_update(command, target_name, os.environ["EMONOS_WP7_BUNDLE_HOST"], "0.2.0-broken")
    reboot_into(command, target, "B")
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.2.0-broken"]
    assert command.poll_until_success("systemctl is-failed --quiet emonos-app.service", tries=20,
                                      timeout=40.0, sleepduration=2)
    failed_boot_id = command.run_check("cat /proc/sys/kernel/random/boot_id")[0]
    # No host/client reboot request: the shipped health monitor must cause it.
    reboot_into(command, target, "A", autonomous=True)
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    assert wait_for_commit(command)["slot"] == "A"
    record = read_record(command, f"/mnt/data/health/failures/{failed_boot_id}.json")
    assert record["slot"] == "B" and record["version"] == "0.2.0-broken"
    assert record["outcome"] == "failed" and 299 <= record["elapsed_seconds"] <= 301
    assert record["reason"] == "application service not active"
    assert_preserved(command, original)

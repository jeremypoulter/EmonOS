"""T7: crash a real install and reboot the same guest disk state."""

import hashlib
import json
import os
import shlex
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest

from power_cut import (complete_guest_triggered_power_cut, cut_qemu_power,
                       power1_state, restore_qemu_power)
from test_rauc import active_slot_hashes, target_file_hash, target_rauc_json
from test_rollback import assert_preserved, wait_for_commit
from common import app_check_command


def partition_hash(image: Path, index: int) -> str:
    table = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)], text=True))["partitiontable"]
    partition = table["partitions"][index - 1]
    sector = table["sectorsize"]
    remaining = partition["size"] * sector
    digest = hashlib.sha256()
    with image.open("rb") as disk:
        disk.seek(partition["start"] * sector)
        while remaining:
            block = disk.read(min(remaining, 4 * 1024 * 1024))
            assert block, "truncated disk image"
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def changed_extent(image: Path, replacement: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    table = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)], text=True))["partitiontable"]
    offset = table["partitions"][4]["start"] * table["sectorsize"]
    first, last, position = None, None, 0
    with image.open("rb") as original, replacement.open("rb") as new:
        original.seek(offset)
        while block := new.read(4 * 1024 * 1024):
            old = original.read(len(block))
            assert len(old) == len(block)
            if old != block:
                if first is None:
                    index = next(i for i in range(len(block)) if old[i] != block[i])
                    first = (offset + position + index, block[index])
                index = next(i for i in range(len(block) - 1, -1, -1) if old[i] != block[i])
                last = (offset + position + index, block[index])
            position += len(block)
    assert first is not None and last is not None and first[0] < last[0]
    return first, last


def read_backing_region(state: Path, offset: int, length: int) -> bytes:
    # Read-only external inspection avoids guest page cache and QMP's
    # qemu-io block-drain semantics, which can finish all queued writes.
    # The concurrent view is only a trigger; a full hash after SIGKILL is the
    # authoritative proof that B was genuinely changed but incomplete.
    with (state / "disk.raw").open("rb") as backing:
        backing.seek(offset)
        content = backing.read(length)
    assert len(content) == length
    return content


@pytest.mark.timeout(1200)
def test_interrupted_install_keeps_active_slot(command, target, target_name: str, repo_root: Path) -> None:
    """T7, OS-14: cut power during an observed inactive-slot write and recover."""
    if os.environ.get("EMONOS_RUN_POWER_CUT_TEST") != "1":
        pytest.skip("set EMONOS_RUN_POWER_CUT_TEST=1 for an interrupted install")
    if target_name == "rpi4":
        return run_interrupted_pi_install(command, target, target_name)
    state = Path(os.environ["EMONOS_QEMU_STATE_DIR"])
    base = Path(os.environ["LG_X86_DISK"])
    bundle = Path(os.environ["EMONOS_QEMU_RAUC_BUNDLE"])
    assert wait_for_commit(command)["slot"] == "A"
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    command.run_check(app_check_command(), timeout=120)
    feed = command.run_check("find /mnt/data/emoncms/phpfina -name '*.dat' | head -1")[0]
    original = {"feed": feed, "feed_hash": target_file_hash(command, feed),
                "id": command.run_check("cat /etc/machine-id")[0],
                "key": target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub"),
                "a_images": active_slot_hashes(command)}
    original_b = partition_hash(base, 5)
    command.run_check(f"head -c {bundle.stat().st_size} /dev/vdb > /mnt/data/t7-update.raucb", timeout=120)
    with bundle.open("rb") as source:
        expected_bundle = hashlib.file_digest(source, "sha256").hexdigest()
    assert target_file_hash(command, "/mnt/data/t7-update.raucb").split()[0] == expected_bundle
    manifest = target_rauc_json(command, "info --output-format=json /mnt/data/t7-update.raucb")
    assert manifest["compatible"] == "emonos-x86-64-vm" and manifest["version"] == "0.2.0"
    new_root = next(entry["rootfs"]["checksum"] for entry in manifest["images"] if "rootfs" in entry)
    assert original_b != new_root
    payload = state / "payload"
    subprocess.run([
        str(repo_root / "output/x86-64-vm/host/bin/rauc"),
        f"--keyring={repo_root / 'output/x86-64-vm/target/etc/rauc/keyring.pem'}",
        "extract", str(bundle), str(payload),
    ], check=True, capture_output=True)
    first_changed, last_changed = changed_extent(base, payload / "rootfs.img")
    region_start = first_changed[0] // 65536 * 65536
    region_length = (last_changed[0] // 65536 + 1) * 65536 - region_start
    table = json.loads(subprocess.check_output(["sfdisk", "--json", str(base)], text=True))["partitiontable"]
    b_start = table["partitions"][4]["start"] * table["sectorsize"]
    with base.open("rb") as disk, (payload / "rootfs.img").open("rb") as replacement:
        disk.seek(region_start)
        replacement.seek(region_start - b_start)
        old_region = disk.read(region_length)
        new_region = replacement.read(region_length)
    command.run_check("sync")
    qemu = target.get_driver("QEMUDriver")
    # Bound the write window for reliable observation without slowing factory
    # bootstrap. This is guest-device I/O throttling, not a host policy change.
    qemu.monitor_command("qom-set", {"path": "/objects/emonos-iolimit", "property": "limits",
                                    "value": {"bps-write": 1024 * 1024}})
    command.run_check("rauc install /mnt/data/t7-update.raucb >/mnt/data/t7-install.log 2>&1 &")
    # Keep output on serial so the cut follows observed progress, not a sleep.
    command.console.sendline(
        "while ! grep -qE '^ [4-8][0-9]% Copying image to rootfs.1$' /mnt/data/t7-install.log; "
        "do sleep 1; done; echo T7_ROOTFS_WRITE_PROGRESS; tail -3 /mnt/data/t7-install.log"
    )
    command.console.expect("T7_ROOTFS_WRITE_PROGRESS\\r?\\n", timeout=180)
    _, preceding, match, _ = command.console.expect(r"([4-8][0-9])% Copying image to rootfs\.1", timeout=30)
    progress = int(match.group(1))
    # RAUC buffered progress may be ahead of physical writes. Probe the
    # QEMU's disk backing itself (not guest page cache) for a changed yet
    # incomplete region. Whole-region comparison also tolerates reordered
    # kernel writeback; two specific bytes need not land in offset order.
    deadline = time.monotonic() + 240
    while True:
        observed_region = read_backing_region(state, region_start, region_length)
        assert observed_region != new_region, "missed the partial-payload write window"
        if observed_region != old_region:
            break
        assert time.monotonic() < deadline, "no changed payload byte reached inactive B"
        time.sleep(0.1)
    evidence = {"phase": "rootfs.1", "observed_copy_progress": progress,
                "progress_note": "RAUC log sample before backing-region probe; not physical write percentage",
                "base_disk": str(base), "bundle_sha256": expected_bundle,
                "state_directory": str(state), "firmware": "unchanged read-only OVMF -bios"}
    evidence["changed_extent_probes"] = {"first": first_changed, "last": last_changed}
    evidence["observed_partial_region"] = {"offset": region_start, "length": region_length,
                                          "sha256": hashlib.sha256(observed_region).hexdigest()}
    evidence.update(cut_qemu_power(target, command, state))
    (state / "cut-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    restore_qemu_power(target, command)
    assert wait_for_commit(command)["slot"] == "A"
    # Recovery boots read-only A and has no auto-install. Inactive B is not
    # mounted or rewritten; read it directly instead of duplicating a 7 GiB
    # disk into temporary storage solely for inspection.
    crashed_b = target_file_hash(command, "/dev/disk/by-partlabel/system-b").split()[0]
    evidence["inactive_sha256"] = crashed_b
    evidence["original_inactive_sha256"] = original_b
    evidence["complete_update_sha256"] = new_root
    assert crashed_b not in (original_b, new_root), evidence
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    status = target_rauc_json(command, "status --output-format=json")
    assert status["booted"] == "A" and status["boot_primary"] == "kernel.0"
    assert_preserved(command, original)
    evidence["recovery"] = "A/0.1.0 healthy, feed/identity/A bytes preserved"
    evidence["preserved_fixture"] = original
    (state / "cut-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"T7 evidence: {state / 'cut-evidence.json'}")


def run_interrupted_pi_install(command, target, target_name: str) -> None:
    """T7 Pi adapter: Power1 cuts only after observed B rootfs write progress."""
    assert target_name == "rpi4"
    state_file = "/mnt/data/wp7-install.log"
    assert wait_for_commit(command)["slot"] == "A"
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    command.run_check(app_check_command(), timeout=120)
    feed = command.run_check("find /mnt/data/emoncms/phpfina -name '*.dat' | head -1")[0]
    original = {"feed": feed, "feed_hash": target_file_hash(command, feed),
                "id": command.run_check("cat /etc/machine-id")[0],
                "key": target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub"),
                "a_images": active_slot_hashes(command)}
    original_b = target_file_hash(command, "/dev/disk/by-partlabel/system-b").split()[0]
    bundle = Path(os.environ["EMONOS_WP7_BUNDLE_HOST"])
    expected_bundle = hashlib.file_digest(bundle.open("rb"), "sha256").hexdigest()
    supplied = os.environ["EMONOS_WP7_BUNDLE_PATH"]
    assert supplied.startswith("/mnt/data/")
    target_bundle = "/mnt/data/t7-update.raucb"
    if supplied != target_bundle:
        command.run_check(f"cp {shlex.quote(supplied)} {target_bundle}", timeout=120)
    assert target_file_hash(command, target_bundle).split()[0] == expected_bundle
    manifest = target_rauc_json(command, f"info --output-format=json {target_bundle}")
    assert manifest["version"] == "0.2.0"
    new_root = next(entry["rootfs"]["checksum"] for entry in manifest["images"] if "rootfs" in entry)
    assert power1_state() == ("ON", "ON"), "Power1 and Power5 must be on before install"
    command.run_check("sync")
    # Progress is global across kernel and rootfs images. rootfs.1 starts near
    # 73%, so target an early 75--85% rootfs write, not 5--15% overall.
    progress_pattern = r"^ (7[5-9]|8[0-5])% Copying image to rootfs.1$"
    power_command = urllib.parse.quote("Power1 OFF")
    watcher_path = "/mnt/data/t7-power-watch.sh"
    watcher_script = (
        "#!/bin/sh\nset -eu\n"
        f"while ! grep -qE {shlex.quote(progress_pattern)} {state_file}; do sleep 0.1; done\n"
        f"line=$(grep -E {shlex.quote(progress_pattern)} {state_file} | tail -1)\n"
        "printf 'T7_POWER_CUT_STAGE:%s\\n' \"$line\" >/dev/ttyS0\n"
        f"curl -fsS --max-time 3 'http://172.16.1.2/cm?cmnd={power_command}' "
        ">/mnt/data/t7-power-command.json\nsync\n"
    )
    command.run_check(
        f": >{state_file}; rm -f /mnt/data/t7-power-command.json; "
        f"printf %s {shlex.quote(watcher_script)} >{watcher_path}; chmod 700 {watcher_path}; "
        "systemd-run --unit=emonos-t7-power-watch --collect --no-block /bin/sh "
        f"{watcher_path}; rauc install {target_bundle} >{state_file} 2>&1 &",
    )
    evidence = {"target": "rpi4", "phase": "rootfs.1",
                "bundle_sha256": expected_bundle, "firmware": "Pi EEPROM/U-Boot unchanged",
                "bootstate": "same raw partition; no flash or reset", "before": original}

    # Detach only ShellDriver before power disappears; leave SerialDriver ready
    # to observe firmware/kernel output and recover the login after Power1 on.
    target.deactivate(command)
    _, _, stage_match, _ = command.console.expect(
        r"T7_POWER_CUT_STAGE: (7[5-9]|8[0-5])% Copying image to rootfs\.1", timeout=240,
    )
    evidence["observed_copy_progress"] = int(stage_match.group(1))
    evidence.update(complete_guest_triggered_power_cut())
    command.console.expect("Booting Slot A", timeout=300)
    command.console.expect("emonos login: ", timeout=300)
    command.console.sendline("")
    target.activate(command)

    assert wait_for_commit(command)["slot"] == "A"
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    status = target_rauc_json(command, "status --output-format=json")
    assert status["booted"] == "A" and status["boot_primary"] == "kernel.0"
    crashed_b = target_file_hash(command, "/dev/disk/by-partlabel/system-b").split()[0]
    evidence.update({"inactive_sha256": crashed_b, "original_inactive_sha256": original_b,
                     "complete_update_sha256": new_root})
    assert crashed_b not in (original_b, new_root), evidence
    assert_preserved(command, original)
    evidence["recovery"] = "A/0.1.0 healthy, feed/identity/A payloads preserved"
    output = Path(os.environ.get("EMONOS_T7_EVIDENCE", "output/test-state/rpi4-t7-evidence.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"T7 evidence: {output}")

"""T4: verify signed bundles and read-only runtime slot reporting, not installs."""

import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest


def target_rauc_json(command, arguments: str) -> dict:
    """Keep GLib diagnostics separate from JSON on a merged serial console."""
    stdout, stderr, returncode = command.run(
        f"rauc {arguments} >/tmp/emonos-rauc.json 2>/tmp/emonos-rauc.log", timeout=120,
    )
    logs = command.run_check("cat /tmp/emonos-rauc.log")
    assert returncode == 0, "\n".join([*stdout, *stderr, *logs])
    return json.loads("\n".join(command.run_check("cat /tmp/emonos-rauc.json")))


def active_slot_hashes(command) -> list[str]:
    """Filter kernel diagnostics interleaved with serial output after boot."""
    command.run_check(
        "sha256sum /dev/disk/by-partlabel/kernel-a /dev/disk/by-partlabel/system-a "
        ">/tmp/emonos-a-images.sha256", timeout=120,
    )
    lines = command.run_check("cat /tmp/emonos-a-images.sha256")
    result = sorted(line for line in lines if re.fullmatch(
        r"[0-9a-f]{64}  /dev/disk/by-partlabel/(kernel|system)-a", line,
    ))
    assert len(result) == 2, lines
    return result


def target_file_hash(command, path: str) -> str:
    """Keep boot-time kernel messages out of a serial SHA-256 result."""
    command.run_check(
        f"sha256sum {shlex.quote(path)} >/tmp/emonos-file.sha256", timeout=120,
    )
    lines = command.run_check("cat /tmp/emonos-file.sha256")
    matches = [line for line in lines if re.fullmatch(
        rf"[0-9a-f]{{64}}  {re.escape(path)}", line,
    )]
    assert len(matches) == 1, lines
    return matches[0]


def test_rauc_bundle_payloads(target_name: str, repo_root: Path, tmp_path: Path) -> None:
    """T4: signed bundle has exactly the factory kernel/system payloads."""
    output = repo_root / "output" / target_name
    images = output / "images"
    bundle = images / f"emonos-{target_name}.raucb"
    rauc = output / "host/bin/rauc"
    keyring = output / "target/etc/rauc/keyring.pem"
    assert bundle.is_file(), f"build the WP5 {target_name} image first"
    result = subprocess.check_output(
        [str(rauc), f"--keyring={keyring}", "info", "--output-format=json", str(bundle)],
        text=True,
    )
    info = json.loads(result)
    assert info["compatible"] == f"emonos-{target_name}"
    assert info["version"] == (output / "target/usr/lib/emonos/version").read_text().strip()
    subprocess.run(
        [str(rauc), f"--keyring={keyring}", "extract", str(bundle), str(tmp_path / "payload")],
        check=True,
    )
    payload = tmp_path / "payload"
    assert {p.name for p in payload.iterdir()} == {
        "kernel.img", "rootfs.img", "manifest.raucm", "install-check",
    }
    for source, packaged in (("kernel.img", "kernel.img"), ("system.img", "rootfs.img")):
        with (images / source).open("rb") as original, (payload / packaged).open("rb") as copied:
            assert hashlib.file_digest(original, "sha256").digest() == hashlib.file_digest(
                copied, "sha256"
            ).digest()
    # No private signing material is permitted in the immutable root.
    assert not list((output / "target").rglob("dev-key.pem"))


def test_rauc_rejects_untrusted_signer(target_name: str, repo_root: Path, tmp_path: Path) -> None:
    """T4: a bundle must not verify against an unrelated development keyring."""
    subprocess.run([str(repo_root / "buildroot-external/ota/dev-keys.sh"), str(tmp_path)],
                   check=True, capture_output=True)
    output = repo_root / "output" / target_name
    result = subprocess.run([
        str(output / "host/bin/rauc"), f"--keyring={tmp_path / 'dev-cert.pem'}",
        "info", str(output / f"images/emonos-{target_name}.raucb"),
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert "signature" in result.stderr.lower() or "certificate" in result.stderr.lower()


@pytest.mark.timeout(180)
def test_rauc_status(command, target_name: str) -> None:
    """T4: RAUC reads the native bootloader state and both grouped raw slots."""
    command.run_check("systemctl is-active --quiet rauc.service")
    result = target_rauc_json(command, "status --detailed --output-format=json")
    assert result["compatible"] == f"emonos-{target_name}"
    assert result["booted"] == "A", json.dumps(result)
    if result["boot_primary"] != "kernel.0":
        if target_name == "x86-64-vm":
            environment = command.run_check("grub-editenv /mnt/boot/EFI/BOOT/grubenv list")
        else:
            environment = command.run_check("fw_printenv BOOT_ORDER BOOT_A_LEFT BOOT_B_LEFT")
        pytest.fail("Unexpected boot primary: " + json.dumps(result) + "\n" + "\n".join(environment))
    slots = {name: details for entry in result["slots"] for name, details in entry.items()}
    assert set(slots) == {"boot.0", "kernel.0", "kernel.1", "rootfs.0", "rootfs.1"}
    version = command.run_check("cat /usr/lib/emonos/version")[0]
    for index, bootname in ((0, "A"), (1, "B")):
        kernel = slots[f"kernel.{index}"]
        rootfs = slots[f"rootfs.{index}"]
        assert kernel["bootname"] == bootname
        assert kernel["boot_status"] == ("good" if index == 0 else "bad")
        assert rootfs["parent"] == f"kernel.{index}"
        assert kernel["state"] == ("booted" if index == 0 else "inactive")
        for slot in (kernel, rootfs):
            assert slot["slot_status"]["bundle"]["version"] == version
    command.run_check("test -w /mnt/data/rauc")


@pytest.mark.timeout(300)
def test_rauc_bundle_on_target(command, target_name: str, repo_root: Path) -> None:
    """T4: target verifies with its baked keyring, without --no-verify or installs."""
    path = os.environ.get("EMONOS_RAUC_BUNDLE_PATH")
    if url := os.environ.get("EMONOS_RAUC_BUNDLE_URL"):
        path = "/mnt/data/wp5-test.raucb"
        command.run_check(
            f"curl -fsS --max-time 240 --proto '=http,https' {shlex.quote(url)} "
            f"-o {path}.part && mv {path}.part {path}", timeout=260,
        )
        host_bundle = repo_root / f"output/{target_name}/images/emonos-{target_name}.raucb"
        with host_bundle.open("rb") as source:
            expected = hashlib.file_digest(source, "sha256").hexdigest()
        assert command.run_check(f"sha256sum {path}")[0].split()[0] == expected
    if target_name == "x86-64-vm" and os.environ.get("EMONOS_QEMU_RAUC_BUNDLE"):
        host_bundle = Path(os.environ["EMONOS_QEMU_RAUC_BUNDLE"])
        path = "/mnt/data/wp5-test.raucb"
        command.run_check(f"head -c {host_bundle.stat().st_size} /dev/vdb > {path}", timeout=120)
        with host_bundle.open("rb") as source:
            expected = hashlib.file_digest(source, "sha256").hexdigest()
        assert command.run_check(f"sha256sum {path}")[0].split()[0] == expected
    if not path:
        pytest.skip("provide a QEMU bundle, target bundle path or bundle URL for T4")
    result = target_rauc_json(command, f"info --output-format=json {shlex.quote(path)}")
    assert result["compatible"] == f"emonos-{target_name}"
    assert result["version"] == command.run_check("cat /usr/lib/emonos/version")[0]


@pytest.mark.timeout(320)
def test_stage_pi_update_bundle(command, target_name: str) -> None:
    """T5 setup: stage a public signed Pi bundle; do not install it yet."""
    if target_name != "rpi4" or not (url := os.environ.get("EMONOS_V2_BUNDLE_URL")):
        pytest.skip("set EMONOS_V2_BUNDLE_URL on the Pi to stage the v2 bundle")
    host_bundle = Path(os.environ["EMONOS_V2_BUNDLE_HOST"])
    with host_bundle.open("rb") as source:
        expected_hash = hashlib.file_digest(source, "sha256").hexdigest()
    path = "/mnt/data/wp6-v2.raucb"
    command.run_check(
        f"curl -fsS --max-time 240 --proto '=http,https' {shlex.quote(url)} "
        f"-o {path}.part && mv {path}.part {path}", timeout=260,
    )
    assert command.run_check(f"sha256sum {path}")[0].split()[0] == expected_hash
    info = target_rauc_json(command, f"info --output-format=json {path}")
    assert info["compatible"] == "emonos-rpi4"
    assert info["version"] == "0.2.0"
    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]


@pytest.mark.timeout(1200)
def test_update_round_trip(command, target, target_name: str) -> None:
    """T5: install signed v2 into inactive B; reboot and keep the data feed."""
    if os.environ.get("EMONOS_RUN_UPDATE_TEST") != "1":
        pytest.skip("set EMONOS_RUN_UPDATE_TEST=1 to install into inactive B and reboot")

    host_bundle = Path(os.environ["EMONOS_V2_BUNDLE_HOST"])
    assert host_bundle.is_file(), f"missing v2 bundle: {host_bundle}"
    with host_bundle.open("rb") as source:
        expected_hash = hashlib.file_digest(source, "sha256").hexdigest()
    path = "/mnt/data/wp6-v2.raucb"

    assert command.run_check("cat /usr/lib/emonos/version") == ["0.1.0"]
    command.run_check("grep -q 'rauc.slot=A' /proc/cmdline")
    before = target_rauc_json(command, "status --detailed --output-format=json")
    assert before["booted"] == "A" and before["boot_primary"] == "kernel.0"
    assert command.poll_until_success(
        "systemctl is-active --quiet emonos-app.service", tries=150,
        timeout=600.0, sleepduration=4,
    )
    test_user = os.environ.get("EMONOS_APP_TEST_USER", "")
    command.run_check(
        f"EMONOS_APP_TEST_USER={shlex.quote(test_user)} /usr/libexec/emonos-app-check",
        timeout=120,
    )
    feed = command.run_check("find /mnt/data/emoncms/phpfina -name '*.dat' | head -1")[0]
    assert feed.startswith("/mnt/data/emoncms/phpfina/")
    feed_hash = target_file_hash(command, feed)
    machine_id = command.run_check("cat /etc/machine-id")[0]
    host_key = target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub")
    active_images = active_slot_hashes(command)

    if target_name == "x86-64-vm":
        assert os.environ.get("EMONOS_QEMU_RAUC_BUNDLE") == str(host_bundle)
        command.run_check(f"head -c {host_bundle.stat().st_size} /dev/vdb > {path}", timeout=120)
    else:
        supplied_path = os.environ["EMONOS_V2_BUNDLE_PATH"]
        assert supplied_path.startswith("/mnt/data/")
        if supplied_path != path:
            command.run_check(f"cp {shlex.quote(supplied_path)} {path}", timeout=120)
    assert command.run_check(f"sha256sum {path}")[0].split()[0] == expected_hash
    manifest = target_rauc_json(command, f"info --output-format=json {path}")
    assert manifest["compatible"] == f"emonos-{target_name}"
    assert manifest["version"] == "0.2.0"

    _, _, install_status = command.run(
        f"rauc install {path} > /mnt/data/wp6-install.log 2>&1", timeout=360,
    )
    assert install_status == 0, "\n".join(command.run_check("tail -80 /mnt/data/wp6-install.log"))
    installed = target_rauc_json(command, "status --detailed --output-format=json")
    assert installed["booted"] == "A" and installed["boot_primary"] == "kernel.1"
    command.run_check(f"test -z \"$(systemctl --failed --no-pager --no-legend --plain)\"")
    command.run_check("sync")
    command.console.sendline("systemctl reboot")
    target.deactivate(command)
    # Serial announcement comes from GRUB/U-Boot, before systemd login.
    command.console.expect("Booting Slot B", timeout=180)
    command.console.expect("emonos login: ", timeout=180)
    command.console.sendline("root")
    target.activate(command)

    assert command.run_check("cat /usr/lib/emonos/version") == ["0.2.0"]
    assert 'VERSION_ID="0.2.0"' in "\n".join(command.run_check("cat /etc/os-release"))
    command.run_check("grep -q 'rauc.slot=B' /proc/cmdline")
    after = target_rauc_json(command, "status --detailed --output-format=json")
    assert after["booted"] == "B"
    slots = {name: details for entry in after["slots"] for name, details in entry.items()}
    assert slots["kernel.1"]["slot_status"]["bundle"]["version"] == "0.2.0"
    assert slots["rootfs.1"]["slot_status"]["bundle"]["version"] == "0.2.0"
    assert slots["kernel.0"]["slot_status"]["bundle"]["version"] == "0.1.0"
    assert active_slot_hashes(command) == active_images
    assert command.poll_until_success(
        "systemctl is-active --quiet emonos-app.service", tries=90,
        timeout=360.0, sleepduration=4,
    )
    # app-check posts new samples to the existing input and can append to its
    # previous feed. Compare the original bytes *before* that write workload.
    assert target_file_hash(command, feed) == feed_hash
    command.run_check(
        f"EMONOS_APP_TEST_USER={shlex.quote(test_user)} /usr/libexec/emonos-app-check",
        timeout=120,
    )
    assert command.run_check("cat /etc/machine-id") == [machine_id]
    assert target_file_hash(command, "/mnt/data/ssh/ssh_host_ed25519_key.pub") == host_key
    command.run_check("test -z \"$(systemctl --failed --no-pager --no-legend --plain)\"")

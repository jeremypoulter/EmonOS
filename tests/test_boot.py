"""Boot and common runtime coverage for HW-3 and TEST-7."""

import base64
import os
import subprocess
import zlib
from pathlib import Path

import pytest

from common import app_check_command


REQUIRED_CGROUP_CONTROLLERS = {"cpu", "cpuset", "io", "memory", "pids"}

PRELOAD_IMAGES = {
    "web": (
        "ghcr.io/jeremypoulter/emoncms@sha256:"
        "2686f3c0631ecc2e9951a69a6b601beb53547688c03f5013e388f57b786fd099",
        "emonos-preload/web:20260924",
    ),
    "db": (
        "mariadb:11.8-noble@sha256:"
        "79d59758afc91b89b120b0a8904d637f5a3b3e1c4900f29b740d6d46c72fef68",
        "emonos-preload/db:20260924",
    ),
    "redis": (
        "redis:8.10-trixie@sha256:"
        "718f745deb7dfefeac6eed7041fc7ec9476b50e61b247932682457c41adafa0e",
        "emonos-preload/redis:20260924",
    ),
    "mqtt": (
        "eclipse-mosquitto:2.0-openssl@sha256:"
        "199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21",
        "emonos-preload/mqtt:20260924",
    ),
}


def test_target_image_is_present(target_name: str, repo_root: Path) -> None:
    """T1, HW-3: the selected target build produces its documented disk image."""
    images = {
        "x86-64-vm": "output/x86-64-vm/images/emonos-x86-64-vm.img",
        "rpi4": "output/rpi4/images/emonos-rpi4.img",
    }
    image = repo_root / images[target_name]
    assert image.is_file(), f"build {target_name} first; missing {image}"


def test_data_partition_type_matches_repart(target_name: str, repo_root: Path) -> None:
    """WP4: systemd-repart must match only the last data partition."""
    expected = "93c548ef-01e5-4bad-a92c-f5b0fd394e78"
    config = (repo_root / "buildroot-external/app/rootfs-overlay/usr/lib/repart.d/80-emonos-data.conf")
    assert f"Type={expected}" in config.read_text()
    image = repo_root / f"output/{target_name}/images/emonos-{target_name}.img"
    if not image.is_file():
        pytest.skip(f"build {target_name} first")
    table = subprocess.check_output(["sfdisk", "-d", str(image)], text=True)
    data = next(line for line in table.splitlines() if ".img7 :" in line)
    assert f"type={expected}" in data.lower()
    for line in table.splitlines():
        if ".img" in line and ".img7 :" not in line:
            assert f"type={expected}" not in line.lower()


def test_pi_bootstate_environment_offset(target_name: str, repo_root: Path) -> None:
    """D8: Linux fw_setenv and U-Boot must use the same 16 KiB block."""
    if target_name != "rpi4":
        pytest.skip("only the Pi uses the raw U-Boot environment")
    config = repo_root / "buildroot-external/board/raspberrypi/rpi4/rootfs-overlay/etc/fw_env.config"
    assert " 0x0000 0x4000 " in config.read_text()
    image = repo_root / "output/rpi4/images/bootstate.img"
    if not image.is_file():
        pytest.skip("build the Pi image first")
    with image.open("rb") as bootstate:
        environment = bootstate.read(0x4000)
        assert bootstate.read(0x4000) == bytes(0x4000)
    expected_crc = int.from_bytes(environment[:4], "little")
    assert zlib.crc32(environment[4:]) == expected_crc
    assert b"BOOT_ORDER=A B" in environment


def test_targets_use_common_runtime(repo_root: Path) -> None:
    """T1, HW-3: both targets select the common EmonOS runtime and kernel policy."""
    for target in ("emonos_x86_64_vm", "emonos_rpi4"):
        content = (repo_root / f"buildroot-external/configs/{target}_defconfig").read_text()
        assert "BR2_EMONOS_RUNTIME=y" in content
        assert "board/common/kernel-container.config" in content


@pytest.mark.timeout(360)
def test_boot_runtime(command, expected_architecture: str) -> None:
    """T2, ARCH-4: boot architecture and common runtime over target serial."""
    assert command.run_check("uname -s") == ["Linux"]
    assert command.run_check("uname -m") == [expected_architecture]
    assert command.run_check("hostname") == ["emonos"]
    failed = command.run_check("systemctl --failed --no-pager --no-legend --plain || true")
    if failed:
        logs = command.run_check(
            "journalctl --no-pager -u emonos-app.service -u sshd.service -n 50 || true"
        )
        boot_logs = command.run_check("journalctl --no-pager -u mnt-boot.mount -n 30 || true")
        db_logs = command.run_check("docker logs emonos-db-1 2>&1 | head -85 || true")
        disk = command.run_check("df -h /mnt/data /var/lib/docker || true")
        pytest.fail("failed units: " + "\n".join([*failed, *logs, *boot_logs, *db_logs, *disk]))

    controllers = set(command.run_check("cat /sys/fs/cgroup/cgroup.controllers")[0].split())
    assert REQUIRED_CGROUP_CONTROLLERS <= controllers

    assert command.poll_until_success(
        "docker info >/dev/null 2>&1", tries=60, timeout=90.0, sleepduration=1
    )
    command.run_check("docker version >/dev/null")
    command.run_check("docker compose version")
    output = command.run_check("docker run --rm hello-world", timeout=120)
    assert "Hello from Docker!" in output


@pytest.mark.timeout(1200)
def test_app_stack(command) -> None:
    """T2, HC-1: first boot loads images and serves the emoncms application."""
    if not command.poll_until_success(
        "systemctl is-active --quiet emonos-app.service", tries=225, timeout=900.0, sleepduration=4
    ):
        diagnostics = command.run_check(
            "SYSTEMD_PAGER=cat systemctl status --no-pager -l emonos-app.service "
            "emonos-preload.service mnt-data.mount var-lib-docker.mount || true"
        )
        logs = command.run_check("journalctl --no-pager -u emonos-app.service -n 30 || true")
        db_logs = command.run_check("docker logs emonos-db-1 2>&1 | tail -30 || true")
        pytest.fail("app did not start: " + "\n".join([*diagnostics, *logs, *db_logs]))
    command.run_check("test ! -e /mnt/data/preload/images.tar")
    command.run_check("test ! -e /mnt/data/preload/images.tar.sha256")
    services = command.run_check(
        "docker compose -f /opt/emonos/docker-compose.yml ps --status running --format '{{.Service}}'"
    )
    assert set(services) == {"web", "db", "redis", "mqtt"}
    assert command.poll_until_success(
        "curl -fsS --max-time 10 http://127.0.0.1/ >/dev/null",
        tries=30,
        timeout=90.0,
        sleepduration=3,
    )

    # This script runs in the guest, avoiding host-side HTTP/DNS assumptions.
    command.run_check(app_check_command(), timeout=120)


@pytest.mark.timeout(600)
def test_ab_layout(command) -> None:
    """T2, ARCH-4: both targets boot A with read-only root and persistent data."""
    command.run_check("grep -q 'rauc.slot=A' /proc/cmdline")
    command.run_check("grep -q ' / squashfs ro,' /proc/mounts")
    command.run_check("grep -q ' /var tmpfs ' /proc/mounts")
    mounts = command.run_check("grep -E ' / | /var | /mnt/data | /var/lib/docker ' /proc/mounts")
    if not any(" /mnt/data ext4 " in mount for mount in mounts):
        status = command.run_check(
            "SYSTEMD_PAGER=cat systemctl status --no-pager mnt-data.mount "
            "emonos-first-boot.service var-lib-docker.mount || true"
        )
        pytest.fail("data mount missing: " + "\n".join([*mounts, *status]))
    command.run_check("grep -q ' /var/lib/docker ' /proc/mounts")
    command.run_check("test -d /mnt/data/emoncms/db")
    command.run_check("test -d /mnt/data/emoncms/phpfina")
    command.run_check("test -d /mnt/data/redis")
    assert command.poll_until_success(
        "test -f /mnt/data/.preload.done", tries=150, timeout=600.0, sleepduration=4
    )
    assert command.run_check("cat /sys/class/block/*/partition | wc -l") == ["7"]


@pytest.mark.timeout(180)
def test_persistent_identity_and_ssh(command, target_name: str) -> None:
    """D8: first boot persists a device ID; SSH keys live on the data partition."""
    if not command.poll_until_success(
        "systemctl is-active --quiet emonos-persist.service",
        tries=30, timeout=120.0, sleepduration=4,
    ):
        logs = command.run_check("journalctl --no-pager -u emonos-persist.service -n 30")
        pytest.fail("machine ID persistence failed: " + "\n".join(logs))
    machine_id = command.run_check("cat /etc/machine-id")[0]
    assert len(machine_id) == 32 and all(ch in "0123456789abcdef" for ch in machine_id)
    if target_name == "x86-64-vm":
        stored_id = command.run_check(
            "grub-editenv /mnt/boot/EFI/BOOT/grubenv list | sed -n 's/^MACHINE_ID=//p'"
        )[0]
    else:
        stored_id = command.run_check("fw_printenv -n MACHINE_ID")[0]
    assert stored_id == machine_id
    command.run_check("mountpoint -q /etc/ssh")
    command.run_check("test -s /mnt/data/ssh/ssh_host_ed25519_key.pub")
    command.run_check("sshd -T | grep -qi '^passwordauthentication no$'")


@pytest.mark.timeout(600)
def test_data_partition_growth(command, target_name: str) -> None:
    """D11: last data partition uses spare media capacity before mounting."""
    if target_name == "x86-64-vm" and not os.environ.get("EMONOS_QEMU_DISK_SIZE"):
        pytest.skip("set EMONOS_QEMU_DISK_SIZE=10G to test repart on a larger VM disk")
    device = "vda7" if target_name == "x86-64-vm" else "mmcblk0p7"
    partition_sectors = int(command.run_check(f"cat /sys/class/block/{device}/size")[0])
    # A 6 GiB initial partition is 12,582,912 sectors; expect expansion.
    assert partition_sectors > 12582912
    blocks = int(command.run_check("df -k /mnt/data | tail -1 | awk '{print $2}'")[0])
    assert blocks > 6 * 1024 * 1024


@pytest.mark.timeout(900)
def test_feed_survives_reboot(command, target) -> None:
    """T3, DATA-2: a PHPFina feed survives a slot-A reboot."""
    if os.environ.get("EMONOS_RUN_REBOOT_TEST") != "1":
        pytest.skip("set EMONOS_RUN_REBOOT_TEST=1 to exercise a guest reboot")

    assert command.poll_until_success(
        "systemctl is-active --quiet emonos-app.service",
        tries=150, timeout=600.0, sleepduration=4,
    )
    command.run_check(app_check_command(), timeout=120)
    feed = command.run_check("find /mnt/data/emoncms/phpfina -name '*.dat' | head -1")[0]
    assert feed
    original = command.run_check(f"sha256sum {feed}")[0]
    machine_id = command.run_check("cat /etc/machine-id")[0]
    host_key = command.run_check("sha256sum /mnt/data/ssh/ssh_host_ed25519_key.pub")[0]
    command.run_check("sync")
    command.console.sendline("systemctl reboot")
    target.deactivate(command)
    # Do not reactivate against the *old* shell prompt before shutdown starts.
    # Wait for a new login prompt, then log in and let ShellDriver reinject run().
    command.console.expect("emonos login: ", timeout=180)
    command.console.sendline("")
    target.activate(command)
    assert command.poll_until_success(
        "systemctl is-active --quiet emonos-app.service",
        tries=90, timeout=360.0, sleepduration=4,
    )
    assert command.run_check(f"sha256sum {feed}") == [original]
    assert command.run_check("cat /etc/machine-id") == [machine_id]
    assert command.run_check("sha256sum /mnt/data/ssh/ssh_host_ed25519_key.pub") == [host_key]
    command.run_check(f"grep -q 'systemd.machine_id={machine_id}' /proc/cmdline")
    command.run_check("test -z \"$(systemctl --failed --no-pager --no-legend --plain)\"")
    command.run_check("test -f /mnt/data/.preload.done")
    # Compare durable feed bytes before the mutating app-check posts samples.
    command.run_check(app_check_command(), timeout=120)


def test_harness_contract(repo_root: Path) -> None:
    """T8, TEST-7: one runner selects either target but the same tests directory."""
    runner = (repo_root / "tests/run.sh").read_text()
    assert '"$test_dir"' in runner
    for target in ("x86-64-vm", "rpi4"):
        assert (repo_root / f"tests/targets/{target}.yaml").is_file()
        assert target in runner
    assert "--junitxml=" in runner and "--lg-log=" in runner


@pytest.mark.timeout(1200)
def test_offline_preload(command, target_name: str) -> None:
    """D11/R5: a clean store can start locally tagged preloaded images offline."""
    if os.environ.get("EMONOS_RUN_PRELOAD_TEST") != "1":
        pytest.skip("set EMONOS_RUN_PRELOAD_TEST=1 to run the slow preload test")
    if command.run_check("awk '$2 == \"/\" {print $3}' /proc/mounts") == ["squashfs"]:
        pytest.skip("WP4 boots with the preload already installed on the data partition")
    if target_name != "x86-64-vm":
        pytest.skip("the Pi preload test is recorded in the implementation plan")

    # The writable root is intentionally small. WP4 mounts Docker on the data
    # partition; use tmpfs here to prove that layout before the partition exists.
    command.run_check("systemctl stop docker.socket docker.service containerd.service")
    command.run_check("mount -t tmpfs -o size=2g tmpfs /mnt")
    # docker save stages an additional copy under Docker's temporary directory.
    command.run_check("mount -t tmpfs -o size=3g tmpfs /var/lib/docker")
    command.run_check("mount -t tmpfs -o size=1g tmpfs /var/lib/containerd")
    command.run_check("systemctl start containerd.service docker.service")
    for source, local_tag in PRELOAD_IMAGES.values():
        command.run_check(f"docker pull -q {source}", timeout=600)
        command.run_check(f"docker tag {source} {local_tag}")

    local_tags = " ".join(local_tag for _, local_tag in PRELOAD_IMAGES.values())
    command.run_check(f"docker save -o /mnt/emonos-images.tar {local_tags}", timeout=300)
    archive_size = int(command.run_check("wc -c < /mnt/emonos-images.tar")[0])
    assert archive_size > 1_000_000_000
    command.run_check("docker image rm -f $(docker images -aq)")
    command.run_check("docker system prune -af --volumes")
    assert command.run_check("docker images -q") == []
    command.run_check("docker load -i /mnt/emonos-images.tar", timeout=300)
    for _, local_tag in PRELOAD_IMAGES.values():
        command.run_check(f"docker image inspect {local_tag} >/dev/null")

    compose = """services:
  web:
    image: emonos-preload/web:20260924
    pull_policy: never
    entrypoint: [sleep]
    command: ["120"]
  db:
    image: emonos-preload/db:20260924
    pull_policy: never
    entrypoint: [sleep]
    command: ["120"]
  redis:
    image: emonos-preload/redis:20260924
    pull_policy: never
    entrypoint: [sleep]
    command: ["120"]
  mqtt:
    image: emonos-preload/mqtt:20260924
    pull_policy: never
    entrypoint: [sleep]
    command: ["120"]
"""
    encoded = base64.b64encode(compose.encode()).decode()
    command.run_check(f"printf %s {encoded} | base64 -d > /mnt/compose.yml")
    default_route = next(
        route for route in command.run_check("ip route list") if route.startswith("default ")
    )
    command.run_check("ip route del default")
    try:
        command.run_check("docker compose -f /mnt/compose.yml up -d", timeout=120)
        running = command.run_check(
            "docker compose -f /mnt/compose.yml ps --status running --format '{{.Service}}'"
        )
        assert set(running) == set(PRELOAD_IMAGES)
    finally:
        command.run_check(f"ip route replace {default_route}")
        command.run_check("docker compose -f /mnt/compose.yml down")

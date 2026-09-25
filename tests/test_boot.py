"""Boot and common runtime coverage for HW-3 and TEST-7."""

import base64
import os
from pathlib import Path

import pytest


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
    """HW-3: the selected target build produces its documented disk image."""
    images = {
        "x86-64-vm": "output/x86-64-vm/images/emonos-x86-64-vm.img",
        "rpi4": "output/rpi4/images/emonos-rpi4.img",
    }
    image = repo_root / images[target_name]
    assert image.is_file(), f"build {target_name} first; missing {image}"


def test_targets_use_common_runtime(repo_root: Path) -> None:
    """HW-3: both targets select the common EmonOS runtime and kernel policy."""
    for target in ("emonos_x86_64_vm", "emonos_rpi4"):
        content = (repo_root / f"buildroot-external/configs/{target}_defconfig").read_text()
        assert "BR2_EMONOS_RUNTIME=y" in content
        assert "board/common/kernel-container.config" in content


@pytest.mark.timeout(360)
def test_boot_runtime(command, expected_architecture: str) -> None:
    """HW-3, TEST-7: exercise the common runtime through the target serial shell."""
    assert command.run_check("uname -s") == ["Linux"]
    assert command.run_check("uname -m") == [expected_architecture]
    assert command.run_check("hostname") == ["emonos"]
    command.run_check("test -z \"$(systemctl --failed --no-legend --plain)\"")

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
def test_offline_preload(command, target_name: str) -> None:
    """D11/R5: a clean store can start locally tagged preloaded images offline."""
    if os.environ.get("EMONOS_RUN_PRELOAD_TEST") != "1":
        pytest.skip("set EMONOS_RUN_PRELOAD_TEST=1 to run the slow preload test")
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

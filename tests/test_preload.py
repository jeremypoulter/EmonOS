"""Host-only checks for the single-pass verify-and-load preload script."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = (Path(__file__).parents[1]
          / "buildroot-external/app/rootfs-overlay/usr/libexec/emonos-preload")
REQUIRED_TOOLS = ("mkfifo", "tee", "sha256sum", "awk", "mktemp")


def run_preload(tmp_path, archive: bytes, checksum: str, docker_exit: int = 0):
    missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if missing:
        pytest.skip(f"host lacks {', '.join(missing)}")
    data = tmp_path / "data"
    (data / "preload").mkdir(parents=True)
    (tmp_path / "run").mkdir()
    (data / "preload/images.tar").write_bytes(archive)
    (data / "preload/images.tar.sha256").write_text(checksum + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    received = tmp_path / "received.tar"
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/sh\ncat > "{received}"\nexit {docker_exit}\n')
    docker.chmod(0o755)
    text = SCRIPT.read_text().replace("/mnt/data", str(data)).replace("/run/", f"{tmp_path}/run/")
    script = tmp_path / "preload"
    script.write_text(text)
    script.chmod(0o755)
    result = subprocess.run(
        [str(script)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    return result, data, received


def test_matching_archive_is_loaded_once_and_removed(tmp_path):
    archive = os.urandom(300_000)
    result, data, received = run_preload(tmp_path, archive, hashlib.sha256(archive).hexdigest())
    assert result.returncode == 0, result.stderr
    assert received.read_bytes() == archive
    assert (data / ".preload.done").exists()
    assert not (data / "preload/images.tar").exists()
    assert not (data / "preload/images.tar.sha256").exists()
    assert not list((tmp_path / "run").iterdir())


def test_checksum_mismatch_fails_and_keeps_the_archive(tmp_path):
    archive = os.urandom(10_000)
    result, data, _ = run_preload(tmp_path, archive, "0" * 64)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not (data / ".preload.done").exists()
    assert (data / "preload/images.tar").read_bytes() == archive


def test_load_failure_is_not_marked_done(tmp_path):
    archive = os.urandom(10_000)
    result, data, _ = run_preload(tmp_path, archive, hashlib.sha256(archive).hexdigest(),
                                  docker_exit=1)
    assert result.returncode != 0
    assert not (data / ".preload.done").exists()
    assert (data / "preload/images.tar").exists()

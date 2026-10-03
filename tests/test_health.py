"""Host-only strict-response and bounded health policy tests."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_rollback import reboot_into


OVERLAY = Path(__file__).parents[1] / "buildroot-external/app/rootfs-overlay"


def test_runtime_tools_are_explicit_and_probe_needs_no_regex_library():
    external = OVERLAY.parents[1]
    for target in ("emonos_x86_64_vm", "emonos_rpi4"):
        config = (external / f"configs/{target}_defconfig").read_text()
        assert "board/common/busybox-health.config" in config
    assert "CONFIG_TIMEOUT=y" in (external / "board/common/busybox-health.config").read_text()
    assert "select BR2_PACKAGE_JQ" in (external / "Config.in").read_text()
    probe = (OVERLAY / "usr/libexec/emonos-health-probe").read_text()
    # Buildroot jq defaults to --without-oniguruma; do not depend on test().
    assert "test(\"" not in probe


@pytest.mark.parametrize("autonomous", [False, True])
def test_reboot_login_is_owned_by_shell_driver(autonomous):
    events = []
    console = SimpleNamespace(
        sendline=lambda value: events.append(("send", value)),
        expect=lambda value, timeout: events.append(("expect", value)),
    )
    command = SimpleNamespace(console=console, run_check=lambda value: events.append(("run", value)))
    target = SimpleNamespace(
        deactivate=lambda _: events.append(("deactivate",)),
        activate=lambda _: events.append(("activate",)),
    )
    reboot_into(command, target, "A", autonomous=autonomous)
    assert ("send", "root") not in events
    assert events[-2:] == [("send", ""), ("activate",)]
    assert (("send", "systemctl reboot") in events) == (not autonomous)
    assert ("expect", "Booting Slot A") in events


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.mark.parametrize("response,success", [
    ([], True),
    ([{"id": "1", "name": "power", "engine": "5"}], True),
    ({"success": False, "message": "invalid key"}, False),
    ([{"name": "power", "engine": 5}], False),
    ([{"id": 1, "name": 42, "engine": 5}], False),
    ("<html>login</html>", False),
])
def test_probe_requires_authenticated_feed_array(tmp_path, response, success):
    if not shutil.which("jq"):
        pytest.skip("install jq for host-only JSON probe tests")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "systemctl", "#!/bin/sh\nexit 0\n")
    executable(bin_dir / "curl", """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
dest = args[args.index('-o')+1]
if 'feed/list.json' in ' '.join(args):
    body = os.environ['FEED_BODY']
elif '/user/' in ' '.join(args):
    body = json.dumps({'success': True, 'apikey_read': 'a' * 32})
else:
    body = '<html>emoncms</html>'
with open(dest, 'w') as output:
    output.write(body)
""")
    body = response if isinstance(response, str) else json.dumps(response)
    result = subprocess.run([str(OVERLAY / "usr/libexec/emonos-health-probe")],
                            capture_output=True, text=True, env={
        **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMPDIR": str(tmp_path), "FEED_BODY": body,
    })
    assert (result.returncode == 0) == success, result.stderr


@pytest.mark.parametrize("probe_ok,mark_ok,outcome,elapsed", [
    (True, True, "good", 0), (True, False, "failed", 0),
    (False, True, "failed", 300),
])
def test_monitor_commit_failure_and_monotonic_deadline(tmp_path, probe_ok, mark_ok, outcome, elapsed):
    if not shutil.which("jq"):
        pytest.skip("install jq for host-only health policy tests")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "clock"
    counter.write_text("0")
    calls = tmp_path / "calls"
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                   "CLOCK": str(counter), "CALLS": str(calls)}
    executable(bin_dir / "cut", """#!/bin/sh
case "$*" in *'/proc/uptime'*) cat "$CLOCK" ;; *) exec /usr/bin/cut "$@" ;; esac
""")
    executable(bin_dir / "sleep", """#!/bin/sh
echo "$(($(cat "$CLOCK") + $1))" > "$CLOCK"
""")
    executable(bin_dir / "sync", "#!/bin/sh\nexit 0\n")
    executable(bin_dir / "timeout", '#!/bin/sh\nshift\nexec "$@"\n')
    executable(bin_dir / "systemctl", '#!/bin/sh\necho "systemctl $*" >> "$CALLS"\n')
    executable(bin_dir / "rauc", f'#!/bin/sh\necho "rauc $*" >> "$CALLS"\nexit {0 if mark_ok else 1}\n')
    probe = tmp_path / "probe"
    executable(probe, f'#!/bin/sh\necho "application service not active"\nexit {0 if probe_ok else 1}\n')
    (tmp_path / "version").write_text("0.2.0-broken\n")
    (tmp_path / "cmdline").write_text("root=x rauc.slot=B\n")
    (tmp_path / "boot-id").write_text("test-boot\n")
    text = (OVERLAY / "usr/libexec/emonos-health").read_text()
    # Instrument paths in a temporary test copy, never introduce test controls
    # that can shorten the installed production monitor's 300-second bound.
    for source, destination in {
        "/usr/lib/emonos/version": tmp_path / "version",
        "/proc/cmdline": tmp_path / "cmdline",
        "/proc/sys/kernel/random/boot_id": tmp_path / "boot-id",
        "/mnt/data/health": tmp_path / "health",
        "/usr/libexec/emonos-health-probe": probe,
        "/run/emonos-health-probe.log": tmp_path / "probe.log",
    }.items():
        text = text.replace(source, str(destination))
    monitor = tmp_path / "monitor"
    executable(monitor, text)
    result = subprocess.run([str(monitor)], env=environment, capture_output=True, text=True)
    assert result.returncode == (0 if outcome == "good" else 1), result.stderr
    record_path = tmp_path / "health" / ("last-good.json" if outcome == "good" else "failures/test-boot.json")
    record = json.loads(record_path.read_text())
    assert record["elapsed_seconds"] == elapsed
    assert record["outcome"] == outcome
    assert record["slot"] == "B" and record["version"] == "0.2.0-broken"
    commands = calls.read_text()
    assert ("rauc status mark-good" in commands) == probe_ok
    assert ("systemctl reboot" in commands) == (outcome != "good")
    if probe_ok and not mark_ok:
        assert record["reason"] == "RAUC mark-good failed"

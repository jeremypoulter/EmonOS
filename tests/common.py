"""Shared shell-command helpers for in-guest acceptance checks."""

import os
import re
import shlex
import time
from pathlib import Path


def app_check_command() -> str:
    if base := os.environ.get("EMONOS_APP_TEST_API_URL"):
        script_url = shlex.quote(f"{base.rstrip('/')}/app-check-with-key.sh")
        key_url = shlex.quote(f"{base.rstrip('/')}/write-key")
        user = os.environ.get("EMONOS_APP_TEST_USER", "")
        user_arg = f"EMONOS_APP_TEST_USER={shlex.quote(user)} " if user else ""
        return (
            f"curl -fsS --max-time 10 {script_url} -o /run/emonos-app-check-with-key "
            "&& chmod 700 /run/emonos-app-check-with-key && "
            f"{user_arg}EMONOS_APP_TEST_APIKEY=\"$(curl -fsS --max-time 10 {key_url})\" "
            "/bin/sh /run/emonos-app-check-with-key"
        )
    assignments = []
    if value := os.environ.get("EMONOS_APP_TEST_USER"):
        assignments.append(f"EMONOS_APP_TEST_USER={shlex.quote(value)}")
    prefix = " ".join(assignments)
    return f"{prefix} /usr/libexec/emonos-app-check" if prefix else "/usr/libexec/emonos-app-check"


APP_STATUS_COMMAND = (
    "echo up=$(cut -d' ' -f1 /proc/uptime) docker=$(systemctl is-active docker) "
    "preload=$(systemctl is-active emonos-preload) app=$(systemctl is-active emonos-app) "
    "health=$(systemctl is-active emonos-health) load=$(cut -d' ' -f1 /proc/loadavg)"
)


def summarize_console(text: str) -> dict:
    """Count boots and health-monitor reboots in a captured serial console log."""
    clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r", "")
    return {
        "boots": clean.count("Booting Slot"),
        "restarts": clean.count("reboot: Restarting system"),
        "health_monitor_stops": clean.count("Stopped EmonOS bounded application health"),
    }


def report_directory(target_name: str) -> Path:
    """Directory CI collects as test evidence for this target."""
    return Path(os.environ.get("EMONOS_TEST_REPORT_DIR") or (
        Path(__file__).parents[1] / "output/test-results" / target_name
    ))


def console_log_text(target_name: str) -> str:
    """Best-effort read of the labgrid serial console log for this test run."""
    try:
        return (report_directory(target_name) / "labgrid/console_main").read_text(errors="replace")
    except OSError:
        return ""


def save_report_file(target_name: str, name: str, text: str) -> None:
    """Best-effort write of extra evidence next to the JUnit report."""
    try:
        directory = report_directory(target_name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text, encoding="utf-8")
    except OSError:
        pass


def wait_for_app(command, budget: float = 900.0, interval: float = 4.0):
    """Wait for emonos-app, recording a status timeline as it goes.

    A health-monitor reboot kills the shell session, so anything gathered after
    the fact is lost. Return (started, timeline, reason): the timeline is the
    evidence of what the guest was doing right up to the failure.
    """
    timeline: list[str] = []
    deadline = time.monotonic() + budget
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            line = " ".join(command.run_check(APP_STATUS_COMMAND, timeout=min(120.0, remaining)))
        except Exception as error:
            return False, timeline, f"console lost ({type(error).__name__}): guest likely rebooted"
        timeline.append(line)
        if " app=active " in f" {line} ":
            return True, timeline, "started"
        time.sleep(interval)
    return False, timeline, f"emonos-app not active after {budget:.0f}s"

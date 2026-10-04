"""Shared shell-command helpers for in-guest acceptance checks."""

import os
import shlex


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

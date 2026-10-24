"""Host-only validation checks for the guarded SD-card flashing command."""

import os
import subprocess
from pathlib import Path

import pytest


FLASH = Path(__file__).parents[1] / "flash.sh"


@pytest.mark.parametrize("minimum", ["", "0", "0000", "not-a-number"])
def test_flash_rejects_nonpositive_or_noninteger_minimum(minimum):
    """Reject empty, zero, and non-integer capacity overrides before any action."""
    result = subprocess.run(
        [str(FLASH), "status"], capture_output=True, text=True,
        env={**os.environ, "EMONOS_MIN_CARD_BYTES": minimum},
    )
    assert result.returncode == 2
    assert "EMONOS_MIN_CARD_BYTES" in result.stderr


@pytest.mark.parametrize("minimum", [
    "9223372036854775808", "999999999999999999999999999999999999999999",
])
def test_flash_rejects_minimum_outside_bash_integer_range(minimum):
    """Reject capacity values that cannot be represented by Bash arithmetic."""
    result = subprocess.run(
        [str(FLASH), "status"], capture_output=True, text=True,
        env={**os.environ, "EMONOS_MIN_CARD_BYTES": minimum},
    )
    assert result.returncode == 2
    assert "integer range" in result.stderr


@pytest.mark.parametrize("minimum", [
    "7658835968", "0007658835968", "9223372036854775807",
])
def test_flash_accepts_positive_decimal_minimum_with_optional_leading_zeroes(
        tmp_path, minimum):
    """Accept and normalize valid decimal capacity overrides."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text("#!/bin/sh\nprintf '%s\\n' '>ON</td>'\n")
    curl.chmod(0o755)
    result = subprocess.run(
        [str(FLASH), "status"], capture_output=True, text=True,
        env={**os.environ, "EMONOS_MIN_CARD_BYTES": minimum,
             "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ON"

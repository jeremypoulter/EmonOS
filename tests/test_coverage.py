"""WP8 documentation drift check for acceptance-ID coverage."""

import subprocess
import sys
from pathlib import Path


def test_generated_coverage_is_current() -> None:
    """T8, TEST-7: all acceptance IDs are linked to collected pytest cases."""
    root = Path(__file__).parents[1]
    subprocess.run([sys.executable, str(root / "tests/requirement_coverage.py"), "--check"],
                   cwd=root, check=True)

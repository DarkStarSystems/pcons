# SPDX-License-Identifier: MIT
"""Run a test program once for each case a file lists; fail if any fails."""

import subprocess
import sys
from pathlib import Path

program, cases = sys.argv[1], Path(sys.argv[2])
failed = [
    c for c in cases.read_text().split() if subprocess.run([program, c]).returncode
]
if failed:
    sys.exit(f"failed: {', '.join(failed)}")

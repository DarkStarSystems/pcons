# SPDX-License-Identifier: MIT
"""Run an editable install's regen command, or say why it can't run.

The build backend copies this file into an editable install's build directory
and runs it under the base Python, which outlives the install. Its arguments
are the regen command, starting with the Python that ran the backend. Under
build isolation, the uv and pip default, that Python is gone once the install
is done (uv), or can't import pcons any more (pip).

It must run on the standard library alone: pcons may be what's missing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GUIDE = "https://pcons.readthedocs.io/en/latest/user-guide/#editable-installs"


def main(command: list[str]) -> int:
    python = command[0]
    if Path(python).exists():
        code = subprocess.call(command)
        imports_pcons = (
            code == 0
            or subprocess.call(
                [python, "-c", "import pcons"], stderr=subprocess.DEVNULL
            )
            == 0
        )
        if imports_pcons:
            return code
        problem = "can't import pcons"
    else:
        problem = "is gone"
    print(
        f"pcons: can't regenerate build.ninja: {python} {problem}.\n"
        "This editable install was built in an isolated environment, which\n"
        "lasted only as long as the install. Re-run the install, or turn build\n"
        "isolation off for this package so ninja can always regenerate. See\n"
        f'"Editable installs" in the pcons user guide: {GUIDE}',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

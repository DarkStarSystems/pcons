# SPDX-License-Identifier: MIT
"""Build-time helper: run a command, then touch a stamp file for ninja.

Some Qt tools (lupdate, macdeployqt, windeployqt) modify trees rather
than producing a single declarable output; ninja still needs one. Used
by the `ninja lupdate` / `ninja deploy` utility targets:

    python -m pcons.toolchains.qt._stamped --stamp <path> -- <prog> [args...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("command", nargs="+")
    args = parser.parse_args(argv)

    result = subprocess.run(args.command)
    if result.returncode != 0:
        return result.returncode

    stamp = Path(args.stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

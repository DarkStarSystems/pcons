# SPDX-License-Identifier: MIT
"""Cross-platform command helpers for pcons build rules.

These helpers are designed to be invoked from ninja build rules using Python.
They handle forward slashes and spaces in paths correctly on all platforms.

Usage in build rules:
    python -m pcons.util.commands copy <src> <dest>
    python -m pcons.util.commands concat <src1> <src2> ... <dest>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def copy(src: str, dest: str) -> None:
    """Copy a file, creating parent directories as needed."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def concat(sources: list[str], dest: str) -> None:
    """Concatenate multiple files into one."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for src in sources:
            with open(src, "rb") as f:
                out.write(f.read())


def main() -> int:
    """Command-line entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python -m pcons.util.commands <command> [args...]", file=sys.stderr
        )
        print("Commands: copy, concat", file=sys.stderr)
        return 1

    cmd = sys.argv[1]

    if cmd == "copy":
        if len(sys.argv) != 4:
            print(
                "Usage: python -m pcons.util.commands copy <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copy(sys.argv[2], sys.argv[3])
        return 0

    elif cmd == "concat":
        if len(sys.argv) < 4:
            print(
                "Usage: python -m pcons.util.commands concat <src1> [src2...] <dest>",
                file=sys.stderr,
            )
            return 1
        concat(sys.argv[2:-1], sys.argv[-1])
        return 0

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: MIT
"""Make a generator's unchanged outputs look unchanged to the build system.

``restat`` (Ninja) re-checks an output's timestamp after the command runs and
skips downstream work when it didn't move. That only helps if the generator
leaves byte-identical outputs alone — and most don't: they rewrite everything
every time, so one added item in an input list rebuilds the world.

This module wraps such a generator without needing its cooperation. The
command becomes::

    python -m pcons.tools.stable_output --pre  $out && <generator> && \\
    python -m pcons.tools.stable_output --post $out

``--pre`` stashes the existing outputs; ``--post`` restores any output that
came back byte-identical, timestamp included. Used by
``env.Command(..., write_if_different=True)``.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

STASH_DIR = ".pcons-stable"


def _stash_path(output: Path, stash_dir: Path) -> Path:
    """Where *output* is stashed: name plus a hash of its full path.

    The hash keeps same-named outputs from different directories apart.
    """
    digest = hashlib.sha1(str(output).encode()).hexdigest()[:12]
    return stash_dir / f"{output.name}.{digest}"


def save(outputs: list[Path], stash_dir: Path) -> None:
    """Copy existing outputs aside, preserving their timestamps."""
    stash_dir.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        if output.is_file():
            shutil.copy2(output, _stash_path(output, stash_dir))


def restore_unchanged(outputs: list[Path], stash_dir: Path) -> list[Path]:
    """Put back every output that came back byte-identical.

    Returns the outputs that really changed (or are new).
    """
    changed: list[Path] = []
    for output in outputs:
        stashed = _stash_path(output, stash_dir)
        if not stashed.is_file():
            changed.append(output)
            continue
        try:
            if output.is_file() and output.read_bytes() == stashed.read_bytes():
                # copy2 brings the original mtime back with the content, so
                # the build system sees a file that was never touched.
                shutil.copy2(stashed, output)
            else:
                changed.append(output)
        finally:
            stashed.unlink(missing_ok=True)
    return changed


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("--pre", "--post"):
        print(
            "usage: python -m pcons.tools.stable_output --pre|--post FILE...",
            file=sys.stderr,
        )
        return 2

    mode, outputs = args[0], [Path(a) for a in args[1:]]
    stash_dir = Path(STASH_DIR)
    if mode == "--pre":
        save(outputs, stash_dir)
    else:
        restore_unchanged(outputs, stash_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

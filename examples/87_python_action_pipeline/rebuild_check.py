# SPDX-License-Identifier: MIT
"""Check what two kinds of edit to the build script re-run, and what they do not.

Run from the example directory, after a first successful build.

Two facts no declarative rebuild block can express, because both need a real
edit to the build script and a real pcons run:

1. Editing the ``embed`` body re-runs both embeds and both links, because the
   two edges share one generated module, and does **not** re-fetch, because
   ``fetch`` has a module of its own that did not change.
2. Editing one URL re-runs that fetch and everything downstream of it, and
   leaves the other chain alone. The URL travels in that edge's argument
   pickle, so only one pickle moves.

The first thing this does is a rebuild of its own, so the baseline it
measures against is settled rather than whatever the rebuild case before it
left behind.

The build script is restored and rebuilt whatever happens, both in the same
``finally``, so a failure here leaves the tree consistent for the next run
rather than half-way through an edit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("pcons-build.py")
BUILD = Path("build")
ORIGINAL = SCRIPT.read_text(encoding="utf-8")

WATCHED = (
    BUILD / "lorem.txt",
    BUILD / "motto.txt",
    BUILD / "lorem.c",
    BUILD / "motto.c",
    BUILD / "pyact" / "fetch.py",
    BUILD / "pyact" / "embed.py",
)


def ninja_command() -> list[str]:
    """How to run ninja here, or exit saying there is no way to."""
    if shutil.which("ninja"):
        return ["ninja"]
    if shutil.which("uvx"):
        return ["uvx", "ninja"]
    print("no ninja available")
    raise SystemExit(1)


def rebuild() -> None:
    """Regenerate with pcons and build, both pinned to build/."""
    generate = [sys.executable, "-m", "pcons", "generate", "-B", "build"]
    build = [*ninja_command(), "-C", "build"]
    for command in (generate, build):
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode != 0:
            print(done.stdout)
            print(done.stderr)
            raise SystemExit(f"{command[0]} failed: {done.returncode}")


def mtimes() -> dict[Path, int]:
    """When each watched file was last written."""
    return {path: path.stat().st_mtime_ns for path in WATCHED}


def check(condition: bool, complaint: str) -> None:
    if not condition:
        raise SystemExit(complaint)


def moved(before: dict[Path, int], after: dict[Path, int]) -> set[Path]:
    return {path for path in before if before[path] != after[path]}


rebuild()
before = mtimes()

try:
    SCRIPT.write_text(
        ORIGINAL.replace(
            'body = ", ".join(str(byte) for byte in data)',
            'body = ", ".join(str(byte) for byte in data)  # one edit',
        ),
        encoding="utf-8",
    )
    rebuild()
    after = moved(before, mtimes())
    check(
        after == {BUILD / "pyact" / "embed.py", BUILD / "lorem.c", BUILD / "motto.c"},
        f"an embed edit moved the wrong set: {sorted(str(p) for p in after)}",
    )
    check(
        (BUILD / "pyact" / "fetch.py") not in after,
        "an embed edit rewrote the fetch module",
    )

    SCRIPT.write_text(ORIGINAL, encoding="utf-8")
    rebuild()

    before = mtimes()
    SCRIPT.write_text(
        ORIGINAL.replace("amount=12&what=words", "amount=7&what=words"),
        encoding="utf-8",
    )
    rebuild()
    after = moved(before, mtimes())
    check(
        after == {BUILD / "motto.txt", BUILD / "motto.c"},
        f"a URL edit moved the wrong set: {sorted(str(p) for p in after)}",
    )
finally:
    SCRIPT.write_text(ORIGINAL, encoding="utf-8")
    rebuild()

print("rebuild ok")

# SPDX-License-Identifier: MIT
"""Check what a rebuild does after two kinds of edit to the build script.

Run from the example directory, after a first successful build.

Editing the function body must re-run the edge, and editing anything else in
the script must not. The second half is the one worth a script of its own: the
generated module is rewritten only when its bytes change, so a comment added
above the decoration regenerates the build files and stops there. Both halves
need an edit to a real file and a real pcons run, which no declarative rebuild
block can express.

The build script is restored whatever happens, so running this by hand in a
checkout leaves nothing behind.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("pcons-build.py")
MODULE = Path("build/pyact/report.py")
REPORT = Path("build/report.txt")
ORIGINAL = SCRIPT.read_text(encoding="utf-8")


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


def state() -> tuple[int, str]:
    """What must not move: the generated module's mtime and the report."""
    return MODULE.stat().st_mtime_ns, REPORT.read_text(encoding="utf-8")


def check(condition: bool, complaint: str) -> None:
    if not condition:
        raise SystemExit(complaint)


before_mtime, before_report = state()

try:
    SCRIPT.write_text(
        ORIGINAL.replace(
            "@env.PyAction(", "# a comment a user might add\n@env.PyAction("
        ),
        encoding="utf-8",
    )
    rebuild()
    after_mtime, after_report = state()
    check(after_mtime == before_mtime, "a comment rewrote the generated module")
    check(after_report == before_report, "a comment re-ran the function")

    SCRIPT.write_text(
        ORIGINAL.replace("lines = [title]", "lines = [title.upper()]"),
        encoding="utf-8",
    )
    rebuild()
    edited_mtime, edited_report = state()
    check(
        edited_mtime != before_mtime, "a changed body left the generated module alone"
    )
    check(edited_report != before_report, "a changed body did not re-run the function")
    check(
        "WORD COUNTS" in edited_report, f"the new body did not run: {edited_report!r}"
    )
finally:
    SCRIPT.write_text(ORIGINAL, encoding="utf-8")

rebuild()
check(state()[1] == before_report, "the report did not come back")

print("rebuild ok")

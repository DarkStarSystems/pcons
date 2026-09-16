# SPDX-License-Identifier: MIT
"""Check what a rebuild does after two kinds of edit to the build script.

Run from the example directory, after a first successful build.

Editing the function body must re-run every edge that reads it, and editing
anything else in the script must not re-run any. The second half is the one
worth a script of its own: the generated module is rewritten only when its
bytes change, so a comment added above the decoration regenerates the build
files and stops there. Both halves need an edit to a real file and a real
pcons run, which no declarative rebuild block can express.

Three edges share one function here, across two environments, so a body edit
has to move all three reports and both generated modules.

The build script is restored whatever happens, so running this by hand in a
checkout leaves nothing behind.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("pcons-build.py")
MODULES = (Path("build/pyact/report.py"), Path("build/strict/pyact/report.py"))
REPORTS = (
    Path("build/report.txt"),
    Path("build/report2.txt"),
    Path("build/strict/report.txt"),
)
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


def state() -> tuple[tuple[int, ...], tuple[str, ...]]:
    """What must not move: every module's mtime and every report's text."""
    return (
        tuple(module.stat().st_mtime_ns for module in MODULES),
        tuple(report.read_text(encoding="utf-8") for report in REPORTS),
    )


def check(condition: bool, complaint: str) -> None:
    if not condition:
        raise SystemExit(complaint)


before_mtimes, before_reports = state()
check(len(set(before_reports)) == 3, "the three reports are not all different")

try:
    SCRIPT.write_text(
        ORIGINAL.replace(
            "def make_report(", "# a comment a user might add\ndef make_report("
        ),
        encoding="utf-8",
    )
    rebuild()
    after_mtimes, after_reports = state()
    check(after_mtimes == before_mtimes, "a comment rewrote a generated module")
    check(after_reports == before_reports, "a comment re-ran the function")

    SCRIPT.write_text(
        ORIGINAL.replace("lines = [title]", "lines = [title.upper()]"),
        encoding="utf-8",
    )
    rebuild()
    edited_mtimes, edited_reports = state()
    for index, (was, now) in enumerate(zip(before_mtimes, edited_mtimes, strict=True)):
        check(was != now, f"a changed body left {MODULES[index]} alone")
    for index, (was, now) in enumerate(
        zip(before_reports, edited_reports, strict=True)
    ):
        check(was != now, f"a changed body did not re-run {REPORTS[index]}")
        check(
            now.split("\n")[0].isupper(),
            f"the new body did not run for {REPORTS[index]}: {now!r}",
        )
finally:
    SCRIPT.write_text(ORIGINAL, encoding="utf-8")

rebuild()
check(state()[1] == before_reports, "the reports did not come back")

print("rebuild ok")

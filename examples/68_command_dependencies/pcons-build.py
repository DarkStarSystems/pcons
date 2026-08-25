# SPDX-License-Identifier: MIT
"""Commands that build what they need first.

`pcons run <name>` builds nothing by default: a command gets a resolved
project, not a built one. A command that needs an artifact says so, and pcons
builds it first:

    publish.depends(report)

    pcons run publish     generates the build files, builds report, then runs

That is the whole feature. `depends` takes targets, the ones this build script
already declared, and takes nothing else -- no names, no paths.

Declaring it is the opt-in. `inspect` below declares nothing and behaves as it
always has: no build files written, no build started, and it has to cope with
an artifact that may not exist yet.

A group declares too, and so does each of its verbs. The group's targets are
built before any verb of it, the verb's own on top.
"""

import subprocess
import sys
from pathlib import Path

from pcons import Project

project = Project("command_dependencies")
env = project.Environment(toolchain="c")

report = project.Program("report", env, ["src/report.c"])

python = sys.executable.replace("\\", "/")
notes_file = env.Command(
    target="notes.txt",
    source=["src/write-notes.py"],
    command=f"{python} $SOURCE $TARGET",
)


def report_path() -> Path:
    return Path(str(report.output_nodes[0].path))


def notes_path() -> Path:
    return project.build_dir / "notes.txt"


@project.cli_command()
def publish() -> None:
    """Run the program, which pcons has built by now."""
    # No existence check and no "run pcons first": the declaration below is
    # what makes that unnecessary.
    result = subprocess.run([str(report_path())], check=False)
    print(f"published (exit {result.returncode})")


publish.depends(report)


@project.cli_command()
def inspect_build() -> None:
    """Declare nothing, and start no build."""
    program = report_path()
    print(f"report exists: {program.exists()}")


@project.cli_group()
def release() -> None:
    """Release tasks."""


release.depends(report)


@release.command("notes")
def release_notes() -> None:
    """Write the notes that go with a release."""
    print(f"notes for {report_path().name}")
    print(f"notes say: {notes_path().read_text().strip()}")


release_notes.depends(notes_file)

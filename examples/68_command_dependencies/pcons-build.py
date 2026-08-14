#!/usr/bin/env python3
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
"""

import subprocess
from pathlib import Path

from pcons import Project

project = Project("command_dependencies")
env = project.Environment(toolchain="c")

report = project.Program("report", env, ["src/report.c"])


def report_path() -> Path:
    return Path(str(report.output_nodes[0].path))


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


# A verb declares no dependencies of its own; the group's cover every verb, so
# `pcons run release notes` builds the report too.
@release.command("notes")
def release_notes() -> None:
    """Write the notes that go with a release."""
    print(f"notes for {report_path().name}")

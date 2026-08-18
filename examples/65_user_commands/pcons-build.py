#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Commands of your own, reachable as `pcons run <name>`.

A build script describes what to build. Anything else a project needs done --
flash a board, publish a release, print where an artifact landed -- usually ends
up in a shell script beside it, with its own way of finding the build directory.
This declares those commands in the build script instead, so they see the
project pcons just described:

    pcons run                 list what is available
    pcons run greet --name x  run the program this build produced
    pcons run where           print where the artifacts are
    pcons run docs list       a group of related commands

A command runs after the script has been read and the project resolved, so it
can ask the project anything -- target names, output paths, the build
directory. It builds nothing unless it declares a dependency, which none here
does -- see `examples/68_command_dependencies` for that. So `greet` below has
to cope with a program that is not built yet.

The decorators return real click objects, so `click.option`, `click.argument`
and `click.Choice` are all available. click is part of pcons' public surface
here, deliberately: a translation layer would only be a smaller click.
"""

import shutil
import subprocess
from pathlib import Path

import click

from pcons import Project

project = Project("user_commands")
env = project.Environment(toolchain="c")

greeter = project.Program("greeter", env, ["src/greeter.c"])


def greeter_path() -> Path:
    """Where the linked program landed.

    `output_nodes` is populated by resolution, which has already happened by the
    time any of these commands run. Nothing here checks the filesystem: the
    graph already says where the program goes.
    """
    return Path(str(greeter.output_nodes[0].path))


@project.cli_command()
@click.option("--name", default="world", help="Who to greet")
def greet(name: str) -> None:
    """Run the program this build produced."""
    program = greeter_path()
    if not program.exists():
        # What a command that declares no dependency has to do. The other
        # choice is `greet.depends(greeter)`, which builds it instead.
        raise click.ClickException(f"{program} is not built yet. Run `pcons` first.")
    result = subprocess.run([str(program), name], check=False)
    if result.returncode != 0:
        raise click.ClickException(f"{program} exited {result.returncode}")


@project.cli_command()
def where() -> None:
    """Print where the artifacts landed."""
    print(f"build_dir: {project.build_dir}")
    print(f"greeter: {greeter_path()}")


@project.cli_group()
def docs() -> None:
    """Documentation tasks."""


# Subcommands are added with click's own decorator, on the group pcons handed
# back. They belong to the group, so they cannot collide with a top-level name
# and pcons never sees them.
@docs.command("list")
def docs_list() -> None:
    """List the source files that would go into the docs."""
    for path in sorted(Path("src").glob("*.c")):
        print(path)


@docs.command("count")
@click.option("--unit", type=click.Choice(["lines", "files"]), default="lines")
def docs_count(unit: str) -> None:
    """Count what there is to document."""
    sources = sorted(Path("src").glob("*.c"))
    if unit == "files":
        print(f"{len(sources)} files")
    else:
        total = sum(len(p.read_text().splitlines()) for p in sources)
        print(f"{total} lines")


@project.cli_command()
@click.option("--tool", default="nonesuch-flasher", help="Tool to look for")
def check_tool(tool: str) -> None:
    """Fail the way a command is meant to fail."""
    # click's conventions are pcons' conventions here: raise ClickException for
    # a message and exit 1, or ctx.exit(n) for a code of your own. A returned
    # value is ignored, so there is nothing else to return.
    if shutil.which(tool) is None:
        raise click.ClickException(f"{tool} is not on PATH")
    print(f"{tool}: found")

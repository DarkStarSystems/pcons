#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Running commands behind a launcher.

A launcher is a program that runs *in front of* the command an edge would
otherwise run: ``ccache`` in front of the compiler, ``time`` in front of
anything you want to measure, ``valgrind`` in front of a test.

    env.cc.launcher = ["ccache"]

It belongs to the tool namespace, so it follows the tool rather than any one
target, and it is a list of tokens like every other command in pcons -- which
is what keeps a launcher whose path contains a space from falling apart.

Launchers stack, outermost first. Here two of them wrap every C compile: a
stand-in for a compiler cache, and one for ``time``. Both are shipped with the
example so it runs anywhere; the real thing needs only the program's name.

For the common case there is a shortcut that picks whichever cache is
installed and sets it on ``cc`` and ``cxx`` for you:

    env.use_compiler_cache()          # sccache if present, else ccache

Note that ``compile_commands.json`` reports the compiler itself, without the
launchers, so clangd and friends still see the real compile.
"""

import sys

from pcons import Project

project = Project("launcher_demo")

env = project.Environment(toolchain="c")

python = sys.executable.replace("\\", "/")
prefix_log = str(project.root_dir / "tools" / "prefix_log.py").replace("\\", "/")
# Launcher tokens are passed to the build tool as written, so paths in them
# must be absolute: the command runs from the build directory, and pcons does
# not rewrite them the way it rewrites an edge's own inputs and outputs.
# `build_dir` is relative to the project root, hence the join.
log = str(project.root_dir / project.build_dir / "launchers.log").replace("\\", "/")

# Outermost first: "cache" runs "timer", which runs the compiler.
env.cc.launcher = [
    python,
    prefix_log,
    "cache",
    log,
    python,
    prefix_log,
    "timer",
    log,
]

hello = project.Program("hello", env, sources=[project.root_dir / "src" / "hello.c"])

# A launcher can also belong to one command rather than to a tool. This is
# what a persistent-worker client would use: the wrapper applies to this edge
# alone, and runs inside any launcher set on the `command` tool.
manifest = env.Command(
    name="manifest",
    target=project.build_dir / "manifest.txt",
    source=project.root_dir / "src" / "hello.c",
    command=[
        python,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[2]).write_text(sys.argv[1])",
        "$SOURCE",
        "$TARGET",
    ],
    launcher=[python, prefix_log, "command", log],
)

# Naming any default target replaces the automatic set, so both go here: a
# command target is not built unless something asks for it.
project.Default(hello, manifest)

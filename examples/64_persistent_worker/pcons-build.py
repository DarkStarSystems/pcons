#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Running an action in a persistent worker.

Ninja assumes starting a command is free. For an action that has to become
*ready* before it can do anything — load a large library, reach a service,
claim a licence — startup can cost more than the work, and every edit pays it
again.

A worker is a process kept alive across actions, so that cost is paid once:

    worker=Worker(preload=["heavy_toolkit"])

``preload`` is what this worker does to become ready. List installed packages
only, never a module of the project being built — that one has to be loaded
fresh, or an edit to it would be masked by the copy the worker already holds.
This example preloads a standard-library parser so it runs anywhere; a real
project would name the package whose import it is tired of waiting for.

Every action still runs in a fresh forked child, so nothing one action does
can reach the next. ``src/render.py`` prints whether it started with the
parser already loaded, which is how you can tell a worker was used.

Nothing here starts the worker: the first action that needs one starts it, and
it exits once it has been idle. If none can be reached — plain ``ninja``, CI,
or Windows, which has no fork — the command simply runs directly, so this
build works either way.
"""

import sys

from pcons import Project, Worker

project = Project("worker_demo")

env = project.Environment()

python = sys.executable.replace("\\", "/")
src_dir = project.root_dir / "src"

report = env.Command(
    name="report",
    target=project.build_dir / "report.txt",
    source=[src_dir / "render.py", src_dir / "items.xml"],
    command=[python, "${SOURCES[0]}", "${SOURCES[1]}", "$TARGET"],
    # Short, so an example run leaves nothing lingering for long.
    worker=Worker(preload=["xml.dom.minidom"], idle_timeout=30),
)

project.Default(report)

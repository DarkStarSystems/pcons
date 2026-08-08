#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Running an action in a persistent worker.

Ninja assumes starting a command is free. Often it is. But an action can cost
far more to *start* than to run — it may have to open a connection, claim a
licence, warm a cache, spin up a runtime — and a build pays that again on every
edit. A worker is a process that is already started, so it does not.

pcons does not implement workers: it defines what one must do
(``docs/worker-protocol.md``) and a project brings whichever kind suits it. A
worker declared as::

    worker=Worker(command=["my-worker", "--profile=render"])

can be anything that speaks the protocol — a compiled binary, a client for a
service already running, a script. ``PythonWorker`` below is the one pcons
bundles, for actions that run a Python script; it becomes ready by importing
what it is told to, and ``setup=`` covers readiness that is not an import.

Whatever the kind, the contract holds: every action is served in isolation, so
nothing one does can reach the next, and an unreachable worker means the
command runs directly rather than the build failing. That is why this example
works the same on Windows, which has no fork, and under plain ``ninja``.

``src/render.py`` prints whether it started with the parser already loaded,
which is how you can see a worker was used. Nothing starts one here: the first
action that needs it does, and it exits once idle.
"""

import sys

from pcons import Project, PythonWorker

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
    worker=PythonWorker(preload=["xml.dom.minidom"], idle_timeout=30),
)

project.Default(report)

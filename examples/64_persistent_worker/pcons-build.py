# SPDX-License-Identifier: MIT
"""Running an action in a persistent worker.

An action that has significant startup time can be handed to a worker process that
has already started. See docs/worker-protocol.md for what a worker is and
what one must do; ``PythonWorker`` here is the one bundled with pcons.

``src/render.py`` prints whether it started with the parser already loaded,
which is how you can see a worker was used. It is not asserted: with no
worker reachable the action runs directly and must produce the same file,
which is why this example works the same on Windows, which has no fork.
"""

import sys

from pcons import Project, PythonWorker

project = Project("worker_demo")

env = project.Environment()

python = sys.executable.replace("\\", "/")
src_dir = project.root_dir / "src"

report = env.Command(
    target=project.build_dir / "report.txt",
    source=[src_dir / "render.py", src_dir / "items.xml"],
    command=[python, "${SOURCES[0]}", "${SOURCES[1]}", "$TARGET"],
    # Short, so an example run leaves nothing lingering for long.
    worker=PythonWorker(preload=["xml.dom.minidom"], idle_timeout=30),
)

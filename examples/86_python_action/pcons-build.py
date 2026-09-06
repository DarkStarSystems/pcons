# SPDX-License-Identifier: MIT
"""Run a Python function as a build step.

``env.PyCommand`` turns a function written here into a build edge. The
function does not run while the build is described: pcons writes its source
to a generated module under the build directory, its keyword arguments to a
pickle beside it, and emits an edge that runs the module under ninja. So the
work happens when ninja decides it is needed, in parallel with everything
else, and not again until an input changes.

Three rules follow from the function travelling alone:

1. It imports what it needs inside its own body. The generated module holds
   the function and nothing else, so a name this script imported does not
   exist there.
2. It reads nothing from around it. A value from the build script is passed
   through ``kwargs=``, which travels in the pickle.
3. The decorated name becomes the ``Target``, not the function, like every
   other pcons builder. ``report`` below is what ``project.Default`` takes.

The function is called as ``fn(sources, targets, **kwargs)``, with the paths
spelled as the build tool sees them.
"""

from pcons import Project

project = Project("python_action")
env = project.Environment()
src = project.root_dir / "src"


@env.PyCommand(
    target=project.build_dir / "report.txt",
    source=[src / "a.txt", src / "b.txt"],
    kwargs={"title": "word counts"},
)
def report(sources, targets, title):
    from pathlib import Path

    lines = [title]
    for name in sources:
        text = Path(name).read_text(encoding="utf-8")
        lines.append(f"{Path(name).name}: {len(text.split())}")
    Path(targets[0]).write_text("\n".join(lines) + "\n", encoding="utf-8")


project.Default(report)

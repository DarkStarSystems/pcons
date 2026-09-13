# SPDX-License-Identifier: MIT
"""Run a Python function as a build step.

``env.PyAction`` turns a function written here into a builder. The function
does not run while the build is described: pcons writes its source to a
generated module under the build directory, each call writes its arguments
to a pickle beside it, and each call emits an edge that runs the module
under ninja. So the work happens when ninja decides it is needed, in
parallel with everything else, and not again until an input changes.

Three rules follow from the function travelling alone:

1. It imports what it needs inside its own body. The generated module holds
   the function and nothing else, so a name this script imported does not
   exist there.
2. It reads nothing from around it. A value from the build script is passed
   as a keyword of the call, which travels in the pickle.
3. The decorated name is a builder, like every other pcons builder, and the
   call returns the ``Target`` that ``project.Default`` takes.

The function is called as ``fn(sources, targets, **kwargs)``, with the paths
spelled as the build tool sees them.
"""

from pcons import Project

project = Project("python_action")
env = project.Environment()
src = project.root_dir / "src"


@env.PyAction()
def report(sources, targets, title):
    from pathlib import Path

    lines = [title]
    for name in sources:
        text = Path(name).read_text(encoding="utf-8")
        lines.append(f"{Path(name).name}: {len(text.split())}")
    Path(targets[0]).write_text("\n".join(lines) + "\n", encoding="utf-8")


counts = report(
    target=project.build_dir / "report.txt",
    source=[src / "a.txt", src / "b.txt"],
    title="word counts",
)

project.Default(counts)

# SPDX-License-Identifier: MIT
"""Run a Python function as a build step.

``env.PyAction`` turns a function written here into a builder, the way
``env.Program`` is a builder. Calling it makes one build edge, and calling it
again makes another, so one function serves as many edges as the build needs.

The function does not run while the build is described. pcons writes its
source once to a generated module under the build directory, each call writes
its own arguments to a pickle beside it, and each call emits an edge that runs
the module under ninja. So the work happens when ninja decides it is needed,
in parallel with everything else, and not again until an input changes.

Three rules follow from the function travelling alone:

1. It imports what it needs inside its own body. The generated module holds
   the function and nothing else, so a name this script imported does not
   exist there.
2. It reads nothing from around it. A value from the build script is taken as
   a parameter and passed at the call, where it travels in the pickle.
3. The decorated name is a builder, and the call returns the ``Target`` that
   ``project.Default`` takes.

The function is called as ``fn(sources, targets, **kwargs)``, with the paths
spelled as the build tool sees them.

The edge's name defaults to the first target's stem, and the argument pickle
is named after it, so two calls whose targets share a stem need ``name=`` to
tell them apart. Here the three targets are ``report.txt``, ``report2.txt``
and ``report.txt`` again in a second environment, whose ``build_prefix``
puts it in its own directory.

Two targets may share a name only when their environments are named and
different, which is why both environments here have a name.

A second environment is served by a plain Python helper that decorates once
per environment. An action is bound to the environment that decorated it.
"""

from pcons import Project

project = Project("python_action")
host = project.Environment(name="host")
strict = project.Environment(name="strict")
strict.build_prefix = "strict"
src = project.root_dir / "src"


def make_report(environment):
    """One decoration per environment, which is the pcons idiom."""

    @environment.PyAction()
    def report(sources, targets, title):
        from pathlib import Path

        lines = [title]
        for name in sources:
            text = Path(name).read_text(encoding="utf-8")
            lines.append(f"{Path(name).name}: {len(text.split())}")
        Path(targets[0]).write_text("\n".join(lines) + "\n", encoding="utf-8")

    return report


report = make_report(host)

first = report(
    target=project.build_dir / "report.txt",
    source=[src / "a.txt", src / "b.txt"],
    title="word counts",
)
second = report(
    target=project.build_dir / "report2.txt",
    source=[src / "c.txt", src / "d.txt"],
    title="word counts, second set",
)
checked = make_report(strict)(
    target="report.txt",
    source=[src / "a.txt"],
    title="word counts, strict",
)

project.Default(first, second, checked)

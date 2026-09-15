# SPDX-License-Identifier: MIT
"""A sub-project's own environment is part of the project's environments.

Builders register nodes with the environment that made them. Some of those
nodes belong to a target and some do not -- a tool invocation's outputs, a
scanner's bookkeeping files, generated sources -- and the ones that do not
reach the build file solely through the environment walk. A sub-directory
script that creates its own ``Environment`` must not drop out of that walk,
or its build edges are silently missing.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from pcons import Generator
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.util.add_subdirectory import add_subdirectory

# A tool whose builder registers its output with the environment and makes
# no target: the plain node-only case, in no particular language.
CHILD_SCRIPT = textwrap.dedent(
    """
    import sys

    from pcons import Project
    from pcons.core.builder import CommandBuilder
    from pcons.tools.tool import BaseTool

    class CopyTool(BaseTool):
        def __init__(self):
            super().__init__("copier")

        def default_vars(self):
            python = sys.executable.replace("\\\\", "/")
            return {
                "cmd": [python, "-m", "pcons.util.commands", "concat"],
                "flags": [],
                "copycmd": ["$copier.cmd", "$copier.flags", "$$in", "$$out"],
            }

        def builders(self):
            return {
                "Copy": CommandBuilder(
                    "Copy",
                    "copier",
                    "copycmd",
                    src_suffixes=[".txt"],
                    target_suffixes=[".txt"],
                    single_source=True,
                ),
            }

    project = Project("child")
    env = project.Environment()
    CopyTool().setup(env)
    env.copier.Copy(project.build_dir / "child_copy.txt", ["in.txt"])
    """
)


def _write_child(root: Path) -> None:
    child = root / "child"
    child.mkdir(parents=True, exist_ok=True)
    (child / "pcons-build.py").write_text(CHILD_SCRIPT, encoding="utf-8")
    (child / "in.txt").write_text("hello\n", encoding="utf-8")


def _ninja(project: Project) -> str:
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    return (project.root_dir / "build" / "build.ninja").read_text(encoding="utf-8")


def test_a_child_environment_is_in_the_projects_environments(
    test_project: Project,
) -> None:
    _write_child(test_project.root_dir)

    add_subdirectory("child")

    child = test_project._children[0]
    assert child._environments
    assert all(env in test_project.environments for env in child._environments)


def test_a_child_environments_nodes_reach_the_build_file(
    test_project: Project,
) -> None:
    """The bug this pins: with the walk stopping at the top-level project,
    the child's edge was missing and ninja refused to load the build."""
    _write_child(test_project.root_dir)

    add_subdirectory("child")

    text = _ninja(test_project)

    assert "child_copy.txt" in text
    assert sys.executable.replace("\\", "/") in text

# SPDX-License-Identifier: MIT
"""End-to-end: a PyAction edge built by real ninja.

The only test that proves the three halves agree: what the decorator emits,
what the generator writes, and what the runner does with the argv it gets.
Everything else asserts against strings.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator

needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)


def generate(project: Project) -> None:
    """Write build.ninja for *project*."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)


def build(tmp_path: Path) -> str:
    """Run ninja in the build directory and return what it said."""
    result = subprocess.run(
        ["ninja"],
        cwd=tmp_path / "build",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def sources_project(tmp_path: Path, title: str) -> Project:
    """A project whose single PyAction concatenates two files under a title."""
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyAction()
    def report(sources, targets, title):
        from pathlib import Path

        body = "".join(Path(name).read_text(encoding="utf-8") for name in sources)
        Path(targets[0]).write_text(f"{title}\n{body}", encoding="utf-8")

    report(target="report.txt", source=["a.txt", "b.txt"], title=title)
    generate(project)
    return project


@needs_ninja
def test_the_function_runs_and_writes_its_target(tmp_path: Path) -> None:
    sources_project(tmp_path, "Report")

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Report\nfirst\nsecond\n"


@needs_ninja
def test_a_second_build_has_nothing_to_do(tmp_path: Path) -> None:
    """The generated module and pickle must not move on a rerun."""
    sources_project(tmp_path, "Report")
    build(tmp_path)

    assert "no work to do" in build(tmp_path)


@needs_ninja
def test_changed_kwargs_rebuild_the_target(tmp_path: Path) -> None:
    """The pickle is a real input of the edge, not a file beside it."""
    sources_project(tmp_path, "Report")
    build(tmp_path)

    Project._clear_tree()
    sources_project(tmp_path, "Second")
    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Second\nfirst\nsecond\n"


@needs_ninja
def test_a_changed_source_rebuilds_the_target(tmp_path: Path) -> None:
    sources_project(tmp_path, "Report")
    build(tmp_path)

    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Report\nchanged\nsecond\n"


@needs_ninja
def test_two_targets_and_no_sources(tmp_path: Path) -> None:
    """``--n-targets 2`` and an empty source tail, split by the runner."""
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyAction()
    def split(sources, targets, n):
        from pathlib import Path

        for index, name in enumerate(targets):
            Path(name).write_text(f"{index} of {n}, {len(sources)} sources\n")

    split(target=["one.txt", "two.txt"], n=2)
    generate(project)
    build(tmp_path)

    assert (tmp_path / "build" / "one.txt").read_text(
        encoding="utf-8"
    ) == "0 of 2, 0 sources\n"
    assert (tmp_path / "build" / "two.txt").read_text(
        encoding="utf-8"
    ) == "1 of 2, 0 sources\n"


@needs_ninja
def test_one_function_makes_two_edges_from_one_module(tmp_path: Path) -> None:
    """The whole point of the builder shape, end to end.

    One decoration, two calls: one generated module, two pickles, two edges,
    and ninja builds both.
    """
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyAction()
    def report(sources, targets, title):
        from pathlib import Path

        body = "".join(Path(name).read_text(encoding="utf-8") for name in sources)
        Path(targets[0]).write_text(f"{title}\n{body}", encoding="utf-8")

    report(target="both.txt", source=["a.txt", "b.txt"], title="Both")
    report(target="just_a.txt", source=["a.txt"], title="Just a")
    generate(project)
    build(tmp_path)

    generated = sorted(q.name for q in (tmp_path / "build" / "pyact").iterdir())
    assert [q for q in generated if q.endswith((".py", ".pkl"))] == [
        "both.args.pkl",
        "just_a.args.pkl",
        "report.py",
    ]
    assert (tmp_path / "build" / "both.txt").read_text(
        encoding="utf-8"
    ) == "Both\nfirst\nsecond\n"
    assert (tmp_path / "build" / "just_a.txt").read_text(
        encoding="utf-8"
    ) == "Just a\nfirst\n"


@needs_ninja
def test_a_subdirectory_builds_its_own_edge(tmp_path: Path) -> None:
    """The path a plain build-relative source would get wrong."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("from the subdirectory\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    with project._enter_subdir("sub"):
        child = Project("child", root_dir=tmp_path / "sub")
        env: Any = child.Environment()

        @env.PyAction()
        def report(sources, targets):
            from pathlib import Path

            Path(targets[0]).write_text(
                Path(sources[0]).read_text(encoding="utf-8"), encoding="utf-8"
            )

        report(target="report.txt", source=["a.txt"])

    generate(project)
    build(tmp_path)

    assert (tmp_path / "build" / "sub" / "report.txt").read_text(
        encoding="utf-8"
    ) == "from the subdirectory\n"

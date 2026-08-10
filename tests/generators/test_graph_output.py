# SPDX-License-Identifier: MIT
"""The graph that --graph/--mermaid asks for actually gets written.

PCONS_GRAPH/PCONS_MERMAID used to be read at the tail of Project.resolve(),
where a graph queued with generator.generate() landed on a pending list the
caller was already draining: the stdout spelling staged into a temporary
directory and read back a file nothing had written, crashing, and the
named-file spelling wrote nothing and said nothing.

They are now read once the whole generation pass has drained, which is also
what makes the graph agree with the build files: a script may resolve() and go
on adding targets, and a graph written from the first resolve() would show
neither those targets nor anything queued after it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator

BUILD_SCRIPT = """\
from pcons import Project

project = Project("demo")
env = project.Environment()
env.Command(target="out.txt", source="in.txt", command="cp $SOURCE $TARGET")
"""


def _make_project(tmp_path: Path) -> Project:
    """A project with one Command target, so the graph has an edge to show."""
    (tmp_path / "in.txt").write_text("source\n")
    project = Project("demo", root_dir=tmp_path, build_dir=tmp_path / "build")
    env = project.Environment()
    env.Command(target="out.txt", source="in.txt", command="cp $SOURCE $TARGET")
    return project


def _generate(project: Project) -> None:
    """Run generation the way the CLI does: from outside resolve()."""
    BaseGenerator._generate_pending(project)


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "in.txt").write_text("source\n")
    (tmp_path / "pcons-build.py").write_text(BUILD_SCRIPT)
    return subprocess.run(
        [sys.executable, "-m", "pcons.cli", "generate", *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )


class TestGraphToStdout:
    """The no-filename spelling, which both options document as stdout."""

    def test_dot_graph_reaches_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv("PCONS_GRAPH", "-")
        _generate(_make_project(tmp_path))

        out = capsys.readouterr().out
        assert 'digraph "demo" {' in out
        assert "in_txt -> out_txt" in out
        assert out.rstrip().endswith("}")

    def test_mermaid_graph_reaches_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv("PCONS_MERMAID", "-")
        _generate(_make_project(tmp_path))

        out = capsys.readouterr().out
        assert "flowchart LR" in out
        assert "in_txt --> out_txt" in out

    def test_stdout_carries_the_diagram_and_no_label_above_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """`pcons generate --mermaid > deps.mmd` must give a file Mermaid reads.

        A label written above the diagram is a comment in DOT but not in
        Mermaid, whose comment marker is %%, so anything printed there makes
        the redirected file invalid.
        """
        monkeypatch.setenv("PCONS_MERMAID", "-")
        _generate(_make_project(tmp_path))

        assert capsys.readouterr().out.startswith("---\ntitle: demo Dependencies\n")

    def test_bare_graph_option_exits_zero_with_a_graph(self, tmp_path: Path) -> None:
        result = _run_cli(tmp_path, "--graph")
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert 'digraph "demo" {' in result.stdout
        assert "in_txt -> out_txt" in result.stdout

    def test_bare_mermaid_option_exits_zero_with_a_graph(self, tmp_path: Path) -> None:
        result = _run_cli(tmp_path, "--mermaid")
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert "flowchart LR" in result.stdout
        assert "in_txt --> out_txt" in result.stdout


class TestGraphToFile:
    """The named-file spelling, including a directory that does not exist yet."""

    def test_dot_graph_reaches_the_named_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PCONS_GRAPH", "g.dot")
        _generate(_make_project(tmp_path))

        dot = (tmp_path / "g.dot").read_text()
        assert dot.startswith('digraph "demo" {')
        assert "in_txt -> out_txt" in dot

    def test_mermaid_graph_reaches_the_named_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PCONS_MERMAID", "diagrams/m.mmd")
        _generate(_make_project(tmp_path))

        mmd = (tmp_path / "diagrams" / "m.mmd").read_text()
        assert "flowchart LR" in mmd
        assert "in_txt --> out_txt" in mmd


class TestGraphDescribesTheWholeProject:
    """What the graph shows and what the build files build are the same thing."""

    def test_targets_added_after_an_explicit_resolve_are_in_the_graph(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A script may resolve() and keep building. examples/18 does.

        Writing from the first resolve() would snapshot a project still under
        construction, and the graph would then disagree with the build.ninja
        written beside it, silently and with a zero exit.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PCONS_GRAPH", "g.dot")
        project = _make_project(tmp_path)
        project.resolve()
        env = project.Environment()
        (tmp_path / "late.txt").write_text("late\n")
        env.Command(target="after.txt", source="late.txt", command="cp $SOURCE $TARGET")
        _generate(project)

        assert "after_txt" in (tmp_path / "g.dot").read_text()

    def test_resolving_twice_writes_one_graph(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Two digraph blocks on one stream is not a document anything reads."""
        monkeypatch.setenv("PCONS_GRAPH", "-")
        project = _make_project(tmp_path)
        project.resolve()
        project.resolve()
        _generate(project)

        assert capsys.readouterr().out.count("digraph") == 1


class TestGraphFailuresAreContained:
    """A diagnostic output the user asked for cannot cost them the build."""

    def test_an_unwritable_destination_still_leaves_the_build_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "blocker").write_text("not a directory\n")
        monkeypatch.setenv("PCONS_GRAPH", "blocker/g.dot")
        project = _make_project(tmp_path)

        with pytest.raises(PconsError) as excinfo:
            _generate(project)

        # The path came from the command line, so the message names it rather
        # than surfacing an errno from mkdir.
        assert "blocker/g.dot" in str(excinfo.value)
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_a_script_that_raises_leaves_no_graph_behind(self, tmp_path: Path) -> None:
        """Generation is cancelled on a crash; the graph goes with it."""
        (tmp_path / "in.txt").write_text("source\n")
        (tmp_path / "pcons-build.py").write_text(
            BUILD_SCRIPT + "\nraise RuntimeError('boom')\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate", "--graph=g.dot"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert result.returncode == 1
        assert not (tmp_path / "g.dot").exists()
        assert not (tmp_path / "build" / "build.ninja").exists()

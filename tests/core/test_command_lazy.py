# SPDX-License-Identifier: MIT
"""A command whose target= or source= is a callable, run during resolve.

The point is naming a file after something only the resolver knows: another
edge's real output path. The callable runs once this edge's dependencies have
produced their files, and what it returns is anchored exactly as a written-out
target is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Generator, Project
from pcons.core.errors import PconsError
from pcons.generators.generator import BaseGenerator


def _project(tmp_path: Path) -> Project:
    (tmp_path / "in.txt").write_text("hello\n", encoding="utf-8")
    return Project("demo", root_dir=tmp_path, build_dir="build")


def _ninja(tmp_path: Path, project: Project) -> str:
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    return (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")


def _outputs(target) -> list[str]:
    return [node.path.as_posix() for node in target.output_nodes]


def _sources(target) -> list[str]:
    info = target.output_nodes[0]._build_info
    assert info is not None
    return [node.path.as_posix() for node in info["sources"]]


def test_a_lazy_target_names_a_dependency_output(tmp_path: Path) -> None:
    """The callable reads what the edge it depends on built."""
    project = _project(tmp_path)
    env = project.Environment()
    first = env.Command(
        target="first.bin",
        source=["in.txt"],
        command=["copy", "$SOURCE", "$TARGET"],
    )
    second = env.Command(
        target=lambda: f"{first.output_nodes[0].path.stem}.hex",
        depends=[first],
        command=["objcopy", "$TARGET"],
    )

    assert second.output_nodes == []

    project.resolve()

    assert _outputs(second) == ["build/first.hex"]


def test_an_unnamed_lazy_command_is_labelled_by_its_output(tmp_path: Path) -> None:
    """No name is needed: the edge is anonymous, and wears its output's
    path once it has one, as every other command does."""
    project = _project(tmp_path)
    env = project.Environment()
    made = env.Command(target=lambda: "late.txt", command=["touch", "$TARGET"])

    assert made.anonymous
    project.resolve()

    assert made.name == "late.txt"


def test_a_named_lazy_command_keeps_its_name(tmp_path: Path) -> None:
    project = _project(tmp_path)
    env = project.Environment()
    made = env.Command(
        name="late", target=lambda: "x.txt", command=["touch", "$TARGET"]
    )

    project.resolve()

    assert made.name == "late"
    assert project.get_target("late") is made


def test_nothing_is_called_until_resolve(tmp_path: Path) -> None:
    """A callable target is not called while the script describes the build."""
    project = _project(tmp_path)
    env = project.Environment()
    calls: list[int] = []

    def naming() -> str:
        calls.append(1)
        return "late.txt"

    made = env.Command(target=naming, command=["touch", "$TARGET"])
    assert calls == []

    project.resolve()

    assert calls == [1]
    assert _outputs(made) == ["build/late.txt"]


def test_a_lazy_target_is_another_commands_source(tmp_path: Path) -> None:
    """A Target source resolves first, so its lazily named output is there."""
    project = _project(tmp_path)
    env = project.Environment()
    first = env.Command(target=lambda: "generated.txt", command=["touch", "$TARGET"])
    second = env.Command(
        target="second.txt",
        source=[first],
        command=["copy", "$SOURCE", "$TARGET"],
    )

    project.resolve()

    assert _sources(second) == ["build/generated.txt"]


def test_a_lazy_source_list(tmp_path: Path) -> None:
    """source= takes a callable too, and it decides the whole list."""
    project = _project(tmp_path)
    env = project.Environment()
    made = env.Command(
        target="out.txt",
        source=lambda: ["in.txt"],
        command=["copy", "$SOURCE", "$TARGET"],
    )

    project.resolve()

    assert _sources(made) == ["in.txt"]


def test_a_target_only_the_callable_names_is_resolved_first(tmp_path: Path) -> None:
    """Nothing orders a Target the callable returns before this edge: it's
    reached only when the callable runs, and it is resolved right then, as a
    written-out source Target is."""
    project = _project(tmp_path)
    env = project.Environment()
    later: list[object] = []
    made = env.Command(
        target="late.txt",
        source=lambda: later,
        command=["copy", "$SOURCE", "$TARGET"],
    )
    later.append(
        env.Command(
            target="other.txt",
            source=["in.txt"],
            command=["copy", "$SOURCE", "$TARGET"],
        )
    )

    project.resolve()

    assert _sources(made) == ["build/other.txt"]


def test_the_edge_reaches_the_generated_build_file(tmp_path: Path) -> None:
    """The nodes arrive in time for command expansion and generation."""
    project = _project(tmp_path)
    env = project.Environment()
    env.Command(
        target=lambda: ["late.txt"],
        source=["in.txt"],
        command=["copy", "$SOURCE", "$TARGET"],
    )

    text = _ninja(tmp_path, project)

    assert "build late.txt: " in text


def test_a_lazy_target_in_a_subdirectory(tmp_path: Path, monkeypatch) -> None:
    """Anchored where the script that declared it builds, as a written-out
    target is, although the callable runs long after that script finished."""
    from pcons.util.add_subdirectory import add_subdirectory

    monkeypatch.chdir(tmp_path)
    project = _project(tmp_path)
    project.Environment()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pcons-build.py").write_text(
        "from pcons import context\n"
        "env = context.current_project.default_environment\n"
        "made = env.Command(target=lambda: 'late.txt', command=['touch', '$TARGET'])\n"
    )
    made = add_subdirectory("sub").made

    project.resolve()

    assert _outputs(made) == ["build/sub/late.txt"]


class TestWhatConsumesALazyCommand:
    """Default(), Alias() and Install() all take the Target, not its nodes."""

    @staticmethod
    def _command(project: Project):
        env = project.Environment()
        return env.Command(
            target=lambda: ["late.txt"],
            source=["in.txt"],
            command=["copy", "$SOURCE", "$TARGET"],
        )

    def test_default(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        made = self._command(project)
        project.Default(made)

        text = _ninja(tmp_path, project)

        assert "default late.txt" in text

    def test_alias(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        made = self._command(project)
        project.Alias("gen", made)

        text = _ninja(tmp_path, project)

        assert "build gen: phony late.txt" in text

    def test_install(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        made = self._command(project)
        installed = project.Install("dist", [made])

        project.resolve()

        assert [node.path.name for node in installed.output_nodes] == ["late.txt"]


class TestRefusals:
    """What a callable may not do."""

    @pytest.mark.parametrize("returned", [[], None])
    def test_a_callable_naming_no_file(self, tmp_path: Path, returned) -> None:
        """A callable that names no file, or whose branches forget to return,
        would leave an edge that builds nothing."""
        project = _project(tmp_path)
        env = project.Environment()
        env.Command(target=lambda: returned, command=["touch", "$TARGET"])

        with pytest.raises(PconsError, match="must return the file"):
            project.resolve()

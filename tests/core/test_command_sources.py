# SPDX-License-Identifier: MIT
"""What a command's sources become on its command line."""

from __future__ import annotations

from pathlib import Path

from pcons import Generator, Project
from pcons.generators.generator import BaseGenerator


def _rule(tmp_path: Path, command: list[str], sources: list[str]) -> str:
    for name in sources:
        (tmp_path / name).write_text("")
    project = Project("demo", root_dir=tmp_path, build_dir="build")
    env = project.Environment()
    env.Command(
        name="gen",
        target=project.build_dir / "out.txt",
        source=[tmp_path / name for name in sources],
        command=command,
    )
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    text = (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")
    return next(
        line for line in text.splitlines() if line.strip().startswith("command =")
    )


def test_source_means_every_source(tmp_path: Path) -> None:
    """`$SOURCE` and `$SOURCES` are the same thing, and it is all of them.

    Easy to read as "the first one", and the mistake is quiet: the extra
    sources arrive as extra arguments, which a script may well ignore.
    """
    rule = _rule(tmp_path, ["run", "$SOURCE"], ["entry.py", "shared.py"])

    assert "run $in" in rule


def test_an_entry_script_beside_its_dependencies(tmp_path: Path) -> None:
    """The shape for a script whose siblings must be watched but not passed:
    list them as sources, and name only the first on the command line."""
    rule = _rule(tmp_path, ["run", "${SOURCES[0]}"], ["entry.py", "shared.py"])

    assert "run $source_0" in rule
    assert "$in" not in rule


def test_every_source_is_still_a_dependency(tmp_path: Path) -> None:
    """Naming one on the command line must not stop ninja watching the rest."""
    for name in ("entry.py", "shared.py"):
        (tmp_path / name).write_text("")
    project = Project("demo", root_dir=tmp_path, build_dir="build")
    env = project.Environment()
    env.Command(
        name="gen",
        target=project.build_dir / "out.txt",
        source=[tmp_path / "entry.py", tmp_path / "shared.py"],
        command=["run", "${SOURCES[0]}"],
    )
    Generator().generate(project)
    BaseGenerator._generate_pending(project)

    text = (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")
    edge = next(line for line in text.splitlines() if line.startswith("build out.txt"))
    assert "entry.py" in edge
    assert "shared.py" in edge


class TestSingularSourceWarning:
    """`$SOURCE` written where several sources will land.

    The spelling that reads as "one" is the one that means "all", and the
    mistake is quiet, so it is worth a word at the moment it is written.
    """

    @staticmethod
    def _command(tmp_path: Path, command, sources: list[str]) -> None:
        for name in sources:
            (tmp_path / name).write_text("")
        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        env.Command(
            name="gen",
            target=project.build_dir / "out.txt",
            source=[tmp_path / name for name in sources],
            command=command,
        )

    def test_warns_on_the_singular_with_several_sources(
        self, tmp_path: Path, caplog
    ) -> None:
        self._command(tmp_path, ["run", "$SOURCE"], ["a.py", "b.py"])

        assert "$SOURCE with 2 sources" in caplog.text
        assert "${SOURCES[0]}" in caplog.text

    def test_the_plural_says_what_it_means(self, tmp_path: Path, caplog) -> None:
        """`cat $SOURCES > $TARGET` is a perfectly good command."""
        self._command(tmp_path, ["cat", "$SOURCES"], ["a.py", "b.py"])

        assert caplog.text == ""

    def test_an_index_is_explicit(self, tmp_path: Path, caplog) -> None:
        self._command(tmp_path, ["run", "${SOURCES[0]}"], ["a.py", "b.py"])

        assert caplog.text == ""

    def test_one_source_is_unambiguous(self, tmp_path: Path, caplog) -> None:
        self._command(tmp_path, ["run", "$SOURCE"], ["a.py"])

        assert caplog.text == ""

    def test_the_string_form_too(self, tmp_path: Path, caplog) -> None:
        self._command(tmp_path, "run $SOURCE --out $TARGET", ["a.py", "b.py"])

        assert "$SOURCE with 2 sources" in caplog.text

    def test_a_source_within_a_larger_token(self, tmp_path: Path, caplog) -> None:
        """`./$SOURCE` runs a just-built tool, and has the same problem."""
        self._command(tmp_path, ["./$SOURCE", "$TARGET"], ["tool", "data"])

        assert "$SOURCE with 2 sources" in caplog.text


class TestWarningAttribution:
    """The warning must name the line somebody wrote.

    `project.Command` forwards to `env.Command`, so a fixed frame depth is
    right for one entry point and names pcons's own source for the other --
    the same two-entry-points shape as the docstrings that disagreed.
    """

    @staticmethod
    def _sources(tmp_path: Path) -> list[Path]:
        for name in ("a.py", "b.py"):
            (tmp_path / name).write_text("")
        return [tmp_path / "a.py", tmp_path / "b.py"]

    def test_through_env_command(self, tmp_path: Path, caplog) -> None:
        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            name="gen",
            target=project.build_dir / "out.txt",
            source=self._sources(tmp_path),
            command=["run", "$SOURCE"],
        )

        assert __file__ in caplog.text
        assert "pcons/core" not in caplog.text

    def test_through_project_command(self, tmp_path: Path, caplog) -> None:
        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        project.Command(
            "gen",
            env,
            target=project.build_dir / "out.txt",
            source=self._sources(tmp_path),
            command=["run", "$SOURCE"],
        )

        assert __file__ in caplog.text
        assert "pcons/core" not in caplog.text


class TestRegisteredBuilder:
    """`project.Command` is a method, but a builder of the same name is also
    registered, and the typing stub comes from that one (see issue #68).

    Nothing exercised it, so it could forward wrongly and no test would say.
    """

    def test_it_forwards_everything_it_accepts(self, tmp_path: Path) -> None:
        from pcons.builders.compile import CommandBuilder

        (tmp_path / "in.txt").write_text("")
        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        target = CommandBuilder.create_target(
            project,
            "gen",
            env,
            target=project.build_dir / "out.txt",
            source=tmp_path / "in.txt",
            command=["copy", "$SOURCE", "$TARGET"],
            restat=True,
            launcher=["time"],
        )

        assert target.name == "gen"
        node = target.output_nodes[0]
        assert node._build_info["restat"] is True
        assert node._build_info["launcher"] == ["time"]

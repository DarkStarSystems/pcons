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

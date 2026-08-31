# SPDX-License-Identifier: MIT
"""A Target or FileNode written straight into a command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Generator, Project
from pcons.core.errors import PconsError
from pcons.generators.generator import BaseGenerator


def _ninja(project: Project) -> str:
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    build_dir = Path(project.root_dir) / "build"
    return (build_dir / "build.ninja").read_text(encoding="utf-8")


def _line(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.strip().startswith(prefix))


def _project(tmp_path: Path, gcc_toolchain) -> Project:
    (tmp_path / "gen.c").write_text("int main(void) { return 0; }\n")
    (tmp_path / "in.txt").write_text("")
    return Project("demo", root_dir=tmp_path, build_dir="build")


def test_a_target_becomes_the_path_the_generator_writes_for_it(
    tmp_path: Path, gcc_toolchain
) -> None:
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[gen, "$SOURCE", "$TARGET"],
    )

    text = _ninja(project)
    built = _line(text, "build gen:").split(":")[0].removeprefix("build ").strip()

    assert f"command = {built} $in $out" in text


def test_the_target_is_a_dependency_of_the_command(
    tmp_path: Path, gcc_toolchain
) -> None:
    """Without the edge, ninja neither builds the tool first nor re-runs the
    command when it changes -- the failure the wrong path would at least
    have made loud."""
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[gen, "$SOURCE", "$TARGET"],
    )

    edge = _line(_ninja(project), "build out.txt:")

    assert "| gen" in edge


def test_the_target_stays_out_of_the_sources(tmp_path: Path, gcc_toolchain) -> None:
    """A caller's ${SOURCES[n]} keeps meaning the input it named."""
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[gen, "${SOURCES[0]}", "$TARGET"],
    )

    text = _ninja(project)

    assert "source_0 = $topdir/in.txt" in text
    assert "source_1" not in text


def test_a_tool_from_another_environment_carries_its_prefix(
    tmp_path: Path, gcc_toolchain
) -> None:
    """The case this exists for: a host tool run over a target artifact."""
    project = _project(tmp_path, gcc_toolchain)
    host = project.Environment(toolchain=gcc_toolchain, name="host")
    host.build_prefix = "host"
    tgt = project.Environment(toolchain=gcc_toolchain, name="tgt")
    tgt.build_prefix = "tgt"
    gen = project.Program("gen", host, sources=["gen.c"])
    app = project.Program("app", tgt, sources=["gen.c"])
    tgt.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=[app],
        command=[gen, "$SOURCE", "$TARGET"],
    )

    text = _ninja(project)

    assert "command = host/gen $in $out" in text
    assert "| host/gen" in _line(text, "build tgt/out.txt:")


def test_a_file_node_names_one_output_of_several(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    pair = env.Command(
        name="pair",
        target=[project.build_dir / "a.txt", project.build_dir / "b.txt"],
        source=["in.txt"],
        command=["touch", "$TARGETS"],
    )
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[pair.output_nodes[1], "$TARGET"],
    )

    text = _ninja(project)

    assert "command = b.txt $out" in text
    assert "| b.txt" in _line(text, "build out.txt:")


def test_a_target_with_several_outputs_says_so(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    pair = env.Command(
        name="pair",
        target=[project.build_dir / "a.txt", project.build_dir / "b.txt"],
        source=["in.txt"],
        command=["touch", "$TARGETS"],
    )
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[pair, "$TARGET"],
    )

    with pytest.raises(PconsError, match=r"a\.txt, build/b\.txt"):
        project.resolve()


def test_a_target_that_builds_nothing_says_so(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    iface = project.HeaderOnlyLibrary("iface")
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[iface, "$TARGET"],
    )

    with pytest.raises(PconsError, match="builds no file"):
        project.resolve()


def test_make_writes_the_same_path(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[gen, "$SOURCE", "$TARGET"],
    )

    Generator("makefile").generate(project)
    BaseGenerator._generate_pending(project)
    text = (tmp_path / "build" / "Makefile").read_text(encoding="utf-8")

    lines = text.splitlines()
    rule = lines.index(_line(text, "out.txt:"))

    assert "gen" in lines[rule].split("|")[0]
    assert Path(lines[rule + 1].split()[0]).name == "gen"

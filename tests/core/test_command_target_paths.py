# SPDX-License-Identifier: MIT
"""A Target or FileNode written straight into a command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Generator, Project
from pcons.core.errors import PconsError
from pcons.generators.generator import BaseGenerator

from ._command_test_utils import (
    as_ninja_command,
    built_path,
    file_name_in,
    runs_as,
)


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
    built = built_path(project, gen)

    assert f"build {built}:" in text
    assert f"command = {as_ninja_command(runs_as(built))} $in $out" in text


def test_a_target_written_as_an_argument_stays_a_plain_path(
    tmp_path: Path, gcc_toolchain
) -> None:
    """Only the first token is what runs; behind a wrapper the same target is
    a file the wrapper is given."""
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=["strip", gen, "$TARGET"],
    )

    text = _ninja(project)
    built = built_path(project, gen)

    assert f"command = strip {built} $out" in text


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
    built = built_path(project, gen)

    assert f"command = {as_ninja_command(runs_as(built))} $in $out" in text
    assert f"| {built}" in _line(text, "build tgt/out.txt:")


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

    assert f"command = {as_ninja_command(runs_as('b.txt'))} $out" in text
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

    with pytest.raises(PconsError, match=r"a\.txt, build[/\\]b\.txt"):
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
    assert file_name_in(lines[rule + 1].split()[0]) == gen.output_nodes[0].path.name


def test_the_main_resolve_loop_reaches_every_command(
    tmp_path: Path, gcc_toolchain
) -> None:
    """The rewrite runs in the Command factory, which only the main resolve
    loop dispatches. A hook that created a Command target after that loop --
    a toolchain's after_resolve, the scanner wiring -- would leave its command
    naming objects the generators cannot render, so the invariant is that no
    such target exists once resolve() returns."""
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        source=["in.txt"],
        command=[gen, "$SOURCE", "$TARGET"],
    )
    env.Command(
        name="tooled",
        target=project.build_dir / "tooled.txt",
        tool=gen,
        source=["in.txt"],
        command="$TOOL $SOURCE $TARGET",
    )

    project.resolve()

    commands = [t for t in project.targets if t._builder_name == "Command"]
    assert commands
    assert all(t._resolved for t in commands)

    from pcons.core.node import FileNode
    from pcons.core.subst import ToolPath
    from pcons.core.target import Target

    for node in project._nodes.values():
        if not isinstance(node, FileNode) or not node._build_info:
            continue
        command = node._build_info.get("command") or []
        if isinstance(command, str):
            continue
        assert not [
            token
            for token in command
            if isinstance(token, (Target, FileNode, ToolPath))
        ]


def test_a_command_that_declares_no_output_resolves(
    tmp_path: Path, gcc_toolchain
) -> None:
    """The rewrite reads the command off the first output node, so a command
    with no output has nothing to rewrite and has to say so rather than
    reaching for a node that is not there."""
    project = _project(tmp_path, gcc_toolchain)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    outputless = env.Command(
        name="run",
        target=[],
        source=["in.txt"],
        command=[gen, "$SOURCE"],
    )

    project.resolve()

    assert outputless._resolved
    assert not outputless.output_nodes

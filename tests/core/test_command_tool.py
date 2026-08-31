# SPDX-License-Identifier: MIT
"""`Command(tool=...)`: the program a command runs, spelled to run."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Generator, Project
from pcons.core.errors import PconsError
from pcons.generators.generator import BaseGenerator


def _ninja(project: Project) -> str:
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    return (Path(project.root_dir) / "build" / "build.ninja").read_text(
        encoding="utf-8"
    )


def _line(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.strip().startswith(prefix))


def _project(tmp_path: Path) -> Project:
    (tmp_path / "gen.c").write_text("int main(void) { return 0; }\n")
    (tmp_path / "in.txt").write_text("")
    return Project("demo", root_dir=tmp_path, build_dir="build")


def test_a_built_tool_runs_without_a_hand_written_dot_slash(
    tmp_path: Path, gcc_toolchain
) -> None:
    """The wart this exists to remove: every caller repeating a platform
    conditional to execute a file whose path pcons already knows."""
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool=gen,
        source=["in.txt"],
        command="$TOOL $SOURCE $TARGET",
    )

    assert "command = ./gen $in $out" in _ninja(project)


def test_the_tool_is_a_dependency_and_not_a_source(
    tmp_path: Path, gcc_toolchain
) -> None:
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool=gen,
        source=["in.txt"],
        command="$TOOL ${SOURCES[0]} $TARGET",
    )

    text = _ninja(project)

    assert "| gen" in _line(text, "build out.txt:")
    assert "source_0 = $topdir/in.txt" in text
    assert "source_1" not in text


def test_a_tool_from_another_environment_carries_its_prefix(
    tmp_path: Path, gcc_toolchain
) -> None:
    project = _project(tmp_path)
    host = project.Environment(toolchain=gcc_toolchain, name="host")
    host.build_prefix = "host"
    tgt = project.Environment(toolchain=gcc_toolchain, name="tgt")
    tgt.build_prefix = "tgt"
    gen = project.Program("gen", host, sources=["gen.c"])
    app = project.Program("app", tgt, sources=["gen.c"])
    tgt.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool=gen,
        source=[app],
        command="$TOOL $SOURCE $TARGET",
    )

    text = _ninja(project)

    assert "command = ./host/gen $in $out" in text
    assert "| host/gen" in _line(text, "build tgt/out.txt:")


def test_an_installed_tool_keeps_its_absolute_path(
    tmp_path: Path, gcc_toolchain
) -> None:
    """The androiddeployqt case: a host tool this build did not produce."""
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool="/opt/qt/bin/androiddeployqt",
        source=["in.txt"],
        command="$TOOL --input $SOURCE --output $TARGET",
    )

    text = _ninja(project)

    assert "command = /opt/qt/bin/androiddeployqt --input $in --output $out" in text
    assert "|" not in _line(text, "build out.txt:")


def test_a_tool_on_the_path_stays_a_bare_name(tmp_path: Path, gcc_toolchain) -> None:
    """A "./" here would look for it in the build directory and never find it."""
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool="zip",
        source=["in.txt"],
        command="$TOOL $TARGET $SOURCE",
    )

    assert "command = zip $out $in" in _ninja(project)


def test_a_tool_nobody_runs_is_an_error(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)

    with pytest.raises(PconsError, match="never run"):
        env.Command(
            name="run",
            target=project.build_dir / "out.txt",
            tool="zip",
            source=["in.txt"],
            command="cp $SOURCE $TARGET",
        )


def test_a_tool_marker_with_no_tool_is_an_error(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)

    with pytest.raises(PconsError, match=r"\$TOOL"):
        env.Command(
            name="run",
            target=project.build_dir / "out.txt",
            source=["in.txt"],
            command="$TOOL $SOURCE $TARGET",
        )


def test_make_runs_the_same_tool(tmp_path: Path, gcc_toolchain) -> None:
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = project.Program("gen", env, sources=["gen.c"])
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool=gen,
        source=["in.txt"],
        command="$TOOL $SOURCE $TARGET",
    )

    Generator("makefile").generate(project)
    BaseGenerator._generate_pending(project)
    text = (tmp_path / "build" / "Makefile").read_text(encoding="utf-8")

    lines = text.splitlines()
    rule = lines.index(_line(text, "out.txt:"))

    assert "gen" in lines[rule].split("|")[0]
    assert Path(lines[rule + 1].split()[0]).name == "gen"


@pytest.mark.parametrize(
    ("path", "windows", "expected"),
    [
        ("gen", False, "./gen"),
        ("host/gen", False, "./host/gen"),
        ("/opt/qt/bin/androiddeployqt", False, "/opt/qt/bin/androiddeployqt"),
        ("./gen", False, "./gen"),
        ("gen.exe", True, "gen.exe"),
        ("host/gen.exe", True, "host\\gen.exe"),
        ("C:/Qt/bin/androiddeployqt.exe", True, "C:\\Qt\\bin\\androiddeployqt.exe"),
    ],
)
def test_the_executable_spelling_each_shell_needs(
    path: str, windows: bool, expected: str
) -> None:
    """cmd.exe reads a leading "/" as a switch and searches the working
    directory; a POSIX shell does neither."""
    from pcons.core.paths import executable_form

    assert executable_form(path, windows=windows) == expected


def test_text_attached_to_the_marker_comes_along(tmp_path: Path, gcc_toolchain) -> None:
    """$TOOL is a path like $SOURCE and $TARGET, so a token may carry it
    inside a larger argument."""
    project = _project(tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    env.Command(
        name="run",
        target=project.build_dir / "out.txt",
        tool="/opt/sdk/bin/signer",
        source=["in.txt"],
        command="cp --helper=$TOOL $SOURCE $TARGET",
    )

    assert "cp --helper=/opt/sdk/bin/signer $in $out" in _ninja(project)

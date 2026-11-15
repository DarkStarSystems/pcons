# SPDX-License-Identifier: MIT
"""Tests for ColorFlag: colored diagnostics, only where the runner strips them."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pcons.cli import _needs_generation
from pcons.core.explain import format_node_command
from pcons.core.project import Project
from pcons.core.subst import ColorFlag, to_shell_command
from pcons.generators.compile_commands import CompileCommandsGenerator
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator, writes_color_flags
from pcons.toolchains.clang_cl import ClangClCCompiler
from pcons.toolchains.flang import FlangCompiler
from pcons.toolchains.gcc import GccCCompiler, GccCxxCompiler
from pcons.toolchains.gfortran import GfortranCompiler
from pcons.toolchains.gnu_common import GNU_COLOR_FLAGS, clang_color_flags
from pcons.toolchains.ifx import IfxCompiler
from pcons.toolchains.llvm import ClangCCompiler, ClangCxxCompiler

COLOR = "-fdiagnostics-color=always"


@pytest.fixture(autouse=True)
def _color_wanted(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NO_COLOR", "FORCE_COLOR", "CLICOLOR_FORCE", "NINJA"):
        monkeypatch.delenv(name, raising=False)


def _project(tmp_path: Path, toolchain: Any) -> Project:
    (tmp_path / "main.c").write_text("int main(void){return 0;}\n")
    project = Project("test", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=toolchain)
    project.Program("app", env, sources=["main.c"])
    return project


def _generate(project: Project, generator: BaseGenerator) -> None:
    generator.generate(project)
    BaseGenerator._generate_pending(project)


def _ninja_text(tmp_path: Path, toolchain: Any) -> str:
    _generate(_project(tmp_path, toolchain), NinjaGenerator())
    return (tmp_path / "build" / "build.ninja").read_text()


class TestNinja:
    def test_writes_the_flag(self, tmp_path, gcc_toolchain):
        text = _ninja_text(tmp_path, gcc_toolchain)
        assert COLOR in text
        assert "# Colored diagnostics: on\n" in text

    def test_no_color_leaves_it_out(self, tmp_path, gcc_toolchain, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        text = _ninja_text(tmp_path, gcc_toolchain)
        assert COLOR not in text
        assert "# Colored diagnostics: off\n" in text

    def test_empty_no_color_is_unset(self, tmp_path, gcc_toolchain, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        assert COLOR in _ninja_text(tmp_path, gcc_toolchain)

    @pytest.mark.parametrize(
        ("runner", "written"),
        [
            ("ninja", True),
            ("/opt/bin/ninja", True),
            ("ninja.exe", True),
            ("n2", False),
            ("samu", False),
        ],
    )
    def test_only_for_a_runner_that_strips_color(
        self, tmp_path, gcc_toolchain, monkeypatch, runner, written
    ):
        """n2 and samurai pass a command's escapes through to a pipe."""
        monkeypatch.setenv("NINJA", runner)
        assert (COLOR in _ninja_text(tmp_path, gcc_toolchain)) is written

    def test_runner_argument_beats_environment(self, monkeypatch):
        monkeypatch.setenv("NINJA", "ninja")
        assert writes_color_flags("n2") is False

    @pytest.mark.parametrize(
        ("env", "runner", "written"),
        [
            ({"FORCE_COLOR": "1"}, "n2", True),
            ({"CLICOLOR_FORCE": "1"}, "samu", True),
            ({"FORCE_COLOR": "0"}, "n2", False),
            ({"FORCE_COLOR": ""}, "n2", False),
            ({"FORCE_COLOR": "1", "NO_COLOR": "1"}, "ninja", True),
            ({"CLICOLOR_FORCE": "1", "NO_COLOR": "1"}, "ninja", False),
        ],
        ids=[
            "force-color",
            "clicolor-force",
            "force-color-0",
            "force-color-empty",
            "force-color-beats-no-color",
            "no-color-beats-clicolor-force",
        ],
    )
    def test_forced_color(self, monkeypatch, env, runner, written):
        """Forced color reaches any runner, as ninja ranks these variables."""
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert writes_color_flags(runner) is written

    def test_flags_come_after_it(self, tmp_path, gcc_toolchain):
        """So a -fdiagnostics-color=never in the flags wins."""
        project = _project(tmp_path, gcc_toolchain)
        project.environments[0].cc.flags.append("-fdiagnostics-color=never")
        _generate(project, NinjaGenerator())
        text = (tmp_path / "build" / "build.ninja").read_text()
        assert text.index(COLOR) < text.index("-fdiagnostics-color=never")


class TestOtherGenerators:
    def test_make_leaves_it_out(self, tmp_path, gcc_toolchain):
        """Make passes a command's escapes through even to a file."""
        _generate(_project(tmp_path, gcc_toolchain), MakefileGenerator())
        assert COLOR not in (tmp_path / "build" / "Makefile").read_text()

    def test_compile_commands_leaves_it_out(self, tmp_path, gcc_toolchain):
        _generate(_project(tmp_path, gcc_toolchain), CompileCommandsGenerator())
        text = (tmp_path / "compile_commands.json").read_text()
        assert "main.c" in text
        assert COLOR not in text

    def test_shell_command_leaves_out_an_unclaimed_marker(self):
        assert to_shell_command(["cc", ColorFlag(COLOR), "a.c"]) == "cc a.c"

    def test_explain_shows_it_as_ninja_runs_it(
        self, tmp_path, gcc_toolchain, monkeypatch
    ):
        project = _project(tmp_path, gcc_toolchain)
        project.resolve()
        obj = next(
            n
            for n in project.targets[0].intermediate_nodes
            if (n._build_info or {}).get("command")
        )
        assert COLOR in (format_node_command(obj) or "")
        monkeypatch.setenv("NO_COLOR", "1")
        assert COLOR not in (format_node_command(obj) or "")


class TestRegeneration:
    def test_changing_no_color_needs_generation(
        self, tmp_path, gcc_toolchain, monkeypatch
    ):
        """A fresh build.ninja is stale once its colors no longer match."""
        _ninja_text(tmp_path, gcc_toolchain)
        script = tmp_path / "pcons-build.py"
        script.write_text("")
        os.utime(script, (0, 0))
        build_dir = tmp_path / "build"
        assert not _needs_generation(build_dir, str(script))
        monkeypatch.setenv("NO_COLOR", "1")
        assert _needs_generation(build_dir, str(script))
        assert _needs_generation(build_dir, str(script), runner="n2")


@pytest.mark.parametrize(
    ("tool", "flags"),
    [
        (GccCCompiler, GNU_COLOR_FLAGS),
        (GccCxxCompiler, GNU_COLOR_FLAGS),
        (ClangCCompiler, clang_color_flags()),
        (ClangCxxCompiler, clang_color_flags()),
        (ClangClCCompiler, clang_color_flags()),
        (GfortranCompiler, GNU_COLOR_FLAGS),
        (FlangCompiler, GNU_COLOR_FLAGS),
        (IfxCompiler, ()),
    ],
)
def test_compilers_color_flags(tool, flags):
    tool_vars = tool().default_vars()
    assert tool_vars["colorflags"] == [ColorFlag(f) for f in flags]
    assert any(str(t).endswith(".colorflags") for t in tool_vars["objcmd"])


def _warning_build(tmp_path: Path) -> Path:
    """Generate a real build whose one compile warns; return its build dir."""
    from pcons.toolchains import find_c_toolchain

    if shutil.which("ninja") is None:
        pytest.skip("needs ninja")
    try:
        toolchain = find_c_toolchain()
    except Exception:
        pytest.skip("needs a C compiler")
    if toolchain.name == "msvc":
        pytest.skip("cl has no colored diagnostics")
    project = _project(tmp_path, toolchain)
    (tmp_path / "main.c").write_text(
        "int f(int x) { if (x) return 1; }\nint main(void){return 0;}\n"
    )
    project.environments[0].cc.flags.append("-Wall")
    _generate(project, NinjaGenerator())
    return tmp_path / "build"


def test_piped_build_output_has_no_escapes(tmp_path):
    """The promise behind writing the flag: ninja strips the color off a pipe."""
    build_dir = _warning_build(tmp_path)
    result = subprocess.run(
        ["ninja", "-C", str(build_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning" in result.stdout
    assert "\x1b" not in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_terminal_build_output_is_colored(tmp_path):
    import pty

    build_dir = _warning_build(tmp_path)
    leader, follower = pty.openpty()
    with subprocess.Popen(
        ["ninja", "-C", str(build_dir)],
        stdout=follower,
        stderr=follower,
        env={**os.environ, "TERM": "xterm"},
    ) as ninja:
        os.close(follower)
        output = b""
        while True:
            try:
                chunk = os.read(leader, 4096)
            except OSError:  # Linux: the terminal closed with the process
                break
            if not chunk:
                break
            output += chunk
    os.close(leader)
    assert ninja.returncode == 0, output
    warning_line = next(ln for ln in output.splitlines() if b"warning" in ln)
    assert b"\x1b[" in warning_line

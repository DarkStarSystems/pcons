# SPDX-License-Identifier: MIT
"""Tests for command launchers (pcons.core.launcher)."""

from __future__ import annotations

from pathlib import Path

from pcons import Generator
from pcons.core.environment import Environment
from pcons.core.launcher import resolve_launcher
from pcons.generators.generator import BaseGenerator


class TestResolveLauncher:
    """Reading a launcher off a tool namespace."""

    def test_every_tool_has_one(self, test_project) -> None:
        """A tool's author need not have thought about launchers."""
        env = Environment()
        env.add_tool("cc").set("cmd", "gcc")

        assert env.cc.launcher == []
        assert resolve_launcher(env, "cc") == []

    def test_tokens_are_returned_in_order(self, test_project) -> None:
        env = Environment()
        env.add_tool("cc").set("cmd", "gcc")
        env.cc.launcher = ["ccache", "time"]

        assert resolve_launcher(env, "cc") == ["ccache", "time"]

    def test_a_bare_string_is_tolerated(self, test_project) -> None:
        env = Environment()
        env.add_tool("cc").set("cmd", "gcc")
        env.cc.set("launcher", "ccache")

        assert resolve_launcher(env, "cc") == ["ccache"]

    def test_unknown_tool_has_no_launcher(self, test_project) -> None:
        assert resolve_launcher(Environment(), "nosuchtool") == []
        assert resolve_launcher(Environment(), None) == []

    def test_launcher_must_be_a_list(self, test_project) -> None:
        """Assignment catches the same mistake `flags = "-Wall"` does."""
        import pytest

        env = Environment()
        env.add_tool("cc").set("cmd", "gcc")

        with pytest.raises(TypeError, match="must be a list"):
            env.cc.launcher = "ccache"


class TestGeneratedCommands:
    """What a launcher does to the build files."""

    @staticmethod
    def _ninja_text(tmp_path: Path, launcher: list[str]) -> str:
        from pcons import Project

        source = tmp_path / "hello.c"
        source.write_text("int main(void) { return 0; }\n")

        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c")
        env.cc.launcher = launcher
        project.Program("hello", env, sources=[source])
        Generator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")

    def test_launcher_precedes_the_compiler(self, tmp_path: Path) -> None:
        text = self._ninja_text(tmp_path, ["ccache"])

        compile_rules = [
            line for line in text.splitlines() if line.strip().startswith("command =")
        ]
        assert any("ccache " in line for line in compile_rules)

    def test_tokens_stay_separate_words(self, tmp_path: Path) -> None:
        """The bug this replaced: `"ccache gcc"` as one quoted word, exit 127."""
        text = self._ninja_text(tmp_path, ["ccache"])

        assert '"ccache ' not in text

    def test_a_launcher_path_with_a_space_is_quoted(self, tmp_path: Path) -> None:
        """Quoted as one word only because it *is* one token."""
        text = self._ninja_text(tmp_path, ["/opt/my tools/ccache"])

        assert '"/opt/my tools/ccache"' in text or "'/opt/my tools/ccache'" in text

    def test_no_launcher_leaves_the_command_alone(self, tmp_path: Path) -> None:
        text = self._ninja_text(tmp_path, [])

        assert "ccache" not in text

    def test_compile_commands_reports_the_real_compiler(self, tmp_path: Path) -> None:
        """An IDE wants the compiler, not whatever is caching it."""
        import json

        from pcons import Project

        source = tmp_path / "hello.c"
        source.write_text("int main(void) { return 0; }\n")

        project = Project("demo", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c")
        env.cc.launcher = ["ccache"]
        project.Program("hello", env, sources=[source])
        Generator().generate(project)
        BaseGenerator._generate_pending(project)

        entries = json.loads(
            (tmp_path / "build" / "compile_commands.json").read_text(encoding="utf-8")
        )
        assert entries
        for entry in entries:
            assert "ccache" not in entry["command"]

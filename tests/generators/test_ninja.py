# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.ninja."""

from pathlib import Path

from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.ninja import NinjaGenerator


class TestNinjaGenerator:
    def test_is_generator(self):
        gen = NinjaGenerator()
        assert gen.name == "ninja"

    def test_creates_build_ninja(self, tmp_path):
        project = Project("test", root_dir=tmp_path)
        gen = NinjaGenerator()

        gen.generate(project, tmp_path)

        ninja_file = tmp_path / "build.ninja"
        assert ninja_file.exists()

    def test_header_contains_project_name(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path)
        gen = NinjaGenerator()

        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "myproject" in content

    def test_writes_builddir_variable(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="out")
        gen = NinjaGenerator()

        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "builddir = out" in content


class TestNinjaBuildStatements:
    def test_writes_build_for_target(self, tmp_path):
        project = Project("test", root_dir=tmp_path)

        # Create a target with a node that has build info
        target = Target("app")
        output_node = FileNode("build/app.o")
        source_node = FileNode("src/main.c")

        # Simulate what a builder would do
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline",
            src_suffixes=[".c"], target_suffixes=[".o"]
        )

        target.nodes.append(output_node)
        target.sources.append(source_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "build build/app.o:" in content
        assert "cc_cmdline" in content
        assert "src/main.c" in content

    def test_writes_rule_for_builder(self, tmp_path):
        project = Project("test", root_dir=tmp_path)

        target = Target("app")
        output_node = FileNode("build/app.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline",
            src_suffixes=[".c"], target_suffixes=[".o"]
        )

        target.nodes.append(output_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "rule cc_cmdline" in content
        assert "command = " in content


class TestNinjaAliases:
    def test_writes_aliases(self, tmp_path):
        project = Project("test", root_dir=tmp_path)

        target = Target("mylib")
        lib_node = FileNode("build/libmy.a")
        target.nodes.append(lib_node)
        project.add_target(target)

        project.Alias("libs", target)

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "build libs: phony" in content
        assert "build/libmy.a" in content


class TestNinjaDefaults:
    def test_writes_defaults(self, tmp_path):
        project = Project("test", root_dir=tmp_path)

        target = Target("app")
        app_node = FileNode("build/app")
        target.nodes.append(app_node)
        project.add_target(target)

        project.Default(target)

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "default build/app" in content


class TestNinjaEscaping:
    def test_escapes_spaces_in_paths(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("path with spaces/file.c"))
        assert escaped == "path$ with$ spaces/file.c"

    def test_escapes_dollar_signs(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("$HOME/file.c"))
        assert escaped == "$$HOME/file.c"

    def test_escapes_colons(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("C:/path/file.c"))
        assert escaped == "C$:/path/file.c"

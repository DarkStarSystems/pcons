# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.compile_commands."""

import json
import os
from pathlib import Path

from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.compile_commands import CompileCommandsGenerator


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison."""
    return p.replace("\\", "/")


class TestCompileCommandsGenerator:
    def test_is_generator(self):
        gen = CompileCommandsGenerator()
        assert gen.name == "compile_commands"

    def test_creates_compile_commands_json(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = CompileCommandsGenerator()

        gen.generate(project)

        output_file = tmp_path / "compile_commands.json"
        assert output_file.exists()

    def test_empty_project_produces_empty_array(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = CompileCommandsGenerator()

        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert content == []


class TestCompileCommandsEntries:
    def test_includes_c_files(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/main.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
        )

        # Use object_nodes for compilation outputs
        target.object_nodes.append(output_node)
        target._sources.append(source_node)
        project.add_target(target)

        gen = CompileCommandsGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert len(content) == 1
        # Normalize path separators for cross-platform comparison
        assert normalize_path(content[0]["file"]) == "src/main.c"
        assert normalize_path(content[0]["output"]) == "build/main.o"

    def test_includes_cpp_files(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/main.o")
        source_node = FileNode("src/main.cpp")
        output_node._build_info = {
            "tool": "cxx",
            "command_var": "cmdline",
            "language": "cxx",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cxx",
            "cmdline",
            src_suffixes=[".cpp"],
            target_suffixes=[".o"],
            language="cxx",
        )

        # Use object_nodes for compilation outputs
        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = CompileCommandsGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert len(content) == 1
        # Normalize path separators for cross-platform comparison
        assert normalize_path(content[0]["file"]) == "src/main.cpp"

    def test_excludes_link_commands(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app")
        source_node = FileNode("build/main.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "cmdline",
            "language": None,  # Linking doesn't have a language
            "sources": [source_node],
        }

        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = CompileCommandsGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert len(content) == 0

    def test_entry_has_directory(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/main.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
        )

        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = CompileCommandsGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert content[0]["directory"] == str(tmp_path.absolute())

    def test_entry_has_command(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/main.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
        )

        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = CompileCommandsGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "compile_commands.json").read_text())
        assert "command" in content[0]
        # Normalize path separators for cross-platform comparison
        assert "src/main.c" in normalize_path(content[0]["command"])


class TestCompileCommandsSymlink:
    def test_creates_symlink_at_project_root(self, tmp_path):
        build_dir = tmp_path / "build"
        project = Project("test", root_dir=tmp_path, build_dir=build_dir)
        gen = CompileCommandsGenerator()

        gen.generate(project)

        link_path = tmp_path / "compile_commands.json"
        assert link_path.is_symlink()
        # Symlink should point to the build dir file
        assert (build_dir / "compile_commands.json").exists()

    def test_symlink_is_relative(self, tmp_path):
        build_dir = tmp_path / "build"
        project = Project("test", root_dir=tmp_path, build_dir=build_dir)
        gen = CompileCommandsGenerator()

        gen.generate(project)

        link_path = tmp_path / "compile_commands.json"
        target = os.readlink(link_path)
        # Should be a relative path, not absolute
        assert not Path(target).is_absolute()

    def test_symlink_idempotent(self, tmp_path):
        build_dir = tmp_path / "build"
        project = Project("test", root_dir=tmp_path, build_dir=build_dir)
        gen = CompileCommandsGenerator()

        gen.generate(project)
        gen.generate(project)  # Second call should not fail

        link_path = tmp_path / "compile_commands.json"
        assert link_path.is_symlink()

    def test_does_not_overwrite_regular_file(self, tmp_path):
        build_dir = tmp_path / "build"
        project = Project("test", root_dir=tmp_path, build_dir=build_dir)

        # Create a regular file at the link location
        regular_file = tmp_path / "compile_commands.json"
        regular_file.write_text("user file")

        gen = CompileCommandsGenerator()
        gen.generate(project)

        # Should not have been replaced
        assert not regular_file.is_symlink()
        assert regular_file.read_text() == "user file"

    def test_updates_wrong_symlink(self, tmp_path):
        build_dir = tmp_path / "build"
        project = Project("test", root_dir=tmp_path, build_dir=build_dir)

        # Create a symlink pointing to the wrong place
        link_path = tmp_path / "compile_commands.json"
        link_path.symlink_to("wrong/path")

        gen = CompileCommandsGenerator()
        gen.generate(project)

        # Should have been updated
        target = os.readlink(link_path)
        assert "wrong" not in target


class TestAutoCompileCommands:
    def test_ninja_generates_compile_commands(self, tmp_path):
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path)
        gen = NinjaGenerator()

        gen.generate(project)

        assert (tmp_path / "compile_commands.json").exists()

    def test_ninja_skip_compile_commands(self, tmp_path):
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path)
        gen = NinjaGenerator()

        gen.generate(project, compile_commands=False)

        assert not (tmp_path / "compile_commands.json").exists()

    def test_compile_commands_generator_no_recursion(self, tmp_path):
        """CompileCommandsGenerator should not trigger itself."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path)
        gen = CompileCommandsGenerator()
        assert not gen._supports_compile_commands

        # Should not recurse
        gen.generate(project)

# SPDX-License-Identifier: MIT
"""Tests for MermaidGenerator."""

from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.mermaid import MermaidGenerator


class TestMermaidGeneratorBasic:
    """Basic tests for MermaidGenerator."""

    def test_generator_creation(self):
        """Test generator can be created."""
        gen = MermaidGenerator()
        assert gen.name == "mermaid"

    def test_generator_with_options(self):
        """Test generator accepts options."""
        gen = MermaidGenerator(
            show_files=True,
            direction="TB",
            output_filename="graph.mmd",
        )
        assert gen._show_files is True
        assert gen._direction == "TB"
        assert gen._output_filename == "graph.mmd"


class TestMermaidGeneratorTargetGraph:
    """Tests for target-level graph generation."""

    def test_empty_project(self, tmp_path):
        """Test generation with no targets."""
        project = Project("empty", build_dir=tmp_path)
        gen = MermaidGenerator()

        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "flowchart LR" in output
        assert "empty Dependencies" in output

    def test_single_target(self, tmp_path):
        """Test generation with single target."""
        project = Project("single", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "myapp" in output

    def test_target_dependencies(self, tmp_path):
        """Test generation shows target dependencies."""
        project = Project("deps", build_dir=tmp_path)

        libmath = Target("libmath", target_type="static_library")
        libphysics = Target("libphysics", target_type="static_library")
        app = Target("app", target_type="program")

        libphysics.link(libmath)
        app.link(libphysics)

        project.add_target(libmath)
        project.add_target(libphysics)
        project.add_target(app)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "libmath" in output
        assert "libphysics" in output
        assert "app" in output
        # Check edges exist
        assert "libmath --> libphysics" in output
        assert "libphysics --> app" in output

    def test_target_shapes(self, tmp_path):
        """Test different target types get different shapes."""
        project = Project("shapes", build_dir=tmp_path)

        project.add_target(Target("mylib", target_type="static_library"))
        project.add_target(Target("myshared", target_type="shared_library"))
        project.add_target(Target("myapp", target_type="program"))
        project.add_target(Target("headers", target_type="interface"))

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        # Static library: rectangle [name]
        assert "mylib[" in output
        # Shared library: stadium ([name])
        assert "myshared([" in output
        # Program: stadium [[name]]
        assert "myapp[[" in output
        # Interface: hexagon {{name}}
        assert "headers{{" in output


class TestMermaidGeneratorFileGraph:
    """Tests for file-level graph generation."""

    def test_file_graph_mode(self, tmp_path):
        """Test file-level graph generation."""
        project = Project("files", build_dir=tmp_path)

        target = Target("myapp", target_type="program")

        # Add some mock nodes
        src = FileNode(Path("src/main.c"))
        obj = FileNode(Path("build/main.o"))
        exe = FileNode(Path("build/myapp"))

        obj.depends([src])
        target.object_nodes.append(obj)
        target.output_nodes.append(exe)

        project.add_target(target)

        gen = MermaidGenerator(show_files=True)
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "main_c" in output  # sanitized source name
        assert "main_o" in output  # sanitized object name
        assert "myapp" in output


class TestMermaidGeneratorDirection:
    """Tests for graph direction options."""

    def test_left_right(self, tmp_path):
        """Test LR direction."""
        project = Project("lr", build_dir=tmp_path)
        gen = MermaidGenerator(direction="LR")
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "flowchart LR" in output

    def test_top_bottom(self, tmp_path):
        """Test TB direction."""
        project = Project("tb", build_dir=tmp_path)
        gen = MermaidGenerator(direction="TB")
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "flowchart TB" in output


class TestMermaidGeneratorSanitization:
    """Tests for ID sanitization."""

    def test_sanitize_path_separators(self, tmp_path):
        """Test paths are sanitized correctly."""
        gen = MermaidGenerator()

        assert gen._sanitize_id("foo/bar") == "foo_bar"
        assert gen._sanitize_id("foo\\bar") == "foo_bar"

    def test_sanitize_dots(self, tmp_path):
        """Test dots are sanitized."""
        gen = MermaidGenerator()

        assert gen._sanitize_id("foo.bar") == "foo_bar"
        assert gen._sanitize_id("main.c") == "main_c"

    def test_sanitize_leading_digit(self, tmp_path):
        """Test leading digits are handled."""
        gen = MermaidGenerator()

        assert gen._sanitize_id("123foo") == "n123foo"
        assert gen._sanitize_id("foo123") == "foo123"


class TestMermaidGeneratorIntegration:
    """Integration tests with resolved projects."""

    def test_resolved_project(self, tmp_path):
        """Test with a fully resolved project."""
        from pcons.tools.toolchain import BaseToolchain, SourceHandler

        # Create a minimal mock toolchain
        class MockToolchain(BaseToolchain):
            def __init__(self):
                super().__init__("mock")

            def _configure_tools(self, config):
                return True

            def get_source_handler(self, suffix):
                if suffix == ".c":
                    return SourceHandler("cc", "c", ".o", None, None)
                return None

            def get_object_suffix(self):
                return ".o"

            def get_static_library_name(self, name):
                return f"lib{name}.a"

            def get_program_name(self, name):
                return name

        # Create project
        project = Project("integrated", build_dir=tmp_path)
        toolchain = MockToolchain()

        env = Environment(toolchain=toolchain)
        env._project = project
        project._environments.append(env)

        # Create a source file
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        # Create target
        app = project.Program("myapp", env)
        app.sources = [project.node(src_file)]

        # Resolve
        project.resolve()

        # Generate mermaid
        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "myapp" in output
        assert "flowchart" in output

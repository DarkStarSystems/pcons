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
            include_headers=True,
            direction="TB",
            output_filename="graph.mmd",
        )
        assert gen._include_headers is True
        assert gen._direction == "TB"
        assert gen._output_filename == "graph.mmd"


class TestMermaidGeneratorGraph:
    """Tests for graph generation."""

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

        # Add mock nodes
        src = FileNode(Path("src/main.c"))
        obj = FileNode(Path("build/main.o"))
        exe = FileNode(Path("build/myapp"))

        obj.depends([src])
        target.object_nodes.append(obj)
        target.output_nodes.append(exe)

        project.add_target(target)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "myapp" in output
        assert "main_c" in output
        assert "main_o" in output

    def test_target_dependencies(self, tmp_path):
        """Test generation shows target dependencies."""
        project = Project("deps", build_dir=tmp_path)

        # Create libmath
        libmath = Target("libmath", target_type="static_library")
        math_src = FileNode(Path("src/math.c"))
        math_obj = FileNode(Path("build/math.o"))
        math_lib = FileNode(Path("build/libmath.a"))
        math_obj.depends([math_src])
        libmath.object_nodes.append(math_obj)
        libmath.output_nodes.append(math_lib)

        # Create libphysics depending on libmath
        libphysics = Target("libphysics", target_type="static_library")
        physics_src = FileNode(Path("src/physics.c"))
        physics_obj = FileNode(Path("build/physics.o"))
        physics_lib = FileNode(Path("build/libphysics.a"))
        physics_obj.depends([physics_src])
        libphysics.object_nodes.append(physics_obj)
        libphysics.output_nodes.append(physics_lib)
        libphysics.link(libmath)

        # Create app depending on libphysics
        app = Target("app", target_type="program")
        app_src = FileNode(Path("src/main.c"))
        app_obj = FileNode(Path("build/main.o"))
        app_exe = FileNode(Path("build/app"))
        app_obj.depends([app_src])
        app.object_nodes.append(app_obj)
        app.output_nodes.append(app_exe)
        app.link(libphysics)

        project.add_target(libmath)
        project.add_target(libphysics)
        project.add_target(app)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        assert "libmath_a" in output
        assert "libphysics_a" in output
        assert "app[[app]]" in output
        # Check library dependency edges
        assert "libmath_a --> libphysics_a" in output
        assert "libphysics_a --> app" in output

    def test_target_shapes(self, tmp_path):
        """Test different target types get different shapes."""
        project = Project("shapes", build_dir=tmp_path)

        # Static library
        lib = Target("mylib", target_type="static_library")
        lib.output_nodes.append(FileNode(Path("build/libmylib.a")))

        # Shared library
        shared = Target("myshared", target_type="shared_library")
        shared.output_nodes.append(FileNode(Path("build/libmyshared.so")))

        # Program
        prog = Target("myapp", target_type="program")
        prog.output_nodes.append(FileNode(Path("build/myapp")))

        # Interface
        iface = Target("headers", target_type="interface")
        iface.output_nodes.append(FileNode(Path("include/headers")))

        project.add_target(lib)
        project.add_target(shared)
        project.add_target(prog)
        project.add_target(iface)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()
        # Static library: rectangle [name]
        assert "libmylib_a[" in output
        # Shared library: stadium ([name])
        assert "libmyshared_so([" in output
        # Program: stadium [[name]]
        assert "myapp[[" in output
        # Interface: hexagon {{name}}
        assert "headers{{" in output


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
    """Integration tests."""

    def test_complete_project(self, tmp_path):
        """Test with a complete multi-target project."""
        project = Project("complete", build_dir=tmp_path)

        # libmath: math.c -> math.o -> libmath.a
        libmath = Target("libmath", target_type="static_library")
        math_src = FileNode(Path("src/math.c"))
        math_obj = FileNode(Path("build/obj.libmath/math.o"))
        math_lib = FileNode(Path("build/libmath.a"))
        math_obj.depends([math_src])
        libmath.object_nodes.append(math_obj)
        libmath.output_nodes.append(math_lib)

        # app: main.c -> main.o -> app (links libmath)
        app = Target("app", target_type="program")
        app_src = FileNode(Path("src/main.c"))
        app_obj = FileNode(Path("build/obj.app/main.o"))
        app_exe = FileNode(Path("build/app"))
        app_obj.depends([app_src])
        app.object_nodes.append(app_obj)
        app.output_nodes.append(app_exe)
        app.link(libmath)

        project.add_target(libmath)
        project.add_target(app)

        gen = MermaidGenerator()
        gen.generate(project, tmp_path)

        output = (tmp_path / "deps.mmd").read_text()

        # Check all nodes present
        assert "math_c" in output
        assert "math_o" in output
        assert "libmath_a" in output
        assert "main_c" in output
        assert "main_o" in output
        assert "app[[app]]" in output

        # Check edges
        assert "math_c --> math_o" in output
        assert "math_o --> libmath_a" in output
        assert "main_c --> main_o" in output
        assert "main_o --> app" in output
        assert "libmath_a --> app" in output

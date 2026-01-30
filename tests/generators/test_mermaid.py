# SPDX-License-Identifier: MIT
"""Tests for MermaidGenerator."""

from pathlib import Path

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

        gen.generate(project)

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
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()
        assert "build_myapp" in output
        assert "src_main_c" in output
        assert "build_main_o" in output

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
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()
        assert "build_libmath_a" in output
        assert "build_libphysics_a" in output
        assert "build_app[[" in output
        # Check library dependency edges
        assert "build_libmath_a --> build_libphysics_a" in output
        assert "build_libphysics_a --> build_app" in output

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
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()
        # Static library: rectangle [name]
        assert "build_libmylib_a[" in output
        # Shared library: stadium ([name])
        assert "build_libmyshared_so([" in output
        # Program: stadium [[name]]
        assert "build_myapp[[" in output
        # Interface: hexagon {{name}}
        assert "include_headers{{" in output


class TestMermaidGeneratorDirection:
    """Tests for graph direction options."""

    def test_left_right(self, tmp_path):
        """Test LR direction."""
        project = Project("lr", build_dir=tmp_path)
        gen = MermaidGenerator(direction="LR")
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()
        assert "flowchart LR" in output

    def test_top_bottom(self, tmp_path):
        """Test TB direction."""
        project = Project("tb", build_dir=tmp_path)
        gen = MermaidGenerator(direction="TB")
        gen.generate(project)

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
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()

        # Check all nodes present (path-based IDs)
        assert "src_math_c" in output
        assert "build_obj_libmath_math_o" in output
        assert "build_libmath_a" in output
        assert "src_main_c" in output
        assert "build_obj_app_main_o" in output
        assert "build_app[[" in output

        # Check edges
        assert "src_math_c --> build_obj_libmath_math_o" in output
        assert "build_obj_libmath_math_o --> build_libmath_a" in output
        assert "src_main_c --> build_obj_app_main_o" in output
        assert "build_obj_app_main_o --> build_app" in output
        assert "build_libmath_a --> build_app" in output

    def test_directory_containment_edges(self, tmp_path):
        """Test that output nodes inside a directory get containment edges."""
        project = Project("bundle", build_dir=tmp_path)

        # Simulate Install targets placing files into a bundle directory
        install1 = Target("install_lib", target_type="command")
        lib_src = FileNode(Path("build/libfoo.dylib"))
        lib_installed = FileNode(Path("build/MyApp.app/lib/libfoo.dylib"))
        lib_installed.depends([lib_src])
        install1.output_nodes.append(lib_installed)

        install2 = Target("install_exe", target_type="command")
        exe_src = FileNode(Path("build/myapp"))
        exe_installed = FileNode(Path("build/MyApp.app/bin/myapp"))
        exe_installed.depends([exe_src])
        install2.output_nodes.append(exe_installed)

        # A target that depends on the bundle directory itself
        pkg = Target("package", target_type="command")
        bundle_dir = FileNode(Path("build/MyApp.app"))
        pkg_output = FileNode(Path("build/MyApp.pkg"))
        pkg_output.depends([bundle_dir])
        pkg.output_nodes.append(pkg_output)

        project.add_target(install1)
        project.add_target(install2)
        project.add_target(pkg)

        gen = MermaidGenerator()
        gen.generate(project)

        output = (tmp_path / "deps.mmd").read_text()

        # The bundle dir is a source dep of the pkg target
        assert "build_MyApp_app>" in output
        # Containment edges: installed files → bundle directory
        assert "build_MyApp_app_lib_libfoo_dylib --> build_MyApp_app" in output
        assert "build_MyApp_app_bin_myapp --> build_MyApp_app" in output

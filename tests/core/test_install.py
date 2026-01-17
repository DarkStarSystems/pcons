# SPDX-License-Identifier: MIT
"""Tests for Project.Install() method."""

from pathlib import Path

import pytest

from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target


class TestInstall:
    """Tests for Project.Install()."""

    def test_install_creates_target(self, tmp_path):
        """Install creates an interface target."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create a source file
        src_file = tmp_path / "mylib.a"
        src_file.touch()

        # Create an install target
        install = project.Install(
            tmp_path / "dist",
            [src_file],
        )

        assert install is not None
        assert isinstance(install, Target)
        assert install.target_type == "interface"
        assert install.name == "install_dist"

    def test_install_custom_name(self, tmp_path):
        """Install can have a custom name."""
        project = Project("test", root_dir=tmp_path)

        src_file = tmp_path / "mylib.a"
        src_file.touch()

        install = project.Install(
            tmp_path / "dist",
            [src_file],
            name="my_install",
        )

        assert install.name == "my_install"

    def test_install_creates_copy_nodes(self, tmp_path):
        """Install creates copy nodes for each source file after resolve."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create source files
        src1 = tmp_path / "file1.txt"
        src2 = tmp_path / "file2.txt"
        src1.touch()
        src2.touch()

        dest_dir = tmp_path / "install"
        install = project.Install(dest_dir, [src1, src2])

        # Nodes are created during resolve
        project.resolve()

        # Should have nodes for each installed file
        assert len(install.output_nodes) == 2

        # Each node should have build_info for copy
        for node in install.output_nodes:
            assert isinstance(node, FileNode)
            assert hasattr(node, "_build_info")
            assert node._build_info["tool"] == "copy"

    def test_install_node_paths(self, tmp_path):
        """Install creates correct destination paths after resolve."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "mylib.so"
        src_file.touch()

        dest_dir = tmp_path / "bundle" / "Contents" / "MacOS"
        install = project.Install(dest_dir, [src_file])

        # Nodes are created during resolve
        project.resolve()

        # Destination should be in dest_dir with same filename
        assert len(install.output_nodes) == 1
        node = install.output_nodes[0]
        assert node.path == dest_dir / "mylib.so"

    def test_install_from_target(self, tmp_path):
        """Install can install output files from a Target after resolve."""
        from pcons.toolchains import find_c_toolchain

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create a library target with output_nodes
        try:
            toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("No C toolchain available")

        env = project.Environment(toolchain=toolchain)
        lib = project.StaticLibrary("mylib", env)

        # Manually add an output node for testing (simulating what resolve would do)
        lib_node = FileNode(tmp_path / "build" / "libmylib.a")
        lib.output_nodes.append(lib_node)
        lib._resolved = True

        # Install the library
        install = project.Install(tmp_path / "dist" / "lib", [lib])

        # Resolve to create install nodes
        project.resolve()

        # Should have a node for the library
        assert len(install.output_nodes) == 1
        assert install.output_nodes[0].path == tmp_path / "dist" / "lib" / "libmylib.a"

    def test_install_target_registered(self, tmp_path):
        """Install target is registered with the project."""
        project = Project("test", root_dir=tmp_path)

        src_file = tmp_path / "file.txt"
        src_file.touch()

        install = project.Install(tmp_path / "dist", [src_file])

        # Target should be findable
        found = project.get_target(install.name)
        assert found is install

    def test_install_node_dependencies(self, tmp_path):
        """Install nodes depend on source files after resolve."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "mylib.a"
        src_file.touch()
        src_node = project.node(src_file)

        install = project.Install(tmp_path / "dist", [src_node])

        # Resolve to create install nodes
        project.resolve()

        # The destination node should depend on the source node
        dest_node = install.output_nodes[0]
        assert src_node in dest_node.explicit_deps


class TestInstallAs:
    """Tests for Project.InstallAs()."""

    def test_install_as_creates_target(self, tmp_path):
        """InstallAs creates an interface target."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "mylib.dylib"
        src_file.touch()

        install = project.InstallAs(
            tmp_path / "bundle" / "mylib.ofx",
            src_file,
        )

        assert install is not None
        assert isinstance(install, Target)
        assert install.target_type == "interface"

    def test_install_as_destination_path(self, tmp_path):
        """InstallAs uses the exact destination path after resolve."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "libplugin.dylib"
        src_file.touch()

        dest_path = tmp_path / "bundle" / "Contents" / "MacOS" / "plugin.ofx"
        install = project.InstallAs(dest_path, src_file)

        # Resolve to create install nodes
        project.resolve()

        assert len(install.output_nodes) == 1
        node = install.output_nodes[0]
        assert node.path == dest_path

    def test_install_as_from_target(self, tmp_path):
        """InstallAs can install from a Target after resolve."""
        from pcons.toolchains import find_c_toolchain

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        try:
            toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("No C toolchain available")

        env = project.Environment(toolchain=toolchain)
        lib = project.SharedLibrary("plugin", env)

        # Add an output node for testing (simulating what resolve would do)
        lib_node = FileNode(tmp_path / "build" / "libplugin.dylib")
        lib.output_nodes.append(lib_node)
        lib._resolved = True

        dest = tmp_path / "bundle" / "plugin.ofx"
        install = project.InstallAs(dest, lib)

        # Resolve to create install nodes
        project.resolve()

        assert len(install.output_nodes) == 1
        assert install.output_nodes[0].path == dest

    def test_install_as_dependency(self, tmp_path):
        """InstallAs destination depends on source after resolve."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "mylib.dylib"
        src_file.touch()
        src_node = project.node(src_file)

        dest = tmp_path / "bundle" / "mylib.ofx"
        install = project.InstallAs(dest, src_node)

        # Resolve to create install nodes
        project.resolve()

        dest_node = install.output_nodes[0]
        assert src_node in dest_node.explicit_deps

    def test_install_as_unique_names(self, tmp_path):
        """Multiple InstallAs with same dest name get unique target names."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src1 = tmp_path / "file1.txt"
        src2 = tmp_path / "file2.txt"
        src1.touch()
        src2.touch()

        # Both install to files named "icon.png" in different dirs
        install1 = project.InstallAs(tmp_path / "dir1" / "icon.png", src1)
        install2 = project.InstallAs(tmp_path / "dir2" / "icon.png", src2)

        # Should have unique names
        assert install1.name != install2.name
        assert install1.name.startswith("install_icon.png")
        assert install2.name.startswith("install_icon.png")


class TestInstallWithNinja:
    """Tests for Install with ninja generation."""

    def test_install_generates_copy_rule(self, tmp_path):
        """Install generates a copy rule in ninja after resolve."""
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        src_file = tmp_path / "mylib.a"
        src_file.touch()

        project.Install(tmp_path / "dist", [src_file])

        # Resolve to create install nodes
        project.resolve()

        # Generate ninja file
        gen = NinjaGenerator()
        gen.generate(project, tmp_path / "build")

        # Read the generated file
        ninja_file = tmp_path / "build" / "build.ninja"
        assert ninja_file.exists()

        content = ninja_file.read_text()

        # Should have a copy rule
        assert "rule copy_copycmd" in content
        # Should have INSTALL description
        assert "INSTALL" in content
        # Should have a build statement for the installed file
        assert "mylib.a" in content


class TestInstallOrderIndependence:
    """Tests that Install/InstallAs work regardless of declaration order."""

    def test_install_before_target_definition(self, tmp_path):
        """Install can reference a target before its output_nodes are populated."""
        from pcons.toolchains import find_c_toolchain

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        try:
            toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("No C toolchain available")

        env = project.Environment(toolchain=toolchain)

        # Create library target (empty sources, output_nodes will be empty)
        lib = project.StaticLibrary("mylib", env)

        # Install the library BEFORE resolve() - output_nodes are still empty
        install = project.Install(tmp_path / "dist" / "lib", [lib])

        # At this point, lib.output_nodes is empty
        assert len(lib.output_nodes) == 0

        # Manually add an output node (simulating what resolve would do)
        lib_node = FileNode(tmp_path / "build" / "libmylib.a")
        lib.output_nodes.append(lib_node)
        lib._resolved = True

        # Now resolve the project (including pending sources)
        project.resolve()

        # Install target should now have the installed file
        assert len(install.output_nodes) == 1
        assert install.output_nodes[0].path == tmp_path / "dist" / "lib" / "libmylib.a"

    def test_install_as_before_target_definition(self, tmp_path):
        """InstallAs can reference a target before its output_nodes are populated."""
        from pcons.toolchains import find_c_toolchain

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        try:
            toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("No C toolchain available")

        env = project.Environment(toolchain=toolchain)

        # Create shared library target
        lib = project.SharedLibrary("plugin", env)

        # InstallAs BEFORE resolve()
        dest = tmp_path / "bundle" / "plugin.ofx"
        install = project.InstallAs(dest, lib)

        # At this point, lib.output_nodes is empty
        assert len(lib.output_nodes) == 0

        # Manually add an output node
        lib_node = FileNode(tmp_path / "build" / "libplugin.dylib")
        lib.output_nodes.append(lib_node)
        lib._resolved = True

        # Resolve project
        project.resolve()

        # Should have the installed file with the custom name
        assert len(install.output_nodes) == 1
        assert install.output_nodes[0].path == dest

    def test_install_chain_order_independence(self, tmp_path):
        """Install targets can be chained in any order."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create a source file
        src_file = tmp_path / "mylib.a"
        src_file.touch()

        # Create intermediate and final install targets in "reverse" order
        # First declare the final install...
        final_dir = tmp_path / "final"
        intermediate_dir = tmp_path / "intermediate"

        # Install from intermediate to final (declared first)
        # Note: This references intermediate_install which doesn't exist yet
        final_install = project.Install(final_dir, [src_file], name="final_install")

        # Install from source to intermediate (declared second)
        intermediate_install = project.Install(
            intermediate_dir, [src_file], name="intermediate_install"
        )

        # Resolve
        project.resolve()

        # Both should work
        assert len(final_install.output_nodes) == 1
        assert len(intermediate_install.output_nodes) == 1
        assert final_install.output_nodes[0].path == final_dir / "mylib.a"
        assert intermediate_install.output_nodes[0].path == intermediate_dir / "mylib.a"

    def test_install_file_before_it_exists(self, tmp_path):
        """Install can reference a file path before the file exists."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Reference a file that doesn't exist yet (will be generated)
        generated_file = tmp_path / "build" / "generated.h"

        # Install it
        install = project.Install(tmp_path / "include", [generated_file])

        # Resolve (file still doesn't exist, but that's OK for generation)
        project.resolve()

        # Should have the install node
        assert len(install.output_nodes) == 1
        assert install.output_nodes[0].path == tmp_path / "include" / "generated.h"

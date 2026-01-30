# SPDX-License-Identifier: MIT
"""Tests for XcodeGenerator."""

from pathlib import Path

from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.xcode import XcodeGenerator


class TestXcodeGeneratorBasic:
    """Basic tests for XcodeGenerator."""

    def test_generator_creation(self):
        """Test generator can be created."""
        gen = XcodeGenerator()
        assert gen.name == "xcode"

    def test_generates_xcodeproj_bundle(self, tmp_path):
        """Test that generation creates .xcodeproj directory."""
        project = Project("myapp", build_dir=tmp_path)

        # Add a minimal target
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        xcodeproj_path = tmp_path / "myapp.xcodeproj"
        assert xcodeproj_path.exists()
        assert xcodeproj_path.is_dir()

    def test_creates_project_pbxproj(self, tmp_path):
        """Test that project.pbxproj file is created."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        pbxproj_path = tmp_path / "myapp.xcodeproj" / "project.pbxproj"
        assert pbxproj_path.exists()
        assert pbxproj_path.is_file()

        # Check it has valid content
        content = pbxproj_path.read_text()
        assert "// !$*UTF8*$!" in content
        assert "PBXProject" in content


class TestXcodeGeneratorTargets:
    """Tests for target handling."""

    def test_program_target(self, tmp_path):
        """Test program target has correct product type."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.tool" in content
        assert "PBXNativeTarget" in content

    def test_static_library_target(self, tmp_path):
        """Test static library target has correct product type."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("mylib", target_type="static_library")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "mylib.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.library.static" in content
        assert "libmylib.a" in content

    def test_shared_library_target(self, tmp_path):
        """Test shared library target has correct product type."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("mylib", target_type="shared_library")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "mylib.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.library.dynamic" in content
        assert "libmylib.dylib" in content

    def test_interface_target_skipped(self, tmp_path):
        """Test interface-only projects don't create xcodeproj."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("headers", target_type="interface")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        # Interface-only projects don't create .xcodeproj
        xcodeproj_path = tmp_path / "mylib.xcodeproj"
        assert (
            not xcodeproj_path.exists()
            or not (xcodeproj_path / "project.pbxproj").exists()
        )


class TestXcodeGeneratorBuildSettings:
    """Tests for build settings mapping."""

    def test_include_dirs_mapped(self, tmp_path):
        """Test include directories are mapped to HEADER_SEARCH_PATHS."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        target.public.include_dirs.append(Path("include"))
        target.private.include_dirs.append(Path("src"))
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "HEADER_SEARCH_PATHS" in content

    def test_defines_mapped(self, tmp_path):
        """Test defines are mapped to GCC_PREPROCESSOR_DEFINITIONS."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        target.public.defines.append("DEBUG=1")
        target.private.defines.append("INTERNAL")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "GCC_PREPROCESSOR_DEFINITIONS" in content

    def test_compile_flags_mapped(self, tmp_path):
        """Test compile flags are mapped to OTHER_CFLAGS."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        target.private.compile_flags.extend(["-Wall", "-Wextra"])
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "OTHER_CFLAGS" in content


class TestXcodeGeneratorDependencies:
    """Tests for target dependencies."""

    def test_target_dependencies(self, tmp_path):
        """Test dependencies are linked correctly."""
        project = Project("myapp", build_dir=tmp_path)

        # Create library
        lib = Target("mylib", target_type="static_library")
        project.add_target(lib)

        # Create app that depends on lib
        app = Target("myapp", target_type="program")
        app.link(lib)
        project.add_target(app)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        # Should have both targets
        assert "mylib" in content
        assert "myapp" in content
        # Should have dependency objects
        assert "PBXTargetDependency" in content


class TestXcodeGeneratorBuildPhases:
    """Tests for build phases."""

    def test_has_sources_phase(self, tmp_path):
        """Test targets have PBXSourcesBuildPhase."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "PBXSourcesBuildPhase" in content

    def test_has_frameworks_phase(self, tmp_path):
        """Test targets have PBXFrameworksBuildPhase."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "PBXFrameworksBuildPhase" in content


class TestXcodeGeneratorConfigurations:
    """Tests for build configurations."""

    def test_has_debug_configuration(self, tmp_path):
        """Test project has Debug configuration."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Debug" in content

    def test_has_release_configuration(self, tmp_path):
        """Test project has Release configuration."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Release" in content


class TestXcodeGeneratorGroups:
    """Tests for file group organization."""

    def test_has_products_group(self, tmp_path):
        """Test project has Products group."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Products" in content

    def test_has_sources_group(self, tmp_path):
        """Test project has Sources group."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Sources" in content


class TestXcodeGeneratorMultiTarget:
    """Tests for multi-target projects."""

    def test_multiple_targets(self, tmp_path):
        """Test project with multiple targets."""
        project = Project("multi", build_dir=tmp_path)

        # Add multiple targets
        lib1 = Target("libmath", target_type="static_library")
        lib2 = Target("libphysics", target_type="static_library")
        app = Target("app", target_type="program")

        lib2.link(lib1)
        app.link(lib2)

        project.add_target(lib1)
        project.add_target(lib2)
        project.add_target(app)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "multi.xcodeproj" / "project.pbxproj").read_text()

        # All targets should be present
        assert "libmath" in content
        assert "libphysics" in content
        assert "app" in content

        # Should have proper product types
        assert content.count("com.apple.product-type.library.static") == 2
        assert content.count("com.apple.product-type.tool") == 1


class TestXcodeGeneratorInstall:
    """Tests for Install target support."""

    def test_install_target_creates_aggregate(self, tmp_path):
        """Test Install target creates PBXAggregateTarget."""
        from pcons.core.node import FileNode

        project = Project("install_test", build_dir=tmp_path)

        # Create an interface target mimicking Install
        install_target = Target("install_bin", target_type="interface")
        install_target._builder_name = "Install"
        install_target._builder_data = {"dest_dir": "bin"}

        # Create a source file node
        source = FileNode(tmp_path / "myfile.txt")

        # Create output node with build_info
        dest = FileNode(tmp_path / "bin" / "myfile.txt")
        dest.depends([source])
        dest._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [source],
            "description": "INSTALL $out",
        }

        install_target.output_nodes.append(dest)
        install_target._install_nodes = [dest]

        project.add_target(install_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "install_test.xcodeproj" / "project.pbxproj").read_text()

        # Should have aggregate target
        assert "PBXAggregateTarget" in content
        assert "install_bin" in content

        # Should have shell script build phase
        assert "PBXShellScriptBuildPhase" in content
        assert "cp" in content  # Copy command in script

    def test_install_target_has_script_phase(self, tmp_path):
        """Test Install target has proper shell script phase."""
        from pcons.core.node import FileNode

        project = Project("install_script", build_dir=tmp_path)

        # Create a source file
        source = FileNode(Path("src/app"))

        # Create install target with output node
        install_target = Target("install_app", target_type="interface")
        install_target._builder_name = "Install"
        install_target._builder_data = {"dest_dir": "bin"}

        dest = FileNode(tmp_path / "bin" / "app")
        dest.depends([source])
        dest._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [source],
        }

        install_target.output_nodes.append(dest)
        install_target._install_nodes = [dest]

        project.add_target(install_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (
            tmp_path / "install_script.xcodeproj" / "project.pbxproj"
        ).read_text()

        # Should have shell script with mkdir and cp
        assert "mkdir -p" in content
        assert "cp" in content


class TestXcodeGeneratorInstallDir:
    """Tests for InstallDir target support."""

    def test_install_dir_creates_aggregate(self, tmp_path):
        """Test InstallDir target creates PBXAggregateTarget with cp -R."""
        from pcons.core.node import FileNode

        project = Project("install_dir_test", build_dir=tmp_path)

        # Create InstallDir target
        install_dir_target = Target("install_assets", target_type="interface")
        install_dir_target._builder_name = "InstallDir"
        install_dir_target._builder_data = {"dest_dir": "dist"}
        install_dir_target._project = project

        # Create source directory node
        source = FileNode(Path("assets"))

        # Create stamp file as output
        stamp = FileNode(tmp_path / ".stamps" / "assets.stamp")
        stamp.depends([source])
        stamp._build_info = {
            "tool": "install",
            "command_var": "copytreecmd",
            "sources": [source],
        }

        install_dir_target.output_nodes.append(stamp)
        install_dir_target._pending_sources = [source]

        project.add_target(install_dir_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (
            tmp_path / "install_dir_test.xcodeproj" / "project.pbxproj"
        ).read_text()

        # Should have aggregate target
        assert "PBXAggregateTarget" in content
        assert "install_assets" in content

        # Should have cp -R in script
        assert "cp -R" in content


class TestXcodeGeneratorArchive:
    """Tests for Archive (Tarfile/Zipfile) target support."""

    def test_tarfile_creates_aggregate(self, tmp_path):
        """Test Tarfile target creates PBXAggregateTarget with tar command."""
        from pcons.core.node import FileNode

        project = Project("archive_test", build_dir=tmp_path)

        # Create Tarfile target
        tar_target = Target("my_archive", target_type="archive")
        tar_target._builder_name = "Tarfile"
        tar_target._builder_data = {
            "tool": "tarfile",
            "output": str(tmp_path / "archive.tar.gz"),
            "compression": "gzip",
            "base_dir": ".",
        }
        tar_target._project = project

        # Create source file nodes
        source1 = FileNode(Path("src/file1.c"))
        source2 = FileNode(Path("src/file2.c"))

        # Create archive output node
        archive = FileNode(tmp_path / "archive.tar.gz")
        archive.depends([source1, source2])
        archive._build_info = {
            "tool": "archive",
            "command_var": "tarcmd",
            "sources": [source1, source2],
        }

        tar_target.output_nodes.append(archive)
        tar_target.nodes.append(archive)

        project.add_target(tar_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "archive_test.xcodeproj" / "project.pbxproj").read_text()

        # Should have aggregate target
        assert "PBXAggregateTarget" in content
        assert "my_archive" in content

        # Should have tar command in script
        assert "tar" in content
        assert "-czf" in content  # gzip compression flag

    def test_zipfile_creates_aggregate(self, tmp_path):
        """Test Zipfile target creates PBXAggregateTarget with zip command."""
        from pcons.core.node import FileNode

        project = Project("zip_test", build_dir=tmp_path)

        # Create Zipfile target
        zip_target = Target("my_zip", target_type="archive")
        zip_target._builder_name = "Zipfile"
        zip_target._builder_data = {
            "tool": "zipfile",
            "output": str(tmp_path / "archive.zip"),
            "base_dir": ".",
        }
        zip_target._project = project

        # Create source file node
        source = FileNode(Path("docs/readme.txt"))

        # Create zip output node
        archive = FileNode(tmp_path / "archive.zip")
        archive.depends([source])
        archive._build_info = {
            "tool": "archive",
            "command_var": "zipcmd",
            "sources": [source],
        }

        zip_target.output_nodes.append(archive)
        zip_target.nodes.append(archive)

        project.add_target(zip_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "zip_test.xcodeproj" / "project.pbxproj").read_text()

        # Should have aggregate target
        assert "PBXAggregateTarget" in content
        assert "my_zip" in content

        # Should have zip command in script
        assert "zip -r" in content


class TestXcodeGeneratorInstallDependencies:
    """Tests for dependencies between Install/Archive and compiled targets."""

    def test_install_depends_on_compiled_target(self, tmp_path):
        """Test Install target depending on a compiled program."""
        from pcons.core.node import FileNode

        project = Project("dep_test", build_dir=tmp_path)

        # Create a program target
        prog = Target("myapp", target_type="program")
        prog._project = project

        # Add output to program target
        prog_output = FileNode(tmp_path / "myapp")
        prog.output_nodes.append(prog_output)

        project.add_target(prog)

        # Create install target that installs the program
        install_target = Target("install_myapp", target_type="interface")
        install_target._builder_name = "Install"
        install_target._builder_data = {"dest_dir": "bin"}
        install_target._project = project

        # Create output node referencing program output
        dest = FileNode(tmp_path / "bin" / "myapp")
        dest.depends([prog_output])
        dest._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [prog_output],
        }

        install_target.output_nodes.append(dest)
        install_target._install_nodes = [dest]

        project.add_target(install_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (tmp_path / "dep_test.xcodeproj" / "project.pbxproj").read_text()

        # Both targets should be present
        assert "myapp" in content
        assert "install_myapp" in content

        # Should have target dependency
        assert "PBXTargetDependency" in content

    def test_archive_depends_on_compiled_target(self, tmp_path):
        """Test Archive target depending on a compiled program."""
        from pcons.core.node import FileNode

        project = Project("archive_dep_test", build_dir=tmp_path)

        # Create a program target
        prog = Target("myapp", target_type="program")
        prog._project = project

        prog_output = FileNode(tmp_path / "myapp")
        prog.output_nodes.append(prog_output)

        project.add_target(prog)

        # Create tarfile target that archives the program
        tar_target = Target("bin_archive", target_type="archive")
        tar_target._builder_name = "Tarfile"
        tar_target._builder_data = {
            "tool": "tarfile",
            "output": str(tmp_path / "myapp.tar.gz"),
            "compression": "gzip",
            "base_dir": ".",
        }
        tar_target._project = project

        # Create archive node referencing program output
        archive = FileNode(tmp_path / "myapp.tar.gz")
        archive.depends([prog_output])
        archive._build_info = {
            "tool": "archive",
            "command_var": "tarcmd",
            "sources": [prog_output],
        }

        tar_target.output_nodes.append(archive)
        tar_target.nodes.append(archive)

        project.add_target(tar_target)

        gen = XcodeGenerator()
        gen.generate(project)

        content = (
            tmp_path / "archive_dep_test.xcodeproj" / "project.pbxproj"
        ).read_text()

        # Both targets should be present
        assert "myapp" in content
        assert "bin_archive" in content

        # Should have target dependency
        assert "PBXTargetDependency" in content

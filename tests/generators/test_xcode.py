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
        gen.generate(project, tmp_path)

        xcodeproj_path = tmp_path / "myapp.xcodeproj"
        assert xcodeproj_path.exists()
        assert xcodeproj_path.is_dir()

    def test_creates_project_pbxproj(self, tmp_path):
        """Test that project.pbxproj file is created."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.tool" in content
        assert "PBXNativeTarget" in content

    def test_static_library_target(self, tmp_path):
        """Test static library target has correct product type."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("mylib", target_type="static_library")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "mylib.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.library.static" in content
        assert "libmylib.a" in content

    def test_shared_library_target(self, tmp_path):
        """Test shared library target has correct product type."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("mylib", target_type="shared_library")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "mylib.xcodeproj" / "project.pbxproj").read_text()
        assert "com.apple.product-type.library.dynamic" in content
        assert "libmylib.dylib" in content

    def test_interface_target_skipped(self, tmp_path):
        """Test interface-only projects don't create xcodeproj."""
        project = Project("mylib", build_dir=tmp_path)
        target = Target("headers", target_type="interface")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "GCC_PREPROCESSOR_DEFINITIONS" in content

    def test_compile_flags_mapped(self, tmp_path):
        """Test compile flags are mapped to OTHER_CFLAGS."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        target.private.compile_flags.extend(["-Wall", "-Wextra"])
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "PBXSourcesBuildPhase" in content

    def test_has_frameworks_phase(self, tmp_path):
        """Test targets have PBXFrameworksBuildPhase."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Debug" in content

    def test_has_release_configuration(self, tmp_path):
        """Test project has Release configuration."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "myapp.xcodeproj" / "project.pbxproj").read_text()
        assert "name = Products" in content

    def test_has_sources_group(self, tmp_path):
        """Test project has Sources group."""
        project = Project("myapp", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        project.add_target(target)

        gen = XcodeGenerator()
        gen.generate(project, tmp_path)

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
        gen.generate(project, tmp_path)

        content = (tmp_path / "multi.xcodeproj" / "project.pbxproj").read_text()

        # All targets should be present
        assert "libmath" in content
        assert "libphysics" in content
        assert "app" in content

        # Should have proper product types
        assert content.count("com.apple.product-type.library.static") == 2
        assert content.count("com.apple.product-type.tool") == 1

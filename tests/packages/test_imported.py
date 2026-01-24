# SPDX-License-Identifier: MIT
"""Tests for ImportedTarget."""

from __future__ import annotations

from pathlib import Path

from pcons.packages.description import ComponentDescription, PackageDescription
from pcons.packages.imported import ImportedTarget


class TestImportedTarget:
    """Tests for ImportedTarget."""

    def test_create_imported_target(self) -> None:
        """Test creating an imported target directly."""
        pkg = PackageDescription(
            name="zlib",
            version="1.2.13",
            include_dirs=["/usr/include"],
            libraries=["z"],
        )

        target = ImportedTarget(name="zlib", package=pkg)
        assert target.name == "zlib"
        assert target.is_imported is True
        assert target.package is pkg

    def test_from_package(self) -> None:
        """Test creating from a PackageDescription."""
        pkg = PackageDescription(
            name="openssl",
            version="3.0",
            include_dirs=["/usr/include"],
            library_dirs=["/usr/lib"],
            libraries=["ssl", "crypto"],
        )

        target = ImportedTarget.from_package(pkg)
        assert target.name == "openssl"
        assert target.version == "3.0"
        assert target.libraries == ["ssl", "crypto"]

    def test_compile_flags(self) -> None:
        """Test getting compile flags."""
        pkg = PackageDescription(
            name="test",
            include_dirs=["/opt/test/include"],
            defines=["TEST_LIB"],
        )

        target = ImportedTarget.from_package(pkg)
        flags = target.compile_flags
        assert "-I/opt/test/include" in flags
        assert "-DTEST_LIB" in flags

    def test_link_flags(self) -> None:
        """Test getting link flags."""
        pkg = PackageDescription(
            name="test",
            library_dirs=["/opt/test/lib"],
            libraries=["testlib"],
            link_flags=["-Wl,-rpath,/opt/test/lib"],
        )

        target = ImportedTarget.from_package(pkg)
        flags = target.link_flags
        assert "-L/opt/test/lib" in flags
        assert "-ltestlib" in flags
        assert "-Wl,-rpath,/opt/test/lib" in flags

    def test_include_dirs_as_paths(self) -> None:
        """Test getting include dirs as Path objects."""
        pkg = PackageDescription(
            name="test",
            include_dirs=["/usr/include", "/opt/include"],
        )

        target = ImportedTarget.from_package(pkg)
        dirs = target.include_dirs
        assert len(dirs) == 2
        assert all(isinstance(d, Path) for d in dirs)
        assert Path("/usr/include") in dirs

    def test_with_components(self) -> None:
        """Test creating with specific components."""
        pkg = PackageDescription(
            name="boost",
            include_dirs=["/usr/include/boost"],
            libraries=["boost_system"],
            components={
                "filesystem": ComponentDescription(
                    name="filesystem",
                    libraries=["boost_filesystem"],
                ),
            },
        )

        target = ImportedTarget.from_package(pkg, components=["filesystem"])
        assert target.requested_components == ["filesystem"]
        assert "boost_system" in target.libraries
        assert "boost_filesystem" in target.libraries

    def test_no_package(self) -> None:
        """Test target with no package returns empty values."""
        target = ImportedTarget(name="empty")
        assert target.compile_flags == []
        assert target.link_flags == []
        assert target.libraries == []
        assert target.version == ""

    def test_repr(self) -> None:
        """Test string representation."""
        pkg = PackageDescription(name="test", version="1.0")
        target = ImportedTarget.from_package(pkg)
        repr_str = repr(target)
        assert "test" in repr_str
        assert "1.0" in repr_str

    def test_public_requirements_populated(self) -> None:
        """Test that public requirements are populated from package."""
        pkg = PackageDescription(
            name="test",
            include_dirs=["/opt/test/include"],
            library_dirs=["/opt/test/lib"],
            libraries=["testlib"],
            defines=["TEST_DEFINE"],
            compile_flags=["-DEXTRA_FLAG"],
            link_flags=["-Wl,-rpath,/opt/test/lib"],
        )

        target = ImportedTarget.from_package(pkg)

        # Verify public requirements are populated
        assert Path("/opt/test/include") in target.public.include_dirs
        assert "TEST_DEFINE" in target.public.defines
        assert "-DEXTRA_FLAG" in target.public.compile_flags
        assert "testlib" in target.public.link_libs
        assert "-L/opt/test/lib" in target.public.link_flags
        assert "-Wl,-rpath,/opt/test/lib" in target.public.link_flags

    def test_public_requirements_frameworks_macos(self) -> None:
        """Test that macOS framework flags are populated in public requirements."""
        pkg = PackageDescription(
            name="CoreFoundation",
            framework_dirs=["/System/Library/Frameworks"],
            frameworks=["CoreFoundation", "Security"],
        )

        target = ImportedTarget.from_package(pkg)

        # Verify framework flags are in public.link_flags
        assert "-F" in target.public.link_flags
        assert "/System/Library/Frameworks" in target.public.link_flags
        assert "-framework" in target.public.link_flags
        assert "CoreFoundation" in target.public.link_flags
        assert "Security" in target.public.link_flags

    def test_public_requirements_with_components(self) -> None:
        """Test public requirements include merged component data."""
        pkg = PackageDescription(
            name="boost",
            include_dirs=["/usr/include/boost"],
            libraries=["boost_system"],
            components={
                "filesystem": ComponentDescription(
                    name="filesystem",
                    libraries=["boost_filesystem"],
                    defines=["BOOST_FILESYSTEM"],
                ),
            },
        )

        target = ImportedTarget.from_package(pkg, components=["filesystem"])

        # Verify both base and component requirements are in public
        assert Path("/usr/include/boost") in target.public.include_dirs
        assert "boost_system" in target.public.link_libs
        assert "boost_filesystem" in target.public.link_libs
        assert "BOOST_FILESYSTEM" in target.public.defines

    def test_empty_package_no_public_requirements(self) -> None:
        """Test that empty package results in empty public requirements."""
        pkg = PackageDescription(name="empty")
        target = ImportedTarget.from_package(pkg)

        assert target.public.include_dirs == []
        assert target.public.defines == []
        assert target.public.compile_flags == []
        assert target.public.link_libs == []
        assert target.public.link_flags == []

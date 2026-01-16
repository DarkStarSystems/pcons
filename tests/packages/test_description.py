# SPDX-License-Identifier: MIT
"""Tests for PackageDescription and ComponentDescription."""

from __future__ import annotations

from pathlib import Path

from pcons.packages.description import ComponentDescription, PackageDescription


class TestComponentDescription:
    """Tests for ComponentDescription."""

    def test_create_component(self) -> None:
        """Test creating a component."""
        comp = ComponentDescription(
            name="widgets",
            include_dirs=["/usr/include/qt/widgets"],
            libraries=["Qt5Widgets"],
        )
        assert comp.name == "widgets"
        assert comp.include_dirs == ["/usr/include/qt/widgets"]
        assert comp.libraries == ["Qt5Widgets"]
        assert comp.defines == []

    def test_to_dict(self) -> None:
        """Test converting component to dict."""
        comp = ComponentDescription(
            name="core",
            libraries=["mylib"],
            defines=["USE_CORE"],
        )
        d = comp.to_dict()
        assert d == {
            "libraries": ["mylib"],
            "defines": ["USE_CORE"],
        }

    def test_from_dict(self) -> None:
        """Test creating component from dict."""
        data = {
            "libraries": ["foo", "bar"],
            "include_dirs": ["/opt/include"],
        }
        comp = ComponentDescription.from_dict("mycomp", data)
        assert comp.name == "mycomp"
        assert comp.libraries == ["foo", "bar"]
        assert comp.include_dirs == ["/opt/include"]


class TestPackageDescription:
    """Tests for PackageDescription."""

    def test_create_package(self) -> None:
        """Test creating a package description."""
        pkg = PackageDescription(
            name="zlib",
            version="1.2.13",
            include_dirs=["/usr/include"],
            libraries=["z"],
        )
        assert pkg.name == "zlib"
        assert pkg.version == "1.2.13"
        assert pkg.include_dirs == ["/usr/include"]
        assert pkg.libraries == ["z"]

    def test_get_compile_flags(self) -> None:
        """Test generating compile flags."""
        pkg = PackageDescription(
            name="mylib",
            include_dirs=["/opt/include", "/usr/local/include"],
            defines=["MYLIB_STATIC", "DEBUG=1"],
            compile_flags=["-std=c++17"],
        )
        flags = pkg.get_compile_flags()
        assert "-I/opt/include" in flags
        assert "-I/usr/local/include" in flags
        assert "-DMYLIB_STATIC" in flags
        assert "-DDEBUG=1" in flags
        assert "-std=c++17" in flags

    def test_get_link_flags(self) -> None:
        """Test generating link flags."""
        pkg = PackageDescription(
            name="mylib",
            library_dirs=["/opt/lib"],
            libraries=["foo", "bar"],
            link_flags=["-pthread"],
        )
        flags = pkg.get_link_flags()
        assert "-L/opt/lib" in flags
        assert "-lfoo" in flags
        assert "-lbar" in flags
        assert "-pthread" in flags

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        pkg = PackageDescription(
            name="test",
            version="1.0",
            include_dirs=["/include"],
            libraries=["test"],
        )
        d = pkg.to_dict()
        assert d["package"]["name"] == "test"
        assert d["package"]["version"] == "1.0"
        assert d["paths"]["include_dirs"] == ["/include"]
        assert d["link"]["libraries"] == ["test"]

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        data = {
            "package": {
                "name": "mypackage",
                "version": "2.0",
            },
            "paths": {
                "include_dirs": ["/opt/mypackage/include"],
            },
            "link": {
                "libraries": ["mypkg"],
            },
        }
        pkg = PackageDescription.from_dict(data)
        assert pkg.name == "mypackage"
        assert pkg.version == "2.0"
        assert pkg.include_dirs == ["/opt/mypackage/include"]
        assert pkg.libraries == ["mypkg"]

    def test_toml_roundtrip(self, tmp_path: Path) -> None:
        """Test saving and loading from TOML."""
        pkg = PackageDescription(
            name="roundtrip",
            version="1.2.3",
            include_dirs=["/usr/include"],
            library_dirs=["/usr/lib"],
            libraries=["rt", "m"],
            defines=["FEATURE_X"],
            prefix="/usr",
            found_by="test",
        )

        toml_path = tmp_path / "test.pcons-pkg.toml"
        pkg.to_toml(toml_path)

        loaded = PackageDescription.from_toml(toml_path)
        assert loaded.name == pkg.name
        assert loaded.version == pkg.version
        assert loaded.include_dirs == pkg.include_dirs
        assert loaded.library_dirs == pkg.library_dirs
        assert loaded.libraries == pkg.libraries
        assert loaded.defines == pkg.defines
        assert loaded.prefix == pkg.prefix
        assert loaded.found_by == pkg.found_by

    def test_components(self) -> None:
        """Test package components."""
        pkg = PackageDescription(
            name="qt",
            version="5.15",
            include_dirs=["/usr/include/qt5"],
            libraries=["Qt5Core"],
            components={
                "widgets": ComponentDescription(
                    name="widgets",
                    libraries=["Qt5Widgets"],
                ),
                "gui": ComponentDescription(
                    name="gui",
                    libraries=["Qt5Gui"],
                ),
            },
        )

        widgets_comp = pkg.get_component("widgets")
        assert widgets_comp is not None
        assert widgets_comp.libraries == ["Qt5Widgets"]
        assert pkg.get_component("nonexistent") is None

    def test_merge_component(self) -> None:
        """Test merging a component with base package."""
        pkg = PackageDescription(
            name="qt",
            include_dirs=["/usr/include/qt5"],
            libraries=["Qt5Core"],
            components={
                "widgets": ComponentDescription(
                    name="widgets",
                    libraries=["Qt5Widgets"],
                    defines=["QT_WIDGETS_LIB"],
                ),
            },
        )

        widgets_comp = pkg.get_component("widgets")
        assert widgets_comp is not None

        merged = pkg.merge_component(widgets_comp)
        assert merged.name == "qt"
        assert merged.include_dirs == ["/usr/include/qt5"]
        assert merged.libraries == ["Qt5Core", "Qt5Widgets"]
        assert merged.defines == ["QT_WIDGETS_LIB"]

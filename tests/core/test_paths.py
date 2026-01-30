# SPDX-License-Identifier: MIT
"""Tests for pcons.core.paths - PathResolver."""

from pathlib import Path

import pytest

from pcons.core.paths import PathResolver


class TestPathResolverCreation:
    def test_basic_creation(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = Path("build")

        resolver = PathResolver(project_root, build_dir)

        assert resolver.project_root == project_root
        assert resolver.build_dir == build_dir

    def test_absolute_build_dir(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = tmp_path / "project" / "out"
        build_dir.mkdir(parents=True)

        resolver = PathResolver(project_root, build_dir)

        assert resolver.build_dir == build_dir
        assert resolver._resolved_build_dir == build_dir


class TestNormalizeTargetPath:
    def test_normalize_target_relative(self, tmp_path: Path) -> None:
        """Relative paths should work unchanged."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Normal relative path - just use it
        result = resolver.normalize_target_path("dist/foo.tar.gz")
        assert result == Path("dist/foo.tar.gz")

    def test_normalize_target_path_string(self, tmp_path: Path) -> None:
        """String and Path arguments should behave identically."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        str_result = resolver.normalize_target_path("dist/foo.tar.gz")
        path_result = resolver.normalize_target_path(Path("dist/foo.tar.gz"))
        assert str_result == path_result

    def test_normalize_target_absolute(self, tmp_path: Path) -> None:
        """Absolute paths outside build_dir should pass through."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Absolute path outside build_dir - pass through unchanged
        external_path = Path("/some/external/path/file.tar.gz")
        result = resolver.normalize_target_path(external_path)
        assert result == external_path

    def test_normalize_target_absolute_under_build_dir(self, tmp_path: Path) -> None:
        """Absolute paths under build_dir should be made relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Absolute path under build_dir - make relative
        abs_path = build_dir / "foo.tar.gz"
        result = resolver.normalize_target_path(abs_path)
        assert result == Path("foo.tar.gz")

    def test_normalize_target_absolute_nested(self, tmp_path: Path) -> None:
        """Nested absolute paths under build_dir should be made relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Nested absolute path under build_dir
        abs_path = build_dir / "dist" / "foo.tar.gz"
        result = resolver.normalize_target_path(abs_path)
        assert result == Path("dist/foo.tar.gz")

    def test_normalize_target_warns_on_build_prefix(self, tmp_path: Path) -> None:
        """Relative paths starting with build_dir name should warn but keep path."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Relative path starting with "build/" - warn but keep
        with pytest.warns(UserWarning, match="starts with build directory name"):
            result = resolver.normalize_target_path("build/foo.tar.gz")

        # Path is kept as-is (NOT stripped)
        assert result == Path("build/foo.tar.gz")

    def test_normalize_target_no_warn_different_prefix(self, tmp_path: Path) -> None:
        """Relative paths not starting with build_dir name should not warn."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # No warning for paths not starting with build dir name
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = resolver.normalize_target_path("dist/foo.tar.gz")
        assert result == Path("dist/foo.tar.gz")

    def test_path_and_string_equivalent(self, tmp_path: Path) -> None:
        """Path("foo") and "foo" should produce identical results."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Test various path formats
        assert resolver.normalize_target_path(
            Path("foo")
        ) == resolver.normalize_target_path("foo")
        assert resolver.normalize_target_path(
            Path("a/b/c")
        ) == resolver.normalize_target_path("a/b/c")
        assert resolver.normalize_target_path(
            Path("file.tar.gz")
        ) == resolver.normalize_target_path("file.tar.gz")

    def test_windows_backslashes(self, tmp_path: Path) -> None:
        """Backslashes should be normalized to forward slashes."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # Windows-style path with backslashes
        result = resolver.normalize_target_path("dist\\subdir\\foo.tar.gz")
        # Path parts should be correct regardless of platform string representation
        # (Windows Path objects use backslashes when stringified, which is fine)
        assert result.parts == ("dist", "subdir", "foo.tar.gz")


class TestNormalizeSourcePath:
    def test_normalize_source_relative(self, tmp_path: Path) -> None:
        """Relative source paths should work unchanged."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        result = resolver.normalize_source_path("src/main.c")
        assert result == Path("src/main.c")

    def test_normalize_source_absolute_under_root(self, tmp_path: Path) -> None:
        """Absolute source paths under project root should be made relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        abs_path = project_root / "src" / "main.c"
        result = resolver.normalize_source_path(abs_path)
        assert result == Path("src/main.c")

    def test_normalize_source_absolute_external(self, tmp_path: Path) -> None:
        """Absolute source paths outside project should pass through."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        external_path = Path("/some/external/include/header.h")
        result = resolver.normalize_source_path(external_path)
        assert result == external_path

    def test_normalize_source_backslashes(self, tmp_path: Path) -> None:
        """Backslashes in source paths should be normalized."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        result = resolver.normalize_source_path("src\\subdir\\main.c")
        assert result.parts == ("src", "subdir", "main.c")


class TestMakeBuildRelative:
    def test_absolute_under_build(self, tmp_path: Path) -> None:
        """Absolute paths under build_dir should be made relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        abs_path = build_dir / "output" / "file.o"
        result = resolver.make_build_relative(abs_path)
        assert result == Path("output/file.o")

    def test_already_relative(self, tmp_path: Path) -> None:
        """Already relative paths should pass through."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        rel_path = Path("output/file.o")
        result = resolver.make_build_relative(rel_path)
        assert result == rel_path

    def test_absolute_outside_build(self, tmp_path: Path) -> None:
        """Absolute paths outside build_dir should pass through."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        external_path = Path("/external/path")
        result = resolver.make_build_relative(external_path)
        assert result == external_path


class TestMakeProjectRelative:
    def test_absolute_under_project(self, tmp_path: Path) -> None:
        """Absolute paths under project root should be made relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        abs_path = project_root / "src" / "main.c"
        result = resolver.make_project_relative(abs_path)
        assert result == "src/main.c"

    def test_returns_forward_slashes(self, tmp_path: Path) -> None:
        """Result should use forward slashes."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        abs_path = project_root / "src" / "subdir" / "main.c"
        result = resolver.make_project_relative(abs_path)
        assert "\\" not in result
        assert "/" in result

    def test_already_relative(self, tmp_path: Path) -> None:
        """Already relative paths should pass through as strings."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        rel_path = Path("src/main.c")
        result = resolver.make_project_relative(rel_path)
        assert result == "src/main.c"


class TestCanonicalize:
    def test_canonicalize_relative_unchanged(self, tmp_path: Path) -> None:
        """Relative paths pass through (with normpath normalization)."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        result = resolver.canonicalize("src/main.c")
        assert result == Path("src/main.c")

    def test_canonicalize_absolute_under_project_becomes_relative(
        self, tmp_path: Path
    ) -> None:
        """Absolute paths under project root become relative."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        abs_path = project_root / "src" / "main.c"
        result = resolver.canonicalize(abs_path)
        assert result == Path("src/main.c")

    def test_canonicalize_absolute_external_stays_absolute(
        self, tmp_path: Path
    ) -> None:
        """External absolute paths stay absolute."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        # Use tmp_path-based path so it's absolute on all platforms
        external = tmp_path / "external_lib" / "libfoo.dylib"
        result = resolver.canonicalize(external)
        assert result == external
        assert result.is_absolute()

    def test_canonicalize_dot_segments_normalized(self, tmp_path: Path) -> None:
        """Dot segments (./foo, foo/../bar) are normalized."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        result = resolver.canonicalize("src/../src/main.c")
        assert result == Path("src/main.c")

        result2 = resolver.canonicalize("./src/main.c")
        assert result2 == Path("src/main.c")

    def test_canonicalize_backslashes_normalized(self, tmp_path: Path) -> None:
        """Backslashes are converted to forward slashes."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        result = resolver.canonicalize("src\\subdir\\main.c")
        assert result.parts == ("src", "subdir", "main.c")

    def test_canonicalize_idempotent(self, tmp_path: Path) -> None:
        """Canonicalizing an already-canonical path returns the same result."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolver = PathResolver(project_root, Path("build"))

        path = "build/obj/hello.o"
        first = resolver.canonicalize(path)
        second = resolver.canonicalize(first)
        assert first == second

# SPDX-License-Identifier: MIT
"""Tests for pcons.core.paths - PathResolver."""

import warnings
from pathlib import Path

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

    def test_a_relative_target_is_taken_as_written(self, tmp_path: Path) -> None:
        """No prefix is guessed at: "build/x" is a build subdirectory of the
        build directory."""
        resolver = PathResolver(tmp_path, Path("build"))
        assert resolver.normalize_target_path("build/foo.tar.gz") == Path(
            "build/foo.tar.gz"
        )
        assert resolver.normalize_target_path("foo.tar.gz") == Path("foo.tar.gz")

    def test_an_absolute_target_under_the_build_dir(self, tmp_path: Path) -> None:
        """An absolute path under the (possibly overridden) build directory
        comes back relative to it; one outside it stays absolute."""
        resolver = PathResolver(tmp_path, Path("build"))
        override = Path("build/mcu")
        assert resolver.normalize_target_path(tmp_path / "build/foo.tar.gz") == Path(
            "foo.tar.gz"
        )
        assert resolver.normalize_target_path(
            tmp_path / "build/mcu/gen/x.h", build_dir=override
        ) == Path("gen/x.h")
        outside = tmp_path / "elsewhere/x.h"
        assert resolver.normalize_target_path(outside, build_dir=override) == outside

    def test_normalize_target_no_warn_different_prefix(self, tmp_path: Path) -> None:
        """Relative paths not starting with build_dir name should not warn."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        build_dir = project_root / "build"
        build_dir.mkdir()

        resolver = PathResolver(project_root, Path("build"))

        # No warning for paths not starting with build dir name

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


class TestLocate:
    """Where a node path points, as a user names it: a target from the build
    directory, a source from the project top."""

    def resolver(self, tmp_path: Path) -> PathResolver:
        return PathResolver(tmp_path, Path("build"))

    def test_a_target_is_named_from_the_build_directory(self, tmp_path: Path) -> None:
        where = self.resolver(tmp_path).locate("build/obj/a.o", built=True)
        assert (where.anchor, str(where)) == ("build", "obj/a.o")

    def test_a_bare_target_name_is_in_the_build_directory(self, tmp_path: Path) -> None:
        where = self.resolver(tmp_path).locate("out.txt", built=True)
        assert (where.anchor, str(where)) == ("build", "out.txt")

    def test_the_build_directory_itself(self, tmp_path: Path) -> None:
        where = self.resolver(tmp_path).locate("build", built=True)
        assert (where.anchor, str(where)) == ("build", ".")

    def test_a_source_is_named_from_the_top(self, tmp_path: Path) -> None:
        where = self.resolver(tmp_path).locate("src/../src/a.c", built=False)
        assert (where.anchor, str(where)) == ("top", "src/a.c")

    def test_an_absolute_path_is_located_too(self, tmp_path: Path) -> None:
        resolver = self.resolver(tmp_path)
        top = tmp_path.resolve()
        assert resolver.locate(top / "build" / "a.o", built=False).anchor == "build"
        assert resolver.locate(top / "src" / "a.c", built=True).path == Path("src/a.c")

    def test_a_path_outside_the_project(self, tmp_path: Path) -> None:
        resolver = PathResolver(tmp_path / "proj", Path("build"))
        outside = (tmp_path / "sdk" / "a.h").resolve()
        where = resolver.locate(outside, built=False)
        assert (where.anchor, str(where)) == ("outside", outside.as_posix())

    def test_a_source_climbing_out_of_the_top_is_outside(self, tmp_path: Path) -> None:
        resolver = PathResolver(tmp_path / "proj", Path("build"))
        where = resolver.locate("../sdk/a.h", built=False)
        assert where.anchor == "outside"
        assert str(where) == ((tmp_path / "proj").resolve() / "../sdk/a.h").as_posix()

    def test_an_absolute_build_directory_has_no_prefix(self, tmp_path: Path) -> None:
        resolver = PathResolver(tmp_path / "proj", tmp_path / "out")
        assert resolver.locate("obj/a.o", built=True).anchor == "build"
        assert resolver.locate("src/a.c", built=False).anchor == "top"


class TestPathText:
    """A path as a program running in some directory must write it."""

    def resolver(self, tmp_path: Path) -> PathResolver:
        return PathResolver(tmp_path, Path("build"))

    def test_from_the_build_directory(self, tmp_path: Path) -> None:
        text = self.resolver(tmp_path).path_text
        assert text("build/obj/a.o", built=True) == "obj/a.o"
        assert text("src/a.c", built=False) == "../src/a.c"
        assert text(tmp_path, built=False) == ".."

    def test_the_reader_names_the_top(self, tmp_path: Path) -> None:
        text = self.resolver(tmp_path).path_text
        assert text("src/a.c", built=False, top="$topdir") == "$topdir/src/a.c"
        assert text(tmp_path, built=False, top="$topdir") == "$topdir"
        assert text("build/a.o", built=True, top="$topdir") == "a.o"

    def test_from_another_directory(self, tmp_path: Path) -> None:
        text = self.resolver(tmp_path).path_text
        work = tmp_path.resolve() / "work"
        assert text("build/out.txt", built=True, run_dir=work) == "../build/out.txt"
        assert text("src/a.c", built=False, run_dir=work) == "../src/a.c"
        assert text("work/in.txt", built=False, run_dir=work) == "in.txt"

    def test_from_the_top(self, tmp_path: Path) -> None:
        text = self.resolver(tmp_path).path_text
        top = tmp_path.resolve()
        assert text("build/obj/a.o", built=True, run_dir=top) == "build/obj/a.o"
        assert text("src/a.c", built=False, run_dir=top) == "src/a.c"

    def test_outside_the_project_stays_absolute(self, tmp_path: Path) -> None:
        resolver = PathResolver(tmp_path / "proj", Path("build"))
        outside = (tmp_path / "sdk" / "a.h").resolve()
        assert resolver.path_text(outside, built=False) == str(outside)

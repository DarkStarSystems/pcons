# SPDX-License-Identifier: MIT
"""Tests for pcons.util.commands."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from pcons.util.commands import (
    _escape_depfile_path,
    concat,
    copy,
    copytree,
    main,
    overlay,
)


class TestCopy:
    """Tests for the copy command."""

    def test_copy_file(self, tmp_path: Path) -> None:
        """Test copying a single file."""
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dest = tmp_path / "dest.txt"

        copy(str(src), str(dest))

        assert dest.exists()
        assert dest.read_text() == "hello"

    def test_copy_file_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that copy creates parent directories."""
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dest = tmp_path / "a" / "b" / "dest.txt"

        copy(str(src), str(dest))

        assert dest.exists()
        assert dest.read_text() == "hello"

    def test_copy_directory(self, tmp_path: Path) -> None:
        """Test copying a directory tree."""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("one")
        (src_dir / "sub").mkdir()
        (src_dir / "sub" / "file2.txt").write_text("two")

        dest_dir = tmp_path / "dest_dir"

        copy(str(src_dir), str(dest_dir))

        assert dest_dir.is_dir()
        assert (dest_dir / "file1.txt").read_text() == "one"
        assert (dest_dir / "sub" / "file2.txt").read_text() == "two"

    def test_copy_directory_overwrites_existing(self, tmp_path: Path) -> None:
        """Test that copying a directory removes existing destination."""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "new.txt").write_text("new")

        dest_dir = tmp_path / "dest_dir"
        dest_dir.mkdir()
        (dest_dir / "old.txt").write_text("old")

        copy(str(src_dir), str(dest_dir))

        assert (dest_dir / "new.txt").exists()
        assert not (dest_dir / "old.txt").exists()


class TestConcat:
    """Tests for the concat command."""

    def test_concat_files(self, tmp_path: Path) -> None:
        """Test concatenating multiple files."""
        src1 = tmp_path / "a.txt"
        src2 = tmp_path / "b.txt"
        src1.write_text("hello ")
        src2.write_text("world")
        dest = tmp_path / "out.txt"

        concat([str(src1), str(src2)], str(dest))

        assert dest.read_text() == "hello world"


class TestCopytree:
    """Tests for the copytree command."""

    def test_copytree_basic(self, tmp_path: Path) -> None:
        """Test basic directory tree copy."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "sub").mkdir()
        (src / "sub" / "b.txt").write_text("b")

        dest = tmp_path / "dest"

        copytree(str(src), str(dest))

        assert (dest / "a.txt").read_text() == "a"
        assert (dest / "sub" / "b.txt").read_text() == "b"

    def test_copytree_with_depfile(self, tmp_path: Path) -> None:
        """Test copytree writes a ninja depfile."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")

        dest = tmp_path / "dest"
        depfile = tmp_path / "deps.d"
        stamp = tmp_path / "stamp"

        copytree(str(src), str(dest), depfile=str(depfile), stamp=str(stamp))

        assert depfile.exists()
        assert stamp.exists()
        content = depfile.read_text()
        assert "a.txt" in content

    def test_copytree_with_stamp(self, tmp_path: Path) -> None:
        """Test copytree creates stamp file."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")

        dest = tmp_path / "dest"
        stamp = tmp_path / "stamp"

        copytree(str(src), str(dest), stamp=str(stamp))

        assert stamp.exists()

    def test_copytree_manifest_removes_only_prior_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "old.txt").write_text("old")
        dest = tmp_path / "dest"
        (dest / "unrelated").mkdir(parents=True)
        (dest / "unrelated" / "keep.txt").write_text("keep")
        manifest = tmp_path / "tree.manifest"

        copytree(str(src), str(dest), manifest=str(manifest))
        (src / "old.txt").unlink()
        (src / "new.txt").write_text("new")

        copytree(str(src), str(dest), manifest=str(manifest))

        assert not (dest / "old.txt").exists()
        assert (dest / "new.txt").read_text() == "new"
        assert (dest / "unrelated" / "keep.txt").read_text() == "keep"

    def test_copytree_manifest_removes_empty_stale_directories(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        (src / "old" / "nested").mkdir(parents=True)
        old = src / "old" / "nested" / "file.txt"
        old.write_text("old")
        dest = tmp_path / "dest"
        manifest = tmp_path / "tree.manifest"

        copytree(str(src), str(dest), manifest=str(manifest))
        old.unlink()
        (src / "old" / "nested").rmdir()
        (src / "old").rmdir()

        copytree(str(src), str(dest), manifest=str(manifest))

        assert not (dest / "old").exists()

    def test_copytree_manifest_recovers_from_corrupt_state(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "current.txt").write_text("current")
        dest = tmp_path / "dest"
        manifest = tmp_path / "tree.manifest"
        manifest.write_text("not json")

        copytree(str(src), str(dest), manifest=str(manifest))

        assert (dest / "current.txt").read_text() == "current"

    def test_copytree_cli_accepts_manifest_option(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("content")
        dest = tmp_path / "dest"
        manifest = tmp_path / "tree.manifest"
        monkeypatch.setattr(
            "sys.argv",
            [
                "pcons.util.commands",
                "copytree",
                "--manifest",
                str(manifest),
                str(src),
                str(dest),
            ],
        )

        assert main() == 0
        assert (dest / "file.txt").read_text() == "content"
        assert manifest.exists()

    def test_copytree_depfile_escapes_spaces(self, tmp_path: Path) -> None:
        """Test that source paths with spaces are escaped in the depfile.

        Ninja depfiles treat unescaped spaces as dependency separators, so a
        path containing a space must be written as ``my\\ file.txt`` (a single
        escaped dependency), not ``my file.txt`` (which ninja would parse as
        two separate dependencies).
        """
        src = tmp_path / "src"
        src.mkdir()
        (src / "my file.txt").write_text("has a space")

        dest = tmp_path / "dest"
        depfile = tmp_path / "deps.d"

        copytree(str(src), str(dest), depfile=str(depfile))

        content = depfile.read_text()
        assert "my\\ file.txt" in content

        # Verify the depfile parses to exactly one dependency for this file:
        # join line continuations, then split on spaces that are NOT
        # backslash-escaped (mimicking ninja's depfile tokenizer), and
        # finally unescape "\ " back to a plain space.
        _, deps_part = content.split(":", 1)
        deps_part = deps_part.replace("\\\n", " ")
        tokens = re.split(r"(?<!\\) ", deps_part)
        deps = [t.strip().replace("\\ ", " ") for t in tokens if t.strip()]
        expected = str(src / "my file.txt").replace("\\", "/")
        assert deps.count(expected) == 1
        assert not any(d.endswith("/my") for d in deps)
        assert "file.txt" not in deps

    def test_copytree_depfile_tracks_directories_and_escapes_target(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "source tree" / "nested"
        src.mkdir(parents=True)
        (src / "file.txt").write_text("content")
        depfile = tmp_path / "build dir" / "stamp file.d"
        stamp = tmp_path / "build dir" / "stamp file"

        copytree(
            str(src), str(tmp_path / "dest"), depfile=str(depfile), stamp=str(stamp)
        )

        content = depfile.read_text()
        target, _ = content.split(": \\\n", 1)
        assert "\\ " in target
        assert _escape_depfile_path(str(src).replace("\\", "/")) in content
        assert _escape_depfile_path(str(src.parent).replace("\\", "/")) in content


class TestCopytreeMerges:
    """An install directory is often shared -- a plugin's config directory, a
    system prefix -- so the copy merges rather than clearing the destination
    first, and skips files already identical."""

    def _tree(self, tmp_path):
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("a\n")
        (src / "sub" / "b.txt").write_text("b\n")
        dest = tmp_path / "dest"
        dest.mkdir()
        return src, dest

    def test_a_file_the_source_lacks_survives(self, tmp_path):
        src, dest = self._tree(tmp_path)
        (dest / "theirs.txt").write_text("not ours\n")

        copytree(str(src), str(dest))

        assert (dest / "theirs.txt").read_text() == "not ours\n"
        assert (dest / "sub" / "b.txt").read_text() == "b\n"

    def test_replace_clears_the_destination(self, tmp_path):
        src, dest = self._tree(tmp_path)
        (dest / "theirs.txt").write_text("not ours\n")

        copytree(str(src), str(dest), replace=True)

        assert not (dest / "theirs.txt").exists()

    def test_an_unchanged_file_is_not_recopied(self, tmp_path):
        src, dest = self._tree(tmp_path)
        copytree(str(src), str(dest))
        before = (dest / "a.txt").stat().st_mtime_ns

        copytree(str(src), str(dest))

        assert (dest / "a.txt").stat().st_mtime_ns == before

    def test_a_changed_file_is_copied(self, tmp_path):
        src, dest = self._tree(tmp_path)
        copytree(str(src), str(dest))
        (src / "a.txt").write_text("changed\n")

        copytree(str(src), str(dest))

        assert (dest / "a.txt").read_text() == "changed\n"


def depfile_deps(depfile: Path) -> set[str]:
    """The dependency paths a written depfile names, unescaped."""
    _, deps_part = depfile.read_text().split(":", 1)
    deps_part = deps_part.replace("\\\n", " ")
    tokens = re.split(r"(?<!\\) ", deps_part)
    return {t.strip().replace("\\ ", " ") for t in tokens if t.strip()}


def posix(path: Path) -> str:
    """The path as the depfile spells it."""
    return str(path).replace("\\", "/")


def overlay_trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two trees sharing one path and one directory, plus a destination."""
    shared, app = tmp_path / "shared", tmp_path / "app"
    (shared / "res").mkdir(parents=True)
    (app / "res").mkdir(parents=True)
    (shared / "both.txt").write_text("shared\n")
    (shared / "res" / "a.txt").write_text("a\n")
    (app / "both.txt").write_text("app\n")
    (app / "res" / "b.txt").write_text("b\n")
    return shared, app, tmp_path / "stage"


class TestOverlay:
    """The overlay command: N trees merged into one directory at build time."""

    def test_the_later_source_wins(self, tmp_path: Path) -> None:
        shared, app, dest = overlay_trees(tmp_path)

        overlay(str(dest), [str(shared), str(app)])

        assert (dest / "both.txt").read_text() == "app\n"
        assert (dest / "res" / "a.txt").read_text() == "a\n"
        assert (dest / "res" / "b.txt").read_text() == "b\n"

    def test_a_missing_source_is_an_error(self, tmp_path: Path) -> None:
        shared, _, dest = overlay_trees(tmp_path)

        with pytest.raises(ValueError, match="not a directory"):
            overlay(str(dest), [str(shared), str(tmp_path / "absent")])

    def test_the_depfile_names_directories_and_copied_files(
        self, tmp_path: Path
    ) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        depfile = tmp_path / "deps.d"

        overlay(str(dest), [str(shared), str(app)], depfile=str(depfile))

        deps = depfile_deps(depfile)
        assert posix(shared / "res") in deps
        assert posix(app / "res") in deps
        assert posix(app / "both.txt") in deps

    def test_a_shadowed_file_is_not_a_dependency(self, tmp_path: Path) -> None:
        """Editing a file another tree shadows changes nothing in the stage."""
        shared, app, dest = overlay_trees(tmp_path)
        depfile = tmp_path / "deps.d"

        overlay(str(dest), [str(shared), str(app)], depfile=str(depfile))

        assert posix(shared / "both.txt") not in depfile_deps(depfile)

    def test_an_excluded_directory_is_nowhere_in_the_depfile(
        self, tmp_path: Path
    ) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        (shared / ".git" / "objects").mkdir(parents=True)
        (shared / ".git" / "objects" / "ab").write_text("blob\n")
        depfile = tmp_path / "deps.d"

        overlay(str(dest), [str(shared), str(app)], [".git"], depfile=str(depfile))

        assert not [d for d in depfile_deps(depfile) if ".git" in d]

    def test_the_stamp_records_what_was_staged(self, tmp_path: Path) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        stamp = tmp_path / "stage.stamp"

        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        assert stamp.read_text().split() == ["both.txt", "res/a.txt", "res/b.txt"]

    def test_a_file_that_no_longer_exists_is_removed(self, tmp_path: Path) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        stamp = tmp_path / "stage.stamp"
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        (shared / "res" / "a.txt").unlink()
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        assert not (dest / "res" / "a.txt").exists()
        assert (dest / "res" / "b.txt").exists()

    def test_a_directory_left_empty_is_removed(self, tmp_path: Path) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        stamp = tmp_path / "stage.stamp"
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        (shared / "res" / "a.txt").unlink()
        (app / "res" / "b.txt").unlink()
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        assert not (dest / "res").exists()
        assert dest.is_dir()

    def test_a_file_the_overlay_never_staged_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        stamp = tmp_path / "stage.stamp"
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))
        (dest / "theirs.txt").write_text("not ours\n")

        (shared / "res" / "a.txt").unlink()
        overlay(str(dest), [str(shared), str(app)], stamp=str(stamp))

        assert (dest / "theirs.txt").read_text() == "not ours\n"

    def test_an_unchanged_file_is_not_recopied(self, tmp_path: Path) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        overlay(str(dest), [str(shared), str(app)])
        before = (dest / "both.txt").stat().st_mtime_ns

        overlay(str(dest), [str(shared), str(app)])

        assert (dest / "both.txt").stat().st_mtime_ns == before


class TestOverlayCommandLine:
    """The overlay command as main() dispatches it.

    The generated build file only ever writes --exclude=PATTERN and the
    two-token --depfile / --stamp, so the other spellings and the usage
    errors are reachable only from a hand-run command line.
    """

    def _run(self, monkeypatch, *args: str) -> int:
        monkeypatch.setattr(sys, "argv", ["commands", *args])
        return main()

    def test_the_command_list_names_overlay(self, monkeypatch, capsys) -> None:
        code = self._run(monkeypatch)

        assert code == 1
        assert "copy, concat, copytree, overlay, env" in capsys.readouterr().err

    def test_a_separate_exclude_argument_filters(self, tmp_path, monkeypatch) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        (shared / "both.txt.orig").write_text("editor leftover\n")

        code = self._run(
            monkeypatch,
            "overlay",
            "--exclude",
            "*.orig",
            str(dest),
            str(shared),
            str(app),
        )

        assert code == 0
        assert not (dest / "both.txt.orig").exists()
        assert (dest / "both.txt").read_text() == "app\n"

    def test_depfile_and_stamp_take_the_equals_spelling(
        self, tmp_path, monkeypatch
    ) -> None:
        shared, app, dest = overlay_trees(tmp_path)
        depfile, stamp = tmp_path / "deps.d", tmp_path / "stage.stamp"

        code = self._run(
            monkeypatch,
            "overlay",
            f"--depfile={depfile}",
            f"--stamp={stamp}",
            str(dest),
            str(shared),
            str(app),
        )

        assert code == 0
        assert posix(app / "both.txt") in depfile_deps(depfile)
        assert stamp.read_text().split() == ["both.txt", "res/a.txt", "res/b.txt"]

    def test_a_destination_with_no_source_is_a_usage_error(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        dest = tmp_path / "stage"

        code = self._run(monkeypatch, "overlay", str(dest))

        assert code == 1
        assert "<dest> <src> [src...]" in capsys.readouterr().err
        assert not dest.exists()


class TestCopytreeSymlinks:
    """A symlinked directory is descended into and copied as a real one, the
    way shutil.copytree does. A macOS framework is built out of them
    (Versions/Current), so stepping over one installs the shape of the bundle
    with none of its contents."""

    def test_a_symlinked_directory_brings_its_contents(self, tmp_path):
        src = tmp_path / "src"
        (src / "real" / "nested").mkdir(parents=True)
        (src / "real" / "file.txt").write_text("content\n")
        (src / "real" / "nested" / "deep.txt").write_text("deep\n")
        (src / "linkdir").symlink_to("real")

        copytree(str(src), str(tmp_path / "dest"))

        dest = tmp_path / "dest"
        assert (dest / "linkdir" / "file.txt").read_text() == "content\n"
        assert (dest / "linkdir" / "nested" / "deep.txt").read_text() == "deep\n"

    def test_a_symlink_loop_terminates(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("x\n")
        (src / "loop").symlink_to("..")

        copytree(str(src), str(tmp_path / "dest"))

        assert (tmp_path / "dest" / "file.txt").read_text() == "x\n"


class TestRunWithEnv:
    """The `env` helper command: env(1) for platforms without one."""

    def test_variables_reach_the_command(self, tmp_path: Path, monkeypatch) -> None:
        import sys

        from pcons.util.commands import run_with_env

        monkeypatch.delenv("PCONS_TEST_GREETING", raising=False)
        out = tmp_path / "out.txt"
        code = run_with_env(
            [
                "PCONS_TEST_GREETING=from-env",
                sys.executable,
                "-c",
                "import os, pathlib, sys; "
                "pathlib.Path(sys.argv[1]).write_text("
                "os.environ['PCONS_TEST_GREETING'])",
                str(out),
            ]
        )

        assert code == 0
        assert out.read_text() == "from-env"

    def test_the_commands_exit_code_is_returned(self, monkeypatch) -> None:
        import sys

        from pcons.util.commands import run_with_env

        monkeypatch.delenv("PCONS_TEST_A", raising=False)
        code = run_with_env(
            ["PCONS_TEST_A=1", sys.executable, "-c", "import sys; sys.exit(3)"]
        )

        assert code == 3

    def test_assignments_stop_at_the_command(self, tmp_path: Path, monkeypatch) -> None:
        """A later argument that happens to contain '=' is the command's own."""
        import sys

        from pcons.util.commands import run_with_env

        monkeypatch.delenv("PCONS_TEST_LATER", raising=False)
        out = tmp_path / "out.txt"
        code = run_with_env(
            [
                sys.executable,
                "-c",
                "import os, pathlib, sys; "
                "pathlib.Path(sys.argv[1]).write_text("
                "os.environ.get('PCONS_TEST_LATER', 'unset'))",
                str(out),
                "PCONS_TEST_LATER=1",
            ]
        )

        assert code == 0
        assert out.read_text() == "unset"

    def test_no_command_is_a_usage_error(self, capsys) -> None:
        from pcons.util.commands import run_with_env

        assert run_with_env(["A=1"]) == 1
        assert "Usage" in capsys.readouterr().err

    def test_a_missing_program_reports_127(self, monkeypatch, capsys) -> None:
        """The shell convention, and no traceback in the build output."""
        from pcons.util.commands import run_with_env

        monkeypatch.delenv("PCONS_TEST_A", raising=False)
        code = run_with_env(["PCONS_TEST_A=1", "pcons-no-such-program-xyzzy"])

        assert code == 127
        assert "pcons-no-such-program-xyzzy" in capsys.readouterr().err

    def test_the_module_entry_point_dispatches(self, tmp_path: Path) -> None:
        """End to end, the way a generated build file invokes it."""
        import subprocess
        import sys

        out = tmp_path / "out.txt"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.util.commands",
                "env",
                "PCONS_TEST_GREETING=via-module",
                sys.executable,
                "-c",
                "import os, pathlib, sys; "
                "pathlib.Path(sys.argv[1]).write_text("
                "os.environ['PCONS_TEST_GREETING'])",
                str(out),
            ]
        )

        assert result.returncode == 0
        assert out.read_text() == "via-module"

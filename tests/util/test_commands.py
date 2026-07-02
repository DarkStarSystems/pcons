# SPDX-License-Identifier: MIT
"""Tests for pcons.util.commands."""

from __future__ import annotations

import re
from pathlib import Path

from pcons.util.commands import concat, copy, copytree


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

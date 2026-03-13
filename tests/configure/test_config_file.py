# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.config_file.configure_file()."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pcons.configure.config_file import configure_file

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ── @VAR@ substitution ("at" style) ─────────────────────────────────────────


class TestAtStyle:
    def test_simple(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "Version: @VERSION@\n")
        out = configure_file(tpl, tmp_path / "t.out", {"VERSION": "1.2.3"}, style="at")
        assert out.read_text() == "Version: 1.2.3\n"

    def test_multiple_variables(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "@A@ and @B@\n")
        out = configure_file(tpl, tmp_path / "t.out", {"A": "x", "B": "y"}, style="at")
        assert out.read_text() == "x and y\n"

    def test_adjacent_vars(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "@A@@B@")
        out = configure_file(tpl, tmp_path / "t.out", {"A": "1", "B": "2"}, style="at")
        assert out.read_text() == "12"

    def test_no_substitutions(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "no vars here\n")
        out = configure_file(tpl, tmp_path / "t.out", {}, style="at")
        assert out.read_text() == "no vars here\n"

    def test_missing_strict(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "@MISSING@")
        with pytest.raises(KeyError, match="MISSING"):
            configure_file(tpl, tmp_path / "t.out", {}, style="at")

    def test_missing_nonstrict(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "[@MISSING@]")
        out = configure_file(tpl, tmp_path / "t.out", {}, style="at", strict=False)
        assert out.read_text() == "[]"


# ── CMake style ──────────────────────────────────────────────────────────────


class TestCmakeStyle:
    def test_cmakedefine01_truthy(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine01 HAVE_ZLIB\n")
        out = configure_file(tpl, tmp_path / "t.out", {"HAVE_ZLIB": "1"})
        assert out.read_text() == "#define HAVE_ZLIB 1\n"

    def test_cmakedefine01_falsy_missing(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine01 HAVE_ZLIB\n")
        out = configure_file(tpl, tmp_path / "t.out", {})
        assert out.read_text() == "#define HAVE_ZLIB 0\n"

    @pytest.mark.parametrize(
        "val", ["", "0", "OFF", "off", "FALSE", "false", "NO", "no"]
    )
    def test_cmakedefine01_falsy_values(self, tmp_path: Path, val: str) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine01 FEAT\n")
        out = configure_file(tpl, tmp_path / "t.out", {"FEAT": val})
        assert out.read_text() == "#define FEAT 0\n"

    def test_cmakedefine_with_value_truthy(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine HAVE_FEATURE 1\n")
        out = configure_file(tpl, tmp_path / "t.out", {"HAVE_FEATURE": "1"})
        assert out.read_text() == "#define HAVE_FEATURE 1\n"

    def test_cmakedefine_with_value_falsy(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine HAVE_FEATURE 1\n")
        out = configure_file(tpl, tmp_path / "t.out", {})
        assert out.read_text() == "/* #undef HAVE_FEATURE */\n"

    def test_cmakedefine_bare_truthy(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine HAVE_THREADS\n")
        out = configure_file(tpl, tmp_path / "t.out", {"HAVE_THREADS": "1"})
        assert out.read_text() == "#define HAVE_THREADS\n"

    def test_cmakedefine_bare_falsy(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#cmakedefine HAVE_THREADS\n")
        out = configure_file(tpl, tmp_path / "t.out", {})
        assert out.read_text() == "/* #undef HAVE_THREADS */\n"

    def test_at_var_also_works(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "#define VER @VERSION@\n")
        out = configure_file(tpl, tmp_path / "t.out", {"VERSION": "42"})
        assert out.read_text() == "#define VER 42\n"

    def test_cmakedefine_with_at_var_value(self, tmp_path: Path) -> None:
        """#cmakedefine VAR @VAR@ — the @VAR@ in the value is also expanded."""
        tpl = _write(tmp_path / "t.in", "#cmakedefine SIZE @SIZE@\n")
        out = configure_file(tpl, tmp_path / "t.out", {"SIZE": "64"})
        assert out.read_text() == "#define SIZE 64\n"

    def test_cmakedefine_suffix_not_confused_with_truthiness(
        self, tmp_path: Path
    ) -> None:
        """Literal '0' suffix in template is NOT used for truthiness check.

        CMake checks the variable's value, not the template suffix.
        ``#cmakedefine FEAT 0`` with FEAT='yes' → ``#define FEAT 0``.
        """
        tpl = _write(tmp_path / "t.in", "#cmakedefine FEAT 0\n")
        # Truthy variable → define with literal suffix preserved
        out = configure_file(tpl, tmp_path / "t.out", {"FEAT": "1"})
        assert out.read_text() == "#define FEAT 0\n"
        # Falsy variable value → undef (even though suffix is "0")
        out = configure_file(tpl, tmp_path / "t.out", {"FEAT": "0"})
        assert out.read_text() == "/* #undef FEAT */\n"
        # Undefined variable → undef
        out = configure_file(tpl, tmp_path / "t.out", {})
        assert out.read_text() == "/* #undef FEAT */\n"

    def test_combined(self, tmp_path: Path) -> None:
        tpl = _write(
            tmp_path / "t.in",
            "#cmakedefine01 USE_DEBUG\n"
            "#cmakedefine HAVE_ZLIB\n"
            "#cmakedefine VERSION_STR @VER@\n"
            "#define PLAIN @PLAIN@\n",
        )
        out = configure_file(
            tpl,
            tmp_path / "t.out",
            {
                "USE_DEBUG": "0",
                "HAVE_ZLIB": "1",
                "VERSION_STR": "1",
                "VER": "3.0",
                "PLAIN": "hello",
            },
        )
        assert out.read_text() == (
            "#define USE_DEBUG 0\n"
            "#define HAVE_ZLIB\n"
            "#define VERSION_STR 3.0\n"
            "#define PLAIN hello\n"
        )


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_invalid_style(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "")
        with pytest.raises(ValueError, match="Unknown"):
            configure_file(tpl, tmp_path / "t.out", {}, style="bad")

    def test_template_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            configure_file(tmp_path / "missing.in", tmp_path / "out", {})

    def test_output_parent_created(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "ok")
        out = configure_file(tpl, tmp_path / "deep" / "dir" / "out.h", {}, style="at")
        assert out.read_text() == "ok"

    def test_returns_path(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "ok")
        result = configure_file(tpl, tmp_path / "out", {}, style="at")
        assert isinstance(result, Path)
        assert result == tmp_path / "out"

    def test_write_if_changed(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "@V@")
        out_path = tmp_path / "out"
        configure_file(tpl, out_path, {"V": "1"}, style="at")
        mtime1 = os.path.getmtime(out_path)
        # Second call with same content should not rewrite
        configure_file(tpl, out_path, {"V": "1"}, style="at")
        mtime2 = os.path.getmtime(out_path)
        assert mtime1 == mtime2

    def test_string_paths(self, tmp_path: Path) -> None:
        tpl = _write(tmp_path / "t.in", "@X@")
        out = configure_file(str(tpl), str(tmp_path / "out"), {"X": "ok"}, style="at")
        assert out.read_text() == "ok"

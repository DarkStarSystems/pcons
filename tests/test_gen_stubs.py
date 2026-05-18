# SPDX-License-Identifier: MIT
"""Tests for `pcons._gen_stubs` — the generator that produces the typed
stub mixins under `pcons/core/_*_stubs.py`.

The freshness check in `test_builder_stubs_fresh.py` exercises only
`write_or_check("check")` against the committed stubs. These tests cover
the other modes (`write`, `print`), the CLI entry point, and the
parameter / annotation formatting helpers.
"""

from __future__ import annotations

import sys
from inspect import Parameter, _ParameterKind
from pathlib import Path

import pytest

from pcons import _gen_stubs

# ---- _format_param / _rewrite_annotation -------------------------------------


def _param(
    name: str,
    *,
    kind: _ParameterKind = Parameter.POSITIONAL_OR_KEYWORD,
    annotation: object = Parameter.empty,
    default: object = Parameter.empty,
) -> Parameter:
    return Parameter(name, kind, annotation=annotation, default=default)


class TestFormatParam:
    def test_var_positional_has_star(self) -> None:
        p = _param("args", kind=Parameter.VAR_POSITIONAL)
        assert _gen_stubs._format_param(p) == "*args"

    def test_var_keyword_has_double_star(self) -> None:
        p = _param("kwargs", kind=Parameter.VAR_KEYWORD)
        assert _gen_stubs._format_param(p) == "**kwargs"

    def test_bare_name_no_annotation_no_default(self) -> None:
        assert _gen_stubs._format_param(_param("x")) == "x"

    def test_annotation_only(self) -> None:
        # `from __future__ import annotations` in source modules means the
        # annotation reaches us as a string already.
        assert _gen_stubs._format_param(_param("x", annotation="int")) == "x: int"

    def test_default_only(self) -> None:
        assert _gen_stubs._format_param(_param("x", default=3)) == "x = 3"

    def test_annotation_and_default_uses_repr_for_default(self) -> None:
        # Defaults flow through repr(), so string defaults gain quotes.
        out = _gen_stubs._format_param(_param("name", annotation="str", default="hi"))
        assert out == "name: str = 'hi'"

    def test_environment_alias_rewritten_to_env(self) -> None:
        # Stub file imports `Environment as Env`; annotations using
        # `Environment` need to be rewritten to match.
        out = _gen_stubs._format_param(_param("e", annotation="Environment | None"))
        assert out == "e: Env | None"


# ---- _owner_of ---------------------------------------------------------------


class TestOwnerOf:
    def test_returns_none_for_plain_function(self) -> None:
        def plain_fn() -> None:
            pass

        assert _gen_stubs._owner_of(plain_fn) is None

    def test_returns_class_for_method(self) -> None:
        # Use a real registered builder: the @builder decorator wraps a
        # class whose create_target is a staticmethod, which is exactly
        # the shape _owner_of is designed to recover.
        from pcons.builders import register_builtin_builders
        from pcons.core.builder_registry import BuilderRegistry

        register_builtin_builders()
        reg = BuilderRegistry.get("Program")
        assert reg is not None
        owner = _gen_stubs._owner_of(reg.create_target)
        assert owner is not None
        assert owner.__name__.endswith("Builder") or "Program" in owner.__name__


# ---- write_or_check: write + print modes -------------------------------------


class TestWriteOrCheck:
    def test_print_mode_writes_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = _gen_stubs.write_or_check("print")
        assert rc == 0
        out = capsys.readouterr().out
        # Headers for all four stub files should appear in --print output.
        assert "# === core/_project_builder_stubs.py ===" in out
        assert "# === core/_environment_stubs.py ===" in out
        assert "# === core/_toolconfig_stubs.py ===" in out
        assert "# === core/_usage_requirements_stubs.py ===" in out
        # And the actual class definitions.
        assert "class _ProjectBuilders" in out
        assert "class _EnvironmentStubs" in out

    def test_write_mode_no_op_when_fresh(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Committed stubs are fresh, so write should be a no-op for each.
        rc = _gen_stubs.write_or_check("write")
        assert rc == 0
        out = capsys.readouterr().out
        assert "is up to date" in out
        assert "Updated" not in out

    def test_write_mode_rewrites_stale_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Redirect the stub-file resolver to a tmp dir so we can corrupt one
        # file and watch write mode restore it, without touching the real
        # committed stubs.
        stub_root = tmp_path / "core"
        stub_root.mkdir()
        monkeypatch.setattr(
            _gen_stubs,
            "_stub_file_path",
            lambda relpath: tmp_path / relpath,
        )

        stale_file = stub_root / "_project_builder_stubs.py"
        stale_file.write_text("# stale\n", encoding="utf-8")

        rc = _gen_stubs.write_or_check("write")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Updated" in out

        rewritten = stale_file.read_text(encoding="utf-8")
        assert "class _ProjectBuilders" in rewritten
        # And the freshly-rewritten file should round-trip equal under
        # check mode.
        assert _gen_stubs.write_or_check("check") == 0

    def test_check_mode_reports_stale(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        stub_root = tmp_path / "core"
        stub_root.mkdir()
        monkeypatch.setattr(
            _gen_stubs,
            "_stub_file_path",
            lambda relpath: tmp_path / relpath,
        )
        # Leave all files missing — read_text falls back to "", which won't
        # match the generator output, so check returns non-zero.
        rc = _gen_stubs.write_or_check("check")
        assert rc == 1
        err = capsys.readouterr().err
        assert "is out of date" in err
        assert "python -m pcons._gen_stubs" in err

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown mode"):
            _gen_stubs.write_or_check("bogus")


# ---- main() CLI --------------------------------------------------------------


class TestMain:
    def test_default_mode_is_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "core").mkdir()
        monkeypatch.setattr(
            _gen_stubs,
            "_stub_file_path",
            lambda relpath: tmp_path / relpath,
        )
        rc = _gen_stubs.main([])
        assert rc == 0
        # All four stub files should now exist under tmp_path.
        for relpath in (
            "core/_project_builder_stubs.py",
            "core/_environment_stubs.py",
            "core/_toolconfig_stubs.py",
            "core/_usage_requirements_stubs.py",
        ):
            assert (tmp_path / relpath).exists()
        capsys.readouterr()  # drain stdout

    def test_check_flag_returns_zero_when_fresh(self) -> None:
        # Real committed stubs are fresh; --check should pass against them.
        assert _gen_stubs.main(["--check"]) == 0

    def test_print_flag_emits_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = _gen_stubs.main(["--print"])
        assert rc == 0
        assert "class _ProjectBuilders" in capsys.readouterr().out

    def test_check_and_print_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            _gen_stubs.main(["--check", "--print"])


# ---- UTF-8 round-trip (regression for the cp1252 mojibake on Windows CI) -----


class TestUtf8RoundTrip:
    def test_em_dash_in_generated_content_round_trips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The generator's module docstrings contain non-ASCII (em-dash, —).
        # Earlier versions used the system default encoding for I/O, which
        # silently mojibaked on Windows. Pin UTF-8 and confirm the check
        # mode agrees with the write mode it just produced.
        monkeypatch.setattr(
            _gen_stubs,
            "_stub_file_path",
            lambda relpath: tmp_path / relpath,
        )
        (tmp_path / "core").mkdir()
        assert _gen_stubs.write_or_check("write") == 0
        assert _gen_stubs.write_or_check("check") == 0
        # And confirm the on-disk file is genuinely UTF-8 (would raise
        # otherwise).
        for relpath in (
            "core/_project_builder_stubs.py",
            "core/_environment_stubs.py",
            "core/_toolconfig_stubs.py",
        ):
            (tmp_path / relpath).read_text(encoding="utf-8")


# ---- module __main__ guard ---------------------------------------------------


def test_module_can_be_invoked_with_python_dash_m() -> None:
    # Exercise `python -m pcons._gen_stubs --check` end-to-end. This covers
    # the `if __name__ == "__main__":` guard and confirms argv plumbing.
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "pcons._gen_stubs", "--check"],
        cwd=Path(_gen_stubs.__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

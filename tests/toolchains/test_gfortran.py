# SPDX-License-Identifier: MIT
"""Tests for the GNU Fortran (gfortran) toolchain."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from pcons.toolchains.fortran_scanner import scan_fortran_source, write_dyndep
from pcons.toolchains.gfortran import (
    FORTRAN_EXTENSIONS,
    GfortranCompiler,
    GfortranLinker,
    GfortranToolchain,
    find_fortran_toolchain,
)
from pcons.tools.toolchain import SourceHandler, toolchain_registry

# =============================================================================
# GfortranToolchain creation and registration
# =============================================================================


def test_gfortran_toolchain_registered() -> None:
    """GfortranToolchain should be registered in the toolchain registry."""
    entry = toolchain_registry.get("gfortran")
    assert entry is not None
    assert entry.toolchain_class is GfortranToolchain
    assert entry.category == "fortran"
    assert "gfortran" in entry.aliases


def test_gfortran_toolchain_creation() -> None:
    """GfortranToolchain can be created and has correct name."""
    tc = GfortranToolchain()
    assert tc.name == "gfortran"


def test_gfortran_language_priority() -> None:
    """GfortranToolchain includes 'fortran' in its language_priority."""
    tc = GfortranToolchain()
    priority = tc.language_priority
    assert "fortran" in priority
    # Fortran should outrank C and C++
    assert priority["fortran"] > priority["c"]
    assert priority["fortran"] >= priority.get("cxx", 0)


# =============================================================================
# Source handler tests
# =============================================================================


def test_get_source_handler_all_fortran_extensions() -> None:
    """GfortranToolchain handles all Fortran file extensions."""
    tc = GfortranToolchain()
    for ext in FORTRAN_EXTENSIONS:
        handler = tc.get_source_handler(ext)
        assert handler is not None, f"No handler for {ext}"
        assert isinstance(handler, SourceHandler)
        assert handler.tool_name == "fc"
        assert handler.language == "fortran"
        assert handler.object_suffix == ".o"
        # No depfile/deps_style for Fortran (uses dyndep instead)
        assert handler.depfile is None
        assert handler.deps_style is None


def test_get_source_handler_c_fallthrough() -> None:
    """GfortranToolchain falls through to UnixToolchain for C/C++ files."""
    tc = GfortranToolchain()
    handler = tc.get_source_handler(".c")
    assert handler is not None
    assert handler.tool_name == "cc"
    assert handler.language == "c"


def test_get_source_handler_unknown() -> None:
    """GfortranToolchain returns None for unknown extensions."""
    tc = GfortranToolchain()
    assert tc.get_source_handler(".txt") is None
    assert tc.get_source_handler(".py") is None


# =============================================================================
# GfortranCompiler.default_vars tests
# =============================================================================


def test_gfortran_compiler_default_vars() -> None:
    """GfortranCompiler default_vars should include moddir flags."""
    compiler = GfortranCompiler()
    vars = compiler.default_vars()

    assert vars["cmd"] == "gfortran"
    assert vars["moddir"] == "modules"
    # Fortran uses dyndep for module deps, no depflags needed
    assert "depflags" not in vars

    objcmd = vars["objcmd"]
    assert isinstance(objcmd, list)

    # Check that -J and -I moddir flags are present
    cmd_str = " ".join(str(t) for t in objcmd)
    assert "-J" in cmd_str
    assert "$fc.moddir" in cmd_str
    assert "-I" in cmd_str


def test_gfortran_linker_default_vars() -> None:
    """GfortranLinker should default to gfortran as linker command."""
    linker = GfortranLinker()
    vars = linker.default_vars()
    assert vars["cmd"] == "gfortran"
    assert "progcmd" in vars
    assert "sharedcmd" in vars


# =============================================================================
# Fortran scanner: module extraction tests
# =============================================================================


def test_scan_module_definition() -> None:
    """Scanner detects MODULE declarations."""
    src = textwrap.dedent("""\
        MODULE greetings
          IMPLICIT NONE
        CONTAINS
          SUBROUTINE say_hello()
            PRINT *, "Hello!"
          END SUBROUTINE
        END MODULE greetings
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == ["greetings"]
    assert consumes == []


def test_scan_use_statement() -> None:
    """Scanner detects USE statements."""
    src = textwrap.dedent("""\
        PROGRAM main
          USE greetings
          IMPLICIT NONE
          CALL say_hello()
        END PROGRAM
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == []
    assert consumes == ["greetings"]


def test_scan_case_insensitive() -> None:
    """Scanner handles case-insensitive Fortran keywords."""
    src = textwrap.dedent("""\
        module mymod
          use other_mod
          use :: third_mod
        end module mymod
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == ["mymod"]
    assert set(consumes) == {"other_mod", "third_mod"}


def test_scan_ignores_intrinsic_modules() -> None:
    """Scanner ignores intrinsic Fortran modules."""
    src = textwrap.dedent("""\
        PROGRAM test
          USE iso_fortran_env
          USE iso_c_binding
          IMPLICIT NONE
        END PROGRAM
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == []
    assert consumes == []


def test_scan_ignores_comments() -> None:
    """Scanner ignores MODULE/USE in comments."""
    src = textwrap.dedent("""\
        ! USE commented_out
        PROGRAM test
          IMPLICIT NONE
          ! MODULE not_a_module
        END PROGRAM
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == []
    assert consumes == []


def test_scan_module_procedure_not_detected() -> None:
    """MODULE PROCEDURE should not be treated as a module definition."""
    src = textwrap.dedent("""\
        MODULE PROCEDURE my_proc
        MODULE real_module
        END MODULE
    """)
    produces, _ = scan_fortran_source(src)
    assert produces == ["real_module"]


def test_scan_module_names_lowercased() -> None:
    """Module names are normalized to lowercase."""
    src = textwrap.dedent("""\
        MODULE MyMod
        END MODULE
    """)
    produces, _ = scan_fortran_source(src)
    assert produces == ["mymod"]


def test_scan_self_use_excluded() -> None:
    """A module that uses itself should not appear in consumes."""
    src = textwrap.dedent("""\
        MODULE foo
          USE foo
        END MODULE
    """)
    produces, consumes = scan_fortran_source(src)
    assert produces == ["foo"]
    assert consumes == []


# =============================================================================
# Dyndep file output format tests
# =============================================================================


def test_write_dyndep_basic(tmp_path: Path) -> None:
    """write_dyndep produces correct Ninja dyndep format."""
    src_file = tmp_path / "greetings.f90"
    src_file.write_text("""\
MODULE greetings
END MODULE greetings
""")
    manifest = [
        {"src": str(src_file), "obj": "obj.hello/greetings.f90.o"},
    ]
    out_path = str(tmp_path / "fortran_modules.dyndep")
    write_dyndep(manifest, "modules", out_path)

    content = Path(out_path).read_text()
    assert "ninja_dyndep_version = 1" in content
    assert "obj.hello/greetings.f90.o" in content
    assert "modules/greetings.mod" in content


def test_write_dyndep_consumer(tmp_path: Path) -> None:
    """write_dyndep includes implicit inputs for USE statements."""
    src_file = tmp_path / "main.f90"
    src_file.write_text("""\
PROGRAM main
  USE greetings
END PROGRAM
""")
    manifest = [
        {"src": str(src_file), "obj": "obj.hello/main.f90.o"},
    ]
    out_path = str(tmp_path / "fortran_modules.dyndep")
    write_dyndep(manifest, "modules", out_path)

    content = Path(out_path).read_text()
    assert "build obj.hello/main.f90.o: dyndep | modules/greetings.mod" in content


def test_write_dyndep_no_modules(tmp_path: Path) -> None:
    """write_dyndep handles sources with no MODULE/USE."""
    src_file = tmp_path / "hello.f90"
    src_file.write_text("PROGRAM hello\nPRINT *, 'Hi'\nEND PROGRAM\n")
    manifest = [{"src": str(src_file), "obj": "obj.hello/hello.f90.o"}]
    out_path = str(tmp_path / "fortran_modules.dyndep")
    write_dyndep(manifest, "modules", out_path)

    content = Path(out_path).read_text()
    assert "ninja_dyndep_version = 1" in content
    # Should have a build statement with no implicit deps or outputs
    assert "build obj.hello/hello.f90.o: dyndep\n" in content


# =============================================================================
# find_fortran_toolchain tests
# =============================================================================


def test_find_fortran_toolchain_raises_when_not_found() -> None:
    """find_fortran_toolchain raises RuntimeError when gfortran not found."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="No Fortran toolchain found"):
            find_fortran_toolchain()


def test_find_fortran_toolchain_returns_toolchain() -> None:
    """find_fortran_toolchain returns GfortranToolchain when gfortran found."""
    with patch("shutil.which", return_value="/usr/bin/gfortran"):
        tc = find_fortran_toolchain()
        assert isinstance(tc, GfortranToolchain)

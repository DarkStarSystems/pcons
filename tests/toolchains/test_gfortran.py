# SPDX-License-Identifier: MIT
"""Tests for the GNU Fortran (gfortran) toolchain."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from pcons import Project
from pcons.configure.platform import get_platform
from pcons.core.target import Target
from pcons.toolchains.fortran import FORTRAN_EXTENSIONS, find_fortran_toolchain
from pcons.toolchains.fortran_scanner import (
    depfile_text,
    expand_includes,
    scan_fortran_source,
    scan_info,
)
from pcons.toolchains.fortran_scanner import main as fortran_scanner_main
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.gfortran import (
    GfortranCompiler,
    GfortranLinker,
    GfortranToolchain,
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
        assert handler.object_suffix == get_platform().object_suffix
        # No depfile/deps_style for Fortran (uses dyndep instead)
        assert handler.depfile is None
        assert handler.deps_style is None


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


def test_scan_submodule_procedures_are_not_modules() -> None:
    """A submodule implements separate module procedures as "module
    subroutine" and "module function"; neither defines a module, and reading
    one as a module named "subroutine" made two submodules collide."""
    src = textwrap.dedent("""\
        submodule (parent) child
        contains
          module subroutine step()
          end subroutine
          module function value() result(v)
            integer :: v
          end function
        end submodule
    """)
    produces, uses = scan_fortran_source(src)
    assert produces == ["parent:child"]
    assert uses == ["parent"]


def test_scan_prefixed_module_procedures_are_not_modules() -> None:
    """ "module pure function f(x)" once read as a module named "pure"."""
    src = textwrap.dedent("""\
        submodule (shapes) impl
        contains
          module pure function area(r) result(a)
          end function
          module elemental subroutine scale(x)
          end subroutine
          module real function perimeter(r)
          end function
        end submodule
    """)
    produces, _ = scan_fortran_source(src)
    assert produces == ["shapes:impl"]


def test_scan_nested_submodule_uses_its_parent() -> None:
    src = "SUBMODULE (Shapes : Impl) Helpers\nEND SUBMODULE\n"
    assert scan_fortran_source(src) == (["shapes:helpers"], ["shapes:impl"])


def test_scan_info_names_submodule_files_as_the_compiler_does() -> None:
    src = "submodule (shapes) impl\nend submodule\n"

    gnu = scan_info(src, "modules")
    flang = scan_info(src, "modules", "{ancestor}-{name}.mod")

    assert gnu["provides"] == [
        {"name": "shapes:impl", "path": "modules/shapes@impl.smod"}
    ]
    assert flang["extra_outputs"] == ["modules/shapes-impl.mod"]
    assert flang["requires"] == ["shapes"]


def test_scan_info_points_submodules_at_gfortran_smod() -> None:
    """gfortran keeps a module's private entities, which its submodules see,
    out of the .mod: they read the .smod it writes beside it."""
    parent = textwrap.dedent("""\
        module shapes
          real, parameter, private :: pi = 3.14159
          interface
            module pure function area(r) result(a)
              real, intent(in) :: r
              real :: a
            end function
          end interface
        end module
    """)
    child = "submodule (shapes) impl\nend submodule\n"

    provider = scan_info(parent, "modules", ancestor_file="{name}.smod")
    consumer = scan_info(child, "modules", ancestor_file="{name}.smod")

    assert provider["provides"] == [
        {"name": "shapes", "path": "modules/shapes.mod"},
        {"name": "shapes:", "path": "modules/shapes.smod"},
    ]
    assert provider["extra_outputs"] == ["modules/shapes.mod", "modules/shapes.smod"]
    assert consumer["requires"] == ["shapes:"]
    # flang and ifx put everything in the .mod.
    assert scan_info(child, "modules")["requires"] == ["shapes"]


@pytest.mark.parametrize(
    "declaration",
    [
        "module subroutine s(x)",
        "integer module function f()",
        "PURE MODULE FUNCTION f(x) RESULT(y)",
        "real(kind=8) module function f()",
    ],
)
def test_scan_separate_module_procedures_make_a_smod(declaration) -> None:
    src = f"module m\ninterface\n  {declaration}\n  end\nend interface\nend module\n"
    produces, _ = scan_fortran_source(src, ancestor_file_apart=True)
    assert produces == ["m", "m:"]


def test_scan_only_a_module_with_separate_procedures_makes_a_smod() -> None:
    """gfortran writes no .smod for the rest; claiming one would leave
    ninja waiting forever for a file nothing writes."""
    src = textwrap.dedent("""\
        module plain
          interface g
            module procedure foo
          end interface
        contains
          subroutine foo()
          end subroutine
        end module
        module hosting
          interface
            module subroutine s()
            end subroutine
          end interface
        end module
        submodule (hosting) impl
        contains
          module subroutine s()
          end subroutine
        end submodule
        module after
        end module
        subroutine external()
        end subroutine
    """)
    produces, consumes = scan_fortran_source(src, ancestor_file_apart=True)
    assert produces == ["plain", "hosting", "after", "hosting:impl", "hosting:"]
    # The submodule's parent is in this same file.
    assert consumes == []


_SMOD_LAYOUTS = {
    "plain": "module m\n  integer :: x\nend module\n",
    "generic interface": (
        "module m\ninterface g\n  module procedure foo\nend interface\n"
        "contains\nsubroutine foo()\nend subroutine\nend module\n"
    ),
    "separate procedure": (
        "module m\ninterface\n  module subroutine s()\n  end subroutine\n"
        "end interface\nend module\n"
    ),
    "prefixes": (
        "module m\ninterface\n  recursive module subroutine s(n)\n"
        "    integer, intent(in) :: n\n  end subroutine\n"
        "  module character(len=8) function name()\n  end function\n"
        "end interface\nend module\n"
    ),
    "continued": (
        "module m\ninterface\n  module &\n    function f() result(r)\n"
        "    integer :: r\n  end function\nend interface\nend module\n"
    ),
    "one line": (
        "module m; interface; module subroutine s(); end subroutine; "
        "end interface; end module\n"
    ),
    "body in the module": (
        "module m\ninterface\n  module subroutine s()\n  end subroutine\n"
        "end interface\ncontains\n  module procedure s\n  end procedure\n"
        "end module\n"
    ),
}


@pytest.mark.skipif(shutil.which("gfortran") is None, reason="needs gfortran")
@pytest.mark.parametrize("layout", sorted(_SMOD_LAYOUTS))
def test_scan_predicts_gfortran_smod(layout, tmp_path) -> None:
    """The scanner claims a module's .smod exactly when gfortran writes one:
    a missed one leaves submodules stale, and one gfortran never writes
    leaves ninja rebuilding forever."""
    source = tmp_path / "m.f90"
    source.write_text(_SMOD_LAYOUTS[layout])
    subprocess.run(["gfortran", "-c", source.name], cwd=tmp_path, check=True)

    produces, _ = scan_fortran_source(source.read_text(), ancestor_file_apart=True)
    assert ("m:" in produces) == (tmp_path / "m.smod").exists()


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
# Scan-info document tests (the scan half of the Scanner primitive)
# =============================================================================


def test_scan_info_provider() -> None:
    """A provided module is both a provide and an extra output."""
    info = scan_info("MODULE greetings\nEND MODULE greetings\n", "modules")

    assert info["version"] == 1
    assert info["provides"] == [{"name": "greetings", "path": "modules/greetings.mod"}]
    # The .mod is not a declared output of the compile, so collate needs it
    # listed here to make it a dyndep implicit output.
    assert info["extra_outputs"] == ["modules/greetings.mod"]
    assert info["requires"] == []


def test_scan_info_consumer() -> None:
    """USE becomes a requirement, by logical name."""
    info = scan_info("PROGRAM main\n  USE greetings\nEND PROGRAM\n", "modules")

    assert info["provides"] == []
    assert info["extra_outputs"] == []
    assert info["requires"] == ["greetings"]


def test_scan_info_multiple_modules_in_one_file() -> None:
    """One compile may provide several modules; each gets both entries."""
    src = textwrap.dedent("""\
        MODULE alpha
        END MODULE alpha
        MODULE beta
          USE alpha
        END MODULE beta
    """)
    info = scan_info(src, "modules")

    assert info["provides"] == [
        {"name": "alpha", "path": "modules/alpha.mod"},
        {"name": "beta", "path": "modules/beta.mod"},
    ]
    assert info["extra_outputs"] == ["modules/alpha.mod", "modules/beta.mod"]
    # alpha is provided by this same file, so it is not a requirement.
    assert info["requires"] == []


def test_scan_info_lowercases_names() -> None:
    """Fortran is case-insensitive; gfortran writes lowercase .mod files."""
    info = scan_info("MODULE MyMod\nEND MODULE\n", "modules")

    assert info["provides"] == [{"name": "mymod", "path": "modules/mymod.mod"}]


def test_scan_info_skips_intrinsic_modules() -> None:
    """Intrinsic modules have no .mod file to depend on."""
    info = scan_info("PROGRAM p\n  USE iso_c_binding\nEND PROGRAM\n", "modules")

    assert info["requires"] == []


def test_scan_info_honors_moddir() -> None:
    """Paths follow the environment's module directory."""
    info = scan_info("MODULE m\nEND MODULE\n", "mods/fortran")

    assert info["provides"] == [{"name": "m", "path": "mods/fortran/m.mod"}]


def test_scan_one_cli_writes_the_document(tmp_path: Path) -> None:
    """The --scan-one CLI writes the scan-info JSON collate reads."""
    src = tmp_path / "greetings.f90"
    src.write_text("MODULE greetings\nEND MODULE greetings\n")
    out = tmp_path / "obj" / "greetings.f90.o.fscan.json"

    rc = fortran_scanner_main(
        ["--scan-one", str(src), "--moddir", "modules", "--out", str(out)]
    )

    assert rc == 0
    assert json.loads(out.read_text()) == scan_info(src.read_text(), "modules")


def test_scan_one_cli_reports_a_missing_source(tmp_path: Path, capsys) -> None:
    """An unreadable source fails the scan edge rather than lying about it."""
    rc = fortran_scanner_main(
        [
            "--scan-one",
            str(tmp_path / "nope.f90"),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )

    assert rc == 1
    assert "cannot read" in capsys.readouterr().err


# =============================================================================
# Included files
# =============================================================================


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def test_includes_are_found_beside_the_source_then_on_the_path(tmp_path) -> None:
    """#include and INCLUDE look in the including file's directory first,
    then the -I directories in order."""
    src = _write(
        tmp_path / "src" / "main.F90",
        """\
        program main
        #include "local.h"
          include 'shared.inc'
        end program
        """,
    )
    _write(tmp_path / "src" / "local.h", "! local\n")
    _write(tmp_path / "first" / "shared.inc", "! first\n")
    _write(tmp_path / "second" / "shared.inc", "! second\n")

    text, found = expand_includes(
        src, src.read_text(), [tmp_path / "first", tmp_path / "second"]
    )

    assert found == [tmp_path / "src" / "local.h", tmp_path / "first" / "shared.inc"]
    assert "! local" in text and "! first" in text and "! second" not in text


def test_an_angle_include_skips_the_source_directory(tmp_path) -> None:
    src = _write(tmp_path / "src" / "main.F90", "#include <defs.h>\n")
    _write(tmp_path / "src" / "defs.h", "")
    _write(tmp_path / "inc" / "defs.h", "")

    _text, found = expand_includes(src, src.read_text(), [tmp_path / "inc"])

    assert found == [tmp_path / "inc" / "defs.h"]


def test_nested_includes_resolve_from_their_own_directory(tmp_path) -> None:
    src = _write(tmp_path / "main.F90", '#include "inc/outer.h"\n')
    _write(tmp_path / "inc" / "outer.h", '#include "inner.h"\n')
    _write(tmp_path / "inc" / "inner.h", "")

    _text, found = expand_includes(src, src.read_text(), [])

    assert found == [tmp_path / "inc" / "outer.h", tmp_path / "inc" / "inner.h"]


def test_a_file_is_read_once_even_when_it_includes_itself(tmp_path) -> None:
    src = _write(tmp_path / "main.F90", '#include "loop.h"\n#include "loop.h"\n')
    _write(tmp_path / "loop.h", '#include "loop.h"\nUSE once\n')

    text, found = expand_includes(src, src.read_text(), [])

    assert found == [tmp_path / "loop.h"]
    assert text.count("USE once") == 1


def test_missing_and_commented_out_includes_are_skipped(tmp_path) -> None:
    """A missing include may be one #if leaves out; the compiler reports it
    if it matters. A comment is no include at all."""
    src = _write(
        tmp_path / "main.F90",
        """\
        #include "missing.h"
        ! include 'commented.inc'
        !#include "commented.h"
        """,
    )
    _write(tmp_path / "commented.inc", "")
    _write(tmp_path / "commented.h", "")

    _text, found = expand_includes(src, src.read_text(), [])

    assert found == []


def test_fixed_form_and_uppercase_include_lines(tmp_path) -> None:
    src = _write(tmp_path / "old.F", '      INCLUDE "common.inc"\n')
    _write(tmp_path / "common.inc", "")

    _text, found = expand_includes(src, src.read_text(), [])

    assert found == [tmp_path / "common.inc"]


def test_modules_reached_through_an_include_count(tmp_path) -> None:
    """An interface header that USEs a module needs it, and a module
    declared in an included file is written by this compile."""
    src = _write(
        tmp_path / "main.F90",
        """\
        #include "decl.inc"
        subroutine s
        #include "s.intfb.h"
        end subroutine
        """,
    )
    _write(tmp_path / "decl.inc", "module declared\nend module declared\n")
    _write(
        tmp_path / "s.intfb.h",
        """\
        interface
          subroutine t(x)
            use parkind1, only: jprb
            real(kind=jprb) :: x
          end subroutine t
        end interface
        """,
    )

    text, found = expand_includes(src, src.read_text(), [])
    info = scan_info(text, "modules", included=[p.as_posix() for p in found])

    assert info["provides"] == [{"name": "declared", "path": "modules/declared.mod"}]
    assert info["requires"] == ["parkind1"]
    assert info["extra_deps"] == [p.as_posix() for p in found]


def test_depfile_escapes_what_make_syntax_reserves() -> None:
    assert depfile_text("a b.json", ["x$y.h", "c#d.inc", "plain.h"]) == (
        "a\\ b.json: x$$y.h c\\#d.inc plain.h\n"
    )


def test_scan_one_cli_reports_includes_and_writes_a_depfile(tmp_path) -> None:
    """The included files become the compile's extra_deps and, in the
    depfile, inputs of the scan edge itself."""
    src = _write(tmp_path / "src" / "main.F90", '#include "defs.h"\nend\n')
    header = _write(tmp_path / "inc" / "defs.h", "")
    out = tmp_path / "obj" / "main.F90.o.fscan.json"
    depfile = tmp_path / "obj" / "main.F90.o.fscan.json.d"

    rc = fortran_scanner_main(
        [
            "--scan-one",
            str(src),
            f"-I{tmp_path / 'inc'}",
            "--out",
            str(out),
            "--depfile",
            str(depfile),
        ]
    )

    assert rc == 0
    assert json.loads(out.read_text())["extra_deps"] == [header.as_posix()]
    assert depfile.read_text() == f"{out}: {header.as_posix()}\n"


# =============================================================================
# Scanner wiring (FortranToolchain.after_resolve)
# =============================================================================


def _configured_gfortran() -> GfortranToolchain:
    """A GfortranToolchain with its tools populated, needing no gfortran."""
    tc = GfortranToolchain()
    tc._tools = {
        "fc": GfortranCompiler(),
        "ar": GccArchiver(),
        "link": GfortranLinker(),
    }
    tc._configured = True
    return tc


@pytest.fixture
def fortran_toolchain() -> GfortranToolchain:
    return _configured_gfortran()


def _two_target_project(
    tmp_path: Path, toolchain: GfortranToolchain
) -> tuple[Project, Target, Target]:
    """A library providing a module and a program using it, resolved."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mathmod.f90").write_text("MODULE mathmod\nEND MODULE\n")
    (tmp_path / "src" / "main.f90").write_text(
        "PROGRAM p\n  USE mathmod\nEND PROGRAM\n"
    )

    project = Project("xt", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=toolchain)
    lib = project.StaticLibrary("mathlib", env, sources=["src/mathmod.f90"])
    prog = project.Program("app", env, sources=["src/main.f90"])
    prog.link(lib)
    project.resolve()
    return project, lib, prog


class TestScannerWiring:
    """after_resolve declares one scanner and attaches it per Fortran target."""

    def test_a_scope_per_fortran_target(self, tmp_path, monkeypatch, fortran_toolchain):
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)

        assert set(project._scan_scopes) == {
            ("fortran-modules", "xt::mathlib"),
            ("fortran-modules", "xt::app"),
        }

    def test_each_fortran_compile_is_governed(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)

        scope = project._scan_scopes[("fortran-modules", "xt::mathlib")]
        governed = [n.path.name for n in scope.governed]

        assert governed == [f"mathmod.f90{get_platform().object_suffix}"]
        assert scope.dyndep_rel == "scan/fortran-modules/xt.mathlib.dyndep"

    def test_governed_compile_takes_the_dyndep_in_order_only_position(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)
        scope = project._scan_scopes[("fortran-modules", "xt::app")]
        obj = scope.governed[0]

        assert obj._build_info["dyndep"] == scope.dyndep_rel
        # Order-only: a rewritten dyndep must not by itself dirty the compile.
        assert scope.collate_node in obj.order_only_deps
        assert scope.collate_node not in obj.implicit_deps

    def test_the_compile_restats(self, tmp_path, monkeypatch, fortran_toolchain):
        """gfortran leaves an unchanged .mod alone; restat keeps ninja quiet."""
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)
        scope = project._scan_scopes[("fortran-modules", "xt::mathlib")]

        assert scope.governed[0]._build_info["restat"] is True

    def test_a_dependent_scope_imports_its_dependency_exports(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        """Cross-target modules resolve through the dependency's exports."""
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)
        lib_scope = project._scan_scopes[("fortran-modules", "xt::mathlib")]
        app_scope = project._scan_scopes[("fortran-modules", "xt::app")]

        manifest = json.loads((tmp_path / "build" / app_scope.manifest_rel).read_text())

        assert manifest["imports"] == [lib_scope.exports_rel]
        assert manifest["on_unresolved"] == "warn"

    def test_the_module_directory_is_created(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        """Compilers will not create the module directory themselves."""
        monkeypatch.chdir(tmp_path)
        _two_target_project(tmp_path, fortran_toolchain)

        assert (tmp_path / "build" / "modules").is_dir()

    def test_a_source_with_its_own_env_uses_its_module_directory(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        """add_sources(env=...): that environment compiles the source, so
        its module directory is where the scan points and what is made."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.f90").write_text("PROGRAM p\nEND PROGRAM\n")
        (tmp_path / "src" / "special.f90").write_text("MODULE special\nEND MODULE\n")
        project = Project("own", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=fortran_toolchain)
        special = env.clone()
        special.fc.moddir = "special_mods"
        prog = project.Program("app", env, sources=["src/main.f90"])
        prog.add_sources(["src/special.f90"], env=special)
        project.resolve()

        scope = project._scan_scopes[("fortran-modules", "own::app")]
        moddirs = {
            n.path.name.split(".")[0]: n._build_info["vars"]["FC_MODDIR"]
            for n in scope.collate_node._build_info["sources"]
            if n.path.name.endswith(".fscan.json")
        }
        assert moddirs == {"main": "modules", "special": "special_mods"}
        assert (tmp_path / "build" / "special_mods").is_dir()

    def test_each_fortran_toolchain_scans_its_own_targets(self, tmp_path, monkeypatch):
        """Two environments, each with its own gfortran: each toolchain
        attaches its scanner only to the targets it compiles."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mathmod.f90").write_text("MODULE mathmod\nEND MODULE\n")
        (tmp_path / "src" / "main.f90").write_text(
            "PROGRAM p\n  USE mathmod\nEND PROGRAM\n"
        )
        project = Project("two", root_dir=tmp_path, build_dir="build")
        lib_env = project.Environment(toolchain=_configured_gfortran(), name="lib")
        app_env = project.Environment(toolchain=_configured_gfortran(), name="app")
        lib = project.StaticLibrary("mathlib", lib_env, sources=["src/mathmod.f90"])
        project.Program("app", app_env, sources=["src/main.f90"]).link(lib)
        project.resolve()

        assert len(project._scan_scopes) == 2

    def test_scan_command_carries_the_module_directory_per_edge(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        """One shared scan rule: the moddir rides a per-edge variable."""
        monkeypatch.chdir(tmp_path)
        project, _lib, _prog = _two_target_project(tmp_path, fortran_toolchain)
        scope = project._scan_scopes[("fortran-modules", "xt::mathlib")]
        info_node = next(
            n
            for n in scope.collate_node._build_info["sources"]
            if n.path.name.endswith(".fscan.json")
        )

        assert info_node._build_info["vars"] == {
            "FC_SCAN_INCLUDES": [],
            "FC_MODDIR": "modules",
            "FC_SUBMODULE_FILE": "{ancestor}@{name}.smod",
            "FC_ANCESTOR_FILE": "{name}.smod",
        }

    def test_scan_searches_the_compiles_include_path(
        self, tmp_path, monkeypatch, fortran_toolchain
    ):
        """The scan resolves includes as the compile does: the
        environment's directories, then those a dependency adds."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.F90").write_text("PROGRAM p\nEND PROGRAM\n")
        project = Project("inc", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=fortran_toolchain)
        env.fc.includes.append("include")
        dep = project.HeaderOnlyLibrary("defs", env)
        dep.public.include_dirs.append(tmp_path / "defs")
        project.Program("app", env, sources=["src/main.F90"]).link(dep)
        project.resolve()

        scope = project._scan_scopes[("fortran-modules", "inc::app")]
        info_node = scope.collate_node._build_info["sources"][0]
        tokens = info_node._build_info["vars"]["FC_SCAN_INCLUDES"]

        assert [(t.prefix, Path(t.path)) for t in tokens] == [
            ("-I", Path("include")),
            ("-I", Path("defs")),
        ]
        assert info_node._build_info["deps_style"] == "gcc"

    def test_no_scanner_without_fortran_sources(
        self, tmp_path, monkeypatch, gcc_toolchain
    ):
        """A project with no Fortran gets no Fortran scan wiring."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.c").write_text("int main(void) { return 0; }\n")
        project = Project("c_only", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_toolchain(GfortranToolchain())
        project.Program("app", env, sources=["src/main.c"])
        project.resolve()

        assert project._scan_scopes == {}


# =============================================================================
# find_fortran_toolchain tests
# =============================================================================


def test_find_fortran_toolchain_raises_when_not_found() -> None:
    """find_fortran_toolchain raises RuntimeError when no compiler is found."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="No Fortran toolchain found"):
            find_fortran_toolchain()


def test_find_fortran_toolchain_returns_toolchain() -> None:
    """find_fortran_toolchain prefers gfortran when every compiler is found."""
    with patch("shutil.which", return_value="/usr/bin/gfortran"):
        tc = find_fortran_toolchain()
        assert isinstance(tc, GfortranToolchain)


class TestGfortranConfigure:
    """GfortranCompiler/Linker configure() delegates to _find_tool_config."""

    @pytest.mark.parametrize("cls", [GfortranCompiler, GfortranLinker])
    def test_configure_finds_program(self, cls, tmp_path):
        from pcons.configure.config import Configure, ProgramInfo

        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda *a, **k: ProgramInfo(path=Path("/usr/bin/gfortran"), version="14")
        )
        cfg = cls().configure(config)
        assert cfg is not None
        assert cfg.cmd == str(Path("/usr/bin/gfortran"))

    @pytest.mark.parametrize("cls", [GfortranCompiler, GfortranLinker])
    def test_configure_returns_none_when_missing(self, cls, tmp_path):
        from pcons.configure.config import Configure

        config = Configure(build_dir=tmp_path)
        config.find_program = lambda *a, **k: None  # type: ignore[method-assign]
        assert cls().configure(config) is None

    def test_linker_builders(self):
        """GfortranLinker.builders exposes the GNU link builders."""
        builders = GfortranLinker().builders()
        assert "Program" in builders

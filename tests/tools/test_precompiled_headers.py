# SPDX-License-Identifier: MIT
"""The precompile_headers target option: how each toolchain builds and uses a
precompiled header, reuse between targets, and what is left alone.

examples/102_precompiled_headers builds one on every platform; these cover
what an example can't reach (errors, every toolchain from any host).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pcons.core.errors import PconsTypeError, PconsValueError
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.subst import PathToken
from pcons.core.target import Target
from pcons.generators.compile_commands import CompileCommandsGenerator
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.toolchains.llvm import (
    ClangCCompiler,
    ClangCxxCompiler,
    LlvmArchiver,
    LlvmLinker,
    LlvmToolchain,
)


@pytest.fixture
def llvm_toolchain() -> LlvmToolchain:
    toolchain = LlvmToolchain()
    toolchain._tools = {
        "cc": ClangCCompiler(),
        "cxx": ClangCxxCompiler(),
        "ar": LlvmArchiver(),
        "link": LlvmLinker(),
    }
    return toolchain


@pytest.fixture
def project(tmp_path: Path) -> Project:
    (tmp_path / "src").mkdir()
    for name in ("main.cpp", "util.cpp", "legacy.c", "common.h", "test.cpp"):
        (tmp_path / "src" / name).write_text("")
    return Project("pch", root_dir=tmp_path, build_dir=tmp_path / "build")


def _app(project: Project, toolchain: Any, headers: object) -> Target:
    env = project.Environment(toolchain=toolchain)
    app = project.Program(
        "app", env, sources=["src/main.cpp", "src/util.cpp", "src/legacy.c"]
    )
    app.set_option("precompile_headers", headers)
    return app


def _object(target: Target, name: str) -> FileNode:
    return next(n for n in target.intermediate_nodes if n.path.name.startswith(name))


def _pch_flag(node: FileNode) -> PathToken | None:
    """The flag that points *node*'s compile at a precompiled header."""
    flags = node._build_info.get("extra_command_flags") or []
    return next((f for f in flags if isinstance(f, PathToken)), None)


def _pch_dep(node: FileNode) -> FileNode:
    [pch] = [d for d in node.implicit_deps if "_pch" in d.path.name]
    assert isinstance(pch, FileNode)
    return pch


class TestGnuAndClang:
    def test_each_language_compiles_with_its_own_header(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, ["src/common.h", "<map>"])
        project.resolve()

        cxx, c = (
            _pch_flag(_object(app, "main.cpp")),
            _pch_flag(_object(app, "legacy.c")),
        )
        assert cxx is not None and c is not None
        assert cxx.prefix == "-include" and str(cxx.path).endswith("cxx_pch.hxx")
        assert c.prefix == "-include" and str(c.path).endswith("c_pch.h")
        assert (
            "-Winvalid-pch"
            in _object(app, "main.cpp")._build_info["extra_command_flags"]
        )
        assert _pch_dep(_object(app, "main.cpp")).path.name == "cxx_pch.hxx.gch"
        assert _pch_dep(_object(app, "legacy.c")).path.name == "c_pch.h.gch"

    def test_the_header_compiles_with_the_sources_flags(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, ["src/common.h"])
        app.env.cxx.defines.append("FEATURE=1")
        app.private.include_dirs.append("src")
        project.resolve()

        pch = _pch_dep(_object(app, "main.cpp"))
        command = [str(t) for t in pch._build_info["command"]]
        assert ["-x", "c++-header"] == command[
            command.index("-x") : command.index("-x") + 2
        ]
        assert "-DFEATURE=1" in command
        assert any(t.endswith("src") and t.startswith("-I") for t in command)

    def test_the_wrapper_includes_each_header(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        _app(project, gcc_toolchain, ["src/common.h", "<map>"])
        project.resolve()

        wrapper = project.root_dir / "build/obj.app/pch/cxx_pch.hxx"
        text = wrapper.read_text()
        assert f'#include "{(project.root_dir / "src/common.h").as_posix()}"' in text
        assert "#include <map>" in text

    def test_clang_names_its_header_pch_and_needs_no_warning(
        self, project: Project, llvm_toolchain: LlvmToolchain
    ) -> None:
        app = _app(project, llvm_toolchain, ["src/common.h"])
        project.resolve()

        main = _object(app, "main.cpp")
        assert _pch_dep(main).path.name == "cxx_pch.hxx.pch"
        assert "-Winvalid-pch" not in main._build_info["extra_command_flags"]

    def test_headers_for_one_language_leave_the_other_alone(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, {"cxx": ["<vector>"]})
        project.resolve()

        assert _pch_flag(_object(app, "main.cpp")) is not None
        assert _pch_flag(_object(app, "legacy.c")) is None

    def test_a_source_with_its_own_environment_is_left_alone(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, ["src/common.h"])
        other = app.env.clone()
        other.cxx.defines.append("ODD=1")
        app.add_sources(["src/util.cpp"], env=other)
        project.resolve()

        assert _pch_flag(_object(app, "main.cpp")) is not None
        assert _pch_flag(_object(app, "util.cpp")) is None

    def test_a_build_variable_turns_them_off(
        self, project: Project, gcc_toolchain: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PCONS_PRECOMPILE_HEADERS", "0")
        app = _app(project, gcc_toolchain, ["src/common.h"])
        project.resolve()

        assert all(_pch_flag(n) is None for n in app.intermediate_nodes)
        assert not (project.root_dir / "build/obj.app/pch").exists()


class TestMsvcFamily:
    def test_a_source_compiled_with_yc_makes_it_and_is_linked(
        self, project: Project, clang_cl_toolchain: Any
    ) -> None:
        app = _app(project, clang_cl_toolchain, ["src/common.h"])
        project.resolve()

        made_by = _object(app, "cxx_pch.cxx")
        outputs = made_by._build_info["outputs"]
        assert outputs["pch"]["path"].name == "cxx_pch.pch"
        assert outputs["pch"]["implicit"]
        assert made_by in app.intermediate_nodes

    def test_each_compile_uses_it_by_absolute_path(
        self, project: Project, clang_cl_toolchain: Any
    ) -> None:
        app = _app(project, clang_cl_toolchain, ["src/common.h"])
        project.resolve()

        flags = _object(app, "main.cpp")._build_info["extra_command_flags"]
        assert [f.prefix for f in flags if isinstance(f, PathToken)] == [
            "/Yu",
            "/FI",
            "/Fp",
        ]
        assert all(
            isinstance(f, PathToken) and f.path_type == "absolute" for f in flags
        )
        assert _pch_dep(_object(app, "main.cpp")).path.name == "cxx_pch.pch"

    def test_the_step_making_it_does_not_depend_on_it(
        self, project: Project, clang_cl_toolchain: Any
    ) -> None:
        """/Fp names the step's own second output, which is not an input."""
        app = _app(project, clang_cl_toolchain, ["src/common.h"])
        project.resolve()

        made_by = _object(app, "cxx_pch.cxx")
        assert not [d for d in made_by.implicit_deps if d.path.suffix == ".pch"]

    def test_a_target_reusing_it_links_the_object_too(
        self, project: Project, clang_cl_toolchain: Any
    ) -> None:
        app = _app(project, clang_cl_toolchain, {"cxx": ["src/common.h"]})
        tests = project.Program("tests", app.env, sources=["src/test.cpp"])
        tests.set_option("precompile_headers", app)
        project.resolve()

        assert _object(app, "cxx_pch.cxx") in tests.intermediate_nodes


class TestReuse:
    def test_a_target_compiling_the_same_way_uses_the_others(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, ["src/common.h"])
        tests = project.Program("tests", app.env, sources=["src/test.cpp"])
        tests.set_option("precompile_headers", app)
        project.resolve()

        assert _pch_dep(_object(tests, "test.cpp")) is _pch_dep(
            _object(app, "main.cpp")
        )
        assert not (project.root_dir / "build/obj.tests/pch").exists()

    def test_it_may_be_declared_before_the_target_it_reuses(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        env = project.Environment(toolchain=gcc_toolchain)
        tests = project.Program("tests", env, sources=["src/test.cpp"])
        app = project.Program("app", env, sources=["src/main.cpp"])
        app.set_option("precompile_headers", ["src/common.h"])
        tests.set_option("precompile_headers", app)
        project.resolve()

        assert _pch_dep(_object(tests, "test.cpp")) is _pch_dep(
            _object(app, "main.cpp")
        )

    def test_different_defines_are_refused(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        app = _app(project, gcc_toolchain, ["src/common.h"])
        tests = project.Program("tests", app.env, sources=["src/test.cpp"])
        tests.private.defines.append("TESTING=1")
        tests.set_option("precompile_headers", app)

        with pytest.raises(
            PconsValueError,
            match=r"(?s)reuses the precompiled C\+\+ header of 'app'.*defines: TESTING=1 here, \(none\) in 'app'",
        ):
            project.resolve()

    def test_a_target_with_none_to_reuse_is_refused(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        env = project.Environment(toolchain=gcc_toolchain)
        app = project.Program("app", env, sources=["src/main.cpp"])
        tests = project.Program("tests", env, sources=["src/test.cpp"])
        tests.set_option("precompile_headers", app)

        with pytest.raises(PconsValueError, match="'app', which has none"):
            project.resolve()

    def test_a_cycle_is_refused(self, project: Project, gcc_toolchain: Any) -> None:
        env = project.Environment(toolchain=gcc_toolchain)
        app = project.Program("app", env, sources=["src/main.cpp"])
        tests = project.Program("tests", env, sources=["src/test.cpp"])
        app.set_option("precompile_headers", tests)
        tests.set_option("precompile_headers", app)

        with pytest.raises(PconsValueError, match="in a cycle"):
            project.resolve()


class TestOptionValue:
    def test_a_value_of_another_type_is_refused(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        _app(project, gcc_toolchain, 42)
        with pytest.raises(PconsTypeError, match="got 42"):
            project.resolve()

    def test_an_unknown_language_is_refused(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        _app(project, gcc_toolchain, {"fortran": ["x.h"]})
        with pytest.raises(PconsValueError, match=r"unknown languages \['fortran'\]"):
            project.resolve()


class TestGenerators:
    def test_compile_commands_includes_the_headers_not_the_precompiled_one(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        _app(project, gcc_toolchain, ["src/common.h", "<map>"])
        project.resolve()
        CompileCommandsGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        import json

        entries = json.loads(
            (project.root_dir / "build/compile_commands.json").read_text()
        )
        [main] = [e for e in entries if e["file"].endswith("main.cpp")]
        assert f"-include{project.root_dir / 'src/common.h'}" in main["command"]
        assert "-includemap" in main["command"]
        assert "cxx_pch" not in main["command"]

    def test_make_builds_the_precompiled_header(
        self, project: Project, gcc_toolchain: Any
    ) -> None:
        _app(project, gcc_toolchain, ["src/common.h"])
        project.resolve()
        MakefileGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        makefile = (project.root_dir / "build/Makefile").read_text()
        assert "obj.app/pch/cxx_pch.hxx.gch:" in makefile

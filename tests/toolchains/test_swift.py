# SPDX-License-Identifier: MIT
"""Tests for the Swift toolchain and the group_sources (whole-module) mechanism.

These construct the build graph without invoking swiftc (tool configs are
populated manually), so they run on machines without Swift installed. The
example tests (46/47) exercise the real end-to-end build where swiftc is
available.
"""

from __future__ import annotations

import pytest

import pcons.toolchains  # noqa: F401 — populate the registry
from pcons import Project
from pcons.core.subst import PathToken
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.swift import (
    SwiftCompiler,
    SwiftLinker,
    SwiftToolchain,
    module_name_for,
)
from pcons.tools.toolchain import toolchain_registry


@pytest.fixture
def swift_toolchain():
    """A pre-configured Swift toolchain (no swiftc needed for graph tests)."""
    toolchain = SwiftToolchain()
    toolchain._tools = {
        "swiftc": SwiftCompiler(),
        "ar": GccArchiver(),
        "link": SwiftLinker(),
    }
    toolchain._configured = True
    return toolchain


@pytest.fixture
def swift_project(tmp_path, swift_toolchain):
    (tmp_path / "src").mkdir()
    for name in ("main.swift", "extra.swift"):
        (tmp_path / "src" / name).write_text("// swift\n")
    project = Project("swifttest", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=swift_toolchain)
    return project, env


class TestModuleName:
    def test_plain_name_unchanged(self) -> None:
        assert module_name_for("Geometry") == "Geometry"

    def test_hyphens_become_underscores(self) -> None:
        assert module_name_for("my-lib") == "my_lib"

    def test_leading_digit_prefixed(self) -> None:
        assert module_name_for("3d-engine") == "_3d_engine"


class TestRegistry:
    def test_swift_resolvable_by_name(self) -> None:
        names = toolchain_registry.known_names()
        assert "swift" in names
        assert "swiftc" in names


class TestGroupedCompile:
    def test_program_sources_compile_as_one_node(self, swift_project) -> None:
        project, env = swift_project
        prog = project.Program(
            "hello", env, sources=["src/main.swift", "src/extra.swift"]
        )
        project.resolve()

        # Whole-module: both sources feed ONE object node.
        assert len(prog.intermediate_nodes) == 1
        obj = prog.intermediate_nodes[0]
        info = obj._build_info
        assert len(info["sources"]) == 2
        assert info["language"] == "swift"
        assert info["tool"] == "swiftc"
        assert info["depfile"] is not None

    def test_module_vars_and_outputs(self, swift_project) -> None:
        project, env = swift_project
        prog = project.Program("hello", env, sources=["src/main.swift"])
        project.resolve()

        info = prog.intermediate_nodes[0]._build_info
        assert info["vars"]["MODULE_NAME"] == "hello"
        assert isinstance(info["vars"]["MODULE_PATH"], PathToken)
        # Programs may have top-level code: no -parse-as-library.
        assert "-parse-as-library" not in info["vars"]["MODULE_FLAGS"]
        # The .swiftmodule is declared as an implicit output.
        outputs = info["outputs"]
        assert outputs["swiftmodule"]["implicit"] is True
        assert str(outputs["swiftmodule"]["path"]).endswith("hello.swiftmodule")

    def test_library_parse_as_library_and_propagation(self, swift_project) -> None:
        project, env = swift_project
        lib = project.StaticLibrary("Geometry", env, sources=["src/extra.swift"])
        project.resolve()

        info = lib.intermediate_nodes[0]._build_info
        assert "-parse-as-library" in info["vars"]["MODULE_FLAGS"]
        # The shared swiftmodules/ dir propagates to dependents.
        assert any(str(p).endswith("swiftmodules") for p in lib.public.include_dirs)

    def test_dependent_compile_orders_after_dep_module(
        self, tmp_path, swift_toolchain
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.swift").write_text("// lib\n")
        (tmp_path / "src" / "main.swift").write_text("// main\n")
        project = Project("dep_order", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=swift_toolchain)
        lib = project.StaticLibrary("Geometry", env, sources=["src/lib.swift"])
        app = project.Program("app", env, sources=["src/main.swift"])
        app.link_private(lib)
        project.resolve()

        dep_module = lib._builder_data["swiftmodule_node"]
        app_compile = app.intermediate_nodes[0]
        assert dep_module in app_compile.implicit_deps

    def test_grouped_nodes_not_shared_between_targets(self, swift_project) -> None:
        """Two targets over the same sources get separate module nodes."""
        project, env = swift_project
        a = project.Program("prog_a", env, sources=["src/main.swift"])
        b = project.Program("prog_b", env, sources=["src/main.swift"])
        project.resolve()

        assert a.intermediate_nodes[0] is not b.intermediate_nodes[0]
        assert a.intermediate_nodes[0]._build_info["vars"]["MODULE_NAME"] == "prog_a"
        assert b.intermediate_nodes[0]._build_info["vars"]["MODULE_NAME"] == "prog_b"


class TestCxxInterop:
    def test_set_cxx_interop_applies_flags(self, swift_project) -> None:
        project, env = swift_project
        env.swiftc.set_cxx_interop("c++20")
        flags = list(env.swiftc.flags)
        assert "-cxx-interoperability-mode=default" in flags
        assert "-Xcc" in flags
        assert "-std=c++20" in flags

    def test_set_cxx_interop_numeric_standard(self, swift_toolchain) -> None:
        preset = swift_toolchain.make_cxx_interop_preset(17)
        flags = preset.contributions[0].flags
        assert "-std=c++17" in flags

    def test_interop_header_emitted_for_libraries(self, swift_project) -> None:
        project, env = swift_project
        env.swiftc.interop_header = True
        lib = project.StaticLibrary("Analyzer", env, sources=["src/extra.swift"])
        project.resolve()

        info = lib.intermediate_nodes[0]._build_info
        header_flags = info["vars"]["HEADER_FLAGS"]
        assert "-emit-clang-header-path" in header_flags
        assert info["outputs"]["clang_header"]["implicit"] is True
        # The header rides output_nodes so consumers' compiles wait on it.
        assert any(str(n.path).endswith("Analyzer-Swift.h") for n in lib.output_nodes)

    def test_no_interop_header_for_programs(self, swift_project) -> None:
        project, env = swift_project
        env.swiftc.interop_header = True
        prog = project.Program("tool", env, sources=["src/main.swift"])
        project.resolve()

        info = prog.intermediate_nodes[0]._build_info
        assert info["vars"]["HEADER_FLAGS"] == []
        assert "clang_header" not in info["outputs"]


class TestClangModuleMap:
    def test_generates_modulemap(self, swift_project) -> None:
        from pcons.toolchains.swift import clang_module_map

        project, env = swift_project
        (project.root_dir / "include").mkdir()
        (project.root_dir / "include" / "clib.h").write_text("void f(void);\n")

        map_dir = clang_module_map(project, "CLib", ["include/clib.h"])

        content = (map_dir / "module.modulemap").read_text()
        assert content.startswith("module CLib {")
        assert "export *" in content
        # Header paths are absolute so the map works from any cwd.
        assert str(project.root_dir / "include" / "clib.h") in content

    def test_write_if_changed(self, swift_project) -> None:
        from pcons.toolchains.swift import clang_module_map

        project, env = swift_project
        (project.root_dir / "clib.h").write_text("void f(void);\n")

        map_file = clang_module_map(project, "CLib", ["clib.h"]) / "module.modulemap"
        first_mtime = map_file.stat().st_mtime_ns
        map_file2 = clang_module_map(project, "CLib", ["clib.h"]) / "module.modulemap"
        assert map_file2.stat().st_mtime_ns == first_mtime  # untouched


class TestLibraryEvolution:
    def test_library_evolution_flags_and_interface(self, swift_project) -> None:
        project, env = swift_project
        env.swiftc.library_evolution = True
        lib = project.StaticLibrary("Geometry", env, sources=["src/extra.swift"])
        project.resolve()

        info = lib.intermediate_nodes[0]._build_info
        assert "-enable-library-evolution" in info["vars"]["MODULE_FLAGS"]
        outputs = info["outputs"]
        assert outputs["swiftinterface"]["implicit"] is True
        assert str(outputs["swiftinterface"]["path"]).endswith(
            "Geometry.swiftinterface"
        )

    def test_programs_unaffected(self, swift_project) -> None:
        project, env = swift_project
        env.swiftc.library_evolution = True
        prog = project.Program("tool", env, sources=["src/main.swift"])
        project.resolve()

        info = prog.intermediate_nodes[0]._build_info
        assert "-enable-library-evolution" not in info["vars"]["MODULE_FLAGS"]


class TestCrossTarget:
    def test_ios_target_contributions(self, swift_toolchain) -> None:
        from types import SimpleNamespace

        cross = SimpleNamespace(
            name="ios-arm64",
            arch="arm64",
            triple="arm64-apple-ios15.0",
            sysroot="/fake/iPhoneOS.sdk",
            extra_compile_flags=(),
            extra_link_flags=(),
            env_vars=None,
        )
        contribs = swift_toolchain._target_contributions(cross)
        swiftc = [c for c in contribs if c.tool == "swiftc"]
        assert swiftc, "expected a swiftc contribution"
        flags = swiftc[0].flags
        assert "-target" in flags and "arm64-apple-ios15.0" in flags
        assert "-sdk" in flags and "/fake/iPhoneOS.sdk" in flags
        # swiftc drives the link: no clang-style -arch link contribution.
        link = [c for c in contribs if c.tool == "link"]
        assert all("-arch" not in c.flags for c in link)
        assert any("-target" in c.flags for c in link)


class TestWindows:
    def test_static_library_gets_static_flag(self, swift_project, monkeypatch) -> None:
        """On Windows, static libs need -static or importers emit __imp_ refs."""
        import pcons.toolchains.swift as swift_mod

        class FakeWindows:
            is_windows = True
            is_macos = False
            object_suffix = ".obj"
            exe_suffix = ".exe"
            shared_lib_suffix = ".dll"

        monkeypatch.setattr(swift_mod, "get_platform", lambda: FakeWindows())
        project, env = swift_project
        lib = project.StaticLibrary("Geometry", env, sources=["src/extra.swift"])
        project.resolve()

        assert (
            "-static" in lib.intermediate_nodes[0]._build_info["vars"]["MODULE_FLAGS"]
        )

    def test_archiver_prefers_llvm_ar_on_windows(self, monkeypatch) -> None:
        """llvm-ar ships with the swift.org Windows toolchain; ar doesn't."""
        import pcons.toolchains.swift as swift_mod
        from pcons.toolchains.swift import SwiftArchiver

        class FakeWindows:
            is_windows = True
            is_macos = False

        monkeypatch.setattr(swift_mod, "get_platform", lambda: FakeWindows())
        assert SwiftArchiver().default_vars()["cmd"] == "llvm-ar"


class TestRuntimeInjection:
    def test_swift_links_cxx_objects(self, swift_toolchain) -> None:
        libs = swift_toolchain.get_runtime_libs("swift", {"swift", "cxx"})
        import sys

        if sys.platform == "darwin":
            assert libs == []  # libc++ comes with the Swift runtime
        else:
            assert "stdc++" in libs

    def test_cxx_links_swift_objects(self, swift_toolchain) -> None:
        assert "swiftCore" in swift_toolchain.get_runtime_libs("cxx", {"swift"})

    def test_no_injection_for_pure_swift(self, swift_toolchain) -> None:
        assert swift_toolchain.get_runtime_libs("swift", {"swift"}) == []


class TestPresets:
    def test_variant_presets_target_swiftc(self, swift_toolchain) -> None:
        preset = swift_toolchain.make_variant_preset("debug")
        contributions = preset.contributions
        assert any(c.tool == "swiftc" and "-Onone" in c.flags for c in contributions)

    def test_werror_preset(self, swift_toolchain) -> None:
        preset = swift_toolchain.make_feature_preset("werror")
        assert preset is not None
        assert any("-warnings-as-errors" in c.flags for c in preset.contributions)

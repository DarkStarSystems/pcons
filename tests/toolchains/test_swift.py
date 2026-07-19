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


class TestPresets:
    def test_variant_presets_target_swiftc(self, swift_toolchain) -> None:
        preset = swift_toolchain.make_variant_preset("debug")
        contributions = preset.contributions
        assert any(c.tool == "swiftc" and "-Onone" in c.flags for c in contributions)

    def test_werror_preset(self, swift_toolchain) -> None:
        preset = swift_toolchain.make_feature_preset("werror")
        assert preset is not None
        assert any("-warnings-as-errors" in c.flags for c in preset.contributions)

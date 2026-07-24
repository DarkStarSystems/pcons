# SPDX-License-Identifier: MIT
"""Tests for the conventional tool-selection environment variables.

CC/CXX/FC/AR/SWIFTC/CUDACXX/RC are authoritative (autoconf/CMake/Meson
convention): when set they select the tool's command; a value that can't
be found is an error, never a silent fall-through. $CXX/$CC also steer
"c" toolchain auto-detection to the named compiler's family.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pcons.configure.compiler_id as compiler_id
from pcons.core.environment import Environment
from pcons.core.errors import ToolNotFoundError
from pcons.tools.tool import resolve_env_cmd_override
from pcons.tools.toolchain import toolchain_registry


def _registry_toolchain(name: str):
    entry = toolchain_registry.get(name)
    assert entry is not None
    return entry.create_toolchain()


def _fake_compiler(tmp_path: Path, name: str) -> Path:
    """An existing file to point an env var at (family: unclassifiable)."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


class TestResolveEnvCmdOverride:
    def test_unset_and_empty_are_none(self, monkeypatch):
        monkeypatch.delenv("CXX", raising=False)
        assert resolve_env_cmd_override("CXX") is None
        monkeypatch.setenv("CXX", "  ")
        assert resolve_env_cmd_override("CXX") is None
        assert resolve_env_cmd_override(None) is None

    def test_absolute_path(self, monkeypatch, tmp_path):
        fake = _fake_compiler(tmp_path, "mycxx")
        monkeypatch.setenv("CXX", str(fake))
        assert resolve_env_cmd_override("CXX") == str(fake)

    @pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX exec bits")
    def test_bare_name_resolved_on_path(self, monkeypatch, tmp_path):
        _fake_compiler(tmp_path, "weird-cxx")
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setenv("CXX", "weird-cxx")
        resolved = resolve_env_cmd_override("CXX")
        assert resolved is not None and Path(resolved).parent == tmp_path

    def test_missing_value_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CXX", str(tmp_path / "no-such-compiler"))
        with pytest.raises(ToolNotFoundError, match=r"\$CXX"):
            resolve_env_cmd_override("CXX")
        monkeypatch.setenv("CXX", "definitely-not-a-real-compiler-xyz")
        with pytest.raises(ToolNotFoundError, match="not found on PATH"):
            resolve_env_cmd_override("CXX")


class TestEnvVarDeclarations:
    """One conventional var per tool that has a real detection step."""

    def test_declared_vars(self):
        from pcons.toolchains.clang_cl import ClangClCCompiler, ClangClCxxCompiler
        from pcons.toolchains.gcc import (
            GccArchiver,
            GccCCompiler,
            GccCxxCompiler,
            GccLinker,
        )
        from pcons.toolchains.gfortran import GfortranCompiler
        from pcons.toolchains.llvm import ClangCCompiler, ClangCxxCompiler
        from pcons.toolchains.msvc import (
            MsvcCompiler,
            MsvcCxxCompiler,
            MsvcResourceCompiler,
        )
        from pcons.toolchains.swift import SwiftCompiler
        from pcons.tools.cuda import CudaCompiler

        assert GccCCompiler.env_var == "CC"
        assert GccCxxCompiler.env_var == "CXX"
        assert GccArchiver.env_var == "AR"
        assert GccLinker.env_var == "CC"  # links through the C driver
        assert ClangCCompiler.env_var == "CC"
        assert ClangCxxCompiler.env_var == "CXX"
        assert GfortranCompiler.env_var == "FC"
        assert SwiftCompiler.env_var == "SWIFTC"
        assert CudaCompiler.env_var == "CUDACXX"
        assert MsvcCompiler.env_var == "CC"
        assert MsvcCxxCompiler.env_var == "CXX"
        assert MsvcResourceCompiler.env_var == "RC"
        assert ClangClCCompiler.env_var == "CC"
        assert ClangClCxxCompiler.env_var == "CXX"

    def test_sdk_toolchains_ignore_env_vars(self):
        # emscripten/wasi own their commands (SDK), so they override
        # setup_presets without calling the env-var base implementation.
        from pcons.toolchains.emscripten import EmscriptenToolchain
        from pcons.toolchains.wasi import WasiToolchain

        assert "setup_presets" in WasiToolchain.__dict__
        assert "setup_presets" in EmscriptenToolchain.__dict__


class TestOverrideApplication:
    def test_cxx_override_applied_and_attributed(
        self, test_project, monkeypatch, tmp_path
    ):  # noqa: F811
        fake = _fake_compiler(tmp_path, "my-g++")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)

        env = Environment(toolchain=_registry_toolchain("gcc"))
        assert env.cxx.cmd == str(fake)
        rows = [r for r in env.cxx.explain().rows if r.token == str(fake)]
        assert rows and rows[0].source == "$CXX"

    def test_cc_override_flows_to_linker(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        fake = _fake_compiler(tmp_path, "my-gcc")
        monkeypatch.setenv("CC", str(fake))
        monkeypatch.delenv("CXX", raising=False)

        env = Environment(toolchain=_registry_toolchain("gcc"))
        assert env.cc.cmd == str(fake)
        assert env.link.cmd == str(fake)  # links through the C driver

    def test_ar_override(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        fake = _fake_compiler(tmp_path, "my-ar")
        monkeypatch.setenv("AR", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.delenv("CXX", raising=False)

        env = Environment(toolchain=_registry_toolchain("llvm"))
        assert env.ar.cmd == str(fake)

    def test_script_assignment_wins_over_env(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        fake = _fake_compiler(tmp_path, "my-g++")
        monkeypatch.setenv("CXX", str(fake))
        env = Environment(toolchain=_registry_toolchain("gcc"))
        env.cxx.cmd = "explicit-compiler"
        assert env.cxx.cmd == "explicit-compiler"

    def test_missing_compiler_raises_at_env_creation(
        self, test_project, monkeypatch, tmp_path
    ):  # noqa: F811
        monkeypatch.setenv("CXX", str(tmp_path / "gone"))
        with pytest.raises(ToolNotFoundError, match=r"\$CXX"):
            Environment(toolchain=_registry_toolchain("gcc"))


class TestFamilyValidation:
    def test_mismatched_family_raises(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        fake = _fake_compiler(tmp_path, "some-g++")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: "gcc")

        with pytest.raises(ValueError, match="gcc-family.*'llvm'"):
            Environment(toolchain=_registry_toolchain("llvm"))

    def test_matching_family_accepted(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        fake = _fake_compiler(tmp_path, "clang++-x")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: "llvm")

        env = Environment(toolchain=_registry_toolchain("llvm"))
        assert env.cxx.cmd == str(fake)

    def test_unclassifiable_accepted(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        # Compiler wrappers can't be classified; accept them.
        fake = _fake_compiler(tmp_path, "ccache-wrapper")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: None)

        env = Environment(toolchain=_registry_toolchain("gcc"))
        assert env.cxx.cmd == str(fake)


class TestOpenmpSniffUsesOverride:
    @pytest.mark.skipif(
        __import__("sys").platform == "win32", reason="POSIX shell script"
    )
    def test_apple_clang_sniff_honors_cxx_override(
        self, test_project, monkeypatch, tmp_path
    ):  # noqa: F811
        # Regression: with CXX pointing at real GCC on macOS, the openmp
        # preset must sniff the override, not the default g++ (Apple shim).
        import sys

        fake = tmp_path / "g++-fake"
        fake.write_text(
            '#!/bin/sh\necho "g++-15 (Homebrew GCC 15.1.0) 15.1.0"\n'
            'echo "Copyright (C) 2025 Free Software Foundation, Inc."\n'
        )
        fake.chmod(0o755)
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")

        toolchain = _registry_toolchain("gcc")
        assert toolchain._compiler_is_apple_clang() is False


class TestAutoDetectSteering:
    def test_cxx_steers_family(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        from pcons.toolchains import find_c_toolchain

        fake = _fake_compiler(tmp_path, "g++-99")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: "gcc")

        toolchain = find_c_toolchain()
        assert toolchain.name == "gcc"

    def test_unclassifiable_falls_through(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        from pcons.toolchains import find_c_toolchain

        fake = _fake_compiler(tmp_path, "mystery-cxx")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: None)

        # Falls through to normal detection; the override still lands.
        toolchain = find_c_toolchain()
        env = Environment(toolchain=toolchain)
        assert env.cxx.cmd == str(fake)

    def test_explicit_prefer_list_respected(self, test_project, monkeypatch, tmp_path):  # noqa: F811
        from pcons.toolchains import find_c_toolchain

        fake = _fake_compiler(tmp_path, "g++-99")
        monkeypatch.setenv("CXX", str(fake))
        monkeypatch.setattr(compiler_id, "compiler_family", lambda p: "gcc")

        toolchain = find_c_toolchain(prefer=["llvm", "gcc"])
        assert toolchain.name == "llvm"


class TestCompilerFamily:
    def test_basename_fast_paths(self):
        assert compiler_id.compiler_family.__wrapped__("/x/clang-cl.exe") == "clang-cl"
        assert compiler_id.compiler_family.__wrapped__("/x/cl.exe") == "msvc"

    def test_version_output_wins_over_name(self, monkeypatch):
        # macOS g++ shim: named g++ but actually Apple clang.
        monkeypatch.setattr(
            compiler_id, "_version_output", lambda p: "Apple clang version 17.0.0"
        )
        assert compiler_id.compiler_family.__wrapped__("/usr/bin/g++") == "llvm"

    def test_gcc_by_version_output(self, monkeypatch):
        monkeypatch.setattr(
            compiler_id,
            "_version_output",
            lambda p: "g++-15 (Homebrew GCC 15.1.0) 15.1.0\n"
            "Copyright (C) 2025 Free Software Foundation, Inc.",
        )
        assert compiler_id.compiler_family.__wrapped__("/x/g++-15") == "gcc"

    def test_unknown_falls_back_to_basename(self, monkeypatch):
        monkeypatch.setattr(compiler_id, "_version_output", lambda p: "")
        assert compiler_id.compiler_family.__wrapped__("/x/clang-19") == "llvm"
        assert compiler_id.compiler_family.__wrapped__("/x/gcc-13") == "gcc"
        assert compiler_id.compiler_family.__wrapped__("/x/mystery") is None

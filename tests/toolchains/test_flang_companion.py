# SPDX-License-Identifier: MIT
"""Beside flang, "c" takes the C/C++ compilers from flang's own LLVM."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pcons import Project
from pcons.toolchains import flang as flang_mod
from pcons.toolchains.flang import FlangToolchain, _own_llvm_compilers
from pcons.toolchains.llvm import ClangCCompiler, LlvmToolchain


def _program(directory: Path, name: str) -> Path:
    """A file the platform runs as *name* from *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f"{name}.exe" if sys.platform == "win32" else name)
    path.write_text("")
    path.chmod(0o755)
    return path


def _no_resource_dir(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _fresh_lookup():
    _own_llvm_compilers.cache_clear()
    yield
    _own_llvm_compilers.cache_clear()


def test_compilers_beside_flangs_real_binary(tmp_path) -> None:
    """apt.llvm.org, LLVM's own builds and conda-forge keep clang next to
    flang, whatever directory the flang on PATH links from."""
    bin_dir = tmp_path / "llvm-21" / "bin"
    flang = _program(bin_dir, "flang")
    clang = _program(bin_dir, "clang")
    clangxx = _program(bin_dir, "clang++")

    with patch.object(flang_mod.subprocess, "run", _no_resource_dir):
        found = _own_llvm_compilers(str(flang), False)

    assert found is not None
    assert Path(found["CC"]) == clang
    assert Path(found["CXX"]) == clangxx


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_compilers_of_the_llvm_flangs_headers_come_from(tmp_path) -> None:
    """Homebrew packages flang apart from LLVM; its resource directory's
    include links into the LLVM it was built with."""
    flang = _program(tmp_path / "Cellar" / "flang" / "bin", "flang")
    llvm = tmp_path / "opt" / "llvm"
    (llvm / "lib" / "clang" / "23" / "include").mkdir(parents=True)
    clang = _program(llvm / "bin", "clang")
    _program(llvm / "bin", "clang++")
    resource = tmp_path / "Cellar" / "flang" / "lib" / "clang" / "23"
    resource.mkdir(parents=True)
    (resource / "include").symlink_to(
        os.path.relpath(llvm / "lib" / "clang" / "23" / "include", resource)
    )

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{resource}\n", stderr="")

    with patch.object(flang_mod.subprocess, "run", run):
        found = _own_llvm_compilers(str(flang), False)

    assert found is not None
    assert Path(found["CC"]) == clang


def test_none_when_flangs_llvm_has_no_clang(tmp_path) -> None:
    flang = _program(tmp_path / "bin", "flang")
    with patch.object(flang_mod.subprocess, "run", _no_resource_dir):
        assert _own_llvm_compilers(str(flang), False) is None


def _flang_beside(tmp_path: Path, monkeypatch) -> FlangToolchain:
    """A flang toolchain whose own LLVM has clang and clang++."""
    bin_dir = tmp_path / "llvm" / "bin"
    flang = _program(bin_dir, "flang")
    _program(bin_dir, "clang")
    _program(bin_dir, "clang++")
    monkeypatch.setattr(flang_mod.subprocess, "run", _no_resource_dir)
    monkeypatch.setenv("FC", str(flang))
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.delenv("CXX", raising=False)
    return FlangToolchain()


@pytest.mark.skipif(sys.platform == "win32", reason="the MSVC ABI takes clang-cl")
def test_c_beside_flang_is_its_own_clang(tmp_path, monkeypatch) -> None:
    flang = _flang_beside(tmp_path, monkeypatch)

    companion = flang.companion_toolchain("c")

    assert isinstance(companion, LlvmToolchain)
    assert Path(companion._bound_commands["CC"]).stem == "clang"
    assert flang.companion_toolchain("fortran") is None


@pytest.mark.parametrize("var", ["CC", "CXX"])
def test_the_users_cc_or_cxx_decides_instead(tmp_path, monkeypatch, var) -> None:
    flang = _flang_beside(tmp_path, monkeypatch)
    monkeypatch.setenv(var, "/usr/bin/cc")

    assert flang.companion_toolchain("c") is None


def test_bound_commands_are_a_preset_explain_names() -> None:
    """They apply like $CC and $CXX, under a name saying where they came
    from; a variable the user set still wins."""
    toolchain = LlvmToolchain()
    toolchain._tools = {"cc": ClangCCompiler()}
    toolchain.bind_commands({"CC": "/llvm/bin/clang"}, source="flang's LLVM")

    with patch.dict(os.environ, {"CC": ""}):
        presets = toolchain.setup_presets(None)  # type: ignore[arg-type]

    assert [p.name for p in presets] == ["flang's LLVM"]
    assert presets[0].contributions[0].cmd == "/llvm/bin/clang"


def test_add_toolchain_takes_the_primarys_companion(tmp_path, gcc_toolchain) -> None:
    """A name the primary toolchain has a companion for skips the search."""
    project = Project("companion", root_dir=tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    companion = LlvmToolchain()

    with patch.object(
        type(gcc_toolchain), "companion_toolchain", lambda self, n: companion
    ):
        env.add_toolchain("c")

    assert companion in env.toolchains

# SPDX-License-Identifier: MIT
"""Tests for string-based toolchain resolution.

`Environment(toolchain="c")` and friends resolve through
`toolchain_registry.resolve()`: finder names auto-detect, aliases require
that specific toolchain, and sequences are preference lists.
"""

from __future__ import annotations

import shutil

import pytest

import pcons.toolchains  # noqa: F401 — populate the registry
from pcons import Project
from pcons.tools.toolchain import toolchain_registry


def _has_c_compiler() -> bool:
    return any(shutil.which(c) for c in ("clang", "gcc", "cc", "cl", "clang-cl"))


class TestRegistryResolve:
    def test_resolve_finder_name(self) -> None:
        """Category names like "c" auto-detect via the registered finder."""
        if not _has_c_compiler():
            pytest.skip("no C compiler found")
        toolchain = toolchain_registry.resolve("c")
        assert toolchain is not None
        assert toolchain.name

    def test_resolve_finder_aliases_equivalent(self) -> None:
        """ "c", "c++", and "cpp" all resolve through the same finder."""
        if not _has_c_compiler():
            pytest.skip("no C compiler found")
        names = {type(toolchain_registry.resolve(n)) for n in ("c", "c++", "cpp")}
        assert len(names) == 1

    def test_resolve_alias_case_insensitive(self) -> None:
        for name in ("llvm", "gcc", "msvc", "clang-cl"):
            try:
                lower = toolchain_registry.resolve(name)
            except RuntimeError:
                continue
            assert type(toolchain_registry.resolve(name.upper())) is type(lower)
            return
        pytest.skip("no specific C toolchain available by alias")

    def test_resolve_unknown_name_lists_known(self) -> None:
        with pytest.raises(ValueError, match="Unknown toolchain 'no-such'.*gcc"):
            toolchain_registry.resolve("no-such")

    def test_resolve_unavailable_alias_raises(self, monkeypatch) -> None:
        """A specific alias must be available — no silent fallback."""
        monkeypatch.setattr(shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="not available"):
            toolchain_registry.resolve("gcc")

    def test_resolve_preference_list(self) -> None:
        """A sequence tries each name in order, first available wins."""
        if not _has_c_compiler():
            pytest.skip("no C compiler found")
        toolchain = toolchain_registry.resolve(
            ["no-such", "llvm", "gcc", "msvc", "clang-cl"]
        )
        assert toolchain is not None

    def test_resolve_preference_list_none_available(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="No toolchain available"):
            toolchain_registry.resolve(["gcc", "llvm"])

    def test_known_names_include_finders_and_aliases(self) -> None:
        names = toolchain_registry.known_names()
        for expected in ("c", "c++", "cpp", "gcc", "llvm", "msvc", "fortran"):
            assert expected in names


class TestEnvironmentToolchainString:
    def test_environment_accepts_string(self, tmp_path) -> None:
        if not _has_c_compiler():
            pytest.skip("no C compiler found")
        project = Project("strtest", root_dir=tmp_path)
        env = project.Environment(toolchain="c")
        assert env.cc is not None  # toolchain populated the tool namespaces

    def test_environment_accepts_preference_list(self, tmp_path) -> None:
        if not _has_c_compiler():
            pytest.skip("no C compiler found")
        project = Project("listtest", root_dir=tmp_path)
        env = project.Environment(toolchain=["llvm", "gcc", "msvc", "clang-cl"])
        assert env.cc is not None

    def test_environment_unknown_string_raises(self, tmp_path) -> None:
        project = Project("badtest", root_dir=tmp_path)
        with pytest.raises(ValueError, match="Unknown toolchain"):
            project.Environment(toolchain="no-such-toolchain")

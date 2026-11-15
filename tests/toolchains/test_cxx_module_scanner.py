# SPDX-License-Identifier: MIT
"""Unit tests for the configure-side C++ module helpers.

Which environments take part in a module pass, which BMI-compatibility class
a compile belongs to, and the flags a scan runs with. The scanning itself is
covered by tests/core/test_scan.py and tests/toolchains/test_cxx_collate.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.subst import PathToken
from pcons.toolchains.cxx_module_scanner import (
    CxxModuleScannerNotFound,
    StdModuleFlagSpec,
    bmi_key_for_flags,
    find_scan_deps,
    merge_scan_compile_flags,
    select_modules_scope,
    select_std_module_flags,
)


class _FakeCxxNamespace:
    """Stand-in for env.cxx with just the `modules` attribute the helper reads."""

    def __init__(self, modules: bool | None) -> None:
        self.modules = modules


class _FakeEnv:
    def __init__(self, modules: bool | None) -> None:
        self.cxx = _FakeCxxNamespace(modules)


class _FakeObj:
    """Stand-in FileNode-ish duck-type for select_modules_scope."""

    def __init__(self, env: _FakeEnv, path: str = "/src/some.cpp") -> None:
        self._build_info = {"env": env}
        self.path = Path(path)


class TestSelectModulesScope:
    def test_no_module_extensions_no_optin_skips(self) -> None:
        env = _FakeEnv(modules=None)
        obj = _FakeObj(env)
        # cxx_pairs only — no .cppm/.ixx, env didn't opt in.
        scope = select_modules_scope({"cxx": [(Path("/src/main.cpp"), obj)]})
        assert scope == ([], [])

    def test_extension_implicit_optin_includes_cxx_pairs(self) -> None:
        env = _FakeEnv(modules=None)
        mod_obj = _FakeObj(env)
        cxx_obj = _FakeObj(env)
        # The .cppm in this env qualifies; sibling .cpp files in the same
        # env come along so partition units in .cpp can be detected.
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx_module": [(Path("/src/MyMod.cppm"), mod_obj)],
                "cxx": [(Path("/src/Helper.cpp"), cxx_obj)],
            }
        )
        assert len(m_pairs) == 1
        assert len(c_pairs) == 1

    def test_explicit_false_vetoes_the_suffix_optin(self) -> None:
        """env.cxx.modules = False beats the .cppm opt-in (with a warning);
        before the tri-state default it was a silent no-op."""
        env = _FakeEnv(modules=False)
        mod_obj = _FakeObj(env, "/src/MyMod.cppm")
        m_pairs, c_pairs = select_modules_scope(
            {"cxx_module": [(Path("/src/MyMod.cppm"), mod_obj)]}
        )
        assert (m_pairs, c_pairs) == ([], [])

    def test_explicit_optin_without_extensions(self) -> None:
        env = _FakeEnv(modules=True)
        cxx_obj = _FakeObj(env)
        m_pairs, c_pairs = select_modules_scope(
            {"cxx": [(Path("/src/main.cpp"), cxx_obj)]}
        )
        assert m_pairs == []
        assert len(c_pairs) == 1

    def test_other_envs_filtered_out(self) -> None:
        # Two envs in the same project — only one opted in. The other env's
        # TUs must NOT be scanned (would slow the build and may produce
        # spurious flags).
        env_modules = _FakeEnv(modules=True)
        env_plain = _FakeEnv(modules=False)
        m_obj = _FakeObj(env_modules)
        p_obj = _FakeObj(env_plain)
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx": [
                    (Path("/m.cpp"), m_obj),
                    (Path("/p.cpp"), p_obj),
                ],
            }
        )
        assert m_pairs == []
        assert len(c_pairs) == 1
        assert c_pairs[0][1] is m_obj


class TestFindScanDeps:
    """A missing scanner executable must raise an actionable error.

    Configure used to silently warn and return None when the scanner wasn't
    on PATH; that produced empty dyndep files and confusing downstream
    failures. Now it raises CxxModuleScannerNotFound with install hints.
    """

    def test_missing_scanner_raises_with_hints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.shutil.which", lambda name: None
        )
        with pytest.raises(CxxModuleScannerNotFound, match="clang-scan-deps"):
            find_scan_deps(
                _FakeEnv(modules=True), ["clang-scan-deps"], "clang-scan-deps hints"
            )

    def test_the_env_override_wins_over_the_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.shutil.which", lambda name: "/found"
        )
        env = _FakeEnv(modules=True)
        env.cxx.scan_deps = "/opt/mine/clang-scan-deps"
        assert find_scan_deps(env, ["clang-scan-deps"], "") == (
            "/opt/mine/clang-scan-deps"
        )


_CLANG_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"-frtti", "-fno-rtti", "-fexperimental-library"}),
    prefixes=("-std=", "-stdlib=", "-isysroot="),
    paired=frozenset({"-target", "-isysroot"}),
    define_prefix="-D",
    define_glob_prefixes=("_LIBCPP_",),
)


_MSVC_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"/MD", "/MDd", "/MT", "/MTd", "/EHsc", "/GR-"}),
    prefixes=("/std:", "/Zc:", "/arch:"),
    paired=frozenset(),
    define_prefix="/D",
    define_glob_prefixes=("_HAS_", "_ITERATOR_DEBUG_LEVEL"),
)


class TestSelectStdModuleFlags:
    """Picks ABI-affecting flags from a user's compile flags so the
    std-module compile and consumer TUs agree on the std library's ABI.

    Mismatches here range from silent corruption (mismatched RTTI) to
    iterator heap corruption (`_ITERATOR_DEBUG_LEVEL`) — the spec is
    load-bearing.
    """

    def test_clang_minimum_set(self) -> None:
        # User flags carry the things every std-module compile needs.
        out = select_std_module_flags(
            ["-std=c++23", "-stdlib=libc++", "-O2", "-Wall", "-fno-rtti"],
            _CLANG_LIKE_SPEC,
        )
        assert "-std=c++23" in out
        assert "-stdlib=libc++" in out
        assert "-fno-rtti" in out
        # Optimization and warning flags are not ABI-relevant; they must
        # NOT propagate (or `-Werror` would turn libc++'s deliberate
        # warnings into hard errors).
        assert "-O2" not in out
        assert "-Wall" not in out

    def test_libcxx_define_propagates(self) -> None:
        # `_LIBCPP_HARDENING_MODE` is the canonical example: the std
        # module must be compiled with the same value as consumer TUs,
        # otherwise libc++ ABI varies between them.
        out = select_std_module_flags(
            [
                "-std=c++23",
                "-D_LIBCPP_HARDENING_MODE=fast",
                "-DAPP_VERSION=42",
                "-DFOO",
            ],
            _CLANG_LIKE_SPEC,
        )
        assert "-D_LIBCPP_HARDENING_MODE=fast" in out
        # User-app defines unrelated to libc++ must NOT propagate — they
        # could break the std-module compile or change preprocessor state.
        assert "-DAPP_VERSION=42" not in out
        assert "-DFOO" not in out

    def test_paired_flag_carries_value_token(self) -> None:
        # GCC-style `-target X86_64-...` and `-isysroot /sdk/path`: both
        # halves must propagate together, in order.
        out = select_std_module_flags(
            ["-std=c++23", "-target", "x86_64-apple-darwin", "-O2"],
            _CLANG_LIKE_SPEC,
        )
        i_target = out.index("-target")
        assert out[i_target + 1] == "x86_64-apple-darwin"

    def test_paired_flag_at_end_is_dropped(self) -> None:
        # If a paired flag appears as the last token (no value), drop it
        # rather than spilling off the end.
        out = select_std_module_flags(["-target"], _CLANG_LIKE_SPEC)
        assert out == []

    def test_msvc_runtime_library_propagates(self) -> None:
        # `/MDd` vs `/MD` is the canonical MSVC ABI footgun: a debug-CRT
        # consumer linked with a release-CRT std module is undefined
        # behavior. The spec MUST carry it.
        out = select_std_module_flags(
            ["/std:c++latest", "/MDd", "/Zc:char8_t-", "/Wall", "/O2"],
            _MSVC_LIKE_SPEC,
        )
        assert "/std:c++latest" in out
        assert "/MDd" in out
        assert "/Zc:char8_t-" in out
        assert "/Wall" not in out
        assert "/O2" not in out

    def test_msvc_iterator_debug_level_propagates(self) -> None:
        # `_ITERATOR_DEBUG_LEVEL` mismatch corrupts the heap. Must propagate.
        out = select_std_module_flags(
            ["/std:c++latest", "/D_ITERATOR_DEBUG_LEVEL=2", "/DUSER_FOO=1"],
            _MSVC_LIKE_SPEC,
        )
        assert "/D_ITERATOR_DEBUG_LEVEL=2" in out
        assert "/DUSER_FOO=1" not in out

    def test_preserves_input_order(self) -> None:
        # Order matters for prefixes that override later (e.g., the user
        # writing `-stdlib=libstdc++` after pcons inserts `-stdlib=libc++`).
        out = select_std_module_flags(
            ["-stdlib=libc++", "-std=c++20", "-D_LIBCPP_FOO=1", "-frtti"],
            _CLANG_LIKE_SPEC,
        )
        assert out == [
            "-stdlib=libc++",
            "-std=c++20",
            "-D_LIBCPP_FOO=1",
            "-frtti",
        ]


class TestBmiKeyForFlags:
    """A BMI's on-disk directory is keyed by the hash of its BMI-sensitive
    flags so compatible compiles share one interface and incompatible ones
    (e.g. different C++ dialects) stay separate.
    """

    def test_identical_bmi_flags_share_key(self) -> None:
        a = bmi_key_for_flags(["-std=c++23", "-O2"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-std=c++23", "-O0"], _CLANG_LIKE_SPEC)
        # -O level is not BMI-sensitive, so the key is the same.
        assert a == b

    def test_different_dialect_gives_different_key(self) -> None:
        a = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-std=c++26"], _CLANG_LIKE_SPEC)
        assert a != b

    def test_order_independent(self) -> None:
        a = bmi_key_for_flags(["-std=c++23", "-frtti"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-frtti", "-std=c++23"], _CLANG_LIKE_SPEC)
        assert a == b

    def test_non_bmi_flags_ignored(self) -> None:
        # Unrelated includes/defines do not change the key.
        a = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(
            ["-std=c++23", "-I/some/inc", "-DUSER_FOO=1"], _CLANG_LIKE_SPEC
        )
        assert a == b

    def test_key_is_short_hex(self) -> None:
        key = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        assert len(key) == 12
        assert all(c in "0123456789abcdef" for c in key)


class TestMergeScanCompileFlags:
    """Tests for merge_scan_compile_flags."""

    def test_dedups_extra_and_context_flags(self) -> None:
        from types import SimpleNamespace

        ctx = SimpleNamespace(
            flags=["-O2", "-std=c++23"],  # -std dup vs base, kept once
            includes=["inc", "/abs/inc"],
            defines=["FOO=1", "BAR"],
        )
        result = merge_scan_compile_flags(
            ["-std=c++23"], ctx, extra_flags=("-fmodules", "-fmodules")
        )
        assert result == [
            "-std=c++23",
            "-fmodules",
            "-O2",
            PathToken("-I", "inc", "project"),
            PathToken("-I", "/abs/inc", "project"),
            "-DFOO=1",
            "-DBAR",
        ]

    def test_include_dirs_are_written_as_the_compile_writes_them(self) -> None:
        """A generated include dir stays build-relative and a source one
        project-relative, so build.ninja names neither absolutely."""
        from types import SimpleNamespace

        from pcons.core.subst import BuildPath, ProjectPath

        ctx = SimpleNamespace(
            flags=[],
            includes=[ProjectPath("include"), BuildPath("gen")],
            system_includes=[ProjectPath("third_party")],
            defines=[],
        )
        result = merge_scan_compile_flags(
            [], ctx, iprefix="/I", isysprefix="/external:I"
        )
        assert result == [
            PathToken("/I", "include", "project"),
            PathToken("/I", "gen", "build"),
            PathToken("/external:I", "third_party", "project"),
        ]

    def test_no_context(self) -> None:
        result = merge_scan_compile_flags(["-std=c++20"], None, extra_flags=("-x",))
        assert result == ["-std=c++20", "-x"]

# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.checks."""

import shutil
import sys

import pytest

from pcons.configure.checks import CheckResult, ToolChecks
from pcons.configure.config import Configure
from pcons.core.environment import Environment


class TestCheckResult:
    def test_creation(self):
        result = CheckResult(success=True)
        assert result.success is True
        assert result.output == ""
        assert result.cached is False

    def test_with_output(self):
        result = CheckResult(success=False, output="error message")
        assert result.output == "error message"

    def test_cached(self):
        result = CheckResult(success=True, cached=True)
        assert result.cached is True


def _find_c_compiler() -> tuple[str | None, bool]:
    """Find a C compiler and return (path, is_msvc_style).

    Returns:
        Tuple of (compiler_path, is_msvc_style) where is_msvc_style is True
        for cl.exe and clang-cl (which use /flag syntax).
    """
    # Check for Unix-style compilers first
    for compiler in ["cc", "gcc", "clang"]:
        path = shutil.which(compiler)
        if path:
            return path, False

    # Check for MSVC-style compilers on Windows
    if sys.platform == "win32":
        for compiler in ["cl.exe", "clang-cl.exe", "clang-cl"]:
            path = shutil.which(compiler)
            if path:
                return path, True

    return None, False


_cc_path, _is_msvc_style = _find_c_compiler()
has_cc = _cc_path is not None


class TestCachedOrCompiler:
    """Cached and no-compiler paths of _cached_or_compiler (no real compiler)."""

    def _make_checks(self, tmp_path, test_project):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")  # no cmd -> no compiler configured
        return config, ToolChecks(config, env, "cc")

    def test_check_header_cached(self, tmp_path, test_project):  # noqa: F811
        config, checks = self._make_checks(tmp_path, test_project)
        config.set(checks._cache_key("header", "stdio.h"), True)
        result = checks.check_header("stdio.h")
        assert result.cached is True
        assert result.success is True

    def test_check_type_cached(self, tmp_path, test_project):  # noqa: F811
        config, checks = self._make_checks(tmp_path, test_project)
        config.set(checks._cache_key("type", "size_t"), True)
        result = checks.check_type("size_t")
        assert result.cached is True
        assert result.success is True

    def test_check_function_cached(self, tmp_path, test_project):  # noqa: F811
        config, checks = self._make_checks(tmp_path, test_project)
        config.set(checks._cache_key("function", "printf"), False)
        result = checks.check_function("printf")
        assert result.cached is True
        assert result.success is False

    def test_no_compiler_returns_failure(self, tmp_path, test_project):  # noqa: F811
        _config, checks = self._make_checks(tmp_path, test_project)
        result = checks.check_header("uncached-header.h")
        assert result.success is False
        assert "No compiler configured" in result.output

    def test_check_function_cache_key_varies_with_headers_and_libs(
        self, tmp_path, test_project
    ):  # noqa: F811
        """headers/libs must be part of the cache key, not just the function name.

        Regression test: check_function() used to cache purely on the
        function name, so check_function("SSL_new", headers=[...], libs=[...])
        and a bare check_function("SSL_new") would collide.
        """
        config, checks = self._make_checks(tmp_path, test_project)
        key_with_headers_libs = checks._cache_key(
            "function", "SSL_new", "openssl/ssl.h", "ssl"
        )
        config.set(key_with_headers_libs, True)

        # A different (bare) combo must not hit that cache entry.
        result_bare = checks.check_function("SSL_new")
        assert result_bare.cached is False

        # The exact same headers/libs combo does hit the cache.
        result_match = checks.check_function(
            "SSL_new", headers=["openssl/ssl.h"], libs=["ssl"]
        )
        assert result_match.cached is True
        assert result_match.success is True

    def test_check_type_cache_key_varies_with_headers(self, tmp_path, test_project):  # noqa: F811
        config, checks = self._make_checks(tmp_path, test_project)
        key_with_header = checks._cache_key("type", "my_type_t", "mylib.h")
        config.set(key_with_header, True)

        result_bare = checks.check_type("my_type_t")
        assert result_bare.cached is False

        result_match = checks.check_type("my_type_t", headers=["mylib.h"])
        assert result_match.cached is True
        assert result_match.success is True

    def test_check_type_size_cache_key_varies_with_headers(
        self, tmp_path, test_project
    ):  # noqa: F811
        config, checks = self._make_checks(tmp_path, test_project)
        key_with_header = checks._cache_key("sizeof", "my_type_t", "mylib.h")
        config.set(key_with_header, 8)

        # Bare call misses the seeded entry; with no compiler configured it
        # falls through to None rather than returning the seeded value.
        assert checks.check_type_size("my_type_t") is None

        # Exact same headers combo hits the cache.
        assert checks.check_type_size("my_type_t", headers=["mylib.h"]) == 8


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestToolChecksWithCompiler:
    """Tests that require a real compiler."""

    @pytest.fixture
    def setup(self, tmp_path, test_project):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")

        if _cc_path:
            env.cc.cmd = _cc_path

        return config, env

    def test_check_flag_valid(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        # Use appropriate flag syntax for the compiler
        flag = "/W4" if _is_msvc_style else "-Wall"
        result = checks.check_flag(flag)
        assert result.success is True

    def test_check_flag_invalid(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        # Use a flag that's invalid for both MSVC and GCC-style compilers
        flag = (
            "/INVALID-FLAG-12345"
            if _is_msvc_style
            else "--this-is-not-a-valid-flag-12345"
        )
        result = checks.check_flag(flag)
        assert result.success is False

    def test_check_flag_rejects_unknown_warning_option(self, setup):
        """Clang accepts unknown -Wno-* flags with exit code 0 but warns.

        check_flag() should detect this via -Werror and reject the flag.
        GCC silently accepts unknown -Wno-* flags even with -Werror,
        so this test only asserts failure on Clang.
        """
        if _is_msvc_style:
            pytest.skip("GCC/Clang-specific warning flag")
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_flag("-Wno-this-is-not-a-real-warning-option")
        # GCC silently accepts unknown -Wno-* flags; only Clang rejects them
        if result.success:
            pytest.skip("Compiler accepts unknown -Wno-* flags (likely GCC)")
        assert result.success is False

    def test_check_header_exists(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_header("stdio.h")
        assert result.success is True

    def test_check_header_not_exists(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_header("this_header_does_not_exist_12345.h")
        assert result.success is False

    def test_check_header_with_defines(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        # stdint.h should work without defines
        result = checks.check_header("stdint.h", defines=["__STDC_LIMIT_MACROS"])
        assert result.success is True

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific header")
    def test_check_header_ucontext_requires_define(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        # On macOS, ucontext.h requires _XOPEN_SOURCE
        result_without = checks.check_header("ucontext.h")
        result_with = checks.check_header("ucontext.h", defines=["_XOPEN_SOURCE"])
        # Without the define it should fail; with it should succeed
        assert result_without.success is False
        assert result_with.success is True

    def test_check_header_with_extra_flags(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        flag = "/W4" if _is_msvc_style else "-Wall"
        result = checks.check_header("stdio.h", extra_flags=[flag])
        assert result.success is True

    def test_check_function_exists(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_function("printf", headers=["stdio.h"])
        assert result.success is True

    def test_check_type_exists(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_type("int")
        assert result.success is True

    def test_check_type_with_header(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_type("size_t", headers=["stddef.h"])
        assert result.success is True

    def test_check_type_size(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        size = checks.check_type_size("int")
        assert size in [2, 4]  # Common sizes for int

    def test_check_type_size_pointer(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        size = checks.check_type_size("void*")
        assert size in [4, 8]  # 32-bit or 64-bit

    def test_try_compile_success(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.try_compile("int main(void) { return 0; }\n")
        assert result.success is True

    def test_try_compile_failure(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.try_compile("this is not valid C code")
        assert result.success is False

    def test_try_compile_with_header(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.try_compile(
            '#include <stdio.h>\nint main(void) { printf("hi"); return 0; }\n'
        )
        assert result.success is True

    def test_try_compile_cached(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        source = "int main(void) { return 42; }\n"
        result1 = checks.try_compile(source)
        assert result1.cached is False
        result2 = checks.try_compile(source)
        assert result2.cached is True
        assert result2.success == result1.success

    def test_try_compile_with_extra_flags(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        flag = "/W4" if _is_msvc_style else "-Wall"
        result = checks.try_compile(
            "int main(void) { return 0; }\n", extra_flags=[flag]
        )
        assert result.success is True

    def test_check_caching(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")

        # First check - not cached
        result1 = checks.check_flag("-Wall")
        assert result1.cached is False

        # Second check - should be cached
        result2 = checks.check_flag("-Wall")
        assert result2.cached is True
        assert result2.success == result1.success


class TestToolChecksWithoutCompiler:
    """Tests that don't require a real compiler."""

    def test_no_compiler_configured(self, tmp_path, test_project):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        # Don't set env.cc.cmd

        checks = ToolChecks(config, env, "cc")
        result = checks.check_flag("-Wall")

        assert result.success is False
        assert "No compiler" in result.output

    def test_cache_key_format(self, tmp_path, test_project):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        checks = ToolChecks(config, env, "cc")
        key = checks._cache_key("flag", "-Wall")

        # The compiler and its flags are folded into a signature hash so the
        # same binary targeting different platforms never shares answers.
        assert "cc" in key
        assert "flag" in key
        assert "-Wall" in key

        # Changing the tool's flags (e.g. a cross preset's --target) must
        # change the key.
        env.cc.set("flags", ["--target=wasm32-wasi"])
        assert checks._cache_key("flag", "-Wall") != key


class TestMsvcStyleDispatch:
    """Toolchain-aware compile/link/preprocess flag rendering (no real MSVC needed).

    Regression tests for ToolChecks hardcoding GCC/Clang-only flags
    (-c, -o, -l, -E), which made every check fail under MSVC/clang-cl.
    """

    def _checks_with_compiler(self, tmp_path, test_project, compiler_cmd):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = compiler_cmd
        return ToolChecks(config, env, "cc")

    def test_is_msvc_style_detects_cl_and_clang_cl(self, tmp_path, test_project):  # noqa: F811
        assert self._checks_with_compiler(
            tmp_path, test_project, "cl.exe"
        )._is_msvc_style()
        assert self._checks_with_compiler(
            tmp_path, test_project, "clang-cl"
        )._is_msvc_style()
        assert not self._checks_with_compiler(
            tmp_path, test_project, "gcc"
        )._is_msvc_style()

    def test_lib_flag_msvc_vs_unix(self, tmp_path, test_project):  # noqa: F811
        msvc_checks = self._checks_with_compiler(tmp_path, test_project, "cl.exe")
        assert msvc_checks._lib_flag("ssl") == "ssl.lib"
        assert msvc_checks._lib_flag("ssl.lib") == "ssl.lib"

        gcc_checks = self._checks_with_compiler(tmp_path, test_project, "gcc")
        assert gcc_checks._lib_flag("ssl") == "-lssl"

    @staticmethod
    def _fake_run(captured):
        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        return run

    def test_try_compile_uses_msvc_flags(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        checks = self._checks_with_compiler(tmp_path, test_project, "cl.exe")
        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", self._fake_run(captured)
        )

        checks.try_compile("int main(void) { return 0; }\n")

        cmd = captured["cmd"]
        assert "/c" in cmd
        assert any(arg.startswith("/Fo") for arg in cmd)
        assert "-c" not in cmd
        assert not any(arg == "-o" for arg in cmd)

    def test_try_preprocess_uses_msvc_flag(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        checks = self._checks_with_compiler(tmp_path, test_project, "cl.exe")
        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", self._fake_run(captured)
        )

        checks.check_define("SOME_MACRO")

        cmd = captured["cmd"]
        assert "/E" in cmd
        assert "-E" not in cmd

    def test_try_compile_link_uses_fe(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        checks = self._checks_with_compiler(tmp_path, test_project, "cl.exe")
        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", self._fake_run(captured)
        )

        checks.try_compile("int main(void) { return 0; }\n", link=True)

        cmd = captured["cmd"]
        assert any(arg.startswith("/Fe") for arg in cmd)
        assert "/c" not in cmd

    def test_unix_style_unaffected(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        checks = self._checks_with_compiler(tmp_path, test_project, "gcc")
        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", self._fake_run(captured)
        )

        checks.try_compile("int main(void) { return 0; }\n")

        cmd = captured["cmd"]
        assert "-c" in cmd
        assert "-o" in cmd


class TestCrossTargetChecks:
    """Checks compile with the tool's flags, so a cross preset's --target
    makes them answer for the target, not the host (docs/presets.md,
    host independence)."""

    def test_check_answers_for_target_not_host(self, tmp_path, test_project):  # noqa: F811
        import shutil

        clang = shutil.which("clang")
        if clang is None:
            pytest.skip("clang not available")

        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = clang
        # i686 needs only the x86 backend, present in every clang build
        # (Apple's clang lacks e.g. the wasm backend).
        env.cc.set("flags", ["--target=i686-unknown-linux-gnu"])

        checks = ToolChecks(config, env, "cc")
        # i686 pointers are 4 bytes; virtually every host is 8. The old
        # behavior (bare compiler, host ctypes) would answer 8.
        assert checks.check_type_size("void*") == 4

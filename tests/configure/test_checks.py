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


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestToolChecksWithCompiler:
    """Tests that require a real compiler."""

    @pytest.fixture
    def setup(self, tmp_path):
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

    @pytest.mark.skipif(_is_msvc_style, reason="GCC/Clang-specific warning flag")
    def test_check_flag_rejects_unknown_warning_option(self, setup):
        """Clang accepts unknown -Wno-* flags with exit code 0 but warns.

        check_flag() should detect this via -Werror and reject the flag.
        """
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        # -Wno-stringop-overflow is GCC-specific; Clang warns about it
        # Even on GCC, this should either succeed (GCC knows it) or fail cleanly
        result = checks.check_flag("-Wno-this-is-not-a-real-warning-option")
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

    def test_no_compiler_configured(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        # Don't set env.cc.cmd

        checks = ToolChecks(config, env, "cc")
        result = checks.check_flag("-Wall")

        assert result.success is False
        assert "No compiler" in result.output

    def test_cache_key_format(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        checks = ToolChecks(config, env, "cc")
        key = checks._cache_key("flag", "-Wall")

        assert "cc" in key
        assert "gcc" in key
        assert "flag" in key
        assert "-Wall" in key

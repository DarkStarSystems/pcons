# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.checks."""

import shutil

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


# Skip tests that require a real compiler if not available
has_cc = shutil.which("cc") is not None or shutil.which("gcc") is not None


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestToolChecksWithCompiler:
    """Tests that require a real compiler."""

    @pytest.fixture
    def setup(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")

        # Find the actual compiler
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if cc:
            env.cc.cmd = cc

        return config, env

    def test_check_flag_valid(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_flag("-Wall")
        assert result.success is True

    def test_check_flag_invalid(self, setup):
        config, env = setup
        checks = ToolChecks(config, env, "cc")
        result = checks.check_flag("--this-is-not-a-valid-flag-12345")
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

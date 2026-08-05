# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.checks."""

import shutil
import sys

import pytest

from pcons.configure.checks import (
    CheckResult,
    ToolChecks,
    _decode_c_string,
    _parse_probe_output,
    _probe_source,
)
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


class TestCheckDefineProbe:
    """The macro probe's source and parsing, with no real compiler."""

    def _checks(self, tmp_path, test_project, compiler_cmd="gcc"):  # noqa: F811
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = compiler_cmd
        return ToolChecks(config, env, "cc")

    @staticmethod
    def _fake_run(captured, stdout):
        class _Result:
            returncode = 0
            stderr = ""

        def run(cmd, **kwargs):
            captured.setdefault("cmds", []).append(cmd)
            result = _Result()
            result.stdout = stdout
            return result

        return run

    def test_probe_source_labels_by_index_not_macro_name(self):
        """`PCONS_PROBE FOO = FOO` expands FOO on *both* sides, leaving
        nothing to key the answer on. The label must be the index."""
        source = _probe_source(["FOO", "BAR"], None, None)

        assert "PCONS_PROBE 0 = FOO" in source
        assert "PCONS_PROBE 1 = BAR" in source
        assert "PCONS_PROBE FOO" not in source

    def test_probe_source_undefs_marker_and_sentinel(self):
        """A header defining either would make it expand in the output."""
        source = _probe_source(["FOO"], ["version.h"], None)

        assert source.index("#undef PCONS_PROBE") > source.index('#include "version.h"')
        assert "#undef PCONS_UNDEFINED" in source

    def test_probe_source_includes_headers_and_defines(self):
        source = _probe_source(["FOO"], ["a/b.h"], ["GATE", "N=3"])

        assert "#define GATE" in source
        assert "#define N 3" in source  # not "#define N=3", which defines nothing
        assert '#include "a/b.h"' in source
        assert source.index("#define GATE") < source.index('#include "a/b.h"')

    def test_parses_the_four_value_cases(self):
        output = "\n".join(
            [
                '# 1 "check.c"',  # linemarker noise
                "PCONS_PROBE 0 = 42",
                "PCONS_PROBE 1 = ",
                'PCONS_PROBE 2 = "Sapphire 2024"',
                "PCONS_PROBE 3 = PCONS_UNDEFINED",
            ]
        )

        values = _parse_probe_output(output, ["NUM", "EMPTY", "STR", "MISSING"])

        assert values == {
            "NUM": "42",
            "EMPTY": "",
            "STR": '"Sapphire 2024"',
            "MISSING": None,
        }

    def test_batch_runs_preprocessor_once(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        """The whole point of the batch form: one process, many answers."""
        checks = self._checks(tmp_path, test_project)
        captured: dict[str, list] = {}
        stdout = "PCONS_PROBE 0 = 1\nPCONS_PROBE 1 = 2\nPCONS_PROBE 2 = 3\n"
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", self._fake_run(captured, stdout)
        )

        values = checks.check_defines(["A", "B", "C"], headers=["h.h"])

        assert values == {"A": "1", "B": "2", "C": "3"}
        assert len(captured["cmds"]) == 1

    def test_include_dirs_rendered_for_unix_and_msvc(
        self,
        tmp_path,
        test_project,  # noqa: F811
        monkeypatch,
    ):
        for compiler, expected in (("gcc", "-I/opt/sdk"), ("cl.exe", "/I/opt/sdk")):
            checks = self._checks(tmp_path, test_project, compiler)
            captured: dict[str, list] = {}
            monkeypatch.setattr(
                "pcons.configure.checks.subprocess.run",
                self._fake_run(captured, "PCONS_PROBE 0 = 1\n"),
            )

            checks.check_define("FOO", headers=["h.h"], include_dirs=["/opt/sdk"])

            assert expected in captured["cmds"][0]

    def test_failed_probe_is_not_cached(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        """A missing header is an error condition, not an answer — and with
        staged generation the header may simply not exist yet."""
        checks = self._checks(tmp_path, test_project)

        class _Failed:
            returncode = 1
            stdout = ""
            stderr = "fatal error: no such file"

        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run", lambda cmd, **kw: _Failed()
        )

        assert checks.check_define("FOO", headers=["missing.h"]) is None

        captured: dict[str, list] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run",
            self._fake_run(captured, "PCONS_PROBE 0 = 7\n"),
        )
        assert checks.check_define("FOO", headers=["missing.h"]) == "7"

    def test_cache_key_varies_with_headers(self, tmp_path, test_project, monkeypatch):  # noqa: F811
        """Same macro name, different header: two different questions."""
        checks = self._checks(tmp_path, test_project)
        captured: dict[str, list] = {}
        monkeypatch.setattr(
            "pcons.configure.checks.subprocess.run",
            self._fake_run(captured, "PCONS_PROBE 0 = 1\n"),
        )

        checks.check_define("FOO", headers=["a.h"])
        checks.check_define("FOO", headers=["b.h"])
        checks.check_define("FOO", headers=["a.h"])  # cached

        assert len(captured["cmds"]) == 2


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestCheckDefineWithCompiler:
    """Reading macros out of a real header with a real preprocessor."""

    @pytest.fixture
    def setup(self, tmp_path, test_project):  # noqa: F811
        header = tmp_path / "probe_header.h"
        header.write_text(
            '#define PROBE_STRING "Sapphire 2024"\n'
            "#define PROBE_NUMBER 42\n"
            "#define PROBE_EMPTY\n"
            "#define PROBE_SPACES 1 + 2\n"
            "#ifdef PROBE_GATE\n"
            "#define PROBE_GATED 1\n"
            "#endif\n"
        )
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        if _cc_path:
            env.cc.cmd = _cc_path
        return ToolChecks(config, env, "cc"), tmp_path

    def test_builtin_macro_still_works(self, setup):
        checks, _ = setup
        # __LINE__ is the one macro every C preprocessor must define, in every
        # mode: __STDC__ is absent under MSVC (and under clang in its default
        # MSVC-compatible mode on Windows), and __GNUC__ is compiler-specific.
        value = checks.check_define("__LINE__")

        assert value is not None
        assert value.isdigit()

    def test_undefined_macro(self, setup):
        checks, _ = setup

        assert checks.check_define("PCONS_NOT_A_REAL_MACRO_XYZ") is None

    def test_value_from_a_project_header(self, setup):
        checks, tmp_path = setup

        value = checks.check_define(
            "PROBE_NUMBER", headers=["probe_header.h"], include_dirs=[tmp_path]
        )

        assert value == "42"

    def test_quoted_and_empty_values_are_distinguishable(self, setup):
        checks, tmp_path = setup

        values = checks.check_defines(
            ["PROBE_STRING", "PROBE_EMPTY", "PROBE_NOPE"],
            headers=["probe_header.h"],
            include_dirs=[tmp_path],
        )

        assert values["PROBE_STRING"] == '"Sapphire 2024"'
        assert values["PROBE_EMPTY"] == ""
        assert values["PROBE_NOPE"] is None

    def test_batch_answers_in_order(self, setup):
        checks, tmp_path = setup
        names = ["PROBE_NUMBER", "PROBE_STRING", "PROBE_EMPTY"]

        values = checks.check_defines(
            names, headers=["probe_header.h"], include_dirs=[tmp_path]
        )

        assert list(values) == names

    def test_expansion_with_spaces(self, setup):
        checks, tmp_path = setup

        value = checks.check_define(
            "PROBE_SPACES", headers=["probe_header.h"], include_dirs=[tmp_path]
        )

        assert value is not None
        assert value.replace(" ", "") == "1+2"

    def test_defines_argument_gates_the_header(self, setup):
        checks, tmp_path = setup
        kwargs = {"headers": ["probe_header.h"], "include_dirs": [tmp_path]}

        assert checks.check_define("PROBE_GATED", **kwargs) is None
        assert (
            checks.check_define("PROBE_GATED", defines=["PROBE_GATE"], **kwargs) == "1"
        )

    def test_answer_is_cached(self, setup):
        checks, tmp_path = setup
        kwargs = {"headers": ["probe_header.h"], "include_dirs": [tmp_path]}

        first = checks.check_define("PROBE_NUMBER", **kwargs)
        (tmp_path / "probe_header.h").write_text("#define PROBE_NUMBER 99\n")

        assert checks.check_define("PROBE_NUMBER", **kwargs) == first


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestChecksUseTheEnvironment:
    """A check has to compile the way the build will.

    The environment's flags, defines and include dirs all have to reach the
    probe. The dangerous shape isn't a probe that fails to compile -- it's a
    header that compiles fine either way and takes a different #ifdef branch,
    handing back a plausible wrong value.
    """

    @pytest.fixture
    def setup(self, tmp_path, test_project):  # noqa: F811
        (tmp_path / "cfg.h").write_text(
            "#ifdef SAPPHIRE\n"
            '#define DIR "/right/path"\n'
            "#else\n"
            '#define DIR "/wrong/path"\n'
            "#endif\n"
        )
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        if _cc_path:
            env.cc.cmd = _cc_path
        return config, env, tmp_path

    def test_env_defines_reach_the_probe(self, setup):
        config, env, tmp_path = setup
        env.cc.defines = ["SAPPHIRE"]
        env.cc.includes = [tmp_path]
        checks = ToolChecks(config, env, "cc")

        value = checks.check_define("DIR", headers=["cfg.h"])

        assert value == '"/right/path"'  # the #ifdef SAPPHIRE branch

    def test_env_includes_let_check_header_find_a_project_header(self, setup):
        config, env, tmp_path = setup
        env.cc.includes = [tmp_path]
        checks = ToolChecks(config, env, "cc")

        assert checks.check_header("cfg.h").success

    def test_without_env_defines_the_other_branch_is_reported(self, setup):
        """The mechanism, from the other side: no define, other branch."""
        config, env, tmp_path = setup
        env.cc.includes = [tmp_path]
        checks = ToolChecks(config, env, "cc")

        assert checks.check_define("DIR", headers=["cfg.h"]) == '"/wrong/path"'

    def test_per_call_defines_add_to_the_environments(self, setup):
        config, env, tmp_path = setup
        env.cc.defines = ["SAPPHIRE"]
        env.cc.includes = [tmp_path]
        checks = ToolChecks(config, env, "cc")

        # GATE comes from the call, SAPPHIRE from the environment; both apply.
        (tmp_path / "gated.h").write_text(
            "#if defined(SAPPHIRE) && defined(GATE)\n#define BOTH 1\n#endif\n"
        )
        value = checks.check_define("BOTH", headers=["gated.h"], defines=["GATE"])

        assert value == "1"

    def test_env_defines_discriminate_the_cache(self, setup):
        config, env, tmp_path = setup
        env.cc.includes = [tmp_path]
        plain = ToolChecks(config, env, "cc").check_define("DIR", headers=["cfg.h"])

        env.cc.defines = ["SAPPHIRE"]
        defined = ToolChecks(config, env, "cc").check_define("DIR", headers=["cfg.h"])

        assert plain != defined  # not served from the first answer's cache entry

    def test_relative_include_dirs_resolve_against_the_project_root(
        self, tmp_path, test_project
    ):  # noqa: F811
        """Probes compile in a temp dir, so a project-relative -I would
        otherwise point nowhere."""
        from pcons.core.project import Project

        project = Project("relinc", root_dir=tmp_path)
        (tmp_path / "inc").mkdir()
        (tmp_path / "inc" / "rel.h").write_text("#define REL_OK 1\n")

        env = project.Environment()
        env.add_tool("cc")
        if _cc_path:
            env.cc.cmd = _cc_path
        env.cc.includes = ["inc"]  # relative to the project root
        checks = ToolChecks(Configure(build_dir=tmp_path), env, "cc")

        assert checks.check_define("REL_OK", headers=["rel.h"]) == "1"


class TestCheckDefineAsString:
    """as_string=True: the string the macro denotes, not its expansion text."""

    def test_concatenates_adjacent_literals(self):
        assert (
            _decode_c_string('"/Applications/" "Sapphire 2022" "/config"', "DIR")
            == "/Applications/Sapphire 2022/config"
        )

    def test_decodes_simple_escapes(self):
        assert (
            _decode_c_string(r'"C:\\Program Files\\App"', "P")
            == r"C:\Program Files\App"
        )
        assert _decode_c_string(r'"a\nb"', "S") == "a\nb"
        assert _decode_c_string(r'"say \"hi\""', "S") == 'say "hi"'

    def test_leaves_exotic_escapes_alone(self):
        """Numeric and universal escapes are rare here, and guessing at them
        silently would be worse than leaving them visible."""
        assert _decode_c_string(r'"\x41"', "S") == r"\x41"

    def test_a_non_string_macro_is_a_caller_error(self):
        with pytest.raises(ValueError, match="not a string literal"):
            _decode_c_string("42", "NUM")

    def test_empty_expansion_is_a_caller_error(self):
        with pytest.raises(ValueError, match="not a string literal"):
            _decode_c_string("", "EMPTY")


@pytest.mark.skipif(not has_cc, reason="No C compiler available")
class TestCheckDefineAsStringWithCompiler:
    @pytest.fixture
    def checks(self, tmp_path, test_project):  # noqa: F811
        (tmp_path / "s.h").write_text(
            '#define DIR "/Applications/BorisFX/" "Sapphire 2022 Adobe" "/config"\n'
            "#define NUM 42\n"
        )
        config = Configure(build_dir=tmp_path)
        env = Environment()
        env.add_tool("cc")
        if _cc_path:
            env.cc.cmd = _cc_path
        env.cc.includes = [tmp_path]
        return ToolChecks(config, env, "cc")

    def test_reads_a_concatenated_path_macro(self, checks):
        value = checks.check_define("DIR", headers=["s.h"], as_string=True)

        assert value == "/Applications/BorisFX/Sapphire 2022 Adobe/config"

    def test_undefined_stays_none(self, checks):
        assert checks.check_define("NOPE", headers=["s.h"], as_string=True) is None

    def test_non_string_macro_raises(self, checks):
        with pytest.raises(ValueError, match="not a string literal"):
            checks.check_define("NUM", headers=["s.h"], as_string=True)

    def test_batch_form_decodes_each(self, checks):
        values = checks.check_defines(["DIR"], headers=["s.h"], as_string=True)

        assert values["DIR"].startswith("/Applications/BorisFX/")

# SPDX-License-Identifier: MIT
"""Feature checking for pcons configure phase.

Provides utilities for testing compiler features, headers,
libraries, and other system capabilities.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.configure.config import Configure
    from pcons.core.environment import Environment


@dataclass
class CheckResult:
    """Result of a feature check.

    Attributes:
        success: Whether the check passed.
        output: Compiler/linker output (for debugging).
        cached: Whether this result came from cache.
    """

    success: bool
    output: str = ""
    cached: bool = False


class ToolChecks:
    """Feature checking for a configured tool.

    Provides methods to test compiler capabilities like:
    - Flag support
    - Header availability
    - Type sizes
    - Predefined macros

    Example:
        checks = ToolChecks(config, env, "cc")

        if checks.check_flag("-Wall"):
            env.cc.flags.append("-Wall")

        if checks.check_header("pthread.h"):
            env.cc.defines.append("HAVE_PTHREAD_H")
    """

    def __init__(
        self,
        config: Configure,
        env: Environment,
        tool_name: str,
    ) -> None:
        """Create a feature checker for a tool.

        Args:
            config: Configure context.
            env: Environment containing the tool.
            tool_name: Name of the tool to check (e.g., 'cc', 'cxx').
        """
        self._config = config
        self._env = env
        self._tool_name = tool_name
        self._tool_config = getattr(env, tool_name, None)

    def _get_compiler(self) -> str | None:
        """Get the compiler command."""
        if self._tool_config is None:
            return None
        return getattr(self._tool_config, "cmd", None)

    def _cache_key(self, check_type: str, *args: str) -> str:
        """Generate a cache key for a check."""
        compiler = self._get_compiler() or "unknown"
        return f"check:{self._tool_name}:{compiler}:{check_type}:{':'.join(args)}"

    def check_flag(self, flag: str) -> CheckResult:
        """Check if the compiler accepts a flag.

        Compiles a minimal program with the flag to test if it's accepted.

        Args:
            flag: Compiler flag to test (e.g., '-Wall', '-std=c++20').

        Returns:
            CheckResult indicating success/failure.
        """
        cache_key = self._cache_key("flag", flag)
        cached = self._config.get(cache_key)
        if cached is not None:
            return CheckResult(success=cached, cached=True)

        compiler = self._get_compiler()
        if compiler is None:
            return CheckResult(success=False, output="No compiler configured")

        # Minimal C program
        source = "int main(void) { return 0; }\n"

        result = self._try_compile(compiler, source, extra_flags=[flag])
        self._config.set(cache_key, result.success)
        return result

    def check_header(self, header: str) -> CheckResult:
        """Check if a header file is available.

        Args:
            header: Header to check (e.g., 'stdio.h', 'pthread.h').

        Returns:
            CheckResult indicating success/failure.
        """
        cache_key = self._cache_key("header", header)
        cached = self._config.get(cache_key)
        if cached is not None:
            return CheckResult(success=cached, cached=True)

        compiler = self._get_compiler()
        if compiler is None:
            return CheckResult(success=False, output="No compiler configured")

        source = f"#include <{header}>\nint main(void) {{ return 0; }}\n"

        result = self._try_compile(compiler, source)
        self._config.set(cache_key, result.success)
        return result

    def check_type(
        self, type_name: str, *, headers: list[str] | None = None
    ) -> CheckResult:
        """Check if a type is defined.

        Args:
            type_name: Type to check (e.g., 'size_t', 'int64_t').
            headers: Headers to include.

        Returns:
            CheckResult indicating success/failure.
        """
        cache_key = self._cache_key("type", type_name)
        cached = self._config.get(cache_key)
        if cached is not None:
            return CheckResult(success=cached, cached=True)

        compiler = self._get_compiler()
        if compiler is None:
            return CheckResult(success=False, output="No compiler configured")

        includes = ""
        if headers:
            includes = "\n".join(f"#include <{h}>" for h in headers)

        source = f"{includes}\nint main(void) {{ {type_name} x; (void)x; return 0; }}\n"

        result = self._try_compile(compiler, source)
        self._config.set(cache_key, result.success)
        return result

    def check_type_size(
        self, type_name: str, *, headers: list[str] | None = None
    ) -> int | None:
        """Get the size of a type.

        Args:
            type_name: Type to check (e.g., 'int', 'long', 'void*').
            headers: Headers to include.

        Returns:
            Size in bytes, or None if check failed.
        """
        cache_key = self._cache_key("sizeof", type_name)
        cached = self._config.get(cache_key)
        if cached is not None:
            return int(cached)

        compiler = self._get_compiler()
        if compiler is None:
            return None

        includes = ""
        if headers:
            includes = "\n".join(f"#include <{h}>" for h in headers)

        # Use compile-time assertion to encode the size
        # This avoids needing to run the compiled program
        for size in [1, 2, 4, 8, 16]:
            source = f"""
{includes}
int check[sizeof({type_name}) == {size} ? 1 : -1];
int main(void) {{ return 0; }}
"""
            result = self._try_compile(compiler, source)
            if result.success:
                self._config.set(cache_key, size)
                return size

        return None

    def check_define(self, define: str) -> str | None:
        """Get the value of a predefined macro.

        Args:
            define: Macro name (e.g., '__GNUC__', '_MSC_VER').

        Returns:
            Macro value as string, or None if not defined.
        """
        cache_key = self._cache_key("define", define)
        cached = self._config.get(cache_key)
        if cached is not None:
            return cached if cached != "__UNDEFINED__" else None

        compiler = self._get_compiler()
        if compiler is None:
            return None

        # Use preprocessor to output the macro value
        source = f"""
#ifdef {define}
PCONS_VALUE={define}
#else
PCONS_UNDEFINED
#endif
"""
        result = self._try_preprocess(compiler, source)
        if not result.success:
            self._config.set(cache_key, "__UNDEFINED__")
            return None

        # Parse the output to find the value
        for line in result.output.split("\n"):
            if line.startswith("PCONS_VALUE="):
                value = line[len("PCONS_VALUE=") :].strip()
                self._config.set(cache_key, value)
                return value
            if "PCONS_UNDEFINED" in line:
                self._config.set(cache_key, "__UNDEFINED__")
                return None

        self._config.set(cache_key, "__UNDEFINED__")
        return None

    def check_function(
        self,
        function: str,
        *,
        headers: list[str] | None = None,
        libs: list[str] | None = None,
    ) -> CheckResult:
        """Check if a function is available.

        Args:
            function: Function name (e.g., 'pthread_create').
            headers: Headers to include.
            libs: Libraries to link.

        Returns:
            CheckResult indicating success/failure.
        """
        cache_key = self._cache_key("function", function)
        cached = self._config.get(cache_key)
        if cached is not None:
            return CheckResult(success=cached, cached=True)

        compiler = self._get_compiler()
        if compiler is None:
            return CheckResult(success=False, output="No compiler configured")

        includes = ""
        if headers:
            includes = "\n".join(f"#include <{h}>" for h in headers)

        # Try to get address of function to check if it exists
        source = f"""
{includes}
int main(void) {{
    void *p = (void*){function};
    (void)p;
    return 0;
}}
"""
        extra_flags: list[str] = []
        if libs:
            extra_flags.extend(f"-l{lib}" for lib in libs)

        result = self._try_compile(compiler, source, extra_flags=extra_flags, link=True)
        self._config.set(cache_key, result.success)
        return result

    def _try_compile(
        self,
        compiler: str,
        source: str,
        *,
        extra_flags: list[str] | None = None,
        link: bool = False,
    ) -> CheckResult:
        """Try to compile source code.

        Args:
            compiler: Compiler command.
            source: Source code to compile.
            extra_flags: Additional compiler flags.
            link: If True, also link the program.

        Returns:
            CheckResult with compilation result.
        """
        suffix = ".c" if self._tool_name == "cc" else ".cpp"

        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / f"check{suffix}"
            out_path = Path(tmpdir) / "check.out"

            src_path.write_text(source)

            cmd = [compiler]
            if not link:
                cmd.append("-c")
            cmd.extend(["-o", str(out_path), str(src_path)])
            if extra_flags:
                cmd.extend(extra_flags)

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return CheckResult(
                    success=result.returncode == 0,
                    output=result.stderr + result.stdout,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                return CheckResult(success=False, output=str(e))

    def _try_preprocess(self, compiler: str, source: str) -> CheckResult:
        """Run the preprocessor on source code.

        Args:
            compiler: Compiler command.
            source: Source code to preprocess.

        Returns:
            CheckResult with preprocessor output.
        """
        suffix = ".c" if self._tool_name == "cc" else ".cpp"

        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / f"check{suffix}"
            src_path.write_text(source)

            cmd = [compiler, "-E", str(src_path)]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return CheckResult(
                    success=result.returncode == 0,
                    output=result.stdout,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                return CheckResult(success=False, output=str(e))

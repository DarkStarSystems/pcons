# SPDX-License-Identifier: MIT
"""Feature checking for pcons configure phase.

Provides utilities for testing compiler features, headers,
libraries, and other system capabilities.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.debug import trace

if TYPE_CHECKING:
    from pcons.configure.config import Configure
    from pcons.core.environment import Environment


#: Cache placeholder for "this macro is not defined" (None can't be cached:
#: an absent key and a cached None are indistinguishable).
_CACHE_UNDEFINED = "__UNDEFINED__"

#: Label emitted by the macro probe, and the token standing for "not defined".
_PROBE_MARKER = "PCONS_PROBE"
_PROBE_UNDEFINED = "PCONS_UNDEFINED"


def _define_lines(defines: list[str] | None) -> str:
    """Render *defines* as ``#define`` directives, one per line.

    An entry is either a bare macro name (``_XOPEN_SOURCE``) or
    ``NAME=value`` (``HAVE_FOO=1``); the latter has to become
    ``#define NAME value``, since ``#define NAME=value`` defines nothing.

    Returns "" for no defines, otherwise text ending in a newline.
    """
    if not defines:
        return ""
    lines: list[str] = []
    for entry in defines:
        name, sep, value = entry.partition("=")
        lines.append(f"#define {name} {value}".rstrip() if sep else f"#define {entry}")
    return "\n".join(lines) + "\n"


def _probe_source(
    names: list[str],
    headers: list[str | Path] | None,
    defines: list[str] | None,
) -> str:
    """Build the preprocessor probe that reports the value of each name.

    Each macro is labelled by its **index**, never by its name: the
    preprocessor would expand the name on the left-hand side too, so
    ``PCONS_PROBE FOO = FOO`` comes back as ``PCONS_PROBE 42 = 42`` with
    nothing left to key the answer on.  Do not "simplify" this back.

    Undefined macros report a sentinel token rather than an empty
    right-hand side, because an empty right-hand side is the answer for a
    macro that *is* defined and expands to nothing.  The marker and the
    sentinel are ``#undef``'d after the includes so that a header defining
    either one cannot make them expand.

    Headers are included in quoted form: the probe lives alone in a fresh
    temporary directory, so the "next to the including file" search finds
    nothing, and quoted form is the one that reliably accepts absolute
    paths (including Windows drive-letter paths, normalized to forward
    slashes here).
    """
    lines: list[str] = []
    define_text = _define_lines(defines)
    if define_text:
        lines.append(define_text.rstrip("\n"))
    for header in headers or []:
        lines.append(f'#include "{Path(header).as_posix()}"')
    lines.append(f"#undef {_PROBE_MARKER}")
    lines.append(f"#undef {_PROBE_UNDEFINED}")
    for index, name in enumerate(names):
        lines.append(f"#ifdef {name}")
        lines.append(f"{_PROBE_MARKER} {index} = {name}")
        lines.append("#else")
        lines.append(f"{_PROBE_MARKER} {index} = {_PROBE_UNDEFINED}")
        lines.append("#endif")
    return "\n".join(lines) + "\n"


def _parse_probe_output(output: str, names: list[str]) -> dict[str, str | None]:
    """Extract macro values from the output of _probe_source().

    Lines that aren't probe results (line markers, blank lines, whatever
    the headers themselves emitted) are ignored.
    """
    results: dict[str, str | None] = dict.fromkeys(names)
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith(_PROBE_MARKER):
            continue
        label, separator, value = line[len(_PROBE_MARKER) :].partition("=")
        if not separator:
            continue
        try:
            name = names[int(label.strip())]
        except (ValueError, IndexError):
            continue
        value = value.strip()
        results[name] = None if value == _PROBE_UNDEFINED else value
    return results


def cache_signature(*parts: str) -> str:
    """Short, stable signature over *parts* for configure cache keys.

    Used wherever a cache entry's validity depends on ambient state (the
    compiler command and its flags, the PATH): the signature goes into the
    key, so changed state misses the cache instead of returning answers
    computed under a different configuration.
    """
    joined = "\x00".join(parts)
    return hashlib.sha1(joined.encode(errors="replace")).hexdigest()[:12]


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

    # Shared across all instances so check dirs are uniquely numbered
    _check_counter: int = 0

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

    @staticmethod
    def _caller_location(depth: int = 2) -> str:
        """Get caller's file:line for trace output."""
        import inspect

        frame = inspect.stack()[depth]
        return f"{frame.filename}:{frame.lineno}"

    @staticmethod
    def _source_preview(source: str, maxlen: int = 80) -> str:
        """One-line preview of source code for trace output."""
        # Collapse to single line, strip leading/trailing whitespace
        oneline = " ".join(source.split())
        if len(oneline) > maxlen:
            return oneline[:maxlen] + "..."
        return oneline

    def _get_compiler(self) -> str | None:
        """Get the compiler command."""
        if self._tool_config is None:
            return None
        return getattr(self._tool_config, "cmd", None)

    def _tool_flags(self) -> list[str]:
        """The tool's current string flags, included in every check compile.

        Cross presets put ``--target=``/``-isysroot``/``--sysroot`` here,
        so a check probes the same compilation the build will do.
        """
        if self._tool_config is None:
            return []
        flags = self._tool_config.get("flags")
        if not isinstance(flags, list):
            return []
        return [f for f in flags if isinstance(f, str)]

    def _cache_key(self, check_type: str, *args: str) -> str:
        """Generate a cache key for a check.

        Keyed by a signature of the compiler command *and its flags*, so
        the same clang binary targeting different platforms (host vs
        ``--target=wasm32-wasi``) never shares cached answers.
        """
        compiler = self._get_compiler() or "unknown"
        sig = cache_signature(compiler, *self._tool_flags())
        return f"check:{self._tool_name}:{sig}:{check_type}:{':'.join(args)}"

    def _cached_or_compiler(self, cache_key: str) -> CheckResult | str:
        """Shared preamble for the check_* methods.

        Returns a CheckResult when the answer is already cached or no compiler
        is configured (the caller returns it directly), otherwise the compiler
        command to use.
        """
        cached = self._config.get(cache_key)
        if cached is not None:
            trace("configure", "  cached: %s", cached)
            return CheckResult(success=cached, cached=True)
        compiler = self._get_compiler()
        if compiler is None:
            return CheckResult(success=False, output="No compiler configured")
        return compiler

    def try_compile(
        self,
        source: str,
        *,
        extra_flags: list[str] | None = None,
        link: bool = False,
    ) -> CheckResult:
        """Try to compile (and optionally link) arbitrary source code.

        Use this for custom compile checks that aren't covered by the
        higher-level methods like check_header() or check_type().

        Results are cached based on a hash of the source code and flags.

        Args:
            source: Source code to compile.
            extra_flags: Additional compiler flags.
            link: If True, also link the program.

        Returns:
            CheckResult indicating success/failure.

        Example::

            checks = ToolChecks(config, env, "cxx")

            has_feature = checks.try_compile('''
                #include <optional>
                int main() { std::optional<int> x = 42; return *x; }
            ''').success
        """
        import hashlib

        code_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
        flags_str = ":".join(extra_flags) if extra_flags else ""
        link_str = "link" if link else "compile"
        trace(
            "configure",
            "try_compile: %s%s (%s) at %s",
            link_str,
            f" flags={extra_flags}" if extra_flags else "",
            self._tool_name,
            self._caller_location(),
        )
        trace("configure", "  source: %s", self._source_preview(source))
        cache_key = self._cache_key("try_compile", code_hash, flags_str, link_str)
        outcome = self._cached_or_compiler(cache_key)
        if isinstance(outcome, CheckResult):
            return outcome
        compiler = outcome

        cdir = self._make_check_dir()
        try:
            result = self._try_compile(
                compiler, source, extra_flags=extra_flags, link=link, check_dir=cdir
            )
        finally:
            self._cleanup_check_dir(*cdir)
        trace("configure", "  result: %s", "yes" if result.success else "no")
        self._config.set(cache_key, result.success)
        return result

    def _is_msvc_style(self) -> bool:
        """Whether the configured compiler uses MSVC-style command lines.

        MSVC and clang-cl (in its cl.exe-compatible mode) both take
        slash-flags (/c, /Fo, /Fe, /E, /WX, ...) instead of the Unix-style
        flags (-c, -o, -l, -E, -Werror, ...) used by GCC/Clang.
        """
        toolchain = getattr(self._env, "_toolchain", None)
        tc_name = getattr(toolchain, "name", "") if toolchain else ""
        if tc_name:
            return tc_name in ("msvc", "clang-cl")
        # No toolchain object attached to the environment (e.g. a bare
        # Environment with cc.cmd set directly) -- fall back to sniffing the
        # compiler executable's name.
        compiler = self._get_compiler() or ""
        return Path(compiler).stem.lower() in ("cl", "clang-cl")

    def _get_werror_flag(self) -> str:
        """Return the appropriate treat-warnings-as-errors flag.

        MSVC and clang-cl use /WX; GCC and Clang use -Werror.
        """
        return "/WX" if self._is_msvc_style() else "-Werror"

    def _lib_flag(self, lib: str) -> str:
        """Return the command-line argument to link against a library.

        MSVC/clang-cl take a bare "name.lib" argument; GCC/Clang take "-lname".
        """
        if self._is_msvc_style():
            return lib if lib.lower().endswith(".lib") else f"{lib}.lib"
        return f"-l{lib}"

    def _include_flag(self, directory: str | Path) -> str:
        """Return the command-line argument adding an include directory."""
        prefix = "/I" if self._is_msvc_style() else "-I"
        return f"{prefix}{Path(directory).as_posix()}"

    def check_flag(self, flag: str) -> CheckResult:
        """Check if the compiler accepts a flag.

        Compiles a minimal program with the flag to test if it's accepted.
        Uses -Werror (or /WX for MSVC/clang-cl) so that flags which produce
        warnings (e.g., clang's "unknown warning option") are rejected.

        Args:
            flag: Compiler flag to test (e.g., '-Wall', '-std=c++20').

        Returns:
            CheckResult indicating success/failure.
        """
        trace(
            "configure",
            "check_flag: %s (%s) at %s",
            flag,
            self._tool_name,
            self._caller_location(),
        )
        cache_key = self._cache_key("flag", flag)
        outcome = self._cached_or_compiler(cache_key)
        if isinstance(outcome, CheckResult):
            return outcome
        compiler = outcome

        source = "int main(void) { return 0; }\n"
        werror = self._get_werror_flag()

        cdir = self._make_check_dir()
        try:
            result = self._try_compile(
                compiler, source, extra_flags=[werror, flag], check_dir=cdir
            )
        finally:
            self._cleanup_check_dir(*cdir)
        trace("configure", "  result: %s", "yes" if result.success else "no")
        self._config.set(cache_key, result.success)
        return result

    def check_header(
        self,
        header: str,
        *,
        defines: list[str] | None = None,
        extra_flags: list[str] | None = None,
    ) -> CheckResult:
        """Check if a header file is available.

        Args:
            header: Header to check (e.g., 'stdio.h', 'pthread.h').
            defines: Preprocessor defines needed to include the header
                (e.g., ['_XOPEN_SOURCE'] for ucontext.h on macOS).
            extra_flags: Additional compiler flags.

        Returns:
            CheckResult indicating success/failure.

        Example::

            # ucontext.h requires _XOPEN_SOURCE on macOS
            checks.check_header("ucontext.h", defines=["_XOPEN_SOURCE"])
        """
        trace(
            "configure",
            "check_header: %s (%s) at %s",
            header,
            self._tool_name,
            self._caller_location(),
        )
        # Defines/flags go in the cache key: different combos, different answers.
        cache_parts = [header]
        if defines:
            cache_parts.extend(sorted(defines))
        if extra_flags:
            cache_parts.extend(sorted(extra_flags))
        cache_key = self._cache_key("header", *cache_parts)
        outcome = self._cached_or_compiler(cache_key)
        if isinstance(outcome, CheckResult):
            return outcome
        compiler = outcome

        # Via the shared helper: the old inline form emitted "#define FOO=1"
        # for the documented "NAME=value" spelling, which defines nothing.
        define_lines = _define_lines(defines)

        source = f"{define_lines}#include <{header}>\nint main(void) {{ return 0; }}\n"

        cdir = self._make_check_dir()
        try:
            result = self._try_compile(
                compiler, source, extra_flags=extra_flags, check_dir=cdir
            )
        finally:
            self._cleanup_check_dir(*cdir)
        trace("configure", "  result: %s", "yes" if result.success else "no")
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
        trace(
            "configure",
            "check_type: %s (%s) at %s",
            type_name,
            self._tool_name,
            self._caller_location(),
        )
        # Headers go in the cache key: a type may only exist via a
        # specific header.
        cache_parts = [type_name]
        if headers:
            cache_parts.extend(sorted(headers))
        cache_key = self._cache_key("type", *cache_parts)
        outcome = self._cached_or_compiler(cache_key)
        if isinstance(outcome, CheckResult):
            return outcome
        compiler = outcome

        includes = ""
        if headers:
            includes = "\n".join(f"#include <{h}>" for h in headers)

        source = f"{includes}\nint main(void) {{ {type_name} x; (void)x; return 0; }}\n"

        cdir = self._make_check_dir()
        try:
            result = self._try_compile(compiler, source, check_dir=cdir)
        finally:
            self._cleanup_check_dir(*cdir)
        trace("configure", "  result: %s", "yes" if result.success else "no")
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
        trace(
            "configure",
            "check_type_size: %s (%s) at %s",
            type_name,
            self._tool_name,
            self._caller_location(),
        )
        # Headers go in the cache key, as in check_type().
        cache_parts = [type_name]
        if headers:
            cache_parts.extend(sorted(headers))
        cache_key = self._cache_key("sizeof", *cache_parts)
        cached = self._config.get(cache_key)
        if cached is not None:
            trace("configure", "  cached: %s", cached)
            return int(cached)

        compiler = self._get_compiler()
        if compiler is None:
            return None

        includes = ""
        if headers:
            includes = "\n".join(f"#include <{h}>" for h in headers)

        # Compile-time assertion encodes the size; nothing is executed.
        cdir = self._make_check_dir()
        try:
            for size in [1, 2, 4, 8, 16]:
                source = f"""
{includes}
int check[sizeof({type_name}) == {size} ? 1 : -1];
int main(void) {{ return 0; }}
"""
                result = self._try_compile(compiler, source, check_dir=cdir)
                if result.success:
                    trace("configure", "  result: %d bytes", size)
                    self._config.set(cache_key, size)
                    return size
        finally:
            self._cleanup_check_dir(*cdir)

        trace("configure", "  result: unknown size")
        return None

    def check_define(
        self,
        define: str,
        *,
        headers: list[str | Path] | None = None,
        defines: list[str] | None = None,
        include_dirs: list[str | Path] | None = None,
    ) -> str | None:
        """Read a macro's value, from the compiler or from a header.

        With no *headers* this reports compiler builtins (``__GNUC__``,
        ``_MSC_VER``). Passing headers makes it read constants out of the
        project's own headers — version strings, feature flags, install
        paths — which is what most of a configure step actually does.

        Args:
            define: Macro name.
            headers: Headers to include before reading the macro.
            defines: Macros to predefine first, as ``NAME`` or ``NAME=value``
                (note this is *input* to the probe; the macro being read is
                the ``define`` argument).
            include_dirs: Directories to search for *headers*.

        Returns:
            The macro's expansion text, or None when it isn't defined. Four
            cases are distinguishable:

            =============================  ==================
            source                         returned
            =============================  ==================
            (not defined)                  ``None``
            ``#define FOO``                ``""``
            ``#define FOO 42``             ``"42"``
            ``#define FOO "Sapphire"``     ``'"Sapphire"'``
            =============================  ==================

            Quotes are kept, so a string literal is distinguishable from a
            number and from a defined-but-empty macro, and the value can go
            straight into a generated config header. Strip them yourself if
            you want the bare text. Function-like macros are not expanded.

        Example:
            # core/version.h:  #define VERSION_NAME "Sapphire 2024"
            name = checks.check_define(
                "VERSION_NAME", headers=["core/version.h"], include_dirs=[src_dir]
            )  # -> '"Sapphire 2024"'
        """
        trace(
            "configure",
            "check_define: %s (%s) at %s",
            define,
            self._tool_name,
            self._caller_location(),
        )
        return self._check_defines(
            [define], headers=headers, defines=defines, include_dirs=include_dirs
        )[define]

    def check_defines(
        self,
        names: list[str],
        *,
        headers: list[str | Path] | None = None,
        defines: list[str] | None = None,
        include_dirs: list[str | Path] | None = None,
    ) -> dict[str, str | None]:
        """Read several macros in a single preprocessor run.

        Same answers as :meth:`check_define` (see it for the return contract),
        but one process instead of one per macro — configure latency is
        dominated by process startup, and reading a dozen constants out of one
        header is the common case.

        Args:
            names: Macro names to read.
            headers: Headers to include before reading them.
            defines: Macros to predefine first (``NAME`` or ``NAME=value``).
            include_dirs: Directories to search for *headers*.

        Returns:
            ``{name: value-or-None}`` in the order given.

        Example:
            values = checks.check_defines(
                ["USE_RLM", "USE_DONGLES", "LICENSE_TYPE"],
                headers=["support/config.h"],
                include_dirs=[src_dir],
            )
        """
        trace(
            "configure",
            "check_defines: %s (%s) at %s",
            ", ".join(names),
            self._tool_name,
            self._caller_location(),
        )
        return self._check_defines(
            names, headers=headers, defines=defines, include_dirs=include_dirs
        )

    def _define_cache_key(
        self,
        name: str,
        headers: list[str | Path] | None,
        defines: list[str] | None,
        include_dirs: list[str | Path] | None,
    ) -> str:
        """Cache key for one macro read in one probe context.

        The context belongs in the key: the same macro name means different
        things after different headers, predefines, or include paths. Lists
        are not sorted (include order is semantic) and each is labelled, so
        two different lists can't collapse to the same signature. With no
        context the key keeps its historical shape, so cached builtin lookups
        survive this change.
        """
        context: list[str] = []
        if headers:
            context.extend(["headers", *(str(h) for h in headers)])
        if defines:
            context.extend(["defines", *defines])
        if include_dirs:
            context.extend(["include_dirs", *(str(d) for d in include_dirs)])
        if not context:
            return self._cache_key("define", name)
        return self._cache_key("define", name, cache_signature(*context))

    def _check_defines(
        self,
        names: list[str],
        *,
        headers: list[str | Path] | None,
        defines: list[str] | None,
        include_dirs: list[str | Path] | None,
    ) -> dict[str, str | None]:
        """Read *names*, answering from the cache and probing only the rest."""
        results: dict[str, str | None] = {}
        missing: list[str] = []
        for name in names:
            cached = self._config.get(
                self._define_cache_key(name, headers, defines, include_dirs)
            )
            if cached is None:
                missing.append(name)
            else:
                results[name] = None if cached == _CACHE_UNDEFINED else cached

        if not missing:
            trace("configure", "  cached: %s", results)
            return {name: results[name] for name in names}

        compiler = self._get_compiler()
        if compiler is None:
            return {name: results.get(name) for name in names}

        source = _probe_source(missing, headers, defines)
        extra_flags = [self._include_flag(d) for d in include_dirs or []]
        cdir = self._make_check_dir()
        try:
            result = self._try_preprocess(
                compiler, source, check_dir=cdir, extra_flags=extra_flags
            )
        finally:
            self._cleanup_check_dir(*cdir)

        if not result.success:
            # Never cache a probe that wouldn't preprocess: a missing header is
            # an error condition, not an answer about the macro. With staged
            # generation (project.generated_input()) a header may simply not
            # exist yet on the first configure pass, and a cached "no such
            # header" would stick after the build produced it.
            trace("configure", "  probe failed; not caching")
            return {name: results.get(name) for name in names}

        probed = _parse_probe_output(result.output, missing)
        for name, value in probed.items():
            self._config.set(
                self._define_cache_key(name, headers, defines, include_dirs),
                _CACHE_UNDEFINED if value is None else value,
            )
            results[name] = value

        trace("configure", "  result: %s", results)
        return {name: results[name] for name in names}

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
        trace(
            "configure",
            "check_function: %s (%s) at %s",
            function,
            self._tool_name,
            self._caller_location(),
        )
        # Headers/libs go in the cache key: they change what the probe
        # compiles/links against.
        cache_parts = [function]
        if headers:
            cache_parts.extend(sorted(headers))
        if libs:
            cache_parts.extend(sorted(libs))
        cache_key = self._cache_key("function", *cache_parts)
        outcome = self._cached_or_compiler(cache_key)
        if isinstance(outcome, CheckResult):
            return outcome
        compiler = outcome

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
            extra_flags.extend(self._lib_flag(lib) for lib in libs)

        cdir = self._make_check_dir()
        try:
            result = self._try_compile(
                compiler, source, extra_flags=extra_flags, link=True, check_dir=cdir
            )
        finally:
            self._cleanup_check_dir(*cdir)
        trace("configure", "  result: %s", "yes" if result.success else "no")
        self._config.set(cache_key, result.success)
        return result

    def _make_check_dir(self) -> tuple[Path, bool]:
        """Create a directory for a configure check.

        When --debug=configure is active, creates a persistent numbered
        directory under <build_dir>/.configure-checks/ so users can
        inspect the source files and compiler output.  Otherwise uses
        a temporary directory that will be cleaned up by the caller.

        Returns:
            (directory_path, persistent) — persistent=True means the caller
            should NOT delete it.
        """
        from pcons.core.debug import is_enabled

        ToolChecks._check_counter += 1

        if is_enabled("configure"):
            base = self._config.build_dir / ".configure-checks"
            check_dir = base / f"check_{self._check_counter:03d}"
            check_dir.mkdir(parents=True, exist_ok=True)
            trace("configure", "  dir: %s", check_dir)
            return check_dir, True

        tmpdir = Path(tempfile.mkdtemp())
        return tmpdir, False

    @staticmethod
    def _cleanup_check_dir(check_dir: Path, persistent: bool) -> None:
        """Remove a check directory if it's not persistent."""
        if not persistent:
            import shutil

            shutil.rmtree(check_dir, ignore_errors=True)

    def _try_compile(
        self,
        compiler: str,
        source: str,
        *,
        extra_flags: list[str] | None = None,
        link: bool = False,
        check_dir: tuple[Path, bool] | None = None,
    ) -> CheckResult:
        """Try to compile source code.

        Args:
            compiler: Compiler command.
            source: Source code to compile.
            extra_flags: Additional compiler flags.
            link: If True, also link the program.
            check_dir: Optional (dir, persistent) from _make_check_dir().
                If not provided, creates and manages its own.

        Returns:
            CheckResult with compilation result.
        """
        suffix = ".c" if self._tool_name == "cc" else ".cpp"
        owns_dir = check_dir is None
        dir_path, persistent = check_dir if check_dir else self._make_check_dir()

        try:
            src_path = dir_path / f"check{suffix}"
            src_path.write_text(source)

            # Tool flags first, so the check probes the target the build
            # will use (see _tool_flags).
            tool_flags = self._tool_flags()
            if self._is_msvc_style():
                out_path = dir_path / ("check.exe" if link else "check.obj")
                cmd = [compiler, "/nologo"]
                cmd.extend(tool_flags)
                if extra_flags:
                    cmd.extend(extra_flags)
                if not link:
                    cmd.append("/c")
                cmd.append(f"/Fe{out_path}" if link else f"/Fo{out_path}")
                cmd.append(str(src_path))
            else:
                out_path = dir_path / "check.out"
                cmd = [compiler]
                cmd.extend(tool_flags)
                if not link:
                    cmd.append("-c")
                cmd.extend(["-o", str(out_path), str(src_path)])
                if extra_flags:
                    cmd.extend(extra_flags)

            trace("configure", "  cmd: %s", " ".join(cmd))

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = result.stderr + result.stdout
                if result.returncode != 0:
                    trace("configure", "  exit code: %d", result.returncode)
                    if output.strip():
                        for line in output.strip().splitlines()[:10]:
                            trace("configure", "  | %s", line)
                return CheckResult(
                    success=result.returncode == 0,
                    output=output,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                trace("configure", "  error: %s", e)
                return CheckResult(success=False, output=str(e))
        finally:
            if owns_dir:
                self._cleanup_check_dir(dir_path, persistent)

    def _try_preprocess(
        self,
        compiler: str,
        source: str,
        *,
        check_dir: tuple[Path, bool] | None = None,
        extra_flags: list[str] | None = None,
    ) -> CheckResult:
        """Run the preprocessor on source code.

        Args:
            compiler: Compiler command.
            source: Source code to preprocess.
            check_dir: Optional (dir, persistent) from _make_check_dir().
            extra_flags: Additional flags (e.g. include directories).

        Returns:
            CheckResult with preprocessor output.
        """
        suffix = ".c" if self._tool_name == "cc" else ".cpp"
        owns_dir = check_dir is None
        dir_path, persistent = check_dir if check_dir else self._make_check_dir()
        extra = list(extra_flags or [])

        try:
            src_path = dir_path / f"check{suffix}"
            src_path.write_text(source)

            # Include tool flags: predefined macros are target-dependent
            # (e.g. __wasm__ under --target=wasm32-wasi).
            if self._is_msvc_style():
                cmd = [
                    compiler,
                    "/nologo",
                    *self._tool_flags(),
                    *extra,
                    "/E",
                    str(src_path),
                ]
            else:
                cmd = [compiler, *self._tool_flags(), *extra, "-E", str(src_path)]
            trace("configure", "  cmd: %s", " ".join(cmd))

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    trace("configure", "  exit code: %d", result.returncode)
                    if result.stderr.strip():
                        for line in result.stderr.strip().splitlines()[:10]:
                            trace("configure", "  | %s", line)
                return CheckResult(
                    success=result.returncode == 0,
                    output=result.stdout,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                trace("configure", "  error: %s", e)
                return CheckResult(success=False, output=str(e))
        finally:
            if owns_dir:
                self._cleanup_check_dir(dir_path, persistent)

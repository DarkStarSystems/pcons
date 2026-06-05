# SPDX-License-Identifier: MIT
"""Test runner for example projects.

Discovers and runs all example projects in examples/.
Each example is a self-contained project that serves as both
a test and documentation for users.

Tests both invocation methods:
- Direct: python pcons-build.py
- CLI: python -m pcons
"""

from __future__ import annotations

import fnmatch
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Try to import tomllib (Python 3.11+) or tomli as fallback
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
IS_WINDOWS = platform.system().lower() == "windows"


def _find_ninja() -> list[str] | None:
    """Find a working ninja command, trying PATH then uvx."""
    if shutil.which("ninja") is not None:
        return ["ninja"]
    if shutil.which("uvx") is not None:
        return ["uvx", "ninja"]
    return None


# Generators to test
# xcode generator works on all platforms but xcodebuild only runs on macOS
GENERATORS = ["ninja", "make", "xcode"]


def adapt_path_for_windows(path: str, gcc_toolchain: bool = False) -> str:
    """Adapt a Unix-style path for Windows.

    Converts:
        ./build/program -> build\\program.exe
        build/file.o -> build\\file.obj (not with gcc_toolchain==True, as GCC on Windows still uses .o)
        build/libfoo.a -> build\\foo.lib
        build/libfoo.so -> build\\foo.dll
        build/program (no extension) -> build\\program.exe
    """
    # Convert forward slashes to backslashes
    path = path.replace("/", "\\")

    # Remove leading .\
    if path.startswith(".\\"):
        path = path[2:]

    if path.endswith("EXT}"):
        # Don't adapt if extension variables are used, as they will be substituted later
        return path

    # Convert extensions
    if path.endswith(".o") and not gcc_toolchain:
        path = path[:-2] + ".obj"
    elif path.endswith(".a"):
        # Convert libfoo.a to foo.lib
        import re

        path = re.sub(r"\\lib([^\\]+)\.a$", r"\\\1.lib", path)
        if path.endswith(".a"):  # Didn't match lib prefix
            path = path[:-2] + ".lib"
    elif path.endswith(".so"):
        # Convert libfoo.so to foo.dll
        import re

        path = re.sub(r"\\lib([^\\]+)\.so$", r"\\\1.dll", path)
        if path.endswith(".so"):  # Didn't match lib prefix
            path = path[:-3] + ".dll"

    # Add .exe to executables (paths in build/ without extension)
    if "\\build\\" in path or path.startswith("build\\"):
        parts = path.rsplit("\\", 1)
        if len(parts) == 2 and "." not in parts[1]:
            path = path + ".exe"

    return path


def adapt_command_for_windows(cmd: str) -> str:
    """Adapt a Unix-style command for Windows.

    Converts:
        cat file -> type file
        ./build/program -> build\\program.exe
    """
    # Convert cat to type
    if cmd.startswith("cat "):
        cmd = "type " + cmd[4:].replace("/", "\\")
    else:
        # Just adapt the path portion
        parts = cmd.split(maxsplit=1)
        if parts:
            parts[0] = adapt_path_for_windows(parts[0])
            cmd = " ".join(parts)

    return cmd


def parse_ninja_output(output: str) -> tuple[list[str], bool]:
    """Parse ninja output to extract rebuilt targets.

    Returns:
        Tuple of (list of rebuilt target paths, is_no_work)
    """
    # Check for "ninja: no work to do."
    is_no_work = "ninja: no work to do." in output

    # Extract targets from lines like "[1/2] RULE target_path"
    # The format is: [N/M] RULE_NAME target_path
    rebuilt_targets: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        # Match lines starting with [N/M]
        if line.startswith("[") and "]" in line:
            # Extract everything after the bracket
            rest = line.split("]", 1)[1].strip()
            # Split into rule name and target path
            parts = rest.split(maxsplit=1)
            if len(parts) >= 2:
                target_path = parts[1]
                rebuilt_targets.append(target_path)

    return rebuilt_targets, is_no_work


def run_rebuild_test(
    work_dir: Path,
    build_dir: Path,
    rebuild_config: dict[str, Any],
    toolchain: str | None = None,
) -> None:
    """Run a single rebuild test scenario.

    Args:
        work_dir: Example directory
        build_dir: Build output directory
        rebuild_config: Dict with keys like 'description', 'touch', 'expect_rebuild',
                       'expect_no_rebuild', 'expect_no_work'
        toolchain: Optional toolchain name (used for platform path adaptation)
    """
    description = rebuild_config.get("description", "unnamed rebuild test")

    # 1. Touch file if 'touch' specified
    touch_file = rebuild_config.get("touch")
    if touch_file:
        touch_path = work_dir / touch_file
        if not touch_path.exists():
            pytest.fail(
                f"Rebuild test '{description}': touch file not found: {touch_file}"
            )
        # Update modification time
        touch_path.touch()

    # 2. Run ninja -C build_dir
    ninja_cmd = _find_ninja()
    assert ninja_cmd is not None, "ninja not available"
    result = subprocess.run(
        [*ninja_cmd, "-C", str(build_dir)],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"Ninja stdout:\n{result.stdout}")
        print(f"Ninja stderr:\n{result.stderr}")
        pytest.fail(
            f"Rebuild test '{description}': ninja failed with code {result.returncode}"
        )

    # 3. Parse output with parse_ninja_output()
    rebuilt_targets, is_no_work = parse_ninja_output(result.stdout)

    # 4. Verify expectations
    # If expect_no_work: verify no_work is True
    if rebuild_config.get("expect_no_work"):
        if not is_no_work:
            pytest.fail(
                f"Rebuild test '{description}': expected no work, "
                f"but ninja rebuilt: {rebuilt_targets}"
            )

    # Adapt expected paths for the current platform
    gcc_tc = toolchain == "gcc"

    def _adapt(p: str) -> str:
        if not IS_WINDOWS:
            return p
        adapted = adapt_path_for_windows(p, gcc_toolchain=gcc_tc)
        # Rebuild paths are build-dir-relative bare names (e.g. "hello");
        # adapt_path_for_windows only adds .exe for build/-prefixed paths.
        if "\\" not in adapted and "." not in adapted and adapted:
            adapted += ".exe"
        return adapted

    # If expect_rebuild: verify each target was rebuilt
    expect_rebuild = [_adapt(p) for p in rebuild_config.get("expect_rebuild", [])]
    for expected in expect_rebuild:
        found = any(fnmatch.fnmatch(target, expected) for target in rebuilt_targets)
        if not found:
            pytest.fail(
                f"Rebuild test '{description}': expected '{expected}' to be rebuilt, "
                f"but rebuilt targets were: {rebuilt_targets}"
            )

    # If expect_no_rebuild: verify each target was NOT rebuilt
    expect_no_rebuild = [_adapt(p) for p in rebuild_config.get("expect_no_rebuild", [])]
    for not_expected in expect_no_rebuild:
        found = any(fnmatch.fnmatch(target, not_expected) for target in rebuilt_targets)
        if found:
            pytest.fail(
                f"Rebuild test '{description}': expected '{not_expected}' NOT to be rebuilt, "
                f"but it was in rebuilt targets: {rebuilt_targets}"
            )


def discover_examples() -> list[Path]:
    """Discover all example directories that have a pcons-build.py and test.toml."""
    examples = []
    if not EXAMPLES_DIR.exists():
        return examples

    for item in sorted(EXAMPLES_DIR.iterdir()):
        if (
            item.is_dir()
            and (item / "pcons-build.py").exists()
            and (item / "test.toml").exists()
        ):
            examples.append(item)

    return examples


def load_test_config(example_dir: Path) -> dict[str, Any]:
    """Load test.toml configuration."""
    if tomllib is None:
        pytest.skip("tomllib/tomli not available")

    config_file = example_dir / "test.toml"
    with open(config_file, "rb") as f:
        return tomllib.load(f)


def should_skip(config: dict[str, Any]) -> str | None:
    """Check if this test should be skipped. Returns skip reason or None."""
    skip_config = config.get("skip", {})

    # Check platform
    skip_platforms = skip_config.get("platforms", [])
    current_platform = platform.system().lower()
    if current_platform in [p.lower() for p in skip_platforms]:
        return f"Skipped on {current_platform}"

    # Check required tools (all must be present)
    # "requires" and "require_commands" are aliases
    requires = skip_config.get("requires", []) + skip_config.get("require_commands", [])
    for tool in requires:
        if shutil.which(tool) is None:
            return f"Required tool '{tool}' not found"

    # Check requires_any (at least one must be present)
    requires_any = skip_config.get("requires_any", [])
    if requires_any:
        if not any(shutil.which(tool) is not None for tool in requires_any):
            return f"None of required tools found: {', '.join(requires_any)}"

    # Check required environment variables
    require_env = skip_config.get("require_env", [])
    for var in require_env:
        if not os.environ.get(var):
            return f"Required environment variable '{var}' not set"

    def _check_msvc_module_support() -> bool | str:
        """On Windows we expect MSVC (which has its own std-module path).
        There we just check that cl.exe is on PATH.
        """
        if shutil.which("cl.exe") is None and shutil.which("cl") is None:
            return "cl.exe not on PATH — run vcvars64.bat first"

    def _check_libcxx_std_module_support() -> bool | str:
        """Check for libc++ std module support.
        Check clang is on PATH and use internal manifest lookup
        to check for libc++ std module support.
        """
        clang = shutil.which("clang++") or shutil.which("clang")
        if clang is None:
            return "clang not found (needed for libc++ std module check)"
        from pcons.toolchains.llvm import _find_libcxx_modules_manifest

        if _find_libcxx_modules_manifest(clang, []) is None:
            return "libc++.modules.json not found - libc++ std module support requires Homebrew LLVM (macOS) or libc++-dev (Linux)"

    def _check_gcc_std_module_support() -> bool | str:
        """Check for GCC std module support.
        Check g++ is on PATH and use internal source lookup
        to check for GCC std module support.
        """
        gxx = shutil.which("g++")
        if gxx is None:
            return "g++ not found (needed for GCC std module check)"
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        if _find_gcc_std_module_source(gxx, "std", []) is None:
            return "GCC std module support requires GCC 15+ with libstdc++"

    # check for C++ std module support if required by the test config
    if skip_config.get("requires_cxx_std_module"):
        toolchain = config.get("toolchain")
        if toolchain is None:
            # toolchain not specified:
            #   - On Windows, assume MSVC
            #   - On MacOs, assume clang
            #   - On Linux, check for gcc then clang
            if current_platform == "windows":
                if (check := _check_msvc_module_support()) is not None:
                    return check
            elif current_platform == "darwin":
                if (check := _check_libcxx_std_module_support()) is not None:
                    return check
            else:  # assume linux
                if (check := _check_gcc_std_module_support()) is not None:
                    return check
                if (check := _check_libcxx_std_module_support()) is not None:
                    return check
        else:
            if toolchain == "msvc":
                if (check := _check_msvc_module_support()) is not None:
                    return check
            elif toolchain == "llvm":
                if (check := _check_libcxx_std_module_support()) is not None:
                    return check
            elif toolchain == "gcc":
                if (check := _check_gcc_std_module_support()) is not None:
                    return check
    return None  # not skipped


def _toolchain_is_available(toolchain: str, current_platform: str) -> bool:
    """Check whether a requested toolchain is available on this host."""
    if toolchain == "msvc":
        return current_platform == "windows" and (
            shutil.which("cl.exe") is not None or shutil.which("cl") is not None
        )

    if toolchain == "llvm":
        clang = shutil.which("clang++") or shutil.which("clang")
        if clang is None:
            return False
        return True

    if toolchain == "gcc":
        gxx = shutil.which("g++")
        if gxx is None:
            return False
        return True

    return False


def get_requested_toolchains(config: dict[str, Any]) -> list[str | None]:
    """Get requested toolchains for the current platform from test config."""
    current_platform = platform.system().lower()
    test_config = config.get("test", {})

    toolchains_table = config.get("toolchains")
    if isinstance(toolchains_table, dict):
        requested_toolchains = toolchains_table.get(current_platform)
    else:
        requested_toolchains = get_platform_value(test_config, "toolchains")

    # Backward-compatible fallback to toolchains_<platform> in [test]
    if requested_toolchains is None:
        requested_toolchains = get_platform_value(test_config, "toolchains")

    if requested_toolchains is None:
        single_toolchain = get_platform_value(test_config, "toolchain")
        return [single_toolchain] if single_toolchain else [None]

    return list(requested_toolchains)


def adapt_outputs_for_generator(
    outputs: list[str], generator: str, project_name: str = ""
) -> list[str]:
    """Adapt expected outputs for the generator being used.

    When testing with make generator, build.ninja should become Makefile.
    When testing with xcode generator:
    - build.ninja becomes <project>.xcodeproj
    - Object files in obj.*/ are skipped (xcode manages intermediates internally)
    - Final products are mapped to build/Build/Products/Release/

    Args:
        outputs: List of expected output paths.
        generator: Generator being used ("ninja", "make", or "xcode").
        project_name: Project name for xcode generator output.

    Returns:
        List of adapted output paths.
    """
    # On macOS plain shared libraries use .dylib regardless of generator, but
    # Python extension modules keep .so (CPython's EXT_SUFFIX is e.g. .cpython-314-darwin.so).
    if platform.system().lower() == "darwin":

        def _is_py_ext(path: str) -> bool:
            base = path.rsplit("/", 1)[-1]
            return any(m in base for m in (".cpython-", ".abi3.", ".pypy"))

        outputs = [
            o[:-3] + ".dylib" if o.endswith(".so") and not _is_py_ext(o) else o
            for o in outputs
        ]

    if generator == "ninja":
        return outputs

    result = []
    for output in outputs:
        # Check for build.ninja with both forward and backslash paths (Windows compat)
        is_build_ninja = (
            output == "build/build.ninja"
            or output == "build\\build.ninja"
            or output.endswith("/build.ninja")
            or output.endswith("\\build.ninja")
        )
        if is_build_ninja:
            if generator == "make":
                result.append(output.replace("build.ninja", "Makefile"))
            elif generator == "xcode":
                # For xcode, replace build.ninja with project.xcodeproj/project.pbxproj
                xcodeproj_name = (
                    f"{project_name}.xcodeproj" if project_name else "project.xcodeproj"
                )
                result.append(
                    output.replace("build.ninja", f"{xcodeproj_name}/project.pbxproj")
                )
        elif generator == "xcode":
            # For xcode, handle different output paths
            # xcodebuild puts outputs in build/Release/ when the xcodeproj
            # is in build/ and xcodebuild runs from the project root
            import re

            # Skip object files - xcode manages intermediates internally
            # Match patterns like build/obj.*/file.o or build/*/file.o
            if output.endswith(".o") or "/obj." in output or "\\obj." in output:
                continue

            # Map final products from build/<name> to build/Release/<name>
            # Handle paths like build/hello, build/debug/variant_demo, etc.
            # Match: build/<something> where <something> has no extension
            match = re.match(r"^build/([^/]+)$", output)
            if match:
                product_name = match.group(1)
                # Skip if it has a file extension (like .a, .dylib)
                if "." not in product_name:
                    result.append(f"build/Release/{product_name}")
                    continue

            # Handle build/<subdir>/<name> patterns (like build/debug/variant_demo)
            match = re.match(r"^build/([^/]+)/([^/]+)$", output)
            if match:
                subdir, product_name = match.groups()
                # Skip if subdir looks like an obj directory or has extension
                if not subdir.startswith("obj") and "." not in product_name:
                    result.append(f"build/Release/{product_name}")
                    continue

            # For libraries, map to xcode output location
            if output.endswith(".a") or output.endswith(".dylib"):
                # Extract just the filename and put in Release folder
                filename = output.rsplit("/", 1)[-1]
                result.append(f"build/Release/{filename}")
                continue

            # Keep other paths as-is
            result.append(output)
        else:
            result.append(output)
    return result


def get_platform_value(
    config: dict[str, Any],
    key: str,
    default: Any = None,
    adapt_for_windows: bool = False,
    gcc_toolchain: bool = False,
) -> Any:
    """Get a platform-specific value from config.

    Supports both simple values and platform-specific overrides:
        key = "value"                    # Simple value for all platforms
        key_windows = "windows_value"    # Windows-specific override
        key_linux = "linux_value"        # Linux-specific override
        key_darwin = "macos_value"       # macOS-specific override

    Args:
        config: Configuration dictionary
        key: Key to look up
        default: Default value if key not found
        adapt_for_windows: If True and on Windows without a platform-specific
            override, automatically adapt Unix paths/commands
        gcc_toolchain: If True and using GCC toolchain, apply GCC-specific adaptations
    Returns the platform-specific value if available, otherwise the base value.
    """
    current_platform = platform.system().lower()
    platform_key = f"{key}_{current_platform}"

    # Check for platform-specific override first
    if platform_key in config:
        return config[platform_key]

    # Get base value
    value = config.get(key, default)

    # Optionally adapt for Windows when no override exists
    if adapt_for_windows and IS_WINDOWS and value is not None:
        if isinstance(value, list):
            result = []
            for v in value:
                if isinstance(v, list):
                    # [path, content] pair — adapt only the path (first element)
                    result.append(
                        [adapt_path_for_windows(str(v[0]), gcc_toolchain=gcc_toolchain)]
                        + v[1:]
                    )
                else:
                    result.append(
                        adapt_path_for_windows(str(v), gcc_toolchain=gcc_toolchain)
                    )
            return result
        elif isinstance(value, str):
            return adapt_path_for_windows(value, gcc_toolchain=gcc_toolchain)

    return value


def _patch_pyproject(work_dir: Path) -> None:
    """Inject [tool.uv.sources] into a copied example's pyproject.toml.

    When an example declares pcons as a build-system requirement, uv would
    normally fetch it from PyPI.  During development pcons.pyproject may not
    yet be published, so we redirect uv to the local checkout by appending a
    [tool.uv.sources] section with an absolute path.  This only affects the
    temporary copy; the original example file is left untouched.
    """
    pyproject_file = work_dir / "pyproject.toml"
    if not pyproject_file.exists() or tomllib is None:
        return

    with open(pyproject_file, "rb") as f:
        data = tomllib.load(f)

    build_requires: list[str] = data.get("build-system", {}).get("requires", [])
    needs_pcons = any(
        req == "pcons" or req.startswith("pcons[") or req.startswith("pcons>=")
        for req in build_requires
    )
    if not needs_pcons:
        return

    # Leave a user-configured pcons source untouched.
    existing_sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    if "pcons" in existing_sources:
        return

    import tomli_w

    pcons_root = Path(__file__).parent.parent
    data.setdefault("tool", {}).setdefault("uv", {}).setdefault("sources", {})[
        "pcons"
    ] = {"path": str(pcons_root)}

    with open(pyproject_file, "wb") as f:
        tomli_w.dump(data, f)


def _run_generate(
    work_dir: Path,
    build_dir: Path,
    invocation: str,
    generator: str,
    test_config: dict[str, Any],
    variant: str | None = None,
    variables: dict[str, str] | None = None,
) -> None:
    """Run the build script to generate build files.

    Args:
        work_dir: Working directory (copied example).
        build_dir: Base build directory.
        invocation: "direct" or "cli".
        generator: Generator name.
        test_config: Test configuration dict.
        variant: Optional variant name (e.g., "debug", "release").
        variables: Optional build variables (KEY=value) for the build script.
    """
    from pcons import Project

    Project._clear_tree()
    if invocation == "direct":
        from pcons.cli import run_script

        build_script = work_dir / "pcons-build.py"
        try:
            exit_code, _projects = run_script(
                build_script,
                build_dir,
                variables=variables,
                generator=generator,
                variant=variant,
            )
            if exit_code != 0:
                variant_msg = f" (variant={variant})" if variant else ""
                pytest.fail(f"pcons-build.py failed with code {exit_code}{variant_msg}")
        except Exception as e:
            variant_msg = f" (variant={variant})" if variant else ""
            pytest.fail(f"pcons-build.py raised {type(e).__name__}: {e}{variant_msg}")
    else:
        cmd = [sys.executable, "-m", "pcons"]
        if variables:
            cmd.extend([f"{k}={v}" for k, v in variables.items()])
        env = {
            **os.environ,
            "PCONS_BUILD_DIR": str(build_dir),
            "PCONS_GENERATOR": generator,
        }
        if variant:
            env["PCONS_VARIANT"] = variant
        timeout = test_config.get("timeout", 60)
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            print(f"pcons stdout:\n{result.stdout}")
            print(f"pcons stderr:\n{result.stderr}")
            variant_msg = f" (variant={variant})" if variant else ""
            pytest.fail(f"pcons failed with code {result.returncode}{variant_msg}")


_variable_expr = re.compile(r"\$\{([^}]+)\}")


def _example_template_vars() -> dict[str, str]:
    """Platform-derived substitutions for ``${...}`` placeholders in test.toml."""
    from pcons.configure.platform import get_platform

    plat = get_platform()
    # Shared libraries install next to executables on DLL platforms (Windows),
    # matching Toolchain.get_install_dir("shared_library").
    shared_install_dir = "bin" if plat.shared_lib_suffix == ".dll" else "lib"
    return {
        "BINARY_EXT": plat.exe_suffix,
        "LIBRARY_EXT": plat.shared_lib_suffix,
        "ARCHIVE_EXT": plat.static_lib_suffix,
        "LIBRARY_PREFIX": plat.shared_lib_prefix,
        "BINARY_INSTALL_DIR": "bin",
        "LIBRARY_INSTALL_DIR": shared_install_dir,
        "ARCHIVE_INSTALL_DIR": "lib",
    }


def _substitute_variables(path: str, extra: dict[str, str] | None = None) -> str:
    """Substitute ``${VAR}`` placeholders in a string.

    *extra* supplies example-defined variables from the ``[test.variables]``
    section, which take precedence over the built-in platform-derived ones and
    let a test.toml factor out long inline scripts (see ``examples/50_pyproject``).
    """
    table = _example_template_vars()
    if extra:
        table = {**table, **extra}

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        if var_name not in table:
            raise KeyError(
                f"Unknown test.toml template variable: ${{{var_name}}}. "
                f"Known: {', '.join(sorted(table))}"
            )
        return table[var_name]

    return _variable_expr.sub(replacer, path)


def run_example(
    example_dir: Path,
    tmp_path: Path,
    invocation: str = "direct",
    generator: str = "ninja",
    toolchain: str | None = None,
) -> None:
    """Run a single example project.

    Args:
        example_dir: Path to the example directory
        tmp_path: Temporary directory for test isolation
        invocation: How to invoke the build script:
            - "direct": python pcons-build.py
            - "cli": python -m pcons
        generator: Which generator to use:
            - "ninja": Generate build.ninja
            - "make": Generate Makefile
        toolchain: Optional toolchain name to pass as TOOLCHAIN build variable.
    """
    config = load_test_config(example_dir)
    config["toolchain"] = toolchain
    test_config = config.get("test", {})

    # Check skip conditions
    skip_reason = should_skip(config)
    if skip_reason:
        pytest.skip(skip_reason)

    # Check if this generator should be skipped
    skip_config = config.get("skip", {})
    skip_generators = skip_config.get("generators", [])
    if generator in [g.lower() for g in skip_generators]:
        pytest.skip(f"Skipped for {generator} generator")

    # CLI invocation requires ninja (pcons CLI runs ninja after generation)
    # Skip CLI tests for examples that use custom build commands (e.g., make)
    if invocation == "cli" and test_config.get("build_command"):
        pytest.skip("CLI invocation requires ninja; this example uses custom build")

    # CLI invocation with xcode generator runs xcodebuild automatically, which is macOS-only
    if (
        invocation == "cli"
        and generator == "xcode"
        and platform.system().lower() != "darwin"
    ):
        pytest.skip(
            "CLI invocation with xcode generator requires xcodebuild (macOS only)"
        )

    # Copy example to temp directory (so we don't pollute the source tree)
    work_dir = tmp_path / example_dir.name
    shutil.copytree(
        example_dir,
        work_dir,
        ignore=shutil.ignore_patterns("build", "compile_commands.json"),
    )

    # If the example uses pcons as a PEP 517 build backend, inject a
    # [tool.uv.sources] override pointing to the local pcons checkout so
    # that `uv sync` (with isolation) installs the dev version rather than
    # falling back to PyPI (which may not yet have the pcons.pyproject module).
    _patch_pyproject(work_dir)

    build_dir = work_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Check for variants (CMake-style: run the script once per variant)
    variants = test_config.get("variants", [None])
    current_platform = platform.system().lower()

    if toolchain is not None and not _toolchain_is_available(
        toolchain, current_platform
    ):
        pytest.skip(f"Requested toolchain '{toolchain}' is not available on this host")

    build_dir.mkdir(parents=True, exist_ok=True)
    tc_vars = {"TOOLCHAIN": toolchain} if toolchain else {}

    # Check expected install outputs exist (auto-adapts for Windows if no override)
    expected_install_outputs = get_platform_value(
        test_config, "expected_install_outputs", [], adapt_for_windows=True
    )

    build_targets = get_platform_value(test_config, "build_targets", [])

    install_prefix = None
    if generator != "xcode":
        if expected_install_outputs:
            if "install" not in build_targets:
                build_targets.append("install")

            install_prefix = tempfile.TemporaryDirectory()
            tc_vars["PCONS_INSTALL_PREFIX"] = install_prefix.name

    for variant in variants:
        _run_generate(
            work_dir,
            build_dir,
            invocation,
            generator,
            test_config,
            variant,
            variables=tc_vars,
        )

    # For variant builds, collect all variant build dirs for the build step
    variant_build_dirs = (
        [work_dir / "build" / v for v in variants if v is not None]
        if any(v is not None for v in variants)
        else [build_dir]
    )

    # Check for custom build command or use appropriate build tool
    build_command = test_config.get("build_command")

    if build_command:
        # Custom build command (e.g., "make -C build")
        # Check for required tool (first word of command)
        build_tool = build_command.split()[0]
        if shutil.which(build_tool) is None:
            pytest.skip(f"{build_tool} not available")

        result = subprocess.run(
            build_command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            print(f"Build stdout:\n{result.stdout}")
            print(f"Build stderr:\n{result.stderr}")
            pytest.fail(f"Build command failed with code {result.returncode}")
    elif generator == "ninja":
        ninja_cmd_base = _find_ninja()
        if ninja_cmd_base is None:
            pytest.skip("ninja not available")

        for vbd in variant_build_dirs:
            ninja_file = vbd / "build.ninja"
            if not ninja_file.exists():
                pytest.fail(f"build.ninja not generated in {vbd}")

            ninja_cmd = [*ninja_cmd_base, "-C", str(vbd)] + build_targets
            result = subprocess.run(
                ninja_cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                print(f"Ninja stdout:\n{result.stdout}")
                print(f"Ninja stderr:\n{result.stderr}")
                print(f"build.ninja contents:\n{ninja_file.read_text()}")
                pytest.fail(f"ninja failed with code {result.returncode}")
    elif generator == "make":
        if shutil.which("make") is None:
            pytest.skip("make not available")

        for vbd in variant_build_dirs:
            makefile = vbd / "Makefile"
            if not makefile.exists():
                pytest.fail(f"Makefile not generated in {vbd}")

            make_cmd = ["make", "-C", str(vbd)] + build_targets
            result = subprocess.run(
                make_cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                print(f"Make stdout:\n{result.stdout}")
                print(f"Make stderr:\n{result.stderr}")
                print(f"Makefile contents:\n{makefile.read_text()}")
                pytest.fail(f"make failed with code {result.returncode}")
    elif generator == "xcode":
        # Use xcodebuild (macOS only)
        # Find the .xcodeproj in the build directory
        xcodeproj_files = list(build_dir.glob("*.xcodeproj"))
        if not xcodeproj_files:
            pytest.fail(f"No .xcodeproj generated in {build_dir}")

        xcodeproj = xcodeproj_files[0]

        if shutil.which("xcodebuild") is None:
            pytest.skip("xcodebuild not available (macOS only)")

        # Run xcodebuild
        result = subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(xcodeproj),
                "-configuration",
                "Release",
                "CODE_SIGNING_ALLOWED=NO",  # avoid keychain prompts in CI/tests
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=180,  # xcodebuild can be slow
        )

        if result.returncode != 0:
            print(f"xcodebuild stdout:\n{result.stdout}")
            print(f"xcodebuild stderr:\n{result.stderr}")
            pytest.fail(f"xcodebuild failed with code {result.returncode}")

    # Check expected outputs exist (auto-adapts for Windows if no override)
    expected_outputs = get_platform_value(
        test_config,
        "expected_outputs",
        [],
        adapt_for_windows=True,
        gcc_toolchain=toolchain == "gcc",
    )
    # Adapt expected outputs for the generator being used
    # For xcode, get project name from any generated .xcodeproj
    project_name = ""
    if generator == "xcode":
        xcodeproj_files = list(build_dir.glob("*.xcodeproj"))
        if xcodeproj_files:
            # Extract name from "foo.xcodeproj" -> "foo"
            project_name = xcodeproj_files[0].stem
    expected_outputs = adapt_outputs_for_generator(
        expected_outputs, generator, project_name
    )
    for output in expected_outputs:
        output = _substitute_variables(output)
        if "*" in output:
            # Handle wildcard outputs (e.g., build/*.o)
            matches = list(work_dir.glob(output))
            if not matches:
                pytest.fail(f"Expected output not found: {output} (no matches)")
        else:
            output_path = work_dir / output
            if not output_path.exists():
                pytest.fail(f"Expected output not found: {output}")

    if generator != "xcode":
        # no install on xcode generator, so skip install output checks
        for output in expected_install_outputs:
            assert install_prefix is not None
            if isinstance(output, list):
                output, content = output[0], output[1]
            else:
                content = None
            output = _substitute_variables(output)
            output_path = Path(install_prefix.name) / output
            if not output_path.exists():
                pytest.fail(f"Expected install output not found: {output}")
            if content is not None:
                actual_content = output_path.read_text()
                if content not in actual_content:
                    pytest.fail(
                        f"Expected '{content}' in installed {output}, got:\n{actual_content}"
                    )

    if install_prefix is not None:
        install_prefix.cleanup()

    # Run verification commands (auto-adapts for Windows if no override)
    verify_config = config.get("verify", {})
    # Check if there's a platform-specific commands override
    current_platform = platform.system().lower()
    has_platform_override = f"commands_{current_platform}" in verify_config
    verify_commands = get_platform_value(verify_config, "commands", [])

    # Example-defined [test.variables] are available as ${name} placeholders in
    # verify commands, letting a test.toml factor out long inline scripts.
    test_variables = verify_config.get("variables", {})

    for cmd_config in verify_commands:
        run_cmd = cmd_config.get("run")
        if not run_cmd:
            continue

        # Expand ${...} placeholders (platform vars + [test.variables]) before
        # any command adaptation or path resolution.
        run_cmd = _substitute_variables(run_cmd, extra=test_variables)

        # Adapt command for Windows if no platform-specific override exists
        if IS_WINDOWS and not has_platform_override:
            run_cmd = adapt_command_for_windows(run_cmd)

        # Adapt ninja commands to make when using make generator
        if generator == "make" and run_cmd.startswith("ninja "):
            run_cmd = "make " + run_cmd[6:]  # Replace "ninja " with "make "

        # Adapt executable paths for xcode generator
        # Map ./build/<exe> to ./build/Release/<exe>
        # xcodebuild puts outputs in build/Release/ when the xcodeproj
        # is in build/ and xcodebuild runs from the project root
        if generator == "xcode":
            import re

            # Match ./build/<name> or build/<name> where <name> has no extension
            match = re.match(r"^(\./)?build/([^/\s]+)(\s.*)?$", run_cmd)
            if match:
                prefix = match.group(1) or ""
                exe_name = match.group(2)
                args = match.group(3) or ""
                # Only adapt if no extension (likely an executable)
                if "." not in exe_name:
                    run_cmd = f"{prefix}build/Release/{exe_name}{args}"

            # Handle build/<subdir>/<name> patterns (like build/debug/variant_demo)
            match = re.match(r"^(\./)?build/([^/]+)/([^/\s]+)(\s.*)?$", run_cmd)
            if match:
                prefix = match.group(1) or ""
                subdir = match.group(2)
                exe_name = match.group(3)
                args = match.group(4) or ""
                # Only adapt if it's not an obj directory and no extension
                if not subdir.startswith("obj") and "." not in exe_name:
                    run_cmd = f"{prefix}build/Release/{exe_name}{args}"

        # Resolve command path relative to work_dir
        cmd_path = work_dir / run_cmd.split()[0]  # Check first word as path
        if cmd_path.exists():
            run_cmd = str(cmd_path) + run_cmd[len(run_cmd.split()[0]) :]

        result = subprocess.run(
            run_cmd,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Check expected return code
        expected_code = cmd_config.get("expect_returncode", 0)
        if result.returncode != expected_code:
            print(f"Command stdout:\n{result.stdout}")
            print(f"Command stderr:\n{result.stderr}")
            pytest.fail(
                f"Command '{run_cmd}' returned {result.returncode}, "
                f"expected {expected_code}"
            )

        # Check expected stdout
        expect_stdout = cmd_config.get("expect_stdout")
        if expect_stdout is not None:
            if expect_stdout not in result.stdout:
                pytest.fail(
                    f"Expected '{expect_stdout}' in stdout, got:\n{result.stdout}"
                )

        # Check expected file content
        expect_file = cmd_config.get("expect_file")
        expect_content = cmd_config.get("expect_content")
        if expect_file and expect_content:
            file_path = work_dir / expect_file
            if not file_path.exists():
                pytest.fail(f"Expected file not found: {expect_file}")
            actual_content = file_path.read_text()
            if expect_content not in actual_content:
                pytest.fail(
                    f"Expected '{expect_content}' in {expect_file}, "
                    f"got:\n{actual_content}"
                )

    # Run rebuild tests (only for "direct" invocation with ninja generator)
    # Rebuild tests rely on ninja's incremental build infrastructure
    rebuild_tests = config.get("rebuild", [])
    if rebuild_tests and invocation == "direct" and generator == "ninja":
        skip_config = config.get("skip", {})
        # Check if rebuild tests should be skipped on Windows
        if sys.platform == "win32" and skip_config.get("rebuild_on_windows"):
            pass  # Skip rebuild tests on Windows
        else:
            # Make sure ninja is available for rebuild tests
            if _find_ninja() is None:
                pytest.skip("ninja not available for rebuild tests")

            for rebuild_config in rebuild_tests:
                run_rebuild_test(
                    work_dir, build_dir, rebuild_config, toolchain=toolchain
                )


# Discover examples and create test parameters
EXAMPLES = discover_examples()

# Invocation methods to test
INVOCATIONS = ["direct", "cli"]


def build_example_params() -> list[pytest.ParameterSet]:
    """Build a complete pytest parameter matrix, including toolchains."""
    params: list[pytest.ParameterSet] = []

    for example_dir in EXAMPLES:
        config = load_test_config(example_dir)
        requested_toolchains = get_requested_toolchains(config)

        for invocation in INVOCATIONS:
            for generator in GENERATORS:
                for toolchain in requested_toolchains:
                    test_id = f"{example_dir.name}-{invocation}-{generator}"
                    if toolchain:
                        test_id = f"{test_id}-{toolchain}"
                    params.append(
                        pytest.param(
                            example_dir,
                            invocation,
                            generator,
                            toolchain,
                            id=test_id,
                        )
                    )

    return params


EXAMPLE_PARAMS = build_example_params()


@pytest.mark.parametrize(
    ("example_dir", "invocation", "generator", "toolchain"),
    EXAMPLE_PARAMS,
)
def test_example(
    example_dir: Path,
    tmp_path: Path,
    invocation: str,
    generator: str,
    toolchain: str | None,
) -> None:
    """Run an example project end-to-end.

    Tests combinations of:
    - Invocation methods: direct (python pcons-build.py), cli (python -m pcons)
    - Generators: ninja (build.ninja), make (Makefile), xcode
    - Toolchains: from each example's test configuration
    """
    run_example(example_dir, tmp_path, invocation, generator, toolchain)


# If no examples found, create a placeholder test
if not EXAMPLE_PARAMS:

    def test_no_examples() -> None:
        """Placeholder when no examples are found."""
        pytest.skip("No example projects found in examples/")

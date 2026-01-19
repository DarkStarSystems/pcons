# SPDX-License-Identifier: MIT
"""Test runner for example projects.

Discovers and runs all example projects in examples/.
Each example is a self-contained project that serves as both
a test and documentation for users.

Tests both invocation methods:
- Direct: python build.py
- CLI: python -m pcons
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
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


def adapt_path_for_windows(path: str) -> str:
    """Adapt a Unix-style path for Windows.

    Converts:
        ./build/program -> build\\program.exe
        build/file.o -> build\\file.obj
        build/libfoo.a -> build\\foo.lib
        build/program (no extension) -> build\\program.exe
    """
    # Convert forward slashes to backslashes
    path = path.replace("/", "\\")

    # Remove leading .\
    if path.startswith(".\\"):
        path = path[2:]

    # Convert extensions
    if path.endswith(".o"):
        path = path[:-2] + ".obj"
    elif path.endswith(".a"):
        # Convert libfoo.a to foo.lib
        import re

        path = re.sub(r"\\lib([^\\]+)\.a$", r"\\\1.lib", path)
        if path.endswith(".a"):  # Didn't match lib prefix
            path = path[:-2] + ".lib"

    # Add .exe to executables (paths in build/ without extension)
    # Check if it's a build output without an extension
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


def discover_examples() -> list[Path]:
    """Discover all example directories that have a build.py and test.toml."""
    examples = []
    if not EXAMPLES_DIR.exists():
        return examples

    for item in sorted(EXAMPLES_DIR.iterdir()):
        if (
            item.is_dir()
            and (item / "build.py").exists()
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

    # Check required tools
    requires = skip_config.get("requires", [])
    for tool in requires:
        if shutil.which(tool) is None:
            return f"Required tool '{tool}' not found"

    return None


def get_platform_value(
    config: dict[str, Any],
    key: str,
    default: Any = None,
    adapt_for_windows: bool = False,
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
            return [adapt_path_for_windows(str(v)) for v in value]
        elif isinstance(value, str):
            return adapt_path_for_windows(value)

    return value


def run_example(example_dir: Path, tmp_path: Path, invocation: str = "direct") -> None:
    """Run a single example project.

    Args:
        example_dir: Path to the example directory
        tmp_path: Temporary directory for test isolation
        invocation: How to invoke the build script:
            - "direct": python build.py
            - "cli": python -m pcons
    """
    config = load_test_config(example_dir)
    test_config = config.get("test", {})

    # Check skip conditions
    skip_reason = should_skip(config)
    if skip_reason:
        pytest.skip(skip_reason)

    # CLI invocation requires ninja (pcons CLI runs ninja after generation)
    # Skip CLI tests for examples that use custom build commands (e.g., make)
    if invocation == "cli" and test_config.get("build_command"):
        pytest.skip("CLI invocation requires ninja; this example uses custom build")

    # Copy example to temp directory (so we don't pollute the source tree)
    work_dir = tmp_path / example_dir.name
    shutil.copytree(example_dir, work_dir)

    build_dir = work_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Run build script using specified invocation method
    if invocation == "direct":
        # Direct: python build.py
        build_script = work_dir / "build.py"
        cmd = [sys.executable, str(build_script)]
        cmd_desc = "build.py"
    else:
        # CLI: python -m pcons
        cmd = [sys.executable, "-m", "pcons"]
        cmd_desc = "pcons"

    result = subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PCONS_BUILD_DIR": str(build_dir)},
    )

    if result.returncode != 0:
        print(f"{cmd_desc} stdout:\n{result.stdout}")
        print(f"{cmd_desc} stderr:\n{result.stderr}")
        pytest.fail(f"{cmd_desc} failed with code {result.returncode}")

    # Check for custom build command or use ninja default
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
    else:
        # Default: use ninja
        ninja_file = build_dir / "build.ninja"
        if not ninja_file.exists():
            pytest.fail(f"build.ninja not generated in {build_dir}")

        if shutil.which("ninja") is None:
            pytest.skip("ninja not available")

        result = subprocess.run(
            ["ninja", "-f", str(ninja_file)],
            cwd=work_dir,  # Run from project root, not build dir
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            print(f"Ninja stdout:\n{result.stdout}")
            print(f"Ninja stderr:\n{result.stderr}")
            print(f"build.ninja contents:\n{ninja_file.read_text()}")
            pytest.fail(f"ninja failed with code {result.returncode}")

    # Check expected outputs exist (auto-adapts for Windows if no override)
    expected_outputs = get_platform_value(
        test_config, "expected_outputs", [], adapt_for_windows=True
    )
    for output in expected_outputs:
        output_path = work_dir / output
        if not output_path.exists():
            pytest.fail(f"Expected output not found: {output}")

    # Run verification commands (auto-adapts for Windows if no override)
    verify_config = config.get("verify", {})
    # Check if there's a platform-specific commands override
    current_platform = platform.system().lower()
    has_platform_override = f"commands_{current_platform}" in verify_config
    verify_commands = get_platform_value(verify_config, "commands", [])

    for cmd_config in verify_commands:
        run_cmd = cmd_config.get("run")
        if not run_cmd:
            continue

        # Adapt command for Windows if no platform-specific override exists
        if IS_WINDOWS and not has_platform_override:
            run_cmd = adapt_command_for_windows(run_cmd)

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


# Discover examples and create test parameters
EXAMPLES = discover_examples()

# Invocation methods to test
INVOCATIONS = ["direct", "cli"]


@pytest.mark.parametrize("invocation", INVOCATIONS, ids=INVOCATIONS)
@pytest.mark.parametrize(
    "example_dir",
    EXAMPLES,
    ids=[e.name for e in EXAMPLES],
)
def test_example(example_dir: Path, tmp_path: Path, invocation: str) -> None:
    """Run an example project end-to-end.

    Tests both invocation methods:
    - direct: python build.py
    - cli: python -m pcons
    """
    run_example(example_dir, tmp_path, invocation)


# If no examples found, create a placeholder test
if not EXAMPLES:

    def test_no_examples() -> None:
        """Placeholder when no examples are found."""
        pytest.skip("No example projects found in examples/")

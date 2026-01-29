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

# Generators to test
# xcode generator works on all platforms but xcodebuild only runs on macOS
GENERATORS = ["ninja", "make", "xcode"]


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
) -> None:
    """Run a single rebuild test scenario.

    Args:
        work_dir: Example directory
        build_dir: Build output directory
        rebuild_config: Dict with keys like 'description', 'touch', 'expect_rebuild',
                       'expect_no_rebuild', 'expect_no_work'
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
    result = subprocess.run(
        ["ninja", "-C", str(build_dir)],
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

    # If expect_rebuild: verify each target was rebuilt (substring match)
    expect_rebuild = rebuild_config.get("expect_rebuild", [])
    for expected in expect_rebuild:
        found = any(expected in target for target in rebuilt_targets)
        if not found:
            pytest.fail(
                f"Rebuild test '{description}': expected '{expected}' to be rebuilt, "
                f"but rebuilt targets were: {rebuilt_targets}"
            )

    # If expect_no_rebuild: verify each target was NOT rebuilt
    expect_no_rebuild = rebuild_config.get("expect_no_rebuild", [])
    for not_expected in expect_no_rebuild:
        found = any(not_expected in target for target in rebuilt_targets)
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
    requires = skip_config.get("requires", [])
    for tool in requires:
        if shutil.which(tool) is None:
            return f"Required tool '{tool}' not found"

    # Check requires_any (at least one must be present)
    requires_any = skip_config.get("requires_any", [])
    if requires_any:
        if not any(shutil.which(tool) is not None for tool in requires_any):
            return f"None of required tools found: {', '.join(requires_any)}"

    return None


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


def run_example(
    example_dir: Path,
    tmp_path: Path,
    invocation: str = "direct",
    generator: str = "ninja",
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
    """
    config = load_test_config(example_dir)
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

    # Copy example to temp directory (so we don't pollute the source tree)
    work_dir = tmp_path / example_dir.name
    shutil.copytree(example_dir, work_dir)

    build_dir = work_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Run build script using specified invocation method
    if invocation == "direct":
        # Direct: python pcons-build.py
        build_script = work_dir / "pcons-build.py"
        cmd = [sys.executable, str(build_script)]
        cmd_desc = "pcons-build.py"
    else:
        # CLI: python -m pcons
        cmd = [sys.executable, "-m", "pcons"]
        cmd_desc = "pcons"

    # Set up environment with generator choice
    env = {
        **os.environ,
        "PCONS_BUILD_DIR": str(build_dir),
        "PCONS_GENERATOR": generator,
    }

    result = subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    if result.returncode != 0:
        print(f"{cmd_desc} stdout:\n{result.stdout}")
        print(f"{cmd_desc} stderr:\n{result.stderr}")
        pytest.fail(f"{cmd_desc} failed with code {result.returncode}")

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
        # Use ninja
        ninja_file = build_dir / "build.ninja"
        if not ninja_file.exists():
            pytest.fail(f"build.ninja not generated in {build_dir}")

        if shutil.which("ninja") is None:
            pytest.skip("ninja not available")

        # Get build targets (if specified)
        build_targets = get_platform_value(test_config, "build_targets", [])

        # Run ninja from the build directory using -C
        ninja_cmd = ["ninja", "-C", str(build_dir)] + build_targets
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
        # Use make
        makefile = build_dir / "Makefile"
        if not makefile.exists():
            pytest.fail(f"Makefile not generated in {build_dir}")

        if shutil.which("make") is None:
            pytest.skip("make not available")

        # Get build targets (if specified)
        build_targets = get_platform_value(test_config, "build_targets", [])

        # Run make from the build directory using -C
        make_cmd = ["make", "-C", str(build_dir)] + build_targets
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
            ["xcodebuild", "-project", str(xcodeproj), "-configuration", "Release"],
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
        test_config, "expected_outputs", [], adapt_for_windows=True
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
            if shutil.which("ninja") is None:
                pytest.skip("ninja not available for rebuild tests")

            for rebuild_config in rebuild_tests:
                run_rebuild_test(work_dir, build_dir, rebuild_config)


# Discover examples and create test parameters
EXAMPLES = discover_examples()

# Invocation methods to test
INVOCATIONS = ["direct", "cli"]


@pytest.mark.parametrize("generator", GENERATORS, ids=GENERATORS)
@pytest.mark.parametrize("invocation", INVOCATIONS, ids=INVOCATIONS)
@pytest.mark.parametrize(
    "example_dir",
    EXAMPLES,
    ids=[e.name for e in EXAMPLES],
)
def test_example(
    example_dir: Path, tmp_path: Path, invocation: str, generator: str
) -> None:
    """Run an example project end-to-end.

    Tests combinations of:
    - Invocation methods: direct (python pcons-build.py), cli (python -m pcons)
    - Generators: ninja (build.ninja), make (Makefile)
    """
    run_example(example_dir, tmp_path, invocation, generator)


# If no examples found, create a placeholder test
if not EXAMPLES:

    def test_no_examples() -> None:
        """Placeholder when no examples are found."""
        pytest.skip("No example projects found in examples/")

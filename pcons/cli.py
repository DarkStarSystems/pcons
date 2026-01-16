# SPDX-License-Identifier: MIT
"""Command-line interface for pcons."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Set up logging
logger = logging.getLogger("pcons")


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """Configure logging based on verbosity level."""
    if debug:
        level = logging.DEBUG
        fmt = "%(levelname)s: %(name)s: %(message)s"
    elif verbose:
        level = logging.INFO
        fmt = "%(levelname)s: %(message)s"
    else:
        level = logging.WARNING
        fmt = "%(levelname)s: %(message)s"

    logging.basicConfig(level=level, format=fmt)


def find_script(name: str, search_dir: Path | None = None) -> Path | None:
    """Find a build script by name.

    Args:
        name: Script name (e.g., 'configure.py', 'build.py')
        search_dir: Directory to search in (default: current dir)

    Returns:
        Path to script if found, None otherwise.
    """
    if search_dir is None:
        search_dir = Path.cwd()

    script_path = search_dir / name
    if script_path.exists() and script_path.is_file():
        return script_path

    return None


def run_script(script_path: Path, build_dir: Path) -> int:
    """Execute a Python build script.

    Args:
        script_path: Path to the script to run.
        build_dir: Build directory to pass to the script.

    Returns:
        Exit code from script execution.
    """
    # Set environment variables for the script
    env = os.environ.copy()
    env["PCONS_BUILD_DIR"] = str(build_dir.absolute())
    env["PCONS_SOURCE_DIR"] = str(script_path.parent.absolute())

    logger.info("Running %s", script_path)
    logger.debug("  PCONS_BUILD_DIR=%s", env["PCONS_BUILD_DIR"])
    logger.debug("  PCONS_SOURCE_DIR=%s", env["PCONS_SOURCE_DIR"])

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            env=env,
            cwd=script_path.parent,
        )
        return result.returncode
    except OSError as e:
        logger.error("Failed to run script: %s", e)
        return 1


def cmd_configure(args: argparse.Namespace) -> int:
    """Run the configure phase.

    This command:
    1. Finds configure.py in the current directory
    2. Creates the build directory
    3. Runs configure.py to detect tools and save configuration
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    script_path = args.configure_script

    # Find configure script
    script: Path
    if script_path:
        script = Path(script_path)
        if not script.exists():
            logger.error("Configure script not found: %s", script_path)
            return 1
    else:
        found_script = find_script("configure.py")
        if found_script is None:
            logger.error("No configure.py found in current directory")
            logger.info("Create a configure.py file to define your configuration")
            return 1
        script = found_script

    # Create build directory
    build_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Build directory: %s", build_dir.absolute())

    # Run configure script
    return run_script(script, build_dir)


def cmd_generate(args: argparse.Namespace) -> int:
    """Run the generate phase.

    This command:
    1. Finds build.py in the current directory
    2. Runs build.py to define the build
    3. Generates build.ninja in the build directory
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    script_path = args.build_script

    # Find build script
    script: Path
    if script_path:
        script = Path(script_path)
        if not script.exists():
            logger.error("Build script not found: %s", script_path)
            return 1
    else:
        found_script = find_script("build.py")
        if found_script is None:
            logger.error("No build.py found in current directory")
            logger.info("Create a build.py file to define your build")
            return 1
        script = found_script

    # Create build directory if it doesn't exist
    build_dir.mkdir(parents=True, exist_ok=True)

    # Run build script
    return run_script(script, build_dir)


def cmd_build(args: argparse.Namespace) -> int:
    """Build targets using ninja.

    This command:
    1. Changes to the build directory
    2. Runs ninja with the specified targets
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    ninja_file = build_dir / "build.ninja"

    if not ninja_file.exists():
        logger.error("No build.ninja found in %s", build_dir)
        logger.info("Run 'pcons generate' first to create build files")
        return 1

    # Find ninja
    ninja = shutil.which("ninja")
    if ninja is None:
        logger.error("ninja not found in PATH")
        logger.info("Install ninja: https://ninja-build.org/")
        return 1

    # Build ninja command
    cmd = [ninja, "-C", str(build_dir)]

    if args.jobs:
        cmd.extend(["-j", str(args.jobs)])

    if args.verbose:
        cmd.append("-v")

    if args.targets:
        cmd.extend(args.targets)

    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run ninja: %s", e)
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean build artifacts.

    This command:
    1. Runs 'ninja -t clean' if build.ninja exists
    2. Optionally removes the entire build directory with --all
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)

    if args.all:
        # Remove entire build directory
        if build_dir.exists():
            logger.info("Removing build directory: %s", build_dir)
            shutil.rmtree(build_dir)
            logger.info("Clean complete")
        else:
            logger.info("Build directory does not exist: %s", build_dir)
        return 0

    # Use ninja -t clean
    ninja_file = build_dir / "build.ninja"
    if not ninja_file.exists():
        logger.info("No build.ninja found, nothing to clean")
        return 0

    ninja = shutil.which("ninja")
    if ninja is None:
        logger.error("ninja not found in PATH")
        return 1

    cmd = [ninja, "-C", str(build_dir), "-t", "clean"]
    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run ninja: %s", e)
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new pcons project.

    Creates template configure.py and build.py files.
    """
    setup_logging(args.verbose, args.debug)

    configure_py = Path("configure.py")
    build_py = Path("build.py")

    if configure_py.exists() and not args.force:
        logger.error("configure.py already exists (use --force to overwrite)")
        return 1

    if build_py.exists() and not args.force:
        logger.error("build.py already exists (use --force to overwrite)")
        return 1

    # Write configure.py template
    configure_template = '''\
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Configure phase: detect tools and set up the build environment."""

import os
from pathlib import Path

from pcons.configure.config import Configure
from pcons.toolchains import GccToolchain, LlvmToolchain

# Get build directory from environment or use default
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))

# Create configure context
config = Configure(build_dir=build_dir)

# Try to find a C/C++ toolchain
# Prefer LLVM/Clang, fall back to GCC
llvm = LlvmToolchain()
gcc = GccToolchain()

if llvm.configure(config):
    config.set("toolchain", "llvm")
    print(f"Found LLVM/Clang toolchain")
elif gcc.configure(config):
    config.set("toolchain", "gcc")
    print(f"Found GCC toolchain")
else:
    print("Warning: No C/C++ toolchain found")

# Save configuration
config.save()
print(f"Configuration saved to {build_dir / 'pcons_config.json'}")
'''

    # Write build.py template
    build_template = '''\
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Build phase: define targets and generate ninja files."""

import os
from pathlib import Path

from pcons.configure.config import Configure, load_config
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import GccToolchain, LlvmToolchain

# Get directories from environment or use defaults
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

# Load configuration
config = Configure(build_dir=build_dir)
toolchain_name = config.get("toolchain", "gcc")

# Select toolchain
if toolchain_name == "llvm":
    toolchain = LlvmToolchain()
else:
    toolchain = GccToolchain()

toolchain.configure(config)

# Create project
project = Project("myproject", root_dir=source_dir, build_dir=build_dir)

# Create environment with toolchain
env = project.Environment(toolchain=toolchain)

# Define your build here
# Example:
# obj = env.cc.Object("hello.o", "hello.c")
# env.link.Program("hello", obj)

# Generate ninja file
generator = NinjaGenerator()
generator.generate(project, build_dir)
print(f"Generated {build_dir / 'build.ninja'}")
'''

    configure_py.write_text(configure_template)
    configure_py.chmod(0o755)
    logger.info("Created %s", configure_py)

    build_py.write_text(build_template)
    build_py.chmod(0o755)
    logger.info("Created %s", build_py)

    print("Project initialized!")
    print("Next steps:")
    print("  1. Edit build.py to define your build targets")
    print("  2. Run 'pcons configure' to detect tools")
    print("  3. Run 'pcons generate' to create build.ninja")
    print("  4. Run 'pcons build' to build your project")

    return 0


def main() -> int:
    """Main entry point for the pcons CLI."""
    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0-dev")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug output"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # pcons init
    init_parser = subparsers.add_parser(
        "init", help="Initialize a new pcons project"
    )
    init_parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite existing files"
    )
    init_parser.add_argument("-v", "--verbose", action="store_true")
    init_parser.add_argument("--debug", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    # pcons configure
    cfg_parser = subparsers.add_parser(
        "configure", help="Run configure phase (tool detection)"
    )
    cfg_parser.add_argument(
        "--build-dir", "-B", default="build", help="Build directory (default: build)"
    )
    cfg_parser.add_argument(
        "--configure-script", "-c", help="Path to configure.py script"
    )
    cfg_parser.add_argument("-v", "--verbose", action="store_true")
    cfg_parser.add_argument("--debug", action="store_true")
    cfg_parser.set_defaults(func=cmd_configure)

    # pcons generate
    gen_parser = subparsers.add_parser(
        "generate", help="Generate build files from build.py"
    )
    gen_parser.add_argument(
        "--build-dir", "-B", default="build", help="Build directory (default: build)"
    )
    gen_parser.add_argument(
        "--build-script", "-b", help="Path to build.py script"
    )
    gen_parser.add_argument("-v", "--verbose", action="store_true")
    gen_parser.add_argument("--debug", action="store_true")
    gen_parser.set_defaults(func=cmd_generate)

    # pcons build
    build_parser = subparsers.add_parser("build", help="Build targets using ninja")
    build_parser.add_argument("targets", nargs="*", help="Targets to build")
    build_parser.add_argument(
        "--build-dir", "-B", default="build", help="Build directory (default: build)"
    )
    build_parser.add_argument(
        "-j", "--jobs", type=int, help="Number of parallel jobs"
    )
    build_parser.add_argument("-v", "--verbose", action="store_true")
    build_parser.add_argument("--debug", action="store_true")
    build_parser.set_defaults(func=cmd_build)

    # pcons clean
    clean_parser = subparsers.add_parser("clean", help="Clean build artifacts")
    clean_parser.add_argument(
        "--build-dir", "-B", default="build", help="Build directory (default: build)"
    )
    clean_parser.add_argument(
        "--all", "-a", action="store_true", help="Remove entire build directory"
    )
    clean_parser.add_argument("-v", "--verbose", action="store_true")
    clean_parser.add_argument("--debug", action="store_true")
    clean_parser.set_defaults(func=cmd_clean)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())

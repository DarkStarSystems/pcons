# SPDX-License-Identifier: MIT
"""Command-line interface for pcons."""

from __future__ import annotations

import argparse
import json
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
        name: Script name (e.g., 'build.py')
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


def parse_variables(args: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse KEY=value arguments from a list.

    Args:
        args: List of arguments.

    Returns:
        Tuple of (variables dict, remaining args).
    """
    variables: dict[str, str] = {}
    remaining: list[str] = []

    for arg in args:
        if "=" in arg and not arg.startswith("-"):
            key, _, value = arg.partition("=")
            if key:  # Valid KEY=value
                variables[key] = value
            else:
                remaining.append(arg)
        else:
            remaining.append(arg)

    return variables, remaining


def run_script(
    script_path: Path,
    build_dir: Path,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    reconfigure: bool = False,
) -> int:
    """Execute a Python build script.

    Args:
        script_path: Path to the script to run.
        build_dir: Build directory to pass to the script.
        variables: Build variables to pass via PCONS_VARS.
        variant: Build variant to pass via PCONS_VARIANT.
        reconfigure: If True, set PCONS_RECONFIGURE=1.

    Returns:
        Exit code from script execution.
    """
    # Set environment variables for the script
    env = os.environ.copy()
    env["PCONS_BUILD_DIR"] = str(build_dir.absolute())
    env["PCONS_SOURCE_DIR"] = str(script_path.parent.absolute())

    if variables:
        env["PCONS_VARS"] = json.dumps(variables)

    if variant:
        env["PCONS_VARIANT"] = variant

    if reconfigure:
        env["PCONS_RECONFIGURE"] = "1"

    logger.info("Running %s", script_path)
    logger.debug("  PCONS_BUILD_DIR=%s", env["PCONS_BUILD_DIR"])
    logger.debug("  PCONS_SOURCE_DIR=%s", env["PCONS_SOURCE_DIR"])
    if variables:
        logger.debug("  PCONS_VARS=%s", env["PCONS_VARS"])
    if variant:
        logger.debug("  PCONS_VARIANT=%s", variant)

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


def run_ninja(
    build_dir: Path,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,
) -> int:
    """Run ninja in the build directory.

    Args:
        build_dir: Build directory containing build.ninja.
        targets: Specific targets to build.
        jobs: Number of parallel jobs.
        verbose: Enable verbose output.

    Returns:
        Exit code from ninja.
    """
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

    if jobs:
        cmd.extend(["-j", str(jobs)])

    if verbose:
        cmd.append("-v")

    if targets:
        cmd.extend(targets)

    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run ninja: %s", e)
        return 1


def cmd_default(args: argparse.Namespace) -> int:
    """Default command: generate and build.

    This is what runs when you just type 'pcons' with no subcommand.
    Equivalent to: pcons generate && pcons build
    """
    # First, generate
    result = cmd_generate(args)
    if result != 0:
        return result

    # Then, build
    return cmd_build(args)


def cmd_generate(args: argparse.Namespace) -> int:
    """Run the generate phase.

    This command:
    1. Finds build.py in the current directory
    2. Runs build.py to define the build (includes configure if needed)
    3. Generates build.ninja in the build directory
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    script_path = getattr(args, "build_script", None)

    # Parse variables from extra args
    variables, remaining = parse_variables(getattr(args, "extra", []))

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
            logger.info("Create a build.py file or run 'pcons init'")
            return 1
        script = found_script

    # Create build directory if it doesn't exist
    build_dir.mkdir(parents=True, exist_ok=True)

    # Get variant and reconfigure flags
    variant = getattr(args, "variant", None)
    reconfigure = getattr(args, "reconfigure", False)

    # Run build script
    return run_script(
        script,
        build_dir,
        variables=variables,
        variant=variant,
        reconfigure=reconfigure,
    )


def cmd_build(args: argparse.Namespace) -> int:
    """Build targets using ninja.

    This command runs ninja in the build directory.
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)

    # Get targets from args
    targets = getattr(args, "targets", None)
    if not targets:
        # Check for remaining args that might be targets
        extra = getattr(args, "extra", [])
        _, remaining = parse_variables(extra)
        targets = remaining if remaining else None

    return run_ninja(
        build_dir,
        targets=targets,
        jobs=getattr(args, "jobs", None),
        verbose=args.verbose,
    )


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


def cmd_info(args: argparse.Namespace) -> int:
    """Show information about the build script.

    Displays the docstring from build.py which should document
    available build variables and usage.
    """
    setup_logging(args.verbose, args.debug)

    script_path = getattr(args, "build_script", None)

    # Find build script
    if script_path:
        script = Path(script_path)
        if not script.exists():
            logger.error("Build script not found: %s", script_path)
            return 1
    else:
        found_script = find_script("build.py")
        if found_script is None:
            logger.error("No build.py found in current directory")
            return 1
        script = found_script

    # Extract docstring using AST
    import ast

    try:
        source = script.read_text()
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
    except SyntaxError as e:
        logger.error("Failed to parse %s: %s", script, e)
        return 1

    print(f"Build script: {script}")
    print()
    if docstring:
        print(docstring)
    else:
        print("(No docstring found in build.py)")
        print()
        print("Tip: Add a docstring to document available build variables:")
        print('  """Build script for MyProject.')
        print()
        print("  Variables:")
        print("      PORT     - Build target: ofx, ae (default: ofx)")
        print("      USE_CUDA - Enable CUDA: 0, 1 (default: 0)")
        print('  """')

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new pcons project.

    Creates a template build.py file.
    """
    setup_logging(args.verbose, args.debug)

    build_py = Path("build.py")

    if build_py.exists() and not args.force:
        logger.error("build.py already exists (use --force to overwrite)")
        return 1

    # Write build.py template
    build_template = '''\
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Build script for the project."""

import os
from pathlib import Path

from pcons import get_var, get_variant
from pcons.configure.config import Configure
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# Get directories from environment or use defaults
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

# Configuration (auto-cached)
config = Configure(build_dir=build_dir)
if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    # Run configuration checks
    toolchain = find_c_toolchain()
    toolchain.configure(config)
    config.set("configured", True)
    config.save()

# Get build variables
variant = get_variant("release")

# Create project
project = Project("myproject", root_dir=source_dir, build_dir=build_dir)

# Create environment with toolchain
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)
env.set_variant(variant)

# Define your build here
# Example:
# app = project.Program("hello", env, sources=["hello.c"])
# project.Default(app)

# Resolve targets
project.resolve()

# Generate ninja file
generator = NinjaGenerator()
generator.generate(project, build_dir)
print(f"Generated {build_dir / 'build.ninja'}")
'''

    build_py.write_text(build_template)
    build_py.chmod(0o755)
    logger.info("Created %s", build_py)

    print("Project initialized!")
    print("Next steps:")
    print("  1. Edit build.py to define your build targets")
    print("  2. Run 'pcons' to build")
    print()
    print("Build variables:")
    print("  pcons VARIANT=debug        # Set build variant")
    print("  pcons -v debug             # Same as above")
    print("  pcons CC=clang PORT=ofx    # Set custom variables")

    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a parser."""
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--debug", action="store_true", help="Debug output")
    parser.add_argument(
        "-B", "--build-dir", default="build", help="Build directory (default: build)"
    )


def add_generate_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for generate-related commands."""
    parser.add_argument(
        "--variant",
        metavar="NAME",
        help="Build variant (debug, release, etc.)",
    )
    parser.add_argument(
        "-C",
        "--reconfigure",
        action="store_true",
        help="Force re-run configuration checks",
    )
    parser.add_argument("-b", "--build-script", help="Path to build.py script")


def main() -> int:
    """Main entry point for the pcons CLI."""
    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
        epilog="Run 'pcons <command> --help' for command-specific help.",
    )
    from pcons import __version__

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    # Default command args (for 'pcons' with no subcommand)
    add_common_args(parser)
    add_generate_args(parser)
    parser.add_argument(
        "-j", "--jobs", type=int, help="Number of parallel jobs for build"
    )
    parser.add_argument(
        "extra",
        nargs="*",
        help="Build variables (KEY=value) or targets",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # pcons info
    info_parser = subparsers.add_parser(
        "info", help="Show build script info and available variables"
    )
    add_common_args(info_parser)
    info_parser.add_argument("-b", "--build-script", help="Path to build.py script")
    info_parser.set_defaults(func=cmd_info)

    # pcons init
    init_parser = subparsers.add_parser("init", help="Initialize a new pcons project")
    init_parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite existing files"
    )
    add_common_args(init_parser)
    init_parser.set_defaults(func=cmd_init)

    # pcons generate
    gen_parser = subparsers.add_parser(
        "generate", help="Generate build files from build.py"
    )
    add_common_args(gen_parser)
    add_generate_args(gen_parser)
    gen_parser.add_argument(
        "extra",
        nargs="*",
        help="Build variables (KEY=value)",
    )
    gen_parser.set_defaults(func=cmd_generate)

    # pcons build
    build_parser = subparsers.add_parser("build", help="Build targets using ninja")
    add_common_args(build_parser)
    build_parser.add_argument("-j", "--jobs", type=int, help="Number of parallel jobs")
    build_parser.add_argument("targets", nargs="*", help="Targets to build")
    build_parser.set_defaults(func=cmd_build)

    # pcons clean
    clean_parser = subparsers.add_parser("clean", help="Clean build artifacts")
    add_common_args(clean_parser)
    clean_parser.add_argument(
        "-a", "--all", action="store_true", help="Remove entire build directory"
    )
    clean_parser.set_defaults(func=cmd_clean)

    args = parser.parse_args()

    # Handle default command (no subcommand specified)
    if args.command is None:
        # Check if any extra args look like targets (don't contain =)
        extra = getattr(args, "extra", [])
        variables, remaining = parse_variables(extra)

        # If we have remaining args and no build.py, they might be targets
        # for an existing build.ninja
        if remaining and not find_script("build.py"):
            # Just run build with the targets
            args.targets = remaining
            return cmd_build(args)

        # Default: generate and build
        return cmd_default(args)

    # Run the specified command
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())

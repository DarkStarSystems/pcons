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
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.project import Project

# Set up logging
logger = logging.getLogger("pcons")


def setup_logging(verbose: bool = False, debug: str | None = None) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbose: Enable INFO level logging.
        debug: Enable DEBUG level logging for specific subsystems.
               Comma-separated list: "resolve,subst,env,configure,generate,deps,all"
               Can also be set via PCONS_DEBUG environment variable.
    """
    import os

    from pcons.core.debug import init_debug

    # Check CLI arg first, then environment variable
    debug_spec = debug or os.environ.get("PCONS_DEBUG")

    if debug_spec:
        level = logging.DEBUG
        fmt = "%(levelname)s: %(name)s: %(message)s"
        init_debug(debug_spec)
    elif verbose:
        level = logging.INFO
        fmt = "%(levelname)s: %(message)s"
    else:
        level = logging.WARNING
        fmt = "%(levelname)s: %(message)s"

    # Force reconfiguration even if basicConfig was already called
    # This is needed because debug mode may be set after logging is initialized
    logging.basicConfig(level=level, format=fmt, force=True)


def find_script(name: str, search_dir: Path | None = None) -> Path | None:
    """Find a build script by name.

    Args:
        name: Script name (e.g., 'pcons-build.py')
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
    generator: str | None = None,
    reconfigure: bool = False,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, list[Project]]:
    """Execute a Python build script in-process.

    Runs the script using exec() so we can access the Project objects
    created by the script via the global registry.

    Args:
        script_path: Path to the script to run.
        build_dir: Build directory to pass to the script.
        variables: Build variables to pass via PCONS_VARS.
        variant: Build variant to pass via PCONS_VARIANT.
        generator: Generator to pass via PCONS_GENERATOR (ninja, make).
        reconfigure: If True, set PCONS_RECONFIGURE=1.
        extra_env: Additional environment variables to set.

    Returns:
        Tuple of (exit_code, list of registered Projects).
    """
    import pcons

    # Clear any previously registered projects
    pcons._clear_registered_projects()

    # Also clear cached CLI vars so they get re-read
    pcons._cli_vars = None

    # Set environment variables (scripts still read these)
    os.environ["PCONS_BUILD_DIR"] = str(build_dir.absolute())
    os.environ["PCONS_SOURCE_DIR"] = str(script_path.parent.absolute())

    if variables:
        os.environ["PCONS_VARS"] = json.dumps(variables)

    if variant:
        os.environ["PCONS_VARIANT"] = variant

    if generator:
        os.environ["PCONS_GENERATOR"] = generator

    if reconfigure:
        os.environ["PCONS_RECONFIGURE"] = "1"

    if extra_env:
        os.environ.update(extra_env)

    logger.info("Running %s", script_path)
    logger.debug("  PCONS_BUILD_DIR=%s", os.environ["PCONS_BUILD_DIR"])
    logger.debug("  PCONS_SOURCE_DIR=%s", os.environ["PCONS_SOURCE_DIR"])
    if variables:
        logger.debug("  PCONS_VARS=%s", os.environ["PCONS_VARS"])
    if variant:
        logger.debug("  PCONS_VARIANT=%s", variant)
    if generator:
        logger.debug("  PCONS_GENERATOR=%s", generator)

    # Save and modify sys.path and cwd for script imports
    old_cwd = os.getcwd()
    old_path = sys.path.copy()

    try:
        os.chdir(script_path.parent)
        sys.path.insert(0, str(script_path.parent))

        # Execute the script
        script_source = script_path.read_text()
        code = compile(script_source, str(script_path), "exec")
        namespace: dict[str, object] = {
            "__name__": "__main__",
            "__file__": str(script_path),
        }
        exec(code, namespace)

        return 0, pcons.get_registered_projects()

    except SystemExit as e:
        # Script called sys.exit()
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        return exit_code, pcons.get_registered_projects()
    except Exception as e:
        logger.error("Build script failed: %s", e)
        traceback.print_exc()
        return 1, []
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        # Clean up environment variables
        for key in [
            "PCONS_BUILD_DIR",
            "PCONS_SOURCE_DIR",
            "PCONS_VARS",
            "PCONS_VARIANT",
            "PCONS_GENERATOR",
            "PCONS_RECONFIGURE",
        ]:
            os.environ.pop(key, None)
        if extra_env:
            for key in extra_env:
                os.environ.pop(key, None)


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
    result, project = cmd_generate(args)
    if result != 0:
        return result

    # Use the actual build directory from the Project
    if project:
        args.build_dir = str(project.build_dir)

    # Then, build
    return cmd_build(args)


def cmd_generate(args: argparse.Namespace) -> tuple[int, Project | None]:
    """Run the generate phase.

    This command:
    1. Finds pcons-build.py in the current directory
    2. Runs pcons-build.py to define the build (includes configure if needed)
    3. Generates build.ninja in the build directory

    Returns:
        Tuple of (exit_code, project) where project is the first registered
        Project, or None if no project was created.
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    script_path = getattr(args, "build_script", None)

    # Parse variables from extra args
    variables, _ = parse_variables(getattr(args, "extra", []))

    # Find build script
    script: Path
    if script_path:
        script = Path(script_path)
        if not script.exists():
            logger.error("Build script not found: %s", script_path)
            return 1, None
    else:
        found_script = find_script("pcons-build.py")
        if found_script is None:
            logger.error("No pcons-build.py found in current directory")
            logger.info("Create a pcons-build.py file or run 'pcons init'")
            return 1, None
        script = found_script

    # Create build directory if it doesn't exist
    build_dir.mkdir(parents=True, exist_ok=True)

    # Get variant, generator, and reconfigure flags
    variant = getattr(args, "variant", None)
    generator = getattr(args, "generator", None)
    reconfigure = getattr(args, "reconfigure", False)
    graph = getattr(args, "graph", None)
    mermaid = getattr(args, "mermaid", None)

    # Set up extra environment for graph output
    extra_env: dict[str, str] = {}
    if graph:
        extra_env["PCONS_GRAPH"] = graph
    if mermaid:
        extra_env["PCONS_MERMAID"] = mermaid

    # Run build script
    exit_code, projects = run_script(
        script,
        build_dir,
        variables=variables,
        variant=variant,
        generator=generator,
        reconfigure=reconfigure,
        extra_env=extra_env if extra_env else None,
    )

    if exit_code != 0:
        return exit_code, None

    if not projects:
        logger.warning("No Project created in build script")
        return 0, None

    if len(projects) > 1:
        logger.warning("Multiple Projects created; using first one")

    return 0, projects[0]


def _cmd_generate_wrapper(args: argparse.Namespace) -> int:
    """Wrapper for cmd_generate that returns only the exit code.

    Used as the handler for the 'generate' subcommand.
    """
    exit_code, _ = cmd_generate(args)
    return exit_code


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

    Displays the docstring from pcons-build.py which should document
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
        found_script = find_script("pcons-build.py")
        if found_script is None:
            logger.error("No pcons-build.py found in current directory")
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
        print("(No docstring found in pcons-build.py)")
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

    Creates a template pcons-build.py file.
    """
    setup_logging(args.verbose, args.debug)

    build_py = Path("pcons-build.py")

    if build_py.exists() and not args.force:
        logger.error("pcons-build.py already exists (use --force to overwrite)")
        return 1

    # Write pcons-build.py template
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
    print("  1. Edit pcons-build.py to define your build targets")
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
    parser.add_argument(
        "--debug",
        type=str,
        metavar="SUBSYSTEMS",
        help="Enable debug tracing for subsystems (comma-separated): "
        "configure,resolve,generate,subst,env,deps,all",
    )
    parser.add_argument(
        "-B", "--build-dir", default="build", help="Build directory (default: build)"
    )


def find_command_in_argv(argv: list[str]) -> str | None:
    """Find a valid command in argv, skipping options and their values.

    Returns the command name if found, None otherwise.
    """
    valid_commands = {"info", "init", "generate", "build", "clean"}
    # Options that take a value
    options_with_value = {
        "-B",
        "--build-dir",
        "-b",
        "--build-script",
        "--variant",
        "-j",
        "--jobs",
        "--graph",
        "--mermaid",
        "--debug",
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-"):
            if arg in options_with_value:
                i += 2  # Skip option and its value
            elif "=" in arg:
                i += 1  # Option with value like --build-dir=foo
            else:
                i += 1  # Boolean flag
        else:
            # First positional argument
            if arg in valid_commands:
                return arg
            return None
    return None


def add_generate_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for generate-related commands."""
    parser.add_argument(
        "--variant",
        metavar="NAME",
        help="Build variant (debug, release, etc.)",
    )
    parser.add_argument(
        "-G",
        "--generator",
        metavar="NAME",
        choices=["ninja", "make", "makefile"],
        help="Generator to use (ninja, make). Default: ninja",
    )
    parser.add_argument(
        "-C",
        "--reconfigure",
        action="store_true",
        help="Force re-run configuration checks",
    )
    parser.add_argument("-b", "--build-script", help="Path to pcons-build.py script")


def create_default_parser() -> argparse.ArgumentParser:
    """Create a parser for default mode (no subcommand).

    This parser is used when no valid subcommand is found in argv.
    It accepts KEY=value args and targets as positional arguments.
    """
    from pcons import __version__

    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
        epilog="Run 'pcons <command> --help' for command-specific help.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
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
    return parser


def create_full_parser() -> argparse.ArgumentParser:
    """Create a parser with subcommands.

    This parser is used when a valid subcommand is found in argv.
    """
    from pcons import __version__

    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
        epilog="Run 'pcons <command> --help' for command-specific help.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    add_common_args(parser)
    add_generate_args(parser)
    parser.add_argument(
        "-j", "--jobs", type=int, help="Number of parallel jobs for build"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # pcons info
    info_parser = subparsers.add_parser(
        "info", help="Show build script info and available variables"
    )
    add_common_args(info_parser)
    info_parser.add_argument(
        "-b", "--build-script", help="Path to pcons-build.py script"
    )
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
        "generate", help="Generate build files from pcons-build.py"
    )
    add_common_args(gen_parser)
    add_generate_args(gen_parser)
    gen_parser.add_argument(
        "--graph",
        nargs="?",
        const="-",
        metavar="FILE",
        help="Output dependency graph in DOT format (default: stdout)",
    )
    gen_parser.add_argument(
        "--mermaid",
        nargs="?",
        const="-",
        metavar="FILE",
        help="Output dependency graph in Mermaid format (default: stdout)",
    )
    gen_parser.add_argument(
        "extra",
        nargs="*",
        help="Build variables (KEY=value)",
    )
    gen_parser.set_defaults(func=_cmd_generate_wrapper)

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

    return parser


def main() -> int:
    """Main entry point for the pcons CLI."""
    # Check if argv contains a valid command
    # If not, use the default parser (no subcommands) to avoid
    # positional arguments being mistaken for commands
    command = find_command_in_argv(sys.argv[1:])

    # Special case: if --help or -h is present without a command,
    # use the full parser so help shows available commands
    if command is None and ("-h" in sys.argv or "--help" in sys.argv):
        parser = create_full_parser()
        parser.parse_args()  # This will print help and exit
        return 0

    if command is None:
        # No command found - use default mode parser
        parser = create_default_parser()
        args = parser.parse_args()
        args.command = None

        # Check if any extra args look like targets (don't contain =)
        extra = getattr(args, "extra", [])
        variables, remaining = parse_variables(extra)

        # If we have remaining args and no pcons-build.py, they might be targets
        # for an existing build.ninja
        if remaining and not find_script("pcons-build.py"):
            # Just run build with the targets
            args.targets = remaining
            return cmd_build(args)

        # Default: generate and build
        return cmd_default(args)

    # Command found - use full parser with subcommands
    parser = create_full_parser()
    args = parser.parse_args()
    args.extra = getattr(args, "extra", [])

    # Run the specified command
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())

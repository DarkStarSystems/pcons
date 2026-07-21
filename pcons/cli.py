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
    from pcons.core.debug import init_debug

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

    # force=True: debug mode may be set after logging is initialized
    logging.basicConfig(level=level, format=fmt, force=True)


def find_script(name: str, search_dir: Path | None = None) -> Path | None:
    """Find a build script by name in search_dir (default: cwd)."""
    if search_dir is None:
        search_dir = Path.cwd()

    script_path = search_dir / name
    if script_path.exists() and script_path.is_file():
        return script_path

    return None


def _needs_generation(build_dir: Path, build_script: str | None = None) -> bool:
    """Check if build files need (re)generation.

    Returns True if no build files exist, or if the build script
    is newer than the existing build files.
    """
    ninja_file = build_dir / "build.ninja"
    makefile = build_dir / "Makefile"
    xcodeproj_files = list(build_dir.glob("*.xcodeproj"))

    # Find the newest build file
    build_file_mtime = 0.0
    for f in [ninja_file, makefile]:
        if f.exists():
            build_file_mtime = max(build_file_mtime, f.stat().st_mtime)
    for f in xcodeproj_files:
        if f.is_dir():
            build_file_mtime = max(build_file_mtime, f.stat().st_mtime)

    if build_file_mtime == 0.0:
        return True  # No build files at all

    # Check if build script is newer than build files
    if build_script:
        script = Path(build_script)
        if not script.exists():
            return True  # Script not found; let cmd_generate handle the error
    else:
        script = find_script("pcons-build.py")

    if script is None:
        return False  # No script to generate from

    return script.stat().st_mtime > build_file_mtime


def parse_variables(args: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse KEY=value arguments; return (variables dict, remaining args)."""
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


def _cancel_pending_generation() -> None:
    """Drop pending auto-generation after a failed build script.

    Build files must not be generated from a partially-executed script.
    """
    from pcons.generators.generator import BaseGenerator

    BaseGenerator._clear_pending()


def run_script(
    script_path: Path,
    build_dir: Path,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    generator: list[str] | str | None = None,
    reconfigure: bool = False,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, list[Project]]:
    """Execute a Python build script in-process via exec(), so its Project
    objects are accessible through the global registry.

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
    import pcons.core.vars

    sentinel = object()
    previous_env: dict[str, str | object] = {}
    updated_keys: set[str] = set()

    def set_env_var(key: str, value: str) -> None:
        if key not in previous_env:
            previous_env[key] = os.environ.get(key, sentinel)
        updated_keys.add(key)
        os.environ[key] = value

    pcons._clear_registered_projects()
    # Clear cached CLI vars so they get re-read
    pcons.core.vars._clear_cli_vars()

    set_env_var("PCONS_BUILD_DIR", str(build_dir.absolute()))
    set_env_var("PCONS_SOURCE_DIR", str(script_path.parent.absolute()))

    if variables:
        set_env_var("PCONS_VARS", json.dumps(variables))

    if variant:
        set_env_var("PCONS_VARIANT", variant)

    if generator:
        gen_spec = ":".join(generator) if isinstance(generator, list) else generator
        set_env_var("PCONS_GENERATOR", gen_spec)

    if reconfigure:
        set_env_var("PCONS_RECONFIGURE", "1")

    if extra_env:
        for key, value in extra_env.items():
            set_env_var(key, value)

    logger.info("Running %s", script_path)
    logger.debug("  PCONS_BUILD_DIR=%s", os.environ["PCONS_BUILD_DIR"])
    logger.debug("  PCONS_SOURCE_DIR=%s", os.environ["PCONS_SOURCE_DIR"])
    if variables:
        logger.debug("  PCONS_VARS=%s", os.environ["PCONS_VARS"])
    if variant:
        logger.debug("  PCONS_VARIANT=%s", variant)
    if generator:
        logger.debug("  PCONS_GENERATOR=%s", os.environ["PCONS_GENERATOR"])

    # Save and modify sys.path and cwd for script imports
    old_cwd = os.getcwd()
    old_path = sys.path.copy()

    try:
        os.chdir(script_path.parent)
        sys.path.insert(0, str(script_path.parent))

        script_source = script_path.read_text()
        code = compile(script_source, str(script_path), "exec")
        namespace: dict[str, object] = {
            "__name__": "__main__",
            "__file__": str(script_path),
        }
        exec(code, namespace)

        # Run any deferred generate requests registered by the script
        try:
            from pcons import Project
            from pcons.generators.generator import BaseGenerator

            top_level = Project.top_level()
            BaseGenerator._generate_pending(top_level)
            return 0, pcons.get_registered_projects()
        except ValueError:
            logger.error("No Project created in build script")
            return 1, []

    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        if exit_code != 0:
            _cancel_pending_generation()
        return exit_code, pcons.get_registered_projects()
    except Exception as e:
        logger.error("Build script failed: %s", e)
        traceback.print_exc()
        _cancel_pending_generation()
        return 1, []
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        for key in updated_keys:
            previous = previous_env[key]
            if isinstance(previous, str):
                os.environ[key] = previous
            else:
                os.environ.pop(key, None)


def _find_ninja(override: str | None = None) -> list[str] | None:
    """Find ninja-compatible executable, falling back to uvx.

    Args:
        override: Explicit program name or path (e.g., "n2"). If given, takes
            precedence over PATH lookup of "ninja". Falls back to the NINJA
            env var if not provided.

    Returns:
        Command prefix list (e.g., ["ninja"], ["n2"], or ["uvx", "ninja"]),
        or None if no runner is found.
    """
    chosen = override or os.environ.get("NINJA")
    if chosen:
        # Allow either an absolute path or a name resolvable on PATH
        resolved = shutil.which(chosen) or (
            chosen if Path(chosen).is_absolute() else None
        )
        if resolved is None:
            logger.error("ninja runner %r not found on PATH", chosen)
            return None
        return [resolved]

    ninja = shutil.which("ninja")
    if ninja is not None:
        return [ninja]

    uvx = shutil.which("uvx")
    if uvx is not None:
        logger.info("ninja not in PATH, using 'uvx ninja'")
        return [uvx, "ninja"]

    return None


def run_ninja(
    build_dir: Path,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,
    runner: str | None = None,
) -> int:
    """Run ninja (or a ninja-compatible tool) in the build directory.

    Args:
        build_dir: Build directory containing build.ninja.
        targets: Specific targets to build.
        jobs: Number of parallel jobs.
        verbose: Enable verbose output.
        runner: Ninja-compatible runner to use (e.g., "n2"). Falls back to the
            NINJA env var, then "ninja".

    Returns:
        Exit code from ninja.
    """
    ninja_file = build_dir / "build.ninja"

    if not ninja_file.exists():
        logger.error("No build.ninja found in %s", build_dir)
        logger.info("Run 'pcons generate' first to create build files")
        return 1

    ninja_cmd = _find_ninja(runner)
    if ninja_cmd is None:
        logger.error("ninja not found in PATH")
        logger.info("Install ninja: https://ninja-build.org/")
        logger.info("Or install uv and run with 'uvx ninja'")
        return 1

    cmd = [*ninja_cmd, "-C", str(build_dir)]

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


def run_xcodebuild(
    build_dir: Path,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,
    configuration: str | None = None,
) -> int:
    """Run xcodebuild in the build directory.

    Args:
        build_dir: Build directory containing the .xcodeproj.
        targets: Specific targets to build (mapped to -target).
        jobs: Number of parallel jobs.
        verbose: Enable verbose output.
        configuration: Build configuration (Debug, Release). Defaults to Release.

    Returns:
        Exit code from xcodebuild.
    """
    xcodeproj_files = list(build_dir.glob("*.xcodeproj"))
    if not xcodeproj_files:
        logger.error("No .xcodeproj found in %s", build_dir)
        return 1

    xcodeproj = xcodeproj_files[0]

    xcodebuild = shutil.which("xcodebuild")
    if xcodebuild is None:
        logger.error("xcodebuild not found in PATH")
        logger.info("xcodebuild is only available on macOS with Xcode installed")
        return 1

    # Map variant to Xcode configuration (capitalize first letter)
    xcode_config = configuration.capitalize() if configuration else "Release"

    cmd = [xcodebuild, "-project", str(xcodeproj), "-configuration", xcode_config]

    if jobs:
        cmd.extend(["-jobs", str(jobs)])

    if targets:
        for target in targets:
            cmd.extend(["-target", target])

    if not verbose:
        cmd.append("-quiet")

    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run xcodebuild: %s", e)
        return 1


def run_make(
    build_dir: Path,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,  # noqa: ARG001 - kept for API consistency
) -> int:
    """Run make in the build directory.

    Args:
        build_dir: Build directory containing Makefile.
        targets: Specific targets to build.
        jobs: Number of parallel jobs.
        verbose: Enable verbose output (not used for make).

    Returns:
        Exit code from make.
    """
    makefile = build_dir / "Makefile"
    if not makefile.exists():
        logger.error("No Makefile found in %s", build_dir)
        return 1

    make = shutil.which("make")
    if make is None:
        logger.error("make not found in PATH")
        return 1

    cmd = [make, "-C", str(build_dir)]

    if jobs:
        cmd.extend(["-j", str(jobs)])

    if targets:
        cmd.extend(targets)

    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run make: %s", e)
        return 1


def cmd_default(args: argparse.Namespace) -> int:
    """Default command (bare 'pcons'): generate, then build."""
    load_user_modules(args)

    result, project = cmd_generate(args)
    if result != 0:
        return result

    # Use the actual build directory from the Project
    if project:
        args.build_dir = str(project.build_dir)

    return cmd_build(args)


def cmd_generate(args: argparse.Namespace) -> tuple[int, Project | None]:
    """Run the generate phase: find and run pcons-build.py, which
    generates build files in the build directory.

    Returns:
        Tuple of (exit_code, first registered Project or None).
    """
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)
    script_path = getattr(args, "build_script", None)

    variables, _ = parse_variables(getattr(args, "extra", []))

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

    build_dir.mkdir(parents=True, exist_ok=True)

    variant = getattr(args, "variant", None)
    generator = getattr(args, "generator", None)
    reconfigure = getattr(args, "reconfigure", False)
    graph = getattr(args, "graph", None)
    mermaid = getattr(args, "mermaid", None)

    extra_env: dict[str, str] = {}
    if graph:
        extra_env["PCONS_GRAPH"] = graph
    if mermaid:
        extra_env["PCONS_MERMAID"] = mermaid

    exit_code, _projects = run_script(
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

    return 0, _projects[0] if _projects else None


def _cmd_generate_wrapper(args: argparse.Namespace) -> int:
    """'generate' subcommand handler: cmd_generate, exit code only."""
    load_user_modules(args)
    exit_code, _ = cmd_generate(args)
    return exit_code


def cmd_build(args: argparse.Namespace) -> int:
    """Build targets with the build tool matching the generated files
    (ninja, make, or xcodebuild), regenerating them first if stale."""
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)

    # Auto-generate if build files are missing or stale
    build_script = getattr(args, "build_script", None)
    if _needs_generation(build_dir, build_script=build_script):
        script = (
            find_script("pcons-build.py") if not build_script else Path(build_script)
        )
        if script is not None and script.exists():
            logger.info("Build files missing or out of date, regenerating...")
            load_user_modules(args)
            result, project = cmd_generate(args)
            if result != 0:
                return result
            if project:
                args.build_dir = str(project.build_dir)
                build_dir = Path(args.build_dir)

    _, targets_list = parse_variables(getattr(args, "extra", []))
    targets = targets_list or None

    jobs = getattr(args, "jobs", None)
    verbose = args.verbose
    variant = getattr(args, "variant", None)
    ninja_runner = getattr(args, "ninja", None)

    # Detect which generator was used and run the matching build tool
    ninja_file = build_dir / "build.ninja"
    makefile = build_dir / "Makefile"
    xcodeproj_files = list(build_dir.glob("*.xcodeproj"))

    if ninja_file.exists():
        return run_ninja(
            build_dir, targets=targets, jobs=jobs, verbose=verbose, runner=ninja_runner
        )
    elif makefile.exists():
        return run_make(build_dir, targets=targets, jobs=jobs, verbose=verbose)
    elif xcodeproj_files:
        return run_xcodebuild(
            build_dir,
            targets=targets,
            jobs=jobs,
            verbose=verbose,
            configuration=variant,
        )
    else:
        logger.error("No build files found in %s", build_dir)
        logger.info("Run 'pcons generate' first to create build files")
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean build artifacts: 'ninja -t clean', or remove the whole
    build directory with --all."""
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)

    if args.all:
        if build_dir.exists():
            logger.info("Removing build directory: %s", build_dir)
            shutil.rmtree(build_dir)
            logger.info("Clean complete")
        else:
            logger.info("Build directory does not exist: %s", build_dir)
        return 0

    ninja_file = build_dir / "build.ninja"
    if not ninja_file.exists():
        logger.info("No build.ninja found, nothing to clean")
        return 0

    ninja_runner = getattr(args, "ninja", None)
    ninja_cmd = _find_ninja(ninja_runner)
    if ninja_cmd is None:
        logger.error("ninja not found in PATH")
        return 1

    # n2 does not implement `-t clean`. Fall back to suggesting `clean --all`.
    if Path(ninja_cmd[-1]).name == "n2":
        logger.error("n2 does not support 'clean'; use 'pcons clean --all' instead")
        return 1

    cmd = [*ninja_cmd, "-C", str(build_dir), "-t", "clean"]
    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd)
        return result.returncode
    except OSError as e:
        logger.error("Failed to run ninja: %s", e)
        return 1


def cmd_info(args: argparse.Namespace) -> int:
    """Show the build script's docstring; with --targets, run the script
    and list all defined targets grouped by type."""
    setup_logging(args.verbose, args.debug)

    script_path = getattr(args, "build_script", None)

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

    if getattr(args, "targets", False):
        return _info_targets(args, script)

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

    print()
    print("To see all targets and aliases, run: pcons info --targets")

    return 0


def _info_targets(args: argparse.Namespace, script: Path) -> int:
    """List all targets defined by the build script."""
    from pcons.core.node import AliasNode, FileNode

    load_user_modules(args)

    build_dir = Path(args.build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    variables, _ = parse_variables(getattr(args, "extra", []))
    variant = getattr(args, "variant", None)
    generator = getattr(args, "generator", None)
    reconfigure = getattr(args, "reconfigure", False)

    exit_code, projects = run_script(
        script,
        build_dir,
        variables=variables,
        variant=variant,
        generator=generator,
        reconfigure=reconfigure,
    )
    if exit_code != 0:
        return exit_code
    if not projects:
        logger.error("No Project created in build script")
        return 1

    project = projects[0]

    aliases = project.aliases
    if aliases:
        print("Aliases:")
        for name, alias_node in aliases.items():
            dep_names: list[str] = []
            for node in alias_node.targets:
                if isinstance(node, FileNode):
                    dep_names.append(node.path.name)
                elif isinstance(node, AliasNode):
                    dep_names.append(node.alias_name)
                else:
                    dep_names.append(str(node))
            deps_str = ", ".join(dep_names) if dep_names else ""
            print(f"  {name:30s} -> {deps_str}")
        print()

    by_type: dict[str, list[tuple[str, str]]] = {}
    type_order = [
        "program",
        "shared_library",
        "static_library",
        "object",
        "interface",
        "command",
        "archive",
        "installer",
    ]

    for target in project.targets:
        ttype = target.target_type
        type_name = ttype if ttype else "other"
        outputs = ""
        if target.output_nodes:
            paths = []
            for n in target.output_nodes:
                if isinstance(n, FileNode):
                    try:
                        paths.append(str(n.path.relative_to(project.build_dir)))
                    except ValueError:
                        paths.append(str(n.path))
            if paths:
                outputs = ", ".join(paths)
        entry = (target.name, outputs)
        by_type.setdefault(type_name, []).append(entry)

    def print_entries(label: str, entries: list[tuple[str, str]]) -> None:
        print(f"  [{label}]")
        for name, outputs in entries:
            if outputs:
                print(f"    {name:30s} -> {outputs}")
            else:
                print(f"    {name}")
        print()

    print("Targets:")
    for ttype in type_order:
        entries = by_type.pop(ttype, None)
        if entries:
            print_entries(ttype, entries)

    # Any remaining types not in our order
    for type_name, entries in by_type.items():
        print_entries(type_name, entries)

    return 0


_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".swift"}

_HELLO_C = """\
#include <stdio.h>

int main(void) {
    printf("Hello from @NAME@!\\n");
    return 0;
}
"""

_HELLO_CPP = """\
#include <iostream>

int main() {
    std::cout << "Hello from @NAME@!\\n";
    return 0;
}
"""


def _find_c_sources(root: Path, build_dir: str) -> list[Path]:
    """Find C/C++ source files in the project root and src/ tree.

    Looks at top-level files and recursively under src/, skipping hidden
    directories and the build directory. Returns sorted paths relative
    to *root*.
    """
    skip_dirs = {build_dir, "build"}
    sources = [
        p for p in root.iterdir() if p.is_file() and p.suffix in _SOURCE_SUFFIXES
    ]
    src = root / "src"
    if src.is_dir():
        sources += [
            p
            for p in src.rglob("*")
            if p.suffix in _SOURCE_SUFFIXES
            and not any(
                part.startswith(".") or part in skip_dirs
                for part in p.relative_to(root).parts
            )
        ]
    return sorted(p.relative_to(root) for p in sources)


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new pcons project.

    Writes a pcons-build.py with a program target for any C/C++ sources
    found; in an empty directory, scaffolds a hello-world starter so the
    project builds and runs immediately.
    """
    import re

    setup_logging(args.verbose, args.debug)

    root = Path.cwd()
    build_py = root / "pcons-build.py"

    if build_py.exists() and not args.force:
        logger.error("pcons-build.py already exists (use --force to overwrite)")
        return 1

    name = re.sub(r"[^A-Za-z0-9_-]+", "_", root.name).strip("_") or "myproject"

    sources = _find_c_sources(root, args.build_dir)
    scaffolded = None
    if not sources:
        scaffolded = Path("src") / ("main.cpp" if args.lang == "cpp" else "main.c")
        hello = _HELLO_CPP if args.lang == "cpp" else _HELLO_C
        (root / "src").mkdir(exist_ok=True)
        (root / scaffolded).write_text(hello.replace("@NAME@", name))
        logger.info("Created %s", scaffolded)
        sources = [scaffolded]

    suffixes = {p.suffix for p in sources}
    if suffixes <= {".swift"}:
        lang = "swift"
    elif suffixes <= {".c"}:
        lang = "c"
    else:
        lang = "c++"
    has_include = (root / "include").is_dir()
    target_lines = [
        f"{'app = ' if has_include else ''}project.Program(",
        f'    "{name}",',
        "    env,",
        "    sources=[",
        *(f'        "{p.as_posix()}",' for p in sources),
        "    ],",
        ")",
    ]
    if has_include:
        target_lines.append('app.private.include_dirs.append("include")')
    target_block = "\n".join(target_lines)

    from pcons import __version__

    build_template = f'''\
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons>={__version__}"]
# ///
"""Build script for {name}.

Run `pcons` to generate build files and build.
Docs: https://pcons.readthedocs.io
"""

from pcons import Project

project = Project("{name}")
env = project.Environment(toolchain="{lang}")
env.apply_preset("warnings")

{target_block}
'''

    build_py.write_text(build_template)
    build_py.chmod(0o755)
    logger.info("Created %s", build_py)

    if scaffolded:
        print(f"Created {scaffolded} and pcons-build.py")
    else:
        n = len(sources)
        print(
            f"Created pcons-build.py with a program target for {n} source file{'s' if n > 1 else ''}"
        )
    exe = Path(args.build_dir) / (name + (".exe" if os.name == "nt" else ""))
    run_cmd = str(exe) if os.name == "nt" else f"./{exe.as_posix()}"
    print()
    print("Next steps:")
    pad = max(len(run_cmd), len("pcons"))
    print(f"  {'pcons'.ljust(pad)}   # configure and build")
    print(f"  {run_cmd.ljust(pad)}   # run it")
    if not scaffolded:
        print()
        print("Edit pcons-build.py to adjust targets and sources.")

    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a parser.

    Note: -C/--directory is handled before argparse in _apply_directory_arg().
    """
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    from pcons.core.debug import SUBSYSTEM_DESCRIPTIONS

    subsystem_names = ",".join(SUBSYSTEM_DESCRIPTIONS.keys()) + ",all,help"
    parser.add_argument(
        "--debug",
        type=str,
        metavar="SUBSYSTEMS",
        help=f"Enable debug tracing for subsystems (comma-separated): {subsystem_names}",
    )
    parser.add_argument(
        "-B", "--build-dir", default="build", help="Build directory (default: build)"
    )
    parser.add_argument(
        "--modules-path",
        type=str,
        metavar="PATHS",
        help="Additional paths to search for pcons modules (colon/semicolon-separated)",
    )


def load_user_modules(args: argparse.Namespace) -> None:
    """Load user modules from search paths."""
    from pcons import modules

    extra_paths: list[Path | str] | None = None
    modules_path = getattr(args, "modules_path", None)
    if modules_path:
        extra_paths = modules_path.split(os.pathsep)

    modules.load_modules(extra_paths)


def _find_command_index(argv: list[str]) -> int | None:
    """Find the index of the subcommand token in argv, or None.

    Skips options and their values so an option value that equals a
    command name (e.g. ``--build-dir test``) is not mistaken for the
    subcommand.
    """
    valid_commands = {"info", "init", "generate", "build", "clean", "test"}
    # Options that take a value (-C/--directory is consumed before this runs)
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
        "--modules-path",
        "--ninja",
        "--manifest",
        "--junit",
        "-L",
        "-LE",
        "-R",
        "-E",
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
                return i
            return None
    return None


def find_command_in_argv(argv: list[str]) -> str | None:
    """Return the command name found in argv, or None."""
    idx = _find_command_index(argv)
    return argv[idx] if idx is not None else None


def add_build_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments that affect how the build is run (not generated)."""

    # n2 is a ninja-compatible runner (Rust rewrite of Ninja) with more advanced
    # rebuild tracking.
    parser.add_argument(
        "--ninja",
        metavar="PROG",
        help=(
            "Ninja-compatible runner to invoke (e.g., 'n2'). "
            "Defaults to the NINJA env var, then 'ninja'."
        ),
    )


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
        action="append",
        choices=["ninja", "make", "makefile", "metadata", "xcode"],
        help="Generator to use (ninja, make, metadata, xcode). Repeatable. Default: ninja",
    )
    parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="Force re-run configuration checks",
    )
    parser.add_argument("-b", "--build-script", help="Path to pcons-build.py script")


def create_default_parser() -> argparse.ArgumentParser:
    """Create the no-subcommand parser: accepts KEY=value args and targets
    as positionals."""
    from pcons import __version__

    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
        epilog=(
            "Use -C DIR to change to DIR before doing anything else.\n"
            "\n"
            "Run 'pcons <command> --help' for command-specific help.\n"
            "\n"
            "GitHub:  https://github.com/DarkStarSystems/pcons\n"
            "Docs:    https://pcons.readthedocs.io/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    add_common_args(parser)
    add_generate_args(parser)
    add_build_args(parser)
    parser.add_argument(
        "-j", "--jobs", type=int, help="Number of parallel jobs for build"
    )
    parser.add_argument(
        "extra",
        nargs="*",
        help="Build variables (KEY=value) and/or targets to build",
    )
    return parser


def create_full_parser() -> argparse.ArgumentParser:
    """Create the parser with subcommands, used when argv names one."""
    from pcons import __version__

    parser = argparse.ArgumentParser(
        prog="pcons",
        description=(
            "A Python-based build system that generates Ninja files.\n"
            "\n"
            "Without a subcommand, generates build files and builds specified\n"
            "targets (or default targets if none given):\n"
            "  pcons                     Generate and build default targets\n"
            "  pcons hello               Generate and build 'hello'\n"
            "  pcons CC=clang hello      Set CC=clang, generate and build 'hello'"
        ),
        epilog=(
            "Use -C DIR to change to DIR before doing anything else.\n"
            "\n"
            "Run 'pcons <command> --help' for command-specific help.\n"
            "\n"
            "GitHub:  https://github.com/DarkStarSystems/pcons\n"
            "Docs:    https://pcons.readthedocs.io/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    add_common_args(parser)
    add_generate_args(parser)
    add_build_args(parser)
    parser.add_argument(
        "-j", "--jobs", type=int, help="Number of parallel jobs for build"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # pcons info
    info_parser = subparsers.add_parser(
        "info", help="Show build script info and available variables"
    )
    add_common_args(info_parser)
    add_generate_args(info_parser)
    info_parser.add_argument(
        "-t",
        "--targets",
        action="store_true",
        help="List all build targets (runs the build script)",
    )
    info_parser.add_argument(
        "extra",
        nargs="*",
        help="Build variables (KEY=value)",
    )
    info_parser.set_defaults(func=cmd_info)

    # pcons init
    init_parser = subparsers.add_parser("init", help="Initialize a new pcons project")
    init_parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite existing files"
    )
    init_parser.add_argument(
        "--lang",
        choices=["c", "cpp"],
        default="cpp",
        help="Language for the starter program when no sources are found (default: cpp)",
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
    build_parser = subparsers.add_parser(
        "build",
        help="Build targets (auto-generates if needed)",
        description="Build targets using the appropriate build tool. "
        "If build files are missing or out of date, generates them first.",
    )
    add_common_args(build_parser)
    add_generate_args(build_parser)
    add_build_args(build_parser)
    build_parser.add_argument("-j", "--jobs", type=int, help="Number of parallel jobs")
    build_parser.add_argument(
        "extra",
        nargs="*",
        metavar="arg",
        help="Build variables (KEY=value) and/or targets to build",
    )
    build_parser.set_defaults(func=cmd_build)

    # pcons clean
    clean_parser = subparsers.add_parser("clean", help="Clean build artifacts")
    add_common_args(clean_parser)
    add_build_args(clean_parser)
    clean_parser.add_argument(
        "-a", "--all", action="store_true", help="Remove entire build directory"
    )
    clean_parser.set_defaults(func=cmd_clean)

    # pcons test is dispatched in main() before argparse runs (so the
    # runner can own its own flags). This subparser exists only so that
    # `pcons --help` lists it; argument parsing is never reached.
    subparsers.add_parser(
        "test",
        help="Run tests declared by project.Test() in pcons-build.py",
        add_help=False,
    )

    return parser


def _apply_directory_arg() -> None:
    """Handle -C DIR / --directory DIR (or --directory=DIR) before any
    other parsing: chdir there and remove the consumed args from sys.argv."""
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ("-C", "--directory"):
            if i + 1 >= len(sys.argv):
                print("error: -C/--directory requires an argument", file=sys.stderr)
                sys.exit(1)
            target_dir = sys.argv[i + 1]
            try:
                os.chdir(target_dir)
            except OSError as e:
                print(f"error: -C {target_dir}: {e}", file=sys.stderr)
                sys.exit(1)
            # Remove -C and DIR from argv so argparse doesn't see them
            del sys.argv[i : i + 2]
        elif arg.startswith("--directory="):
            target_dir = arg.split("=", 1)[1]
            try:
                os.chdir(target_dir)
            except OSError as e:
                print(f"error: --directory={target_dir}: {e}", file=sys.stderr)
                sys.exit(1)
            del sys.argv[i]
        else:
            i += 1


def main() -> int:
    """Main entry point for the pcons CLI."""
    _apply_directory_arg()

    command = find_command_in_argv(sys.argv[1:])

    # `pcons test` is dispatched directly to the test runner, which has
    # its own argument parser. This avoids duplicating the runner's flags
    # (-L, -R, --junit, etc.) in pcons's top-level argparse.
    if command == "test":
        from pcons.test_runner import main as test_main

        # Locate the subcommand positionally, not by scanning for the
        # literal "test", which could match an option's value.
        idx = _find_command_index(sys.argv[1:])
        assert idx is not None  # command == "test" guarantees a match
        return test_main(sys.argv[idx + 2 :])

    # Special case: if --help or -h is present without a command,
    # use the full parser so help shows available commands
    if command is None and ("-h" in sys.argv or "--help" in sys.argv):
        parser = create_full_parser()
        parser.parse_args()  # This will print help and exit
        return 0

    if command is None:
        parser = create_default_parser()
        args = parser.parse_args()
        args.command = None

        extra = getattr(args, "extra", [])
        variables, remaining = parse_variables(extra)

        # Non-KEY=value args with no pcons-build.py: treat as targets
        # for an existing build.ninja
        if remaining and not find_script("pcons-build.py"):
            args.targets = remaining
            return cmd_build(args)

        return cmd_default(args)

    parser = create_full_parser()
    args = parser.parse_args()
    args.extra = getattr(args, "extra", [])

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

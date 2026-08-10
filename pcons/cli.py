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

import click
from click.core import ParameterSource

from pcons import __version__
from pcons._cli_click import (
    ROUTED_TO_DEFAULT,
    DefaultCommand,
    MergingCommand,
    PconsGroup,
    _namespace,
    build_options,
    common_options,
    directory_option,
    generate_options,
    jobs_option,
    watch_option,
)
from pcons.core.errors import PconsError

if TYPE_CHECKING:
    from pcons.core.cache import BuildCache
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


def _split_generators(spec: str | None) -> tuple[list[str], list[str]]:
    """Split a colon-separated generator spec into (build, auxiliary) name lists."""
    import pcons

    build: list[str] = []
    aux: list[str] = []
    for name in [n.strip().lower() for n in (spec or "").split(":") if n.strip()]:
        gen = pcons.GENERATORS.get(name)
        if getattr(gen, "_is_build_generator", False):
            build.append(name)
        else:
            aux.append(name)
    return build, aux


def _merge_generator_spec(cached: str | None, new_spec: str) -> str:
    """Merge a new ``-G`` spec into the cached one.

    A new build generator replaces the cached one (the build slot is sticky);
    auxiliary generators come from the new spec. So an aux-only ``-G metadata``
    keeps the cached build generator, leaving a later bare run something to build.
    """
    cached_build, _ = _split_generators(cached)
    new_build, new_aux = _split_generators(new_spec)
    build = new_build if new_build else cached_build
    return ":".join(build + new_aux)


def _as_str(value: object) -> str | None:
    """Return *value* when it is a string, else None (cache values are untyped)."""
    return value if isinstance(value, str) else None


def _parse_pcons_vars(raw: str | None) -> dict[str, str]:
    """Parse an inherited ``PCONS_VARS`` JSON blob, tolerating a malformed one."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _warn_unread_cached_vars(
    cached_vars: dict[str, str], cli_vars: dict[str, str]
) -> None:
    """Warn about persisted vars the build script never read this run.

    Catches a typo like `pcons FEATRUE=on`, which persists and then does nothing
    forever (CMake warns about unused cache entries the same way). Only vars that
    came from the cache are checked; a var set fresh on this run's command line is
    not nagged, since the script may only start reading it on a later run.
    """
    import pcons.core.vars

    read = pcons.core.vars._accessed_var_names()
    unread = sorted(set(cached_vars) - read - set(cli_vars))
    for name in unread:
        logger.warning(
            "cached variable %r was never read by the build script "
            "(typo, or no longer used?). `pcons cache clear` or --fresh to drop it.",
            name,
        )


def _persist_run_settings(
    cache: BuildCache,
    variables: dict[str, str],
    variant: str | None,
    generator: str | None,
    source_dir: str,
) -> None:
    """Persist the settings resolved for this run into the build-dir cache.

    The caller has already merged the cache with this run's command line (CLI
    wins). Environment overrides are intentionally excluded from these values,
    so a transient ``VAR=x pcons`` never rewrites the persisted cache.

    ``source_dir`` is recorded so a later run can detect a cache that belongs to
    a different source tree (a copied or moved build dir) and refuse to apply it.
    """
    updates: dict[str, object] = {"source_dir": source_dir}
    if variables:
        updates["vars"] = dict(variables)
    if variant:
        updates["variant"] = variant
    if generator:
        updates["generator"] = generator
    cache.update(updates)


def run_script(
    script_path: Path,
    build_dir: Path,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    generator: list[str] | str | None = None,
    reconfigure: bool = False,
    extra_env: dict[str, str] | None = None,
    persist: bool = True,
    fresh: bool = False,
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
        persist: If True (default), write the resolved settings back to the
            build-dir cache after a successful run. A regen re-invoke (ninja's
            self-regeneration rule) passes False so it never writes a cache into
            the directory it regenerates; its argv is already self-contained.
        fresh: If True, discard the persisted cache before resolving settings,
            so the run starts clean (like cmake --fresh).

    Returns:
        Tuple of (exit_code, list of registered Projects).
    """
    import pcons
    import pcons.core.cache
    import pcons.core.invocation
    import pcons.core.vars

    # Absolute from here on, so the script sees the same __file__ however
    # pcons was started. CPython does this for a script's __file__ too (3.9+),
    # and `root = Path(__file__).parent` is the first line of most build
    # scripts: left relative, every path derived from it would change spelling
    # between a user's run and the regen edge's, quietly producing a different
    # manifest on the second pass.
    script_path = script_path.absolute()

    # Resolve persisted settings up front, before recording the invocation, so
    # the regen command carries the effective vars/variant/generator and stays
    # self-contained however the user arrived at them. Precedence lives here, in
    # one place: this run's command line > environment > persisted cache > default.
    # The core readers (get_var/get_variant/Generator) only see the PCONS_* env
    # vars set from these values below.
    cache = pcons.core.cache.BuildCache(build_dir)
    current_source = str(script_path.parent.absolute())
    recorded_source = cache.get("source_dir")
    if isinstance(recorded_source, str) and recorded_source != current_source:
        # The cache belongs to a different source tree (copied or moved build
        # dir). Ignore its settings and start fresh rather than silently applying
        # values meant for another project.
        logger.warning(
            "cache at %s was created for source dir %s but this run's source is "
            "%s; ignoring the persisted settings and starting fresh.",
            cache.path,
            recorded_source,
            current_source,
        )
        fresh = True
    if fresh:
        # Discard any persisted settings before resolving, so this run starts
        # from a clean cache (like cmake --fresh). The subsequent reads then see
        # nothing, and only this run's own settings get persisted below.
        cache.clear()
    cli_vars = dict(variables or {})
    # An inherited PCONS_VARS (exported by the user) overrides the cache but loses
    # to this run's own KEY=value args; like any environment value it is not
    # persisted, so it never rewrites the cache.
    inherited_vars = _parse_pcons_vars(os.environ.get("PCONS_VARS"))
    cached_vars = cache.get("vars")
    cached_vars = cached_vars if isinstance(cached_vars, dict) else {}
    # `persist_vars` (cache <- this run's CLI) is what gets written back. `effective
    # _vars` is what the script reads: cache < inherited PCONS_VARS < this-run CLI,
    # and a cached var shadowed by a same-named bare env var is dropped so `VAR=x
    # pcons` still beats the cache (env names are unknowable, but cache keys aren't,
    # so we omit those from PCONS_VARS and let get_var fall through to the env).
    persist_vars = {**cached_vars, **cli_vars}
    merged_vars = {**cached_vars, **inherited_vars, **cli_vars}
    effective_vars = {
        k: v
        for k, v in merged_vars.items()
        if k in cli_vars or k in inherited_vars or k not in os.environ
    }

    cached_variant = _as_str(cache.get("variant"))
    effective_variant = (
        variant
        or os.environ.get("PCONS_VARIANT")
        or os.environ.get("VARIANT")
        or cached_variant
    )
    persist_variant = variant or cached_variant

    cached_gen = _as_str(cache.get("generator"))
    cli_gen = ":".join(generator) if isinstance(generator, list) else generator
    merged_gen = _merge_generator_spec(cached_gen, cli_gen) if cli_gen else None
    effective_gen = (
        merged_gen
        or os.environ.get("PCONS_GENERATOR")
        or os.environ.get("GENERATOR")
        or cached_gen
    )
    persist_gen = merged_gen or cached_gen

    pcons.core.invocation.record(
        pcons.core.invocation.Invocation(
            script=script_path,
            variables=dict(effective_vars),
            variant=effective_variant,
            generators=effective_gen.split(":") if effective_gen else [],
        )
    )

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

    if effective_vars:
        set_env_var("PCONS_VARS", json.dumps(effective_vars))

    if effective_variant:
        set_env_var("PCONS_VARIANT", effective_variant)

    if effective_gen:
        set_env_var("PCONS_GENERATOR", effective_gen)

    if reconfigure:
        set_env_var("PCONS_RECONFIGURE", "1")

    if extra_env:
        for key, value in extra_env.items():
            set_env_var(key, value)

    logger.info("Running %s", script_path)
    logger.debug("  PCONS_BUILD_DIR=%s", os.environ["PCONS_BUILD_DIR"])
    logger.debug("  PCONS_SOURCE_DIR=%s", os.environ["PCONS_SOURCE_DIR"])
    if effective_vars:
        logger.debug("  PCONS_VARS=%s", os.environ["PCONS_VARS"])
    if effective_variant:
        logger.debug("  PCONS_VARIANT=%s", effective_variant)
    if effective_gen:
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
            if persist:
                _warn_unread_cached_vars(cached_vars, cli_vars)
                _persist_run_settings(
                    cache, persist_vars, persist_variant, persist_gen, current_source
                )
            return 0, pcons.get_registered_projects()
        except ValueError:
            logger.error("No Project created in build script")
            return 1, []

    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        if exit_code != 0:
            _cancel_pending_generation()
        return exit_code, pcons.get_registered_projects()
    except PconsError as e:
        # Expected configure/generate failures carry actionable messages;
        # a Python traceback would only bury them.
        logger.error("%s", e)
        _cancel_pending_generation()
        return 1, []
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
        # PCONS_BUILD_DIR is restored above; drop the singleton bound to it.
        pcons.core.cache.reset_cache()


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


def _parse_ninja_targets(stdout: str, build_dir: Path) -> set[Path]:
    """Absolute paths from ``ninja -t targets all`` output (``path: rule``).

    Phony rules name aliases rather than files, and an alias can collide with
    a real directory name, so they are left out.
    """
    outputs: set[Path] = set()
    for line in stdout.splitlines():
        # rpartition, not split: a Windows path carries its own colon.
        path, sep, rule = line.rpartition(":")
        if not sep or not path.strip() or rule.strip() == "phony":
            continue
        # Normalize after joining: ninja names outputs relative to the build
        # directory, and one outside it ("../src/generated.txt") keeps its ".."
        # through a pathlib join, which would never match a watched path.
        outputs.add(Path(os.path.normpath(build_dir / path.strip())))
    return outputs


def ninja_outputs(build_dir: Path, runner: str | None = None) -> set[Path]:
    """Every file ninja knows how to build, as absolute paths.

    Asked of ninja rather than taken from the Project, because a watch outlives
    the run that generated the manifest: later regenerations happen inside
    ninja's own subprocess, where pcons never sees the resulting graph.
    """
    ninja_cmd = _find_ninja(runner)
    if ninja_cmd is None:
        return set()
    cmd = [*ninja_cmd, "-C", str(build_dir), "-t", "targets", "all"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        logger.debug("Could not list ninja targets: %s", e)
        return set()
    if result.returncode != 0:
        return set()
    return _parse_ninja_targets(result.stdout, build_dir.resolve())


def _explain_reasons(output: str) -> list[str]:
    """Ninja's own explanations for the work it still wants to do."""
    marker = "ninja explain:"
    return [
        line.split(marker, 1)[1].strip()
        for line in output.splitlines()
        if marker in line
    ]


def unconverged_reasons(
    build_dir: Path, targets: list[str] | None = None, runner: str | None = None
) -> list[str]:
    """Ask ninja whether the build that just finished actually converged.

    A command that never creates the output it declares leaves ninja with work
    to do forever: it reruns that edge on every build and says nothing, exiting
    0 each time. One dry run straight afterwards turns a silent rebuild-forever
    into a message naming the output. Returns the reasons, empty when all is
    well (and when there is no ninja build to ask about).
    """
    ninja_cmd = _find_ninja(runner)
    if ninja_cmd is None:
        return []
    cmd = [*ninja_cmd, "-C", str(build_dir), "-n", "-d", "explain", *(targets or [])]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        logger.debug("Could not probe for convergence: %s", e)
        return []
    combined = result.stdout + result.stderr
    if result.returncode != 0 or "no work to do" in combined:
        return []
    return _explain_reasons(combined)


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

    # cmd_build generates on its own when the build files are stale, which is
    # the right entry point for a watch: it regenerates only when needed.
    if getattr(args, "watch", False):
        return cmd_build(args)

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
        persist=not getattr(args, "no_cache", False),
        fresh=getattr(args, "fresh", False),
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
    (ninja, make, or xcodebuild), regenerating them first if stale.

    With --watch, build once and then keep rebuilding as sources change.
    """
    if getattr(args, "watch", False):
        return _watch_build(args)
    return _build_targets(args)


def _watch_build(args: argparse.Namespace) -> int:
    """Build, then rebuild whenever a watched file changes.

    Each iteration is just another build: ninja's regen edge re-runs pcons
    when the build description itself changed, so editing the build script
    needs no special handling here.
    """
    from pcons import watch

    setup_logging(args.verbose, args.debug)
    try:
        watch.ensure_available()
    except PconsError as e:
        logger.error("%s", e)
        return 1

    # What ninja knows how to build. Refreshed whenever the manifest changes and
    # consulted live by the watch, so an output landing in the source tree never
    # retriggers the build that wrote it.
    outputs: set[Path] = set()
    manifest_mtime = 0.0
    runner = getattr(args, "ninja", None)

    def build() -> int:
        nonlocal manifest_mtime
        code = _build_targets(args)
        build_dir = Path(args.build_dir).absolute()

        manifest = build_dir / "build.ninja"
        mtime = manifest.stat().st_mtime if manifest.exists() else 0.0
        if mtime != manifest_mtime:
            manifest_mtime = mtime
            outputs.clear()
            outputs.update(ninja_outputs(build_dir, runner))

        if code == 0:
            _, targets = parse_variables(getattr(args, "extra", []))
            _warn_unconverged(unconverged_reasons(build_dir, targets, runner))
        return code

    try:
        watch.run_build(build)
    except KeyboardInterrupt:
        # Interrupted before the watch (and its handler) is up.
        return 0

    # Read the build directory only now: the first build settles it, since
    # the build script may choose a directory other than the requested one.
    build_dir = Path(args.build_dir).absolute()
    script = _find_build_script(args)
    root = (script.parent if script else Path.cwd()).absolute()

    # An in-source build (-B .) has nothing to exclude by directory without
    # excluding the project; there the output list carries it alone.
    excluded_dirs = [build_dir] if build_dir != root else []

    return watch.watch_and_build(
        build,
        [root],
        excluded_dirs=excluded_dirs,
        excluded_paths=outputs,
    )


def _warn_unconverged(reasons: list[str], limit: int = 5) -> None:
    """Report a build that left ninja with work still to do."""
    if not reasons:
        return
    logger.warning(
        "the build did not converge: ninja still has work to do right after a "
        "successful build, so it will run these again every time. Usually a "
        "command does not create the output it declares. Ninja explains:"
    )
    for reason in reasons[:limit]:
        logger.warning("    %s", reason)
    if len(reasons) > limit:
        logger.warning("    ... and %d more", len(reasons) - limit)


def _find_build_script(args: argparse.Namespace) -> Path | None:
    """Locate the build script named by --build-script, or in the cwd."""
    script_arg = getattr(args, "build_script", None)
    if script_arg:
        return Path(script_arg)
    return find_script("pcons-build.py")


def _build_targets(args: argparse.Namespace) -> int:
    """Run one build, regenerating build files first if they are stale."""
    setup_logging(args.verbose, args.debug)

    build_dir = Path(args.build_dir)

    # Auto-generate if build files are missing or stale
    build_script = getattr(args, "build_script", None)
    if _needs_generation(build_dir, build_script=build_script):
        script = _find_build_script(args)
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
        # Xcode picks the configuration at build time; fall back to the cached
        # variant so a bare build matches what was generated, not Release.
        if variant is None:
            import pcons.core.cache

            cached = pcons.core.cache.BuildCache(build_dir).get("variant")
            variant = cached if isinstance(cached, str) else None
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


def cmd_cache(args: argparse.Namespace) -> int:
    """Inspect or clear the per-build-dir cache (pcons_cache.json).

    Reads the cache file directly; never runs the build script. The build
    directory comes from -B / $PCONS_BUILD_DIR (default 'build').
    """
    from pcons.core.cache import BuildCache

    cache = BuildCache(Path(args.build_dir))
    action = getattr(args, "cache_action", None) or "list"

    if action == "path":
        print(cache.path)
        return 0

    if cache.path is None or not cache.path.exists():
        print(f"No cache at {cache.path}")
        return 0

    if action == "clear":
        cache.clear()
        print(f"Cleared {cache.path}")
        return 0

    # list / show: print the user-facing settings, one per line.
    cached_vars = cache.get("vars")
    if isinstance(cached_vars, dict):
        for key in sorted(cached_vars):
            print(f"{key}={cached_vars[key]}")
    variant = cache.get("variant")
    if isinstance(variant, str):
        print(f"variant={variant}")
    generator = cache.get("generator")
    if isinstance(generator, str):
        print(f"generator={generator}")

    if action == "show":
        source_dir = cache.get("source_dir")
        if isinstance(source_dir, str):
            print(f"# source_dir: {source_dir}")
        print(f"# cache file: {cache.path}")
    return 0


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


def load_user_modules(args: argparse.Namespace) -> None:
    """Load user modules from search paths."""
    from pcons import modules

    extra_paths: list[Path | str] | None = None
    modules_path = getattr(args, "modules_path", None)
    if modules_path:
        extra_paths = modules_path.split(os.pathsep)

    modules.load_modules(extra_paths)


_DESCRIPTION = """\
A Python-based build system that generates Ninja files.

\b
Without a subcommand, generates build files and builds specified
targets (or default targets if none given):
  pcons                     Generate and build default targets
  pcons hello               Generate and build 'hello'
  pcons CC=clang hello      Set CC=clang, generate and build 'hello'
"""

_EPILOG = """\
Use -C DIR to change to DIR before doing anything else.

Run 'pcons <command> --help' for command-specific help.

\b
GitHub:  https://github.com/DarkStarSystems/pcons
Docs:    https://pcons.readthedocs.io/
"""


def _run_default(args: argparse.Namespace) -> int:
    """The no-subcommand path: build the named targets, or generate and build.

    A non-KEY=value argument with no build script to run is a target of an
    existing build.ninja, not something to generate from.
    """
    _variables, remaining = parse_variables(args.extra)
    if remaining and not find_script("pcons-build.py"):
        return cmd_build(args)
    return cmd_default(args)


@click.group(
    cls=PconsGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=_DESCRIPTION,
    epilog=_EPILOG,
)
@click.version_option(__version__, "--version", message="%(prog)s %(version)s")
@directory_option
@common_options
@generate_options
@build_options
@watch_option
@jobs_option
@click.pass_context
def cli(ctx: click.Context, **kw: object) -> None:
    # A command name that resolved to nothing has already been routed to the
    # catch-all command, which is about to run. Only a command line naming no
    # command at all gets it invoked from here.
    if ctx.invoked_subcommand is None and not ctx.meta.get(ROUTED_TO_DEFAULT):
        ctx.exit(_run_default(_namespace(ctx, None, **kw)))


@cli.command(
    "info",
    cls=MergingCommand,
    short_help="Show build script info and available variables",
    help=(
        "Show build script info and available variables.\n\n"
        "EXTRA is build variables (KEY=value)."
    ),
)
@directory_option
@common_options
@generate_options
@click.option(
    "-t",
    "--targets",
    is_flag=True,
    default=False,
    help="List all build targets (runs the build script)",
)
@click.argument("extra", nargs=-1)
@click.pass_context
def cli_info(ctx: click.Context, **kw: object) -> None:
    ctx.exit(cmd_info(_namespace(ctx, "info", **kw)))


@cli.command("init", cls=MergingCommand, short_help="Initialize a new pcons project")
@directory_option
@common_options
@click.option(
    "-f", "--force", is_flag=True, default=False, help="Overwrite existing files"
)
@click.option(
    "--lang",
    type=click.Choice(["c", "cpp"]),
    default="cpp",
    help="Language for the starter program when no sources are found (default: cpp)",
)
@click.pass_context
def cli_init(ctx: click.Context, **kw: object) -> None:
    ctx.exit(cmd_init(_namespace(ctx, "init", **kw)))


@cli.command(
    "generate",
    cls=MergingCommand,
    short_help="Generate build files from pcons-build.py",
    help=(
        "Generate build files from pcons-build.py.\n\n"
        "EXTRA is build variables (KEY=value)."
    ),
)
@directory_option
@common_options
@generate_options
# Internal: the self-regeneration rule re-invokes `generate` with this so it
# doesn't persist a cache into the directory it regenerates. Not for users.
@click.option("--no-cache", is_flag=True, default=False, hidden=True)
# --graph and --mermaid take an optional value: the filename, or stdout when
# the option stands alone. Do not spell `default=None` here. click decides an
# option may stand alone by testing whether its default is unset, and an
# explicit None counts as a default, which turns `--graph` back into an option
# that demands an argument. Absent, the value is None either way.
#
# The brackets in the metavar are literal text. click renders an option that
# may stand alone exactly like one that may not, so `--graph FILE` would read
# as if the filename were required. Only the help record uses the metavar, so
# the brackets cost nothing elsewhere.
@click.option(
    "--graph",
    is_flag=False,
    flag_value="-",
    metavar="[FILE]",
    help="Output dependency graph in DOT format (default: stdout)",
)
@click.option(
    "--mermaid",
    is_flag=False,
    flag_value="-",
    metavar="[FILE]",
    help="Output dependency graph in Mermaid format (default: stdout)",
)
@click.argument("extra", nargs=-1)
@click.pass_context
def cli_generate(ctx: click.Context, **kw: object) -> None:
    ctx.exit(_cmd_generate_wrapper(_namespace(ctx, "generate", **kw)))


@cli.command(
    "build",
    cls=MergingCommand,
    short_help="Build targets (auto-generates if needed)",
    help=(
        "Build targets using the appropriate build tool. "
        "If build files are missing or out of date, generates them first.\n\n"
        "EXTRA is build variables (KEY=value) and/or targets to build."
    ),
)
@directory_option
@common_options
@generate_options
@build_options
@watch_option
@jobs_option
@click.argument("extra", nargs=-1)
@click.pass_context
def cli_build(ctx: click.Context, **kw: object) -> None:
    ctx.exit(cmd_build(_namespace(ctx, "build", **kw)))


@cli.command("clean", cls=MergingCommand, short_help="Clean build artifacts")
@directory_option
@common_options
@build_options
@click.option(
    "-a", "--all", is_flag=True, default=False, help="Remove entire build directory"
)
@click.pass_context
def cli_clean(ctx: click.Context, **kw: object) -> None:
    ctx.exit(cmd_clean(_namespace(ctx, "clean", **kw)))


@cli.command(
    "cache", cls=MergingCommand, short_help="Inspect or clear the per-build-dir cache"
)
@directory_option
@common_options
@click.argument(
    "cache_action",
    required=False,
    default="list",
    type=click.Choice(["list", "show", "clear", "path"]),
)
@click.pass_context
def cli_cache(ctx: click.Context, **kw: object) -> None:
    ctx.exit(cmd_cache(_namespace(ctx, "cache", **kw)))


@cli.command(
    "test",
    short_help="Run tests declared by project.Test() in pcons-build.py",
    # The runner owns its own flags, so everything after `test` is handed over
    # untouched, including --help.
    context_settings={"ignore_unknown_options": True, "help_option_names": []},
)
@directory_option
@click.argument("argv", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def cli_test(ctx: click.Context, argv: tuple[str, ...]) -> None:
    from pcons.test_runner import main as test_main

    # Options before the subcommand never reach the runner's parser, so a build
    # directory spelled there is forwarded explicitly. It goes first, so the
    # runner's own -B, spelled after `test`, still wins. Only a -B the user
    # actually typed is forwarded: with none, the runner searches upward from
    # the current directory for the manifest, and passing it a default would
    # silently stop that search.
    forwarded: list[str] = []
    parent = ctx.parent
    if (
        parent is not None
        and parent.get_parameter_source("build_dir") is ParameterSource.COMMANDLINE
    ):
        forwarded = ["-B", str(parent.params["build_dir"])]
    ctx.exit(test_main(forwarded + list(argv)))


@cli.command("_default", cls=DefaultCommand, hidden=True)
@directory_option
@common_options
@generate_options
@build_options
@watch_option
@jobs_option
@click.argument("extra", nargs=-1)
@click.pass_context
def cli_default(ctx: click.Context, **kw: object) -> None:
    ctx.exit(_run_default(_namespace(ctx, None, **kw)))


def main() -> int:
    """Main entry point for the pcons CLI."""
    try:
        # windows_expand_args: with args=None on Windows, click applies
        # expanduser, expandvars and glob to every token. pcons positionals are
        # build variables and target names, not paths, and the expansion runs
        # after the shell, so quoting cannot escape it: cmd would keep
        # "CFLAGS=-DV=%FOO%" literal and click would then substitute it. Unix
        # has no such rewrite, so leaving it on makes Windows strictly worse.
        result = cli.main(
            args=None,
            prog_name="pcons",
            standalone_mode=False,
            windows_expand_args=False,
        )
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Abort:
        return 130
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())

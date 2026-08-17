# SPDX-License-Identifier: MIT
"""Command-line interface for pcons."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from click.core import ParameterSource
from click.shell_completion import CompletionItem

from pcons import __version__, _cli_completion
from pcons import commands as user_commands
from pcons._cli_click import (
    DefaultCommand,
    MergingGroup,
    PconsContext,
    PconsGroup,
    PconsPath,
    TargetsCommand,
    _adopt_options_spelled_earlier,
    build_options,
    common_options,
    configure_logging,
    directory_option,
    generate_options,
    jobs_option,
    load_declared_modules,
    pass_pcons_context,
    run_cli,
    targets_argument,
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
            return True  # Script not found; let _generate report it
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


def _describes_a_build_already() -> bool:
    """Whether the program that started this process has already described one.

    A build script may hand over to the CLI from a ``__main__`` guard, but only
    before it describes anything: the guard is reached with argv unparsed, so
    everything above it read no build variables and no variant. A top-level
    project already built by the file this interpreter was started on means the
    description happened up there, on values that were not the user's.

    Both halves are needed. A project on its own is what an embedder has when
    it drives the CLI, and ``sys.argv[0]`` on its own says nothing about when
    the guard was reached.
    """
    from pcons.core.project import Project

    if not Project.has_current():
        return False
    program = sys.argv[0] if sys.argv else ""
    if not program:
        return False
    try:
        return Path(Project.top_level().defined_at.filename).resolve() == (
            Path(program).resolve()
        )
    except (OSError, ValueError):  # pragma: no cover - unresolvable, not ours
        return False


def _read_variables_already() -> bool:
    """Whether the program that started this process has already read a variable.

    A build variable or the variant read above a hand-over to the CLI returned
    a default, because no command line had been parsed yet. The read counts
    only when the file that made it is the file this interpreter was started
    on: a read from a script pcons itself is running has an invocation
    recorded and is never noted in the first place.
    """
    from pcons.core.invocation import running_as_a_program
    from pcons.core.vars import _read_site_outside_a_run

    site = _read_site_outside_a_run()
    if site is None:
        return False
    return running_as_a_program(Path(site))


def _acted_before_handing_over() -> bool:
    """Whether the program did pcons work above its hand-over to the CLI."""
    return _describes_a_build_already() or _read_variables_already()


_ACTED_BEFORE_HANDING_OVER = (
    "this build script described its build or read a build variable before "
    "handing over to pcons.\n"
    "Everything above the hand-over ran without the command line, so build "
    "variables and the variant were still unset.\n"
    "Put the entry point above everything else:\n"
    "\n"
    '    if __name__ == "__main__":\n'
    "        import sys\n"
    "\n"
    "        import pcons.cli\n"
    "\n"
    "        sys.exit(pcons.cli.main())"
)


def _maybe_postmortem(exc: BaseException) -> None:
    """Drop into pdb postmortem on *exc* when --pdb / PCONS_PDB=1 asks for it.

    The one capability direct runs used to provide was postmortem on a
    crashing build script; the CLI is the only entry point now, so it offers
    the same explicitly. No-op unless requested, and never for SystemExit or
    click's control-flow exceptions, which the callers handle above.
    """
    if not os.environ.get("PCONS_PDB"):
        return
    import pdb

    print(
        "Entering pdb postmortem (--pdb). 'up'/'down' to walk the stack, 'q' to quit.",
        file=sys.stderr,
    )
    pdb.post_mortem(exc.__traceback__)


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


def _declared_command_listing() -> list[dict[str, str]]:
    """What the build script declared, for `pcons run` to list without running it.

    Script-origin only: a module's commands are known from the filesystem at
    startup, and caching them would list them twice on a machine that has the
    module and once, staler, on one that does not. Declaration order, like
    `PconsGroup.list_commands`.
    """
    return [
        {"name": name, "help": entry.command.get_short_help_str()}
        for name, entries in user_commands.declared().items()
        for entry in entries
        if entry.origin == user_commands.SCRIPT_ORIGIN
    ]


def _record_command_listing(cache: BuildCache, *, persist: bool) -> None:
    """Write what the build script declared into the build dir's listing.

    Written on every *generating* run, not only a persisting one, so ninja's
    self-regeneration edge refreshes it: that re-invoke passes ``--no-cache``,
    and without this a command added to the script would never reach the
    listing again, build.ninja being newer than the script from then on.

    Including an empty list. Unlike the keys that fall back to a default when
    absent, a name left in place would be listed forever after it was deleted
    from the script.

    A regen still never *creates* a cache, which is the other half of what
    ``persist=False`` means, so an untouched build directory is left alone.
    """
    if persist or not cache.is_empty:
        cache.update({"commands": _declared_command_listing()})


def _buildable_names(project: Project) -> list[str]:
    """Every name a build tool accepts for this project, as the tool sees it.

    Output paths rendered from the build directory, which is the contract every
    generator that runs there shares, plus the aliases and the ``all`` phony the
    generators add. Not ``target.name``: a target that sets ``output_name`` or
    ``output_prefix`` is spelled differently in the build file, so
    ``examples/03_variants`` would offer ``variant_demo_debug`` for a build file
    that only knows ``debug/variant_demo``.

    Only shell completion reads this. It must never run the build script, so the
    names are recorded when one does run.
    """
    from pcons.core.node import FileNode

    resolver = project._path_resolver
    names = {"all", *project.tree_aliases}
    for target in project.targets:
        for node in target.output_nodes:
            if isinstance(node, FileNode):
                names.add(resolver.make_execution_relative(node.path))
    return sorted(names)


def _persist_run_settings(
    cache: BuildCache,
    variables: dict[str, str],
    variant: str | None,
    generator: str | None,
    source_dir: str,
    targets: list[str] | None = None,
    variants: set[str] | None = None,
) -> None:
    """Persist the settings resolved for this run into the build-dir cache.

    The caller has already merged the cache with this run's command line (CLI
    wins). Environment overrides are intentionally excluded from these values,
    so a transient ``VAR=x pcons`` never rewrites the persisted cache.

    ``source_dir`` is recorded so a later run can detect a cache that belongs to
    a different source tree (a copied or moved build dir) and refuse to apply it.

    The declared-command listing is written separately, by the caller: it
    describes the build script rather than this run's argv, so it is recorded
    on every generating run and not only on a persisting one.

    ``targets`` is ``None`` for a run that did not generate, which leaves any
    recorded names alone. A run that only reads, `pcons info` among them, never
    resolves the targets, so treating its empty result as an answer would wipe
    what the last generate recorded.

    ``variants`` accumulates instead of replacing, which is the one place it
    differs from ``targets``. A script that branches on ``get_variant()`` names
    only the variant this run asked for, so replacing would leave a build dir
    completing whichever variant it was configured with last. ``--fresh`` is the
    way back to an empty set.
    """
    updates: dict[str, object] = {"source_dir": source_dir}
    if variables:
        updates["vars"] = dict(variables)
    if variant:
        updates["variant"] = variant
    if generator:
        updates["generator"] = generator
    if targets is not None:
        updates["targets"] = targets
    if variants:
        recorded = cache.get("variants")
        recorded = recorded if isinstance(recorded, list) else []
        updates["variants"] = sorted({*recorded, *variants} - {""})
    cache.update(updates)


def _persist_run_settings_to_projects(
    projects: list[Project],
    cli_build_dir: Path,
    variables: dict[str, str],
    variant: str | None,
    generator: str | None,
    source_dir: str,
    variants: set[str] | None = None,
) -> None:
    """Persist this run's settings into each sibling project's build directory.

    The CLI's cache lives in the -B directory, which belongs to the first
    project; a later ``pcons -B <sibling's dir>`` must see the same
    settings, not defaults. The recorded target names are each project's
    own — relative to its build directory, so completion under
    ``-B <sibling's dir>`` offers what that directory can build.
    """
    from pcons.core.cache import BuildCache

    cli_dir = os.path.normcase(os.path.normpath(cli_build_dir.absolute()))
    for project in projects:
        project_dir = project._effective_output_dir()
        if os.path.normcase(str(project_dir)) == cli_dir:
            continue
        cache = BuildCache(project_dir)
        _persist_run_settings(
            cache,
            variables,
            variant,
            generator,
            source_dir,
            targets=_buildable_names(project),
            variants=variants,
        )
        # The declared-command listing too: `pcons -B <this dir> run` reads
        # it from here, and must list the same commands the primary does.
        _record_command_listing(cache, persist=True)


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
    generate: bool = True,
    inside: Callable[[], None] | None = None,
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
        generate: If False, drop the script's deferred generate requests
            instead of running them, and resolve the top-level project so the
            graph is usable without writing any build files. For inspection
            commands (`pcons explain`) and user-declared commands, which need
            the project graph but must leave the build directory alone.
        inside: Called once after the script has run and settled, with the
            script's environment still up: PCONS_* variables set, sys.path and
            the cwd still the script's. Its exceptions propagate untouched,
            since click signals both success and failure by exception.

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
        #
        # A run that persists nothing must not mutate the build directory
        # either: `pcons run` is documented to leave it alone, and it reaches
        # here whenever the cache was written for another source tree. Writing
        # the emptied cache out would destroy that directory's vars, variant,
        # generator and command listing on behalf of a command that only meant
        # to read.
        #
        # So `--fresh --no-cache` together no longer empty the file, only this
        # run's view of it. The two ask for opposite things and "do not touch
        # the cache" is the safer half to honour; --no-cache is internal, and
        # the regen re-invoke that uses it never passes --fresh.
        if persist:
            cache.clear()
        else:
            cache.discard()
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

    from pcons import Project

    # Clear stuff
    pcons._clear_registered_projects()
    Project._clear_tree()
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
        # Commands the script declares belong to this run of it, and the
        # previous run's are dropped on the way in.
        with user_commands.script_scope():
            try:
                os.chdir(script_path.parent)
                sys.path.insert(0, str(script_path.parent))

                script_source = script_path.read_text()
                code = compile(script_source, str(script_path), "exec")
                namespace: dict[str, object] = {
                    "__name__": pcons.core.invocation.RUN_NAME,
                    "__file__": str(script_path),
                }
                exec(code, namespace)

                # Run any deferred generate requests registered by the script
                from pcons.generators.generator import BaseGenerator

                top_levels = Project._top_level_projects()
                if not top_levels:
                    logger.error("No Project created in build script")
                    return 1, []

                if generate:
                    for top_level in top_levels:
                        BaseGenerator._generate_pending(top_level)
                else:
                    _cancel_pending_generation()
                    for top_level in top_levels:
                        if not top_level._resolved:
                            top_level.resolve()
                if generate:
                    _record_command_listing(cache, persist=persist)
                if persist:
                    _warn_unread_cached_vars(cached_vars, cli_vars)
                    _persist_run_settings(
                        cache,
                        persist_vars,
                        persist_variant,
                        persist_gen,
                        current_source,
                        # Union over the sibling projects: bare
                        # `pcons <TAB>` routes a target to whichever
                        # sibling owns it, so the primary cache offers
                        # every project's names.
                        targets=sorted(
                            {n for p in top_levels for n in _buildable_names(p)}
                        )
                        if generate
                        else None,
                        variants=pcons.core.vars._seen_variant_names(),
                    )
                    if generate:
                        _persist_run_settings_to_projects(
                            top_levels,
                            build_dir,
                            persist_vars,
                            persist_variant,
                            persist_gen,
                            current_source,
                            variants=pcons.core.vars._seen_variant_names(),
                        )

            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
                if not isinstance(e.code, int) and e.code is not None:
                    # CPython's top-level handler prints a non-int exit code to
                    # stderr; exec() bypasses it, so match it here.
                    print(e.code, file=sys.stderr)
                if exit_code != 0:
                    _cancel_pending_generation()
                    return exit_code, pcons.get_registered_projects()
                # sys.exit(0) ends the script successfully partway: the
                # generation it asked for still belongs to this run, and the
                # finally block below is about to restore cwd and env.
                from pcons.generators.generator import BaseGenerator

                top_levels = Project._top_level_projects()
                if generate and top_levels:
                    for top_level in top_levels:
                        BaseGenerator._generate_pending(top_level)
                else:
                    _cancel_pending_generation()
                return exit_code, pcons.get_registered_projects()
            except PconsError as e:
                # Expected configure/generate failures carry actionable messages;
                # a Python traceback would only bury them.
                logger.error("%s", e)
                _cancel_pending_generation()
                _maybe_postmortem(e)
                return 1, []
            except Exception as e:
                logger.error("Build script failed: %s", e)
                traceback.print_exc()
                _cancel_pending_generation()
                _maybe_postmortem(e)
                return 1, []

            # Outside every handler above, deliberately: click raises to signal
            # success as well as failure (`ctx.exit(0)` raises
            # `click.exceptions.Exit`, a RuntimeError), so catching here would
            # report a user command's normal exit as a failed build script.
            if inside is not None:
                inside()
            return 0, pcons.get_registered_projects()
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


def _generate(
    build_dir: Path,
    *,
    script: Path | None = None,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    generator: list[str] | None = None,
    reconfigure: bool = False,
    fresh: bool = False,
    no_cache: bool = False,
    graph: str | None = None,
    mermaid: str | None = None,
    jobs: int | None = None,
) -> tuple[int, list[Project]]:
    """Run the build script, which writes the build files into *build_dir*.

    Logging and user modules are the caller's business: this runs the script
    and nothing else.

    Args:
        script: The build script, or None to look for pcons-build.py here.
        graph: Where to write a DOT dependency graph, "-" for stdout.
        mermaid: The same, in Mermaid.
        jobs: How many subprocesses configure may run at once. Configure has
            its own parallel work (C++ module scanning), and a user who capped
            the build's jobs meant to cap that too.

    Returns:
        Tuple of (exit code, the top-level projects the script created, in
        creation order — empty on failure or when it created none).
    """
    if script is not None:
        if not script.exists():
            logger.error("Build script not found: %s", script)
            return 1, []
    else:
        found_script = find_script("pcons-build.py")
        if found_script is None:
            logger.error("No pcons-build.py found in current directory")
            logger.info("Create a pcons-build.py file or run 'pcons init'")
            return 1, []
        script = found_script

    build_dir.mkdir(parents=True, exist_ok=True)

    extra_env: dict[str, str] = {}
    if graph:
        extra_env["PCONS_GRAPH"] = graph
    if mermaid:
        extra_env["PCONS_MERMAID"] = mermaid
    if jobs:
        extra_env["PCONS_JOBS"] = str(jobs)

    exit_code, _projects = run_script(
        script,
        build_dir,
        variables=variables or {},
        variant=variant,
        generator=generator,
        reconfigure=reconfigure,
        extra_env=extra_env if extra_env else None,
        persist=not no_cache,
        fresh=fresh,
    )

    if exit_code != 0:
        return exit_code, []

    return 0, [p for p in _projects if p.is_top_level]


def _watch(
    *,
    build: Callable[[], tuple[int, list[Path]]],
    script: Path | None,
    targets: list[str] | None = None,
    ninja: str | None = None,
) -> int:
    """Run *build*, then run it again whenever a watched file changes.

    Each iteration is just another build: ninja's regen edge re-runs pcons when
    the build description itself changed, so editing the build script needs no
    special handling here.

    Args:
        build: Runs one build and reports where it ran — several directories
            when the script describes several projects. Which those are
            settles on the first call, since the script picks its own.
        script: The build script, whose directory is the tree to watch.
    """
    from pcons import watch

    try:
        watch.ensure_available()
    except PconsError as e:
        logger.error("%s", e)
        return 1

    # What ninja knows how to build. Refreshed whenever a manifest changes and
    # consulted live by the watch, so an output landing in the source tree never
    # retriggers the build that wrote it.
    outputs: dict[Path, set[Path]] = {}
    manifest_mtimes: dict[Path, float] = {}
    settled_dirs: list[Path] = [Path.cwd()]

    def build_once() -> int:
        nonlocal settled_dirs
        code, wheres = build()
        settled_dirs = [where.absolute() for where in wheres]

        for build_dir in settled_dirs:
            manifest = build_dir / "build.ninja"
            mtime = manifest.stat().st_mtime if manifest.exists() else 0.0
            if mtime != manifest_mtimes.get(build_dir):
                manifest_mtimes[build_dir] = mtime
                outputs[build_dir] = ninja_outputs(build_dir, ninja)

        if code == 0:
            for build_dir in settled_dirs:
                _warn_unconverged(unconverged_reasons(build_dir, targets, ninja))
        return code

    try:
        watch.run_build(build_once)
    except KeyboardInterrupt:
        # Interrupted before the watch (and its handler) is up.
        return 0

    # Read the build directories only now: the first build settled them.
    root = (script.parent if script else Path.cwd()).absolute()

    # An in-source build (-B .) has nothing to exclude by directory without
    # excluding the project; there the output list carries it alone.
    excluded_dirs = [d for d in settled_dirs if d != root]

    return watch.watch_and_build(
        build_once,
        [root],
        excluded_dirs=excluded_dirs,
        excluded_paths=set().union(*outputs.values()) if outputs else set(),
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


def _generators(chosen: tuple[str, ...]) -> list[str] | None:
    """click's tuple as the list downstream tests for truthiness.

    An empty selection has to become None, not [], because the code below asks
    whether a generator was named rather than how many were.
    """
    return list(chosen) or None


def _resolve_build_script(script: Path | None) -> Path | None:
    """The build script named on the command line, or the one in the cwd."""
    return script if script is not None else find_script("pcons-build.py")


def _run_build_tool(
    build_dir: Path,
    *,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,
    ninja: str | None = None,
    variant: str | None = None,
) -> int:
    """Run whichever build tool matches the files already in *build_dir*."""
    ninja_file = build_dir / "build.ninja"
    makefile = build_dir / "Makefile"
    xcodeproj_files = list(build_dir.glob("*.xcodeproj"))

    if ninja_file.exists():
        return run_ninja(
            build_dir, targets=targets, jobs=jobs, verbose=verbose, runner=ninja
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


def _no_build_described() -> int:
    """The status for a script that ran cleanly and created no project.

    A script may decide it has nothing to build: a missing optional toolchain,
    an environment it is not meant to run in. It says so and exits 0, which is
    not the failure that no build files usually means, so the build is skipped
    rather than looked for and missed.

    Said at a level the default shows. A build was asked for and none happened,
    and a script that stops without a word of its own would otherwise leave
    that as silence and a zero.
    """
    logger.warning("Build script described no build, nothing to do")
    return 0


#: Ninja targets which every pcons manifest defines, so a request for one goes to
#: every sibling project rather than being looked up in any of them.
_TARGETS_IN_EVERY_PROJECT = frozenset({"all", "test-build"})


def _route_targets(
    projects: list[Project], targets: list[str] | None
) -> list[tuple[Project, list[str] | None]] | None:
    """Assign each named target to the top-level project that owns it.

    Returns the build plan as (project, its targets) pairs in script order.
    With no targets named, every project builds its defaults. A name owned
    by several siblings must be qualified (``project::target``); an unknown
    or still-ambiguous name logs an error and returns None.
    """
    if not targets:
        return [(p, None) for p in projects]
    if len(projects) == 1:
        # Pass through: ninja may know names pcons doesn't (file paths).
        return [(projects[0], targets)]

    from pcons.core.target import split_qualified_name

    def owns_target(p: Project, token: str) -> bool:
        try:
            return p.get_target(token, raise_if_missing=False) is not None
        except KeyError:
            return True  # duplicate name inside this project: it owns it

    routed: dict[int, list[str]] = {id(p): [] for p in projects}
    for token in targets:
        if token in _TARGETS_IN_EVERY_PROJECT:
            for p in projects:
                routed[id(p)].append(token)
            continue

        prefix, base = split_qualified_name(token)
        if prefix is not None:
            named = next((p for p in projects if p.name == prefix), None)
            if named is not None:
                # Ninja knows the plain name, not the qualified spelling.
                routed[id(named)].append(base)
                continue
            # Not a sibling's name: fall through and look the whole token
            # up as an in-tree qualified name (subproject::target).

        # An alias is a user-level grouping, so one name declared by
        # several projects (at any level of their trees) means all of
        # them: build each project's group.
        alias_owners = [p for p in projects if token in p.tree_aliases]
        if alias_owners:
            confusable = [
                p for p in projects if p not in alias_owners and owns_target(p, token)
            ]
            if confusable:
                names = ", ".join(
                    f"{p.name}::{token}" for p in (*alias_owners, *confusable)
                )
                logger.error(
                    "'%s' is an alias in one project and a target in "
                    "another; qualify it: %s",
                    token,
                    names,
                )
                return None
            for p in alias_owners:
                routed[id(p)].append(token)
            continue

        owners = [p for p in projects if owns_target(p, token)]
        if not owners:
            searched = ", ".join(p.name for p in projects)
            logger.error(
                "no project owns a target named '%s' (searched: %s)",
                token,
                searched,
            )
            return None
        if len(owners) > 1:
            names = ", ".join(f"{p.name}::{token}" for p in owners)
            logger.error(
                "target '%s' exists in several projects; qualify it: %s",
                token,
                names,
            )
            return None
        routed[id(owners[0])].append(base if prefix is not None else token)

    return [(p, routed[id(p)]) for p in projects if routed[id(p)]]


def _build(
    build_dir: Path,
    *,
    regenerate: Callable[[], tuple[int, list[Project]]],
    projects: list[Project] | None = None,
    script: Path | None = None,
    targets: list[str] | None = None,
    jobs: int | None = None,
    verbose: bool = False,
    ninja: str | None = None,
    variant: str | None = None,
) -> tuple[int, list[Path]]:
    """Run one build, regenerating first if the build files are stale.

    The regeneration is passed in rather than described by more parameters:
    what it needs is the whole of `_generate`'s signature, and the caller
    already holds those values. *projects* is the result of the caller's
    most recent generation, if it ran one; a regeneration here replaces it.
    Without either, the build runs in *build_dir* alone.

    One project builds in its directory; several top-level projects build
    serialized, in script order, each named target routed to the project
    that owns it. A failure stops the run there.

    Returns:
        Tuple of (exit code, the directories built, in order). Not always
        the one asked for: a regeneration may run a script that picks its
        own.
    """
    if _needs_generation(build_dir, build_script=str(script) if script else None):
        found = _resolve_build_script(script)
        if found is not None and found.exists():
            logger.info("Build files missing or out of date, regenerating...")
            code, projects = regenerate()
            if code != 0:
                return code, [build_dir]
            if not projects:
                return _no_build_described(), [build_dir]

    if not projects:
        # No regeneration ran: build the requested directory. With sibling
        # projects, -B scopes the build to the one owning that directory.
        return _run_build_tool(
            build_dir,
            targets=targets,
            jobs=jobs,
            verbose=verbose,
            ninja=ninja,
            variant=variant,
        ), [build_dir]

    plan = _route_targets(projects, targets)
    if plan is None:
        return 1, [build_dir]

    built: list[Path] = []
    for project, project_targets in plan:
        where = project._effective_output_dir()
        built.append(where)
        code = _run_build_tool(
            where,
            targets=project_targets,
            jobs=jobs,
            verbose=verbose,
            ninja=ninja,
            variant=variant,
        )
        if code != 0:
            return code, built
    return 0, built


def _clean(build_dir: Path, *, everything: bool, ninja: str | None) -> int:
    """Clean build artifacts: 'ninja -t clean', or the whole directory."""
    if everything:
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

    ninja_cmd = _find_ninja(ninja)
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


def _open_cache(build_dir: Path) -> BuildCache:
    """The build directory's cache. Reads the file; never runs the script."""
    from pcons.core.cache import BuildCache

    return BuildCache(build_dir)


def _cache_path(build_dir: Path) -> int:
    """Where the cache lives, whether or not it exists yet."""
    print(_open_cache(build_dir).path)
    return 0


def _print_persisted_settings(cache: BuildCache) -> None:
    """The user-facing settings, one per line."""
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


def _cache_list(build_dir: Path) -> int:
    cache = _open_cache(build_dir)
    if cache.path is None or not cache.path.exists():
        print(f"No cache at {cache.path}")
        return 0
    _print_persisted_settings(cache)
    return 0


def _cache_show(build_dir: Path) -> int:
    cache = _open_cache(build_dir)
    if cache.path is None or not cache.path.exists():
        print(f"No cache at {cache.path}")
        return 0
    _print_persisted_settings(cache)
    source_dir = cache.get("source_dir")
    if isinstance(source_dir, str):
        print(f"# source_dir: {source_dir}")
    print(f"# cache file: {cache.path}")
    return 0


def _cache_clear(build_dir: Path) -> int:
    """Discard everything this build directory remembers.

    The persisted settings, and the C++ module scan results beside them: both
    are answers from an earlier run, and asking for them to be forgotten means
    both. Deleting the scan cache costs one rescan.
    """
    from pcons.toolchains._scan_cache import CACHE_FILE as SCAN_CACHE_FILE

    cache = _open_cache(build_dir)
    cleared: list[Path] = []
    if cache.path is not None and cache.path.exists():
        cache.clear()
        cleared.append(cache.path)
    scan_cache = build_dir / SCAN_CACHE_FILE
    if scan_cache.exists():
        scan_cache.unlink()
        cleared.append(scan_cache)

    if not cleared:
        print(f"No cache at {cache.path}")
        return 0
    for path in cleared:
        print(f"Cleared {path}")
    return 0


def _info(script: Path | None) -> int:
    """Show the build script's docstring, without running it."""
    resolved = _resolve_build_script(script)
    if script is not None and not script.exists():
        logger.error("Build script not found: %s", script)
        return 1
    if resolved is None:
        logger.error("No pcons-build.py found in current directory")
        return 1
    script = resolved

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


def _info_targets(
    build_dir: Path,
    script: Path,
    *,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    generator: list[str] | None = None,
    reconfigure: bool = False,
) -> int:
    """Run the build script and list every target it defines."""
    from pcons.core.node import AliasNode, FileNode

    build_dir.mkdir(parents=True, exist_ok=True)

    exit_code, projects = run_script(
        script,
        build_dir,
        variables=variables or {},
        variant=variant,
        generator=generator,
        reconfigure=reconfigure,
    )
    if exit_code != 0:
        return exit_code
    if not projects:
        logger.error("No Project created in build script")
        return 1

    top_levels = [p for p in projects if p.is_top_level]
    # With sibling projects, a bare name no longer says which build it is.
    qualify = len(top_levels) > 1

    alias_lines: list[str] = []
    for project in top_levels:
        for name, alias_nodes in project.tree_aliases.items():
            dep_names: list[str] = []
            for node in alias_nodes:
                if isinstance(node, FileNode):
                    dep_names.append(node.path.name)
                elif isinstance(node, AliasNode):
                    dep_names.append(node.alias_name)
                else:
                    dep_names.append(str(node))
            deps_str = ", ".join(dep_names) if dep_names else ""
            shown = f"{project.name}::{name}" if qualify else name
            alias_lines.append(f"  {shown:30s} -> {deps_str}")
    if alias_lines:
        print("Aliases:")
        for line in alias_lines:
            print(line)
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

    for project in top_levels:
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
            shown = target.qualified_name if qualify else target.name
            entry = (shown, outputs)
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


def _explain_targets(
    build_dir: Path,
    script: Path,
    *,
    target_names: list[str] | None = None,
    variables: dict[str, str] | None = None,
    variant: str | None = None,
    generator: list[str] | None = None,
    reconfigure: bool = False,
    fresh: bool = False,
    color: str = "auto",
    width: int | None = None,
) -> int:
    """Run the build script and show how each target's commands are built.

    Writes no build files and persists nothing: the script runs with its
    deferred generation dropped, the project is resolved in-process, and the
    report (see `pcons._cli_explain`) is printed: each target's concrete
    commands and attributed requirements, then every environment's flag
    provenance (``env.explain()``).
    """
    from pcons import _cli_explain

    build_dir.mkdir(parents=True, exist_ok=True)

    exit_code, projects = run_script(
        script,
        build_dir,
        variables=variables or {},
        variant=variant,
        generator=generator,
        reconfigure=reconfigure,
        fresh=fresh,
        persist=False,
        generate=False,
    )
    if exit_code != 0:
        return exit_code
    if not projects:
        logger.error("No Project created in build script")
        return 1

    top_levels = [p for p in projects if p.is_top_level]
    for project in top_levels:
        if not project._resolved:
            project.resolve()

    if target_names:
        per_project: dict[int, list] = {id(p): [] for p in top_levels}
        missing: list[str] = []
        for name in target_names:
            owners = []
            for p in top_levels:
                try:
                    target = p.get_target(name, raise_if_missing=False)
                except KeyError as e:  # duplicate name inside one project
                    logger.error("%s", e)
                    return 1
                if target is not None:
                    owners.append((p, target))
            if not owners:
                missing.append(name)
            elif len(owners) > 1:
                qualified = ", ".join(f"{p.name}::{name}" for p, _ in owners)
                logger.error(
                    "target '%s' exists in several projects; qualify it: %s",
                    name,
                    qualified,
                )
                return 1
            else:
                p, target = owners[0]
                per_project[id(p)].append(target)
        if missing:
            known = sorted({t.name for p in top_levels for t in p.targets})
            logger.error("No such target: %s", ", ".join(missing))
            logger.error("Targets: %s", ", ".join(known))
            return 1
        selections = [(p, per_project[id(p)]) for p in top_levels if per_project[id(p)]]
    else:
        selections = [(p, list(p.targets)) for p in top_levels]

    use_color = _cli_explain.resolve_color(color)
    for project, targets in selections:
        for line in _cli_explain.render_explanation(
            project,
            targets,
            explicit_targets=bool(target_names),
            color=use_color,
            width=_cli_explain.resolve_width(width),
            show_project=len(top_levels) > 1,
        ):
            click.echo(line, color=use_color)

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


def _init(build_dir: Path, *, force: bool, lang: str) -> int:
    """Initialize a new pcons project.

    Writes a pcons-build.py with a program target for any C/C++ sources
    found; in an empty directory, scaffolds a hello-world starter so the
    project builds and runs immediately.
    """
    import re

    root = Path.cwd()
    build_py = root / "pcons-build.py"

    if build_py.exists() and not force:
        logger.error("pcons-build.py already exists (use --force to overwrite)")
        return 1

    name = re.sub(r"[^A-Za-z0-9_-]+", "_", root.name).strip("_") or "myproject"

    # str: _find_c_sources compares it against single path components.
    sources = _find_c_sources(root, str(build_dir))
    scaffolded = None
    if not sources:
        scaffolded = Path("src") / ("main.cpp" if lang == "cpp" else "main.c")
        hello = _HELLO_CPP if lang == "cpp" else _HELLO_C
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

    build_template = f'''\
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
    exe = build_dir / (name + (".exe" if os.name == "nt" else ""))
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


def _load_user_modules(modules_path: str | None) -> None:
    """Load user add-on modules, from *modules_path* as well as the defaults."""
    from pcons import modules

    extra_paths: list[Path | str] | None = None
    if modules_path:
        # list() because load_modules declares list[Path | str], and list is
        # invariant, so the list[str] that split() returns is not one.
        extra_paths = list(modules_path.split(os.pathsep))

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
@pass_pcons_context
def cli(ctx: PconsContext, **declared_but_unused: object) -> None:
    # Before any command: a script that did pcons work and only then called the
    # CLI did so without a command line, so whatever it decided was decided on
    # the wrong values. Checked here rather than where the script is run, so a
    # command that skips generation refuses too.
    if _acted_before_handing_over():
        logger.error(_ACTED_BEFORE_HANDING_OVER)
        ctx.exit(1)

    # The group declares these so they can be spelled before a command name;
    # each command reads them off this context. The group itself uses none.
    #
    # A command name that resolved to nothing has already been routed to the
    # catch-all command, which is about to run. Only a command line naming no
    # command at all gets it invoked from here, and forward() hands it the
    # values parsed here rather than restating them.
    if ctx.invoked_subcommand is None and not ctx.routed_to_default:
        # forward() calls the callback, so the catch-all's own invoke does not
        # run and what it sets up has to be set up here.
        configure_logging(ctx)
        load_declared_modules(cli_default, ctx)
        ctx.exit(ctx.forward(cli_default))


@cli.command(
    "info",
    loads_modules=True,
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
@pass_pcons_context
def cli_info(
    ctx: PconsContext,
    build_dir: Path,
    variant: str | None,
    generator: tuple[str, ...],
    reconfigure: bool,
    build_script: str | None,
    targets: bool,
    extra: tuple[str, ...],
    **declared_but_unused: object,
) -> None:
    """Show build script info and available variables."""
    # --fresh comes with the options every generating command declares, and
    # listing targets runs the script without persisting anything.
    script = Path(build_script) if build_script else None

    if not targets:
        ctx.exit(_info(script))

    resolved = _resolve_build_script(script)
    if script is not None and not script.exists():
        logger.error("Build script not found: %s", script)
        ctx.exit(1)
    if resolved is None:
        logger.error("No pcons-build.py found in current directory")
        ctx.exit(1)

    variables, _ = parse_variables(list(extra))
    ctx.exit(
        _info_targets(
            build_dir,
            resolved,
            variables=variables,
            variant=variant,
            generator=list(generator) or None,
            reconfigure=reconfigure,
        )
    )


@cli.command(
    "explain",
    cls=TargetsCommand,
    loads_modules=True,
    short_help="Show each target's commands and where its flags came from",
    help=(
        "Show how each target's commands are constructed, then attribute "
        "every flag and define to the preset, variant or toolchain that "
        "set it.\n\n"
        "Runs the build script but writes no build files and persists "
        "nothing.\n\n"
        "EXTRA is targets to explain and/or build variables (KEY=value); "
        "with no targets, every target is explained."
    ),
)
@directory_option
@common_options
@generate_options
@click.option(
    "--color",
    type=click.Choice(["auto", "always", "never"]),
    default="auto",
    help="Colorize the report (auto: only on a terminal)",
)
@click.option(
    "--width",
    metavar="COLS",
    type=int,
    help=(
        "Truncate command lines to COLS columns; 0 for unlimited "
        "(default: terminal width, unlimited when piped)"
    ),
)
@targets_argument
@pass_pcons_context
def cli_explain(
    ctx: PconsContext,
    build_dir: Path,
    variant: str | None,
    generator: tuple[str, ...],
    reconfigure: bool,
    fresh: bool,
    build_script: str | None,
    color: str,
    width: int | None,
    extra: tuple[str, ...],
    **declared_but_unused: object,
) -> None:
    """Show how each target's commands are constructed."""
    script = Path(build_script) if build_script else None
    resolved = _resolve_build_script(script)
    if script is not None and not script.exists():
        logger.error("Build script not found: %s", script)
        ctx.exit(1)
    if resolved is None:
        logger.error("No pcons-build.py found in current directory")
        ctx.exit(1)

    variables, target_names = parse_variables(list(extra))
    ctx.exit(
        _explain_targets(
            build_dir,
            resolved,
            target_names=target_names or None,
            variables=variables,
            variant=variant,
            generator=_generators(generator),
            reconfigure=reconfigure,
            fresh=fresh,
            color=color,
            width=width,
        )
    )


@cli.command("init", short_help="Initialize a new pcons project")
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
@pass_pcons_context
def cli_init(
    ctx: PconsContext,
    build_dir: Path,
    force: bool,
    lang: str,
    **declared_but_unused: object,
) -> None:
    # No docstring, as in cli_clean.
    ctx.exit(_init(build_dir, force=force, lang=lang))


@cli.command(
    "generate",
    loads_modules=True,
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
    type=PconsPath(dir_okay=False),
    metavar="[FILE]",
    help="Output dependency graph in DOT format (default: stdout)",
)
@click.option(
    "--mermaid",
    is_flag=False,
    flag_value="-",
    type=PconsPath(dir_okay=False),
    metavar="[FILE]",
    help="Output dependency graph in Mermaid format (default: stdout)",
)
@jobs_option
@click.argument("extra", nargs=-1)
@pass_pcons_context
def cli_generate(
    ctx: PconsContext,
    build_dir: Path,
    variant: str | None,
    generator: tuple[str, ...],
    reconfigure: bool,
    fresh: bool,
    build_script: str | None,
    no_cache: bool,
    graph: str | None,
    mermaid: str | None,
    jobs: int | None,
    extra: tuple[str, ...],
    **declared_but_unused: object,
) -> None:
    """Generate build files from pcons-build.py."""
    variables, _ = parse_variables(list(extra))
    code, projects = _generate(
        build_dir,
        script=Path(build_script) if build_script else None,
        variables=variables,
        variant=variant,
        generator=_generators(generator),
        reconfigure=reconfigure,
        fresh=fresh,
        no_cache=no_cache,
        graph=graph,
        mermaid=mermaid,
        jobs=jobs,
    )
    if code == 0 and not projects:
        ctx.exit(_no_build_described())
    ctx.exit(code)


@cli.command(
    "build",
    cls=TargetsCommand,
    loads_modules=True,
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
@targets_argument
@pass_pcons_context
def cli_build(
    ctx: PconsContext,
    build_dir: Path,
    verbose: bool,
    variant: str | None,
    generator: tuple[str, ...],
    reconfigure: bool,
    fresh: bool,
    build_script: str | None,
    ninja: str | None,
    watch: bool,
    jobs: int | None,
    extra: tuple[str, ...],
    **declared_but_unused: object,
) -> None:
    """Build targets, generating first if the build files are stale."""
    script = Path(build_script) if build_script else None
    variables, targets = parse_variables(list(extra))

    # The projects from the last regeneration. A watch iteration whose build
    # files are fresh skips regeneration, and without these it would fall
    # back to the -B directory and quietly stop building the other siblings.
    known_projects: list[Project] = []

    def regenerate() -> tuple[int, list[Project]]:
        code, projects = _generate(
            build_dir,
            script=script,
            variables=variables,
            variant=variant,
            generator=_generators(generator),
            reconfigure=reconfigure,
            fresh=fresh,
            jobs=jobs,
        )
        if code == 0:
            known_projects[:] = projects
        return code, projects

    def build_once() -> tuple[int, list[Path]]:
        return _build(
            build_dir,
            regenerate=regenerate,
            projects=list(known_projects) or None,
            script=script,
            targets=targets or None,
            jobs=jobs,
            verbose=verbose,
            ninja=ninja,
            variant=variant,
        )

    if watch:
        ctx.exit(
            _watch(
                build=build_once,
                script=_resolve_build_script(script),
                targets=targets,
                ninja=ninja,
            )
        )
    ctx.exit(build_once()[0])


@cli.command("clean", short_help="Clean build artifacts")
@directory_option
@common_options
@build_options
@click.option(
    "-a",
    "--all",
    "everything",
    is_flag=True,
    default=False,
    help="Remove entire build directory",
)
@pass_pcons_context
def cli_clean(
    ctx: PconsContext,
    build_dir: Path,
    ninja: str | None,
    everything: bool,
    **declared_but_unused: object,
) -> None:
    # No docstring: click would print it as this command's description, which
    # `pcons clean --help` has never had. See the known issue.
    ctx.exit(_clean(build_dir, everything=everything, ninja=ninja))


@cli.group(
    "cache",
    invoke_without_command=True,
    short_help="Inspect or clear the per-build-dir cache",
    help=(
        "Inspect or clear the per-build-dir cache (pcons_cache.json).\n\n"
        "Reads the cache file directly; never runs the build script. The build "
        "directory comes from -B / $PCONS_BUILD_DIR (default 'build'). "
        "Without a subcommand, lists what is persisted."
    ),
)
@directory_option
@common_options
@pass_pcons_context
def cli_cache(ctx: PconsContext, build_dir: Path, **kw: object) -> None:
    if ctx.invoked_subcommand is None:
        ctx.exit(_cache_list(build_dir))


@cli_cache.command("list", short_help="What is persisted")
@directory_option
@common_options
@pass_pcons_context
def cli_cache_list(ctx: PconsContext, build_dir: Path, **kw: object) -> None:
    """List the settings this build directory has persisted."""
    ctx.exit(_cache_list(build_dir))


@cli_cache.command("show", short_help="The whole cache")
@directory_option
@common_options
@pass_pcons_context
def cli_cache_show(ctx: PconsContext, build_dir: Path, **kw: object) -> None:
    """List the persisted settings, then where they came from and live."""
    ctx.exit(_cache_show(build_dir))


@cli_cache.command("clear", short_help="Discard it")
@directory_option
@common_options
@pass_pcons_context
def cli_cache_clear(ctx: PconsContext, build_dir: Path, **kw: object) -> None:
    """Discard the persisted settings."""
    ctx.exit(_cache_clear(build_dir))


@cli_cache.command("path", short_help="Where it lives")
@directory_option
@common_options
@pass_pcons_context
def cli_cache_path(ctx: PconsContext, build_dir: Path, **kw: object) -> None:
    """Print the cache file's path, whether or not it exists yet."""
    ctx.exit(_cache_path(build_dir))


def _protected_args(ctx: click.Context) -> list[str]:
    """The command name click has parsed but not yet descended into.

    click's own private attribute, read because a group whose commands only
    exist once the build script has run has no public way to see the name
    before dispatch. Read loudly: behind a `getattr` default, click renaming it
    would turn `pcons run <cmd>` into a silent listing instead of a run.
    `pyproject.toml` bounds click below 9 for the same reason.
    """
    return list(ctx._protected_args)


class RunGroup(MergingGroup):
    """The commands a build script or an add-on module declared.

    Names come from the build directory's cache, so listing and help never run
    the build script. Dispatch does run it, and hands the command its live
    environment with the project resolved and no build files written.

    It lives here rather than in `_cli_click` because it has to run the build
    script, and `cli` imports `_cli_click`; the generic classes there stay
    generic.
    """

    @staticmethod
    def _on_the_command_line(ctx: click.Context, name: str) -> object | None:
        """A value this context parsed from the command line, and only that.

        Not the default, and **not the environment**: `common_options` gives
        `-B` an envvar, and `_adopt_options_spelled_earlier`
        (`pcons/_cli_click.py`) is explicit that a value click took from the
        environment must not beat a `-B` spelled before the command. Accepting
        it here would make the listing and the dispatch read different build
        directories under `PCONS_BUILD_DIR=x pcons -B out run`.
        """
        if name in ctx.params and (
            ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE
        ):
            return ctx.params[name]
        return None

    @classmethod
    def _spelled(cls, ctx: click.Context, name: str) -> object | None:
        """A value the user spelled, on either side of the command name.

        After the name wins, which is what `_adopt_options_spelled_earlier`
        promises: "spelled on both sides, the later one wins". Reading the
        parent first would let `pcons -B other run -B out --help` list `other`
        while dispatching out of `out`. Neither side counts the environment;
        the caller falls back to it once nothing was spelled at all.

        The parent is still needed for the help path: `_adopt_options_spelled_earlier`
        runs after `parse_args`, and `--help` exits from inside it, so a value
        spelled before this group's name never reaches `ctx.params` here.
        """
        here = cls._on_the_command_line(ctx, name)
        if here is not None:
            return here
        parent = ctx.parent
        return None if parent is None else cls._on_the_command_line(parent, name)

    def _build_dir_option(self) -> click.Parameter | None:
        return next((p for p in self.params if p.name == "build_dir"), None)

    #: What the listing needs before `--help` can print it.
    _LISTING_OPTIONS = ("build_dir", "modules_path")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Make the options the listing reads eager, so help sees their values.

        `pcons run -B out --help` must list `out`'s commands, and
        `pcons run --modules-path mods --help` the modules'. click processes
        eager parameters first, so with an eager help option the callback prints
        the listing before either value has reached `ctx.params`. Promoting
        these two and demoting help (see `get_help_option`) fixes the order
        whichever way round the user spelled them.

        Only this group's own copies are touched: `common_options` builds a
        fresh `Option` per command.
        """
        super().__init__(*args, **kwargs)
        for param in self.params:
            if param.name in self._LISTING_OPTIONS:
                param.is_eager = True

    def get_help_option(self, ctx: click.Context) -> click.Option | None:
        """click's own help option, demoted so the listing's options beat it.

        Overriding rather than declaring one keeps `help_option_names` from the
        context settings, so `run` answers to `-h` and `--help` like every other
        command. click caches the object, so this runs once.
        """
        option = super().get_help_option(ctx)
        if option is not None:
            option.is_eager = False
        return option

    def _build_dir(self, ctx: click.Context) -> Path:
        """Which build directory to read the listing from.

        The fallback is the parameter's own default rather than a literal
        "build": `common_options` declares it with an envvar, and a literal here
        would silently ignore PCONS_BUILD_DIR.
        """
        spelled = self._spelled(ctx, "build_dir")
        if spelled is not None:
            return Path(str(spelled))
        option = self._build_dir_option()
        if option is not None:
            from_env = option.resolve_envvar_value(ctx)
            if from_env:
                return Path(str(from_env))
            default = option.get_default(ctx)
            if default is not None:
                return Path(str(default))
        return Path("build")

    def _load_modules(self, ctx: click.Context) -> None:
        """Add-on commands are invisible until their modules are imported.

        Listing and dispatch both need this, and both can be reached without a
        callback having run first. `load_modules` skips what it already has.
        """
        modules_path = self._spelled(ctx, "modules_path")
        _load_user_modules(modules_path if isinstance(modules_path, str) else None)

    def _cached_rows(self, ctx: click.Context) -> list[tuple[str, str]]:
        """(name, short help) for the script's commands, as generate left them.

        The cache has no schema version and a build directory outlives a pcons
        upgrade, so an entry that is not shaped as expected is skipped rather
        than raised over.
        """
        raw = _open_cache(self._build_dir(ctx)).get("commands")
        if not isinstance(raw, list):
            return []
        rows: list[tuple[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            help_text = entry.get("help")
            rows.append((name, help_text if isinstance(help_text, str) else ""))
        return rows

    def rows(self, ctx: click.Context) -> list[tuple[str, str]]:
        """Everything on offer: the script's cached names, then the modules'."""
        self._load_modules(ctx)
        rows = self._cached_rows(ctx)
        listed = {name for name, _ in rows}
        for name, entries in user_commands.declared().items():
            if name in listed:
                continue
            for entry in entries:
                if entry.origin != user_commands.SCRIPT_ORIGIN:
                    rows.append((name, entry.command.get_short_help_str()))
                    break
        return rows

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        """Format the listing from the cache, not from resolved commands.

        click's own version asks `get_command` for each name and drops whatever
        answers None, which every script-declared command does until the script
        has run. Formatting must not run it, so the help text comes from the
        cache with the name.
        """
        rows = self.rows(ctx)
        if not rows:
            return
        with formatter.section("Commands"):
            formatter.write_dl(rows)

    def shell_complete(
        self, ctx: click.Context, incomplete: str
    ) -> list[CompletionItem]:
        """Complete from the cached listing, not from resolved commands.

        click's own version drops every name whose `get_command` answers None,
        exactly as `format_commands` did, so a script-declared command would
        complete to nothing until the script had run -- and completion must not
        run it. The names and their help come from the cache instead.

        The tail is `Command.shell_complete`, this group's own options.
        `click.Group.shell_complete` is the method being replaced: calling it
        would put the dropped names back through `get_command`, which also
        raises on a name two origins declare, into a stream that carries
        nothing but completion candidates.

        Add-on modules are deliberately *not* loaded here. Loading execs every
        module on the path and runs its `register()`, so anything one prints
        lands ahead of click's completion protocol and is parsed as candidates,
        and a slow or exiting `register()` breaks every TAB. That is what the
        `loads_modules` gate exists to prevent, and the cache already holds the
        names, so completion reads them from there.
        """
        if _protected_args(ctx):
            # A command name has already been typed. click would normally have
            # descended into it by now; it could not, because `get_command`
            # answers None without the script. Offering the *sibling* names here
            # would be worse than offering nothing.
            return []
        items = [
            CompletionItem(name, help=help_text)
            for name, help_text in self._cached_rows(ctx)
            if name.startswith(incomplete)
        ]
        items.extend(super(click.Group, self).shell_complete(ctx, incomplete))
        return items

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """The declared command, or None so click reports "No such command".

        Only meaningful once the script has run, which `invoke` guarantees for
        the dispatch path. Help and completion go through `rows` instead.
        """
        self._load_modules(ctx)
        try:
            return user_commands.lookup(cmd_name)
        except PconsError as e:
            # A name two origins both declare. Neither runs, and the message
            # names both so the user can tell which to rename.
            raise click.ClickException(str(e)) from e

    def _dispatch_only(self, ctx: click.Context) -> Any:
        """click's own dispatch, without `MergingGroup`'s prologue.

        `invoke` below runs that prologue itself, before the build script is
        read. Reaching `MergingGroup.invoke` here would run it a second time,
        and on the dispatch path that second time lands *inside* the script's
        window, where `configure_logging`'s ``basicConfig(force=True)`` would
        tear down whatever logging the build script had just set up.
        """
        return click.Group.invoke(self, ctx)

    def invoke(self, ctx: click.Context) -> Any:
        """Dispatch inside the build script's environment.

        `click.Group.invoke` resolves the name *and* invokes the command, both
        inside itself, so wrapping it once puts the lookup and the user's
        callback inside the live environment.
        """
        # `MergingGroup.invoke` would do the first two, but only the bare path
        # reaches it: dispatch goes through the script's window instead. Both
        # have to happen before the script is read, or -v and --debug say
        # nothing about the one thing this command does.
        _adopt_options_spelled_earlier(self, ctx)
        configure_logging(ctx)
        # Not `load_declared_modules`: this group needs its modules on the help
        # and completion paths too, where no command is ever invoked.
        self._load_modules(ctx)
        args = [*_protected_args(ctx), *ctx.args]
        if not args:
            # Bare `pcons run` lists, and the listing comes from the cache, so
            # do not pay for a script run to print it.
            return self._dispatch_only(ctx)

        spelled_script = self._spelled(ctx, "build_script")
        script = _resolve_build_script(
            Path(str(spelled_script)) if spelled_script is not None else None
        )
        if script is None:
            # No script, so no window and no project: only a module's commands
            # can resolve, and a script's name is simply unknown.
            return self._dispatch_only(ctx)

        parent_invoke = self._dispatch_only  # bound now: a bare super() in the
        dispatched: list[Any] = []  # lambda would look for it in the closure

        def dispatch() -> None:
            dispatched.append(parent_invoke(ctx))

        exit_code, _projects = run_script(
            script,
            self._build_dir(ctx),
            generate=False,
            persist=False,
            inside=dispatch,
        )
        if exit_code != 0:
            # The script itself failed; it has already said why.
            ctx.exit(exit_code)
        if not dispatched:
            # The script ended itself, cleanly enough that run_script reported
            # success -- `sys.exit(0)` reaches here. Saying nothing would exit 0
            # having run no command at all.
            raise click.ClickException(
                f"{script} exited before the command could run, "
                "so nothing was dispatched."
            )
        return dispatched[0]


@cli.group(
    "run",
    cls=RunGroup,
    invoke_without_command=True,
    short_help="Run a command declared by the build script",
    help=(
        "Run a command declared by the build script or an add-on module.\n\n"
        "A command runs with the build script's environment live and its "
        "project resolved, and writes no build files. Without a command name, "
        "lists what is available; that listing comes from the build directory, "
        "so a newly declared command appears after the next generate."
    ),
)
@directory_option
@common_options
@pass_pcons_context
def cli_run(ctx: PconsContext, **kw: object) -> None:
    # A subcommand has already been resolved by RunGroup.invoke by the time this
    # runs for it; only a bare `pcons run` gets the listing. Logging is set up
    # there rather than here, since here is already inside the script's window.
    if ctx.invoked_subcommand is not None:
        return
    group = ctx.command
    assert isinstance(group, RunGroup)
    rows = group.rows(ctx)
    if not rows:
        print(
            "No commands declared. A build script declares one with "
            "@project.cli_command(); the listing comes from the build "
            "directory, so run `pcons generate` first."
        )
        ctx.exit(0)
    width = max(len(name) for name, _ in rows)
    for name, help_text in rows:
        print(f"  {name.ljust(width)}  {help_text}".rstrip())
    ctx.exit(0)


@cli.command(
    "test",
    short_help="Run tests declared by project.Test() in pcons-build.py",
    # The runner owns its own flags, so everything after `test` is handed over
    # untouched, including --help.
    context_settings={"ignore_unknown_options": True, "help_option_names": []},
)
@directory_option
@click.argument("argv", nargs=-1, type=click.UNPROCESSED)
@pass_pcons_context
def cli_test(ctx: PconsContext, argv: tuple[str, ...]) -> None:
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


@cli.group(
    "completion",
    short_help="Set up tab completion for your shell",
    help=(
        "Set up tab completion for your shell.\n\n"
        "The script is generated from the command tree, so it completes command "
        "names, option names and the values of options that have a fixed set. "
        "SHELL is bash, zsh or fish, and defaults to what $SHELL names."
    ),
)
@directory_option
def cli_completion() -> None:
    pass


@cli_completion.command("show", short_help="Print the completion script")
@directory_option
@click.argument("shell", type=click.Choice(_cli_completion.SHELLS), required=False)
@pass_pcons_context
def cli_completion_show(ctx: PconsContext, shell: str | None) -> None:
    """Print the completion script on stdout, for the shell to evaluate.

    Nothing outside the project is written, so this is the form to put in a
    startup file yourself: eval "$(pcons completion show)".
    """
    ctx.exit(_cli_completion.emit(shell))


@cli_completion.command("install", short_help="Write the script and wire it up")
@directory_option
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True, default=False, help="Do not ask first"
)
@click.argument("shell", type=click.Choice(_cli_completion.SHELLS), required=False)
@pass_pcons_context
def cli_completion_install(
    ctx: PconsContext, shell: str | None, assume_yes: bool
) -> None:
    """Write the completion script where the shell reads it.

    For bash and zsh a startup file gains a few lines, which are shown for
    confirmation first. Both edits are undone by 'pcons completion uninstall'.
    """
    ctx.exit(_cli_completion.install(shell, assume_yes=assume_yes))


@cli_completion.command("uninstall", short_help="Undo what install wrote")
@directory_option
@click.argument("shell", type=click.Choice(_cli_completion.SHELLS), required=False)
@pass_pcons_context
def cli_completion_uninstall(ctx: PconsContext, shell: str | None) -> None:
    """Remove the completion script and the startup lines that read it."""
    ctx.exit(_cli_completion.uninstall(shell))


@cli.command("_default", cls=DefaultCommand, hidden=True, loads_modules=True)
@directory_option
@common_options
@generate_options
@build_options
@watch_option
@jobs_option
@targets_argument
@pass_pcons_context
def cli_default(
    ctx: PconsContext,
    build_dir: Path,
    verbose: bool,
    variant: str | None,
    generator: tuple[str, ...],
    reconfigure: bool,
    fresh: bool,
    build_script: str | None,
    ninja: str | None,
    watch: bool,
    jobs: int | None,
    extra: tuple[str, ...],
    **declared_but_unused: object,
) -> None:
    """The no-subcommand path: generate, then build."""
    script = Path(build_script) if build_script else None

    # Don't try to generate without a pcons-build.py.
    # (`pcons build` still drives build files without a script; weird corner case but OK)
    if _resolve_build_script(script) is None:
        logger.error("No pcons-build.py found in current directory")
        ctx.exit(1)

    variables, targets = parse_variables(list(extra))

    # The projects from the last regeneration; see cli_build for why a
    # watch needs them remembered across iterations.
    known_projects: list[Project] = []

    def regenerate() -> tuple[int, list[Project]]:
        code, regenerated = _generate(
            build_dir,
            script=script,
            variables=variables,
            variant=variant,
            generator=_generators(generator),
            reconfigure=reconfigure,
            fresh=fresh,
            jobs=jobs,
        )
        if code == 0:
            known_projects[:] = regenerated
        return code, regenerated

    # _build generates on its own when the build files are stale, which is the
    # right entry point for a watch: it regenerates only when needed.
    if watch:
        ctx.exit(
            _watch(
                build=lambda: _build(
                    build_dir,
                    regenerate=regenerate,
                    projects=list(known_projects) or None,
                    script=script,
                    targets=targets or None,
                    jobs=jobs,
                    verbose=verbose,
                    ninja=ninja,
                    variant=variant,
                ),
                script=_resolve_build_script(script),
                targets=targets,
                ninja=ninja,
            )
        )

    code, projects = regenerate()
    if code != 0:
        ctx.exit(code)
    if not projects:
        ctx.exit(_no_build_described())
    # Build in the projects' own directories, which the script chooses;
    # they need not match the -B request.
    ctx.exit(
        _build(
            build_dir,
            regenerate=regenerate,
            projects=projects,
            script=script,
            targets=targets or None,
            jobs=jobs,
            verbose=verbose,
            ninja=ninja,
            variant=variant,
        )[0]
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the pcons CLI."""
    return run_cli(cli, prog_name="pcons", argv=argv)


if __name__ == "__main__":
    sys.exit(main())

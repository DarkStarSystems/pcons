import runpy
import sys
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import overload

from pcons.core.environment import Environment as Env
from pcons.core.invocation import RUN_NAME
from pcons.core.project import Project, _in_virtualenv
from pcons.core.vars import VarValue, scoped_vars


def _module_origins(module: object) -> list[Path]:
    """Where a module came from, as paths.

    Its own file for anything with one, a module or a regular package alike. A
    namespace package has no file, so its search locations are what says where
    it came from, and it may legitimately have several.
    """
    filename = getattr(module, "__file__", None)
    if filename:
        return [Path(filename)]
    return [Path(entry) for entry in getattr(module, "__path__", None) or ()]


def _release_local_modules(
    project: Project, subdir_path: Path, before: frozenset[str]
) -> None:
    """Uncache the modules an inclusion imported from its own directory.

    An inclusion owns the modules beside its script, so two subdirectories
    carrying the same module name get one each, and a directory included twice
    re-imports rather than reusing what the first pass computed. Modules from
    anywhere else stay cached: re-importing a package would re-run whatever it
    registered on import, and shared build-description modules are shared on
    purpose.

    Whether to uncache and whether to record are separate questions. Anything
    originating under the directory is uncached, packages and compiled modules
    included, or a stale entry would outlive the parent it belongs to. Only
    Python sources become configure dependencies, and only outside a virtualenv,
    matching what the scan over ``sys.modules`` records; that scan runs long
    after every inclusion has returned, which is why these are recorded here.
    """
    root = project.top.root_dir.resolve()
    for name, module in list(sys.modules.items()):
        if name in before:
            continue
        try:
            origins = [path.resolve() for path in _module_origins(module)]
        except OSError:
            continue
        if not origins or not all(p.is_relative_to(subdir_path) for p in origins):
            continue
        for path in origins:
            if path.suffix == ".py" and not _in_virtualenv(path, root):
                project.add_configure_dependency(path)
        del sys.modules[name]


@overload
def add_subdirectory(
    subdir: str | Path,
    pick: list[str],
    *,
    project: Project | None = None,
    env: Env | None = None,
    vars: Mapping[str, VarValue] | None = None,
) -> tuple: ...


@overload
def add_subdirectory(
    subdir: str | Path,
    pick: None = None,
    *,
    project: Project | None = None,
    env: Env | None = None,
    vars: Mapping[str, VarValue] | None = None,
) -> SimpleNamespace: ...


def add_subdirectory(
    subdir: str | Path,
    pick: list[str] | None = None,
    *,
    project: Project | None = None,
    env: Env | None = None,
    vars: Mapping[str, VarValue] | None = None,
) -> tuple | SimpleNamespace:
    """Adds a subdirectory to the project.

    Looks for a ``pcons-build.py`` file in the specified subdirectory and
    executes it in the context of the current project.
    Any name assigned at module scope in that script is *exported*: it becomes
    an attribute of the returned ``SimpleNamespace``, so callers can write ``ns.my_lib``
    instead of looking up targets by string.

    Example:

        # subdir/pcons-build.py
        my_lib = project.StaticLibrary("my_lib", env, sources=["lib.c"])

        # parent pcons-build.py
        sub = add_subdirectory("subdir")
        app.link(sub.my_lib)

        # Or, with pick:
        my_lib, = add_subdirectory("subdir", pick=["my_lib"])
        app.link(my_lib)

    Args:
        subdir: The subdirectory, relative to the anchoring project's
            current directory.
        pick: Names to return from the subdirectory script, instead of
            everything.
        project: The project to add the subdirectory to. Defaults to the
            current project; in a script with several top-level projects,
            name the one you mean (or call ``project.add_subdirectory()``).
        env: The environment the included tree builds in. It becomes the
            default environment for the duration of the call, so a script
            reading ``project.parent.default_environment`` gets it. Include
            the same directory once per environment to build it twice::

                add_subdirectory("sub", env=host)
                add_subdirectory("sub", env=mcu)

            The two environments need different ``build_prefix`` values,
            and both must be named, or their targets collide.
        vars: Build variables the included tree reads with ``get_var``, set for
            the duration of the call. They shadow the command line, since the
            caller is configuring what it includes rather than offering a
            default; pass ``get_var(name, default)`` as a value to let the
            command line back in::

                add_subdirectory("libfoo", vars={"LIBFOO_PYTHON": False})

            Names not mentioned keep whatever the command line gave them.

    Returns:
        - If ``pick`` is not specified, a ``SimpleNamespace`` whose attributes
          are all module-level names defined in the subdirectory script.
        - If ``pick`` is specified, a tuple containing only the listed names
          (in order), e.g. ``lib, hdr = add_subdirectory("sub", pick=["lib", "hdr"])``.
    """
    if project is None:
        project = Project.current()
    subdir_path = project.current_dir / subdir
    script = subdir_path / "pcons-build.py"
    if not script.exists():
        raise FileNotFoundError(f"No pcons-build.py found in {subdir_path}")

    # runpy doesn't touch sys.modules, so the automatic scan can't see this
    # script; register it directly or editing it wouldn't re-run pcons.
    project.add_configure_dependency(script)

    with project._enter_subdir(subdir, env=env):
        # The script reaches its own neighbours the way a root build script
        # does, which the CLI arranges for that one.
        old_path = sys.path.copy()
        before = frozenset(sys.modules)
        sys.path.insert(0, str(subdir_path))
        try:
            with scoped_vars(vars) if vars else nullcontext():
                module = runpy.run_path(str(script), run_name=RUN_NAME)
        finally:
            # Released first: a namespace package's search locations are
            # recomputed from sys.path, so they still name this directory.
            _release_local_modules(project, subdir_path.resolve(), before)
            sys.path[:] = old_path
        if pick is not None:
            return tuple(module[name] for name in pick)
        else:
            return SimpleNamespace(**module)

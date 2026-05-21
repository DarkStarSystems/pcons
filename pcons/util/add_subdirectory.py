import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import overload

from pcons.core.project import Project


@overload
def add_subdirectory(subdir: str | Path, pick: list[str]) -> tuple: ...


@overload
def add_subdirectory(subdir: str | Path, pick: None = None) -> SimpleNamespace: ...


def add_subdirectory(
    subdir: str | Path, pick: list[str] | None = None
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

    Returns:
        - If ``pick`` is not specified, a ``SimpleNamespace`` whose attributes
          are all module-level names defined in the subdirectory script.
        - If ``pick`` is specified, a tuple containing only the listed names
          (in order), e.g. ``lib, hdr = add_subdirectory("sub", pick=["lib", "hdr"])``.
    """
    project = Project.current()
    subdir_path = project.current_dir / subdir
    if not (subdir_path / "pcons-build.py").exists():
        raise FileNotFoundError(f"No pcons-build.py found in {subdir_path}")

    with project._enter_subdir(subdir):
        module = runpy.run_path(str(subdir_path / "pcons-build.py"))
        if pick is not None:
            return tuple(module[name] for name in pick)
        else:
            return SimpleNamespace(**module)

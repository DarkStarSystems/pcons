import runpy
from pathlib import Path

from pcons.core.project import Project


def add_subdirectory(subdir: str | Path):
    """Adds a subdirectory to the project.

    This function looks for a pcons-build.py file in the specified subdirectory and executes it in the context of the current project.
    This allows you to organize your project into multiple subdirectories,

    Returns:
        The loaded module from the subdirectory's pcons-build.py,
        which can be used to access any variables or functions defined there.
    """
    project = Project.current()
    if project is None:
        raise RuntimeError("add_subdirectory() must be called within a Project context")

    subdir_path = project.current_dir / subdir
    if not (subdir_path / "pcons-build.py").exists():
        raise FileNotFoundError(f"No pcons-build.py found in {subdir_path}")

    with project._enter_subdir(subdir):
        return runpy.run_path(str(subdir_path / "pcons-build.py"))

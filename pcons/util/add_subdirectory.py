import runpy

from pcons.core.project import Project


def add_subdirectory(subdir):
    """Adds a subdirectory to the project by running its pcons-build.py.

    The subdir must contain a pcons-build.py that defines a function
    `build_subdir(build_dir)` which adds targets to the given project.

    Args:
        project: The parent Project instance to which the subdir will add targets.
        subdir: Path to the subdirectory containing pcons-build.py.

    Returns:
        The result of the subdir's build function, if any.
    """
    project = Project.current()
    if project is None:
        raise RuntimeError("add_subdirectory() must be called within a Project context")

    subdir_path = project.root_dir / subdir
    if not (subdir_path / "pcons-build.py").exists():
        raise FileNotFoundError(f"No pcons-build.py found in {subdir_path}")

    with project._enter_subdir(subdir):
        return runpy.run_path(str(subdir_path / "pcons-build.py"))

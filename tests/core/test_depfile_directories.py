# SPDX-License-Identifier: MIT
"""A directory named in a depfile is what makes a new file get noticed.

A build-time reader of a directory reports what it read in a depfile. Files
alone are not enough: a file that did not exist yet cannot be in the list, so
the reader must also name every directory it listed. The build tool then stats
those directories and re-runs the edge when one gains an entry.

Both halves are checked here, because only the pair is evidence: the
files-only case must fail to notice, or the directory case proves nothing.
"""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator

LISTER = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import os
    import sys
    from pathlib import Path

    root, out, dep, target, mode = sys.argv[1:6]
    files = []
    dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        dirs.append(dirpath)
        files.extend(os.path.join(dirpath, n) for n in sorted(filenames))
    Path(out).write_text("".join(sorted(Path(f).name + "\\n" for f in files)))
    listed = files + (dirs if mode == "dirs" else [])
    deps = " ".join(p.replace("\\\\", "/") for p in listed)
    Path(dep).write_text(target + ": " + deps + "\\n")
    """
)


def write(path: Path, text: str) -> Path:
    """Write *text* to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def reader_project(root: Path, mode: str) -> Project:
    """A project whose one edge lists ``tree/`` and writes a *mode* depfile."""
    write(root / "tree" / "a.txt", "a\n")
    write(root / "tree" / "sub" / "s.txt", "s\n")
    write(root / "lister.py", LISTER)

    project = Project("depdirs", root_dir=root, build_dir=root / "build")
    env = project.Environment(name="host")
    listing = env.Command(
        target="listing.txt",
        source="lister.py",
        command=[
            sys.executable,
            "$SOURCE",
            (root / "tree").as_posix(),
            "$TARGET",
            "$TARGET.d",
            "$TARGET",
            mode,
        ],
        depfile=".d",
    )
    project.Default(listing)
    project.resolve()
    return project


def listed_names(root: Path) -> set[str]:
    """The file names the reader edge last wrote, read back from its output."""
    return set((root / "build" / "listing.txt").read_text().split())


needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)
needs_make = pytest.mark.skipif(
    shutil.which("make") is None, reason="make not installed"
)


def _generate(root: Path, generator: BaseGenerator, project: Project) -> Path:
    """Write the build files for *project*; return the build directory."""
    generator.generate(project)
    BaseGenerator._generate_pending(project)
    return root / "build"


def run_ninja(build_dir: Path) -> str:
    """Build with no configure in between: the freshness question."""
    result = subprocess.run(
        ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def run_make(build_dir: Path) -> str:
    result = subprocess.run(
        ["make", "-C", str(build_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


@needs_ninja
class TestNinjaDepfileDirectories:
    """ninja stats every path a depfile names, directories included."""

    def test_a_file_added_deep_re_runs_the_edge(self, tmp_path):
        project = reader_project(tmp_path, "dirs")
        build_dir = _generate(tmp_path, NinjaGenerator(), project)
        run_ninja(build_dir)

        write(tmp_path / "tree" / "sub" / "new.txt", "new\n")
        output = run_ninja(build_dir)

        assert "no work to do" not in output
        assert "new.txt" in listed_names(tmp_path)

    def test_a_file_added_at_the_root_re_runs_the_edge(self, tmp_path):
        project = reader_project(tmp_path, "dirs")
        build_dir = _generate(tmp_path, NinjaGenerator(), project)
        run_ninja(build_dir)

        write(tmp_path / "tree" / "top.txt", "top\n")
        run_ninja(build_dir)

        assert "top.txt" in listed_names(tmp_path)

    def test_an_unchanged_tree_does_no_work(self, tmp_path):
        project = reader_project(tmp_path, "dirs")
        build_dir = _generate(tmp_path, NinjaGenerator(), project)
        run_ninja(build_dir)

        assert "no work to do" in run_ninja(build_dir)

    def test_a_files_only_depfile_misses_the_new_file(self, tmp_path):
        """The control: without the directories the check above proves nothing."""
        project = reader_project(tmp_path, "files")
        build_dir = _generate(tmp_path, NinjaGenerator(), project)
        run_ninja(build_dir)

        write(tmp_path / "tree" / "sub" / "new.txt", "new\n")
        output = run_ninja(build_dir)

        assert "no work to do" in output
        assert "new.txt" not in listed_names(tmp_path)


@needs_make
class TestMakefileDepfileDirectories:
    """The same fact for the Makefile generator, which -includes the depfiles."""

    def test_a_file_added_deep_re_runs_the_edge(self, tmp_path):
        project = reader_project(tmp_path, "dirs")
        build_dir = _generate(tmp_path, MakefileGenerator(), project)
        run_make(build_dir)

        write(tmp_path / "tree" / "sub" / "new.txt", "new\n")
        run_make(build_dir)

        assert "new.txt" in listed_names(tmp_path)

    def test_a_files_only_depfile_misses_the_new_file(self, tmp_path):
        project = reader_project(tmp_path, "files")
        build_dir = _generate(tmp_path, MakefileGenerator(), project)
        run_make(build_dir)

        write(tmp_path / "tree" / "sub" / "new.txt", "new\n")
        run_make(build_dir)

        assert "new.txt" not in listed_names(tmp_path)

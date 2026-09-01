# SPDX-License-Identifier: MIT
"""Tests for Project.OverlayDir(): N source trees merged into one directory."""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcons.core.errors import BuilderError
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def write(path: Path, text: str) -> Path:
    """Write *text* to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_trees(root: Path) -> tuple[Path, Path]:
    """Two trees sharing a relative path, a directory, and holding a deep one."""
    shared = root / "shared"
    app = root / "app"
    write(shared / "Manifest.xml", "shared manifest\n")
    write(shared / "shared_only.txt", "shared only\n")
    write(shared / "res" / "xml" / "a.txt", "shared xml\n")
    write(shared / "src" / "com" / "example" / "Thing.java", "class Thing {}\n")
    write(app / "Manifest.xml", "app manifest\n")
    write(app / "res" / "drawable" / "b.txt", "app drawable\n")
    return shared, app


def overlay_project(root: Path, sources: list[Path]) -> tuple[Project, Target]:
    """A resolved project whose single target overlays *sources* into "stage"."""
    project = Project("test", root_dir=root, build_dir=root / "build")
    env = project.Environment(name="host")
    stage = project.OverlayDir(env, "stage", sources=sources)
    project.resolve()
    return project, stage


def build(project: Project, root: Path) -> Path:
    """Generate build.ninja and run ninja; return the build directory."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    build_dir = root / "build"
    result = subprocess.run(
        ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return build_dir


needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)


BUILD_SCRIPT = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    from pcons import Project

    project = Project("freshness")
    env = project.Environment(name="host")
    stage = project.OverlayDir(env, "stage", sources=["shared", "app"])
    project.Default(stage)
    """
)


def freshness_project(root: Path) -> Path:
    """Two trees plus a build script, laid out for a real `pcons` run."""
    make_trees(root)
    write(root / "pcons-build.py", BUILD_SCRIPT)
    return root / "build" / "stage"


def run_pcons(root: Path) -> str:
    """Configure and build from scratch, as a user typing `pcons` would."""
    result = subprocess.run(
        [sys.executable, "-m", "pcons"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def run_ninja(root: Path) -> str:
    """Build again with no configure in between: the freshness question."""
    result = subprocess.run(
        ["ninja"],
        cwd=root / "build",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


class TestOverlayGraph:
    """What the target looks like before anything is built."""

    def test_creates_an_interface_target(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        assert isinstance(stage, Target)
        assert stage.target_type == "interface"
        assert stage.name == "overlay_stage"

    def test_takes_a_name(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(name="host")
        stage = project.OverlayDir(env, "stage", sources=[shared, app], name="pkg")

        assert stage.name == "pkg"

    def test_one_output_node_per_surviving_file(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        paths = {node.path for node in stage.output_nodes}
        assert paths == {
            Path("build/stage/Manifest.xml"),
            Path("build/stage/shared_only.txt"),
            Path("build/stage/res/xml/a.txt"),
            Path("build/stage/res/drawable/b.txt"),
            Path("build/stage/src/com/example/Thing.java"),
        }

    def test_destination_is_anchored_under_the_env_build_dir(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        assert all(
            node.path.parts[0] == "build" and node.role is None
            for node in stage.output_nodes
        )

    def test_no_install_prefix_is_applied(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        assert not any("dist" in node.path.parts for node in stage.output_nodes)

    def test_one_producer_per_output(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        paths = [node.path for node in stage.output_nodes]
        assert len(paths) == len(set(paths))

    def test_two_overlays_into_one_destination_collide(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(name="host")
        project.OverlayDir(env, "stage", sources=[shared, app])
        project.OverlayDir(env, "stage", sources=[shared, app])

        with pytest.raises(Exception, match="one producer"):
            project.resolve()

    def test_missing_source_directory_is_an_error(self, tmp_path):
        shared, _ = make_trees(tmp_path)
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(name="host")
        project.OverlayDir(env, "stage", sources=[shared, tmp_path / "absent"])

        with pytest.raises(BuilderError, match="not a directory"):
            project.resolve()

    def test_generated_rule_copies_each_file(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "rule install_copycmd" in content
        assert "build stage/src/com/example/Thing.java:" in content


@needs_ninja
class TestOverlayBuild:
    """What lands in the destination after a real build."""

    def test_later_source_wins_a_shared_path(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        build_dir = build(project, tmp_path)

        assert (build_dir / "stage" / "Manifest.xml").read_bytes() == (
            app / "Manifest.xml"
        ).read_bytes()

    def test_reversing_the_order_reverses_the_winner(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [app, shared])
        build_dir = build(project, tmp_path)

        assert (build_dir / "stage" / "Manifest.xml").read_bytes() == (
            shared / "Manifest.xml"
        ).read_bytes()

    def test_a_nested_path_is_not_flattened(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        build_dir = build(project, tmp_path)

        deep = build_dir / "stage" / "src" / "com" / "example" / "Thing.java"
        assert deep.read_text() == "class Thing {}\n"
        assert not (build_dir / "stage" / "Thing.java").exists()

    def test_both_trees_fill_one_shared_directory(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        build_dir = build(project, tmp_path)

        assert (build_dir / "stage" / "res" / "xml" / "a.txt").exists()
        assert (build_dir / "stage" / "res" / "drawable" / "b.txt").exists()

    def test_a_file_only_one_tree_has_arrives(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        build_dir = build(project, tmp_path)

        assert (build_dir / "stage" / "shared_only.txt").read_text() == "shared only\n"

    def test_the_second_build_has_nothing_to_do(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        build_dir = build(project, tmp_path)

        again = subprocess.run(
            ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
        )
        assert again.returncode == 0, again.stderr or again.stdout
        assert "no work to do" in again.stdout


class TestOverlayConfigureDependencies:
    """What resolving registers, before any build runs."""

    def test_every_source_directory_is_registered(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])

        deps = set(project.configure_dependencies)
        assert deps >= {
            Path("shared"),
            Path("shared/res"),
            Path("shared/res/xml"),
            Path("shared/src"),
            Path("shared/src/com"),
            Path("shared/src/com/example"),
            Path("app"),
            Path("app/res"),
            Path("app/res/drawable"),
        }

    def test_registering_only_the_roots_would_not_be_enough(self, tmp_path):
        """The mtime signal the regen edge reads stops at the direct parent."""
        shared, _ = make_trees(tmp_path)
        deep = shared / "src" / "com" / "example"
        before = shared.stat().st_mtime_ns

        write(deep / "New.java", "class New {}\n")

        assert shared.stat().st_mtime_ns == before
        assert deep.stat().st_mtime_ns != before


@needs_ninja
class TestOverlayFreshness:
    """A second build after a change, with no configure in between."""

    def test_a_file_added_deep_appears_on_the_next_build(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(tmp_path / "shared/src/com/example/New.java", "class New {}\n")
        output = run_ninja(tmp_path)

        assert "Regenerating" in output
        assert (stage / "src/com/example/New.java").read_text() == "class New {}\n"

    def test_a_new_directory_added_deep_is_noticed(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(tmp_path / "shared/src/com/example/util/Util.java", "class Util {}\n")
        run_ninja(tmp_path)

        assert (stage / "src/com/example/util/Util.java").exists()

    def test_an_unchanged_tree_does_no_work(self, tmp_path):
        """Registering a directory must not put the regen edge in a loop."""
        freshness_project(tmp_path)
        run_pcons(tmp_path)

        assert "no work to do" in run_ninja(tmp_path)

    def test_a_removed_file_loses_its_edge_but_keeps_its_copy(self, tmp_path):
        """Pinned: this stages files, it does not mirror."""
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        (tmp_path / "shared/shared_only.txt").unlink()
        output = run_ninja(tmp_path)

        assert "Regenerating" in output
        manifest = (tmp_path / "build" / "build.ninja").read_text()
        assert "shared_only.txt" not in manifest
        assert (stage / "shared_only.txt").exists()

    def test_cleandead_removes_the_stale_copy(self, tmp_path):
        """The remedy the builder documents, exercised rather than asserted."""
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        (tmp_path / "shared/shared_only.txt").unlink()
        run_ninja(tmp_path)
        subprocess.run(
            ["ninja", "-t", "cleandead"],
            cwd=tmp_path / "build",
            capture_output=True,
            text=True,
            check=True,
        )

        assert not (stage / "shared_only.txt").exists()
        assert (stage / "Manifest.xml").exists()

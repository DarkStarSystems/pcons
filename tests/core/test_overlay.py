# SPDX-License-Identifier: MIT
"""Tests for Project.OverlayDir(): N source trees merged into one directory.

The merged set is decided by the overlay command at build time, so almost
everything here is read back out of the staged directory after a real build.
Asserting on the target's output nodes would only say what pcons intended.
"""

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


def overlay_project(
    root: Path, sources: list[Path], exclude: list[str] | None = None
) -> tuple[Project, Target]:
    """A resolved project whose single target overlays *sources* into "stage"."""
    project = Project("test", root_dir=root, build_dir=root / "build")
    env = project.Environment(name="host")
    stage = project.OverlayDir(env, "stage", sources=sources, exclude=exclude or [])
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


def overlay_build(
    root: Path, sources: list[Path], exclude: list[str] | None = None
) -> Path:
    """Overlay *sources* into "stage" for real; return the build directory."""
    project, _ = overlay_project(root, sources, exclude)
    return build(project, root)


def staged(build_dir: Path) -> set[str]:
    """The destination-relative paths the overlay actually produced."""
    stage = build_dir / "stage"
    return {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}


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

GENERATOR = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import sys
    import time
    from pathlib import Path

    time.sleep(2)
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("generated\\n")
    """
)

GENERATED_BUILD_SCRIPT = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import sys
    from pathlib import Path

    from pcons import Project

    project = Project("generated")
    env = project.Environment(name="host")
    root = Path(__file__).parent
    made = env.Command(
        target=str(root / "shared" / "generated.txt"),
        source="mk.py",
        command=[sys.executable, "$SOURCE", "$TARGET"],
    )
    stage = project.OverlayDir(env, "stage", sources=["shared", "app"])
    stage.depends(made)
    project.Default(stage)
    """
)


def freshness_project(root: Path) -> Path:
    """Two trees plus a build script, laid out for a real `pcons` run."""
    make_trees(root)
    write(root / "pcons-build.py", BUILD_SCRIPT)
    return root / "build" / "stage"


def generated_project(root: Path) -> Path:
    """A source tree one build edge writes into, slowly enough to be seen."""
    make_trees(root)
    write(root / "mk.py", GENERATOR)
    write(root / "pcons-build.py", GENERATED_BUILD_SCRIPT)
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

    def test_the_only_output_is_one_stamp(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        assert [node.path for node in stage.output_nodes] == [
            Path("build/.stamps/stage.stamp")
        ]

    def test_the_source_roots_are_the_edge_inputs(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        inputs = [node.path for node in stage.output_nodes[0].explicit_deps]
        assert inputs == [Path("shared"), Path("app")]

    def test_no_install_prefix_is_applied(self, tmp_path):
        shared, app = make_trees(tmp_path)
        _, stage = overlay_project(tmp_path, [shared, app])

        assert not any("dist" in node.path.parts for node in stage.output_nodes)

    def test_a_destination_outside_the_build_directory_stays_absolute(self, tmp_path):
        """No relative_to() answer exists, so the anchored path is used as is."""
        shared, app = make_trees(tmp_path)
        outside = tmp_path / "outside" / "stage"
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(name="host")
        stage = project.OverlayDir(env, outside, sources=[shared, app])
        project.resolve()
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        stamp = stage.output_nodes[0].path
        assert stamp.parent == Path("build/.stamps")
        assert stamp.name.endswith("_outside_stage.stamp")
        content = (tmp_path / "build" / "build.ninja").read_text()
        assert (
            f"overlay --depfile $out.d --stamp $out {outside.as_posix()} $in" in content
        )

    def test_two_destinations_outside_the_build_directory_keep_apart(self, tmp_path):
        """The flattened stamp name carries the whole path, not just the tail."""
        shared, app = make_trees(tmp_path)
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(name="host")
        one = project.OverlayDir(env, tmp_path / "a" / "stage", sources=[shared])
        two = project.OverlayDir(env, tmp_path / "b" / "stage", sources=[app])
        project.resolve()

        assert one.output_nodes[0].path != two.output_nodes[0].path

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

    def test_the_generated_rule_is_one_overlay_edge(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "pcons.util.commands overlay" in content
        assert "build .stamps/stage.stamp:" in content
        assert "build stage/" not in content

    def test_the_exclude_patterns_reach_the_command(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app], ["*.orig", ".git"])
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "--exclude=*.orig" in content
        assert "--exclude=.git" in content


@needs_ninja
class TestOverlayBuild:
    """What lands in the destination after a real build."""

    def test_later_source_wins_a_shared_path(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app])

        assert (build_dir / "stage" / "Manifest.xml").read_bytes() == (
            app / "Manifest.xml"
        ).read_bytes()

    def test_reversing_the_order_reverses_the_winner(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [app, shared])

        assert (build_dir / "stage" / "Manifest.xml").read_bytes() == (
            shared / "Manifest.xml"
        ).read_bytes()

    def test_a_nested_path_is_not_flattened(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app])

        deep = build_dir / "stage" / "src" / "com" / "example" / "Thing.java"
        assert deep.read_text() == "class Thing {}\n"
        assert not (build_dir / "stage" / "Thing.java").exists()

    def test_both_trees_fill_one_shared_directory(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app])

        assert (build_dir / "stage" / "res" / "xml" / "a.txt").exists()
        assert (build_dir / "stage" / "res" / "drawable" / "b.txt").exists()

    def test_a_file_only_one_tree_has_arrives(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app])

        assert (build_dir / "stage" / "shared_only.txt").read_text() == "shared only\n"

    def test_the_second_build_has_nothing_to_do(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app])

        again = subprocess.run(
            ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
        )
        assert again.returncode == 0, again.stderr or again.stdout
        assert "no work to do" in again.stdout


class TestOverlayConfigureDependencies:
    """Nothing is enumerated at configure time any more."""

    def test_no_source_directory_is_registered(self, tmp_path):
        shared, app = make_trees(tmp_path)
        project, _ = overlay_project(tmp_path, [shared, app])

        deps = set(project.configure_dependencies)
        assert not [d for d in deps if d.parts and d.parts[0] in ("shared", "app")]


@needs_ninja
class TestOverlayFreshness:
    """A second build after a change, with no configure in between."""

    def test_a_file_added_deep_appears_on_the_next_build(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(tmp_path / "shared/src/com/example/New.java", "class New {}\n")
        output = run_ninja(tmp_path)

        assert "Regenerating" not in output
        assert (stage / "src/com/example/New.java").read_text() == "class New {}\n"

    def test_a_new_directory_added_deep_is_noticed(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(tmp_path / "shared/src/com/example/util/Util.java", "class Util {}\n")
        run_ninja(tmp_path)

        assert (stage / "src/com/example/util/Util.java").exists()

    def test_an_edit_in_place_restages_the_file(self, tmp_path):
        """No directory changes here, so the depfile's file half is what fires."""
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(tmp_path / "shared/shared_only.txt", "edited\n")
        run_ninja(tmp_path)

        assert (stage / "shared_only.txt").read_text() == "edited\n"

    def test_an_unchanged_tree_does_no_work(self, tmp_path):
        freshness_project(tmp_path)
        run_pcons(tmp_path)

        assert "no work to do" in run_ninja(tmp_path)

    def test_a_removed_file_loses_its_staged_copy(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        (tmp_path / "shared/shared_only.txt").unlink()
        run_ninja(tmp_path)

        assert not (stage / "shared_only.txt").exists()
        assert (stage / "Manifest.xml").exists()

    def test_a_removed_directory_leaves_nothing_behind(self, tmp_path):
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        shutil.rmtree(tmp_path / "shared/src")
        run_ninja(tmp_path)

        assert not (stage / "src").exists()

    def test_a_file_the_overlay_never_wrote_is_left_alone(self, tmp_path):
        """Stale removal reads what it staged, not what the destination holds."""
        stage = freshness_project(tmp_path)
        run_pcons(tmp_path)

        write(stage / "installed_by_someone_else.txt", "not ours\n")
        (tmp_path / "shared/shared_only.txt").unlink()
        run_ninja(tmp_path)

        assert (stage / "installed_by_someone_else.txt").exists()


@needs_ninja
class TestOverlayGeneratedSource:
    """A file written into a source tree by the same build."""

    def test_a_generated_file_is_staged_by_the_first_invocation(self, tmp_path):
        """The generator is slow on purpose: a fast one is scheduled first."""
        stage = generated_project(tmp_path)
        run_pcons(tmp_path)

        assert (tmp_path / "shared" / "generated.txt").exists()
        assert (stage / "generated.txt").read_text() == "generated\n"

    def test_the_second_invocation_has_nothing_to_do(self, tmp_path):
        generated_project(tmp_path)
        run_pcons(tmp_path)

        assert "no work to do" in run_ninja(tmp_path)


@needs_ninja
class TestOverlayExclude:
    """`exclude=`, matched against the path relative to each source root."""

    def test_nothing_is_excluded_by_default(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / ".git" / "HEAD", "ref: refs/heads/main\n")
        write(shared / "NOTES.md", "notes\n")
        build_dir = overlay_build(tmp_path, [shared, app])

        assert ".git/HEAD" in staged(build_dir)
        assert "NOTES.md" in staged(build_dir)

    def test_a_file_at_a_tree_root_is_dropped(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "NOTES.md", "notes\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["NOTES.md"])

        assert "NOTES.md" not in staged(build_dir)
        assert "shared_only.txt" in staged(build_dir)

    def test_a_file_nested_deep_is_dropped(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "src" / "com" / "example" / "Thing.java.orig", "old\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["*.orig"])

        assert staged(build_dir) >= {"src/com/example/Thing.java"}
        assert "src/com/example/Thing.java.orig" not in staged(build_dir)

    def test_a_bare_name_matches_at_any_depth(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "NOTES.md", "root\n")
        write(shared / "src" / "NOTES.md", "nested\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["NOTES.md"])

        assert not [p for p in staged(build_dir) if p.endswith("NOTES.md")]

    def test_a_pattern_with_a_separator_is_anchored_at_the_root(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "src" / "res" / "xml" / "a.txt", "not the root one\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["res/xml/a.txt"])

        assert "res/xml/a.txt" not in staged(build_dir)
        assert "src/res/xml/a.txt" in staged(build_dir)

    def test_an_excluded_directory_takes_its_contents(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / ".git" / "HEAD", "ref: refs/heads/main\n")
        write(shared / ".git" / "objects" / "ab" / "cdef", "blob\n")
        build_dir = overlay_build(tmp_path, [shared, app], [".git"])

        assert not [p for p in staged(build_dir) if p.startswith(".git")]

    def test_the_pattern_applies_to_every_source_root(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "NOTES.md", "shared\n")
        write(app / "NOTES.md", "app\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["NOTES.md"])

        assert "NOTES.md" not in staged(build_dir)

    def test_excluding_a_conflict_winner_leaves_no_file(self, tmp_path):
        """Pinned: the loser is not promoted, because it matches too.

        Patterns are relative to each source root, so a path excluded in the
        winning tree is excluded in every tree that holds it. Promoting the
        loser would ship the very path the caller asked to drop.
        """
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app], ["Manifest.xml"])

        assert "Manifest.xml" not in staged(build_dir)

    def test_a_pattern_matching_nothing_is_not_an_error(self, tmp_path):
        shared, app = make_trees(tmp_path)
        build_dir = overlay_build(tmp_path, [shared, app], ["*.absent"])

        assert "shared_only.txt" in staged(build_dir)

    def test_matching_is_case_sensitive_on_every_platform(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "NOTES.md", "notes\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["notes.md"])

        assert "NOTES.md" in staged(build_dir)

    def test_an_excluded_file_never_reaches_the_destination(self, tmp_path):
        shared, app = make_trees(tmp_path)
        write(shared / "NOTES.md", "notes\n")
        write(app / "res" / "drawable" / "b.txt.orig", "old\n")
        build_dir = overlay_build(tmp_path, [shared, app], ["NOTES.md", "*.orig"])

        assert not (build_dir / "stage" / "NOTES.md").exists()
        assert not (build_dir / "stage" / "res" / "drawable" / "b.txt.orig").exists()
        assert (build_dir / "stage" / "res" / "drawable" / "b.txt").exists()

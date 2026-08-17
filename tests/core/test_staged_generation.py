# SPDX-License-Identifier: MIT
"""Configure dependencies, the regen edge, and staged generation."""

from pathlib import Path

import pytest

from pcons.core import invocation
from pcons.core.errors import PconsError
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator


@pytest.fixture
def recorded_invocation(tmp_path):
    """Record an invocation, as the CLI does, so a regen edge is emitted."""
    script = tmp_path / "pcons-build.py"
    script.write_text("# build script\n")
    invocation.record(invocation.Invocation(script=script))
    return script


class TestConfigureDependencies:
    def test_explicit_dependency_is_recorded(self, tmp_path):
        project = Project("p", root_dir=tmp_path)
        project.add_configure_dependency(tmp_path / "plugins.def")

        assert Path("plugins.def") in project.configure_dependencies

    def test_dependency_is_deduplicated(self, tmp_path):
        project = Project("p", root_dir=tmp_path)
        project.add_configure_dependency(tmp_path / "a.txt")
        project.add_configure_dependency("a.txt")

        assert project.configure_dependencies.count(Path("a.txt")) == 1

    def test_subproject_dependency_lands_on_top_level(self, tmp_path):
        top = Project("top", root_dir=tmp_path)
        (tmp_path / "sub").mkdir()
        with top._enter_subdir("sub"):
            sub = Project("sub", root_dir=tmp_path / "sub")

        sub.add_configure_dependency(tmp_path / "sub" / "data.txt")

        assert Path("sub/data.txt") in top.configure_dependencies


class TestRegenEdge:
    def test_ninja_emits_generator_edge(self, tmp_path, recorded_invocation):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "rule pcons_regen" in content
        assert "generator = 1" in content
        assert "build build.ninja: pcons_regen |" in content
        assert "$topdir/plugins.def" in content

    def test_build_tree_dependency_matches_its_producing_edge(
        self, tmp_path, recorded_invocation
    ):
        """A generated manifest must be named the way its own rule names it,
        or ninja can't connect the two."""
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(Path("build/gen/list.txt"))

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        regen_edge = next(
            line
            for line in content.splitlines()
            if line.startswith("build build.ninja")
        )
        assert " gen/list.txt" in regen_edge
        assert "$topdir/build/gen/list.txt" not in regen_edge

    def test_no_edge_without_a_reconstructable_invocation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["/usr/bin/pytest"])
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "pcons_regen" not in content

    def test_makefile_emits_remake_rule(self, tmp_path, recorded_invocation):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        MakefileGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        assert "Makefile: " in content
        assert str(tmp_path / "plugins.def") in content


class TestGeneratedInput:
    def test_returns_none_and_registers_when_absent(self, tmp_path):
        project = Project("p", root_dir=tmp_path, build_dir="build")

        result = project.generated_input(Path("build/gen/list.txt"))

        assert result is None
        assert Path("build/gen/list.txt") in project.configure_dependencies

    def test_returns_path_when_present(self, tmp_path):
        gen = tmp_path / "build" / "gen"
        gen.mkdir(parents=True)
        (gen / "list.txt").write_text("one\n")
        project = Project("p", root_dir=tmp_path, build_dir="build")

        result = project.generated_input(Path("build/gen/list.txt"))

        assert result == gen / "list.txt"

    def test_when_generated_skips_the_block_until_the_input_exists(self, tmp_path):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        ran = []

        @project.when_generated(Path("build/gen/list.txt"))
        def _block(path):
            ran.append(path)

        assert ran == []

    def test_when_generated_runs_the_block_once_present(self, tmp_path):
        gen = tmp_path / "build" / "gen"
        gen.mkdir(parents=True)
        (gen / "list.txt").write_text("one\n")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        ran = []

        @project.when_generated(Path("build/gen/list.txt"))
        def _block(path):
            ran.append(path)

        assert ran == [gen / "list.txt"]

    def test_unproduced_staged_input_is_an_error(self, tmp_path, gcc_toolchain):
        """Waiting on a file no rule produces would wait forever."""
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.Environment(toolchain=gcc_toolchain)
        project.generated_input(Path("build/gen/list.txt"))

        with pytest.raises(PconsError, match="not produced by any build rule"):
            project.resolve()

    def test_staged_input_produced_by_a_command_is_accepted(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=Path("build/gen/list.txt"),
            source=None,
            command="generate $TARGET",
        )
        project.generated_input(Path("build/gen/list.txt"))

        project.resolve()  # does not raise


class TestRegenReproducesTheManifest:
    """The regen edge must not silently emit a *different* build.

    `root = Path(__file__).parent` is the first line of most build scripts, and
    the regen edge spells the script's path differently than the user did (it
    passes `-b pcons-build.py` relative). Handing the script that spelling
    verbatim would move every path derived from `root` between the user's run
    and the regenerated one: the first build works, the one after the first
    regeneration is broken, with nothing to see. So pcons makes `__file__`
    absolute, as CPython does for a script's own `__file__` (3.9+).
    """

    SCRIPT = """\
from pathlib import Path

from pcons import Project

root = Path(__file__).parent
project = Project("regen")
env = project.Environment(toolchain="c")
env.Command(
    target="out.txt",
    source="in.txt",
    command=["python3", str(root / "gen.py"), "$SOURCE", "$TARGET"],
    name="generate",
)
"""

    def _project_dir(self, tmp_path):
        (tmp_path / "pcons-build.py").write_text(self.SCRIPT)
        (tmp_path / "gen.py").write_text("# generator\n")
        (tmp_path / "in.txt").write_text("in\n")
        return tmp_path

    def _run(self, cwd, *args):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "pcons", "generate", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result

    def test_regen_reproduces_the_users_manifest(self, tmp_path):
        root = self._project_dir(tmp_path)

        # As a user runs it, from the source directory.
        self._run(root)
        as_user = (root / "build" / "build.ninja").read_text()

        # As the regen edge runs it: from the build directory, naming the
        # script relatively. These are the arguments pcons writes itself.
        self._run(root / "build", "-C", "..", "-B", "build", "-b", "pcons-build.py")
        as_regen = (root / "build" / "build.ninja").read_text()

        assert as_regen == as_user

    def test_the_script_sees_an_absolute_file(self, tmp_path):
        """The mechanism, pinned directly: a relative -b still reaches the
        script as an absolute __file__, so `Path(__file__).parent` is the
        project root whoever ran pcons."""
        root = self._project_dir(tmp_path)
        (root / "pcons-build.py").write_text(
            "from pathlib import Path\n"
            "from pcons import Project\n"
            "assert Path(__file__).is_absolute(), __file__\n"
            "Project('regen')\n"
        )

        self._run(root, "-b", "pcons-build.py")


class TestVirtualenvIsNotAConfigureDependency:
    """A build description split across build-scripts/*.py is a configure
    input; an in-tree virtualenv is not. `uv venv` puts one at .venv by
    default, and pulling its site-packages into the regen edge would re-run
    pcons whenever any dependency was upgraded."""

    def test_a_venv_module_is_excluded(self, tmp_path):
        from pcons.core.project import _in_virtualenv

        venv = tmp_path / ".venv"
        (venv / "lib" / "python3.12" / "site-packages" / "pkg").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("")
        module = venv / "lib" / "python3.12" / "site-packages" / "pkg" / "__init__.py"
        module.write_text("")

        assert _in_virtualenv(module, tmp_path)

    def test_a_project_module_is_not(self, tmp_path):
        from pcons.core.project import _in_virtualenv

        helper = tmp_path / "build-scripts" / "helper.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("")

        assert not _in_virtualenv(helper, tmp_path)

    def test_the_venv_is_found_by_marker_not_by_name(self, tmp_path):
        """`.venv` is a convention; pyvenv.cfg is what every venv has."""
        from pcons.core.project import _in_virtualenv

        env = tmp_path / "some-env"
        env.mkdir()
        (env / "pyvenv.cfg").write_text("")
        module = env / "mod.py"
        module.write_text("")

        assert _in_virtualenv(module, tmp_path)

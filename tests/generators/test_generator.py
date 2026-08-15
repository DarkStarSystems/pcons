# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

import subprocess
import sys
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator, Generator, MultiGenerator
from tests.support import subprocess_env


class MockGenerator(BaseGenerator):
    """Mock that overrides generate() directly — for protocol tests."""

    def __init__(self) -> None:
        super().__init__("mock")
        self.generated = False
        self.last_project: Project | None = None

    def generate(self, project: Project) -> None:  # type: ignore[override]
        self.generated = True
        self.last_project = project

    def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
        pass


class DeferredMockGenerator(BaseGenerator):
    """Mock that uses _generate_impl — for deferred-execution tests."""

    def __init__(self) -> None:
        super().__init__("mock")
        self.executed = False

    def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
        self.executed = True


class TestGeneratorProtocol:
    def test_base_generator_is_generator(self):
        gen = MockGenerator()
        assert isinstance(gen, Generator)


class TestBaseGenerator:
    def test_properties(self):
        gen = MockGenerator()
        assert gen.name == "mock"

    def test_generate_called(self):
        gen = MockGenerator()
        project = Project("test")

        gen.generate(project)

        assert gen.generated is True
        assert gen.last_project is project

    def test_repr(self):
        gen = MockGenerator()
        assert "MockGenerator" in repr(gen)
        assert "mock" in repr(gen)


class TestDeferredGenerate:
    def test_generate_defers_execution(self, tmp_path):
        gen = DeferredMockGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        gen.generate(project)

        assert not gen.executed

    def test_generate_pending_executes(self, tmp_path):
        gen = DeferredMockGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        assert gen.executed

    def test_generate_pending_clears_queue(self, tmp_path):
        gen = DeferredMockGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        gen.generate(project)
        BaseGenerator._generate_pending(project)
        gen.executed = False
        BaseGenerator._generate_pending(project)

        assert not gen.executed

    def test_generate_pending_uses_top_level_when_no_project_arg(self, tmp_path):
        gen = DeferredMockGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        gen.generate(project)
        BaseGenerator._generate_pending()  # no arg → resolves top-level project

        assert gen.executed

    def test_generate_pending_reraises_on_error(self, tmp_path):
        class FailingGenerator(BaseGenerator):
            def __init__(self) -> None:
                super().__init__("failing")

            def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
                raise RuntimeError("generation failed")

        gen = FailingGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        gen.generate(project)

        with pytest.raises(RuntimeError, match="generation failed"):
            BaseGenerator._generate_pending(project)


class TestARealScriptInASubprocess:
    """What running a build script does is a property of a whole process, and
    calling anything in-process does not reproduce it. These tests run a real
    script in a real subprocess.

    This is the coverage that was missing for four months: ``tests/test_examples.py``
    reached generation through ``run_script()``, which is the CLI's own
    function, so nothing exercised what a whole process does.
    """

    def _run(self, script: Path, *, by_hand: bool) -> subprocess.CompletedProcess[str]:
        argv = [str(script)] if by_hand else ["-m", "pcons", "generate"]
        return subprocess.run(
            [sys.executable, *argv],
            cwd=script.parent,
            capture_output=True,
            text=True,
            timeout=120,
            env=subprocess_env(),
        )

    def _script(self, tmp_path: Path, body: str = "") -> Path:
        (tmp_path / "hello.c").write_text("int main(void) { return 0; }\n")
        script = tmp_path / "pcons-build.py"
        script.write_text(
            "from pcons import Project\n"
            "project = Project('probe')\n"
            "env = project.Environment(toolchain='c')\n"
            "project.Program('hello', env, sources=['hello.c'])\n" + body
        )
        return script

    def test_pcons_writes_build_files(self, tmp_path):
        """A build script run by pcons must leave a usable build.ninja."""
        result = self._run(self._script(tmp_path), by_hand=False)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists(), result.stderr

    def test_a_script_run_by_hand_builds_nothing(self, tmp_path):
        """Generation used to happen at interpreter shutdown, so a script run
        by hand still got build files. Nothing runs at shutdown now, so it
        describes a build nobody asked for and exits."""
        result = self._run(self._script(tmp_path), by_hand=True)

        assert result.returncode == 0, result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_a_worker_pool_is_usable_from_a_build_script(self, tmp_path):
        """The point of the whole exercise. Nothing reachable from configure
        runs while the interpreter is tearing itself down any more, so a
        ThreadPoolExecutor works; started from a shutdown handler it raises."""
        script = self._script(
            tmp_path,
            body=(
                "from concurrent.futures import ThreadPoolExecutor\n"
                "with ThreadPoolExecutor(max_workers=1) as pool:\n"
                "    assert pool.submit(int, '1').result() == 1\n"
            ),
        )
        result = self._run(script, by_hand=False)

        assert result.returncode == 0, result.stderr


class TestMultiGenerator:
    def test_name_is_colon_joined(self):
        a = MockGenerator()
        b = MockGenerator()
        multi = MultiGenerator([a, b])
        assert multi.name == "mock:mock"

    def test_generate_calls_all(self):
        a = MockGenerator()
        b = MockGenerator()
        multi = MultiGenerator([a, b])
        project = Project("test")

        multi.generate(project)

        assert a.generated
        assert b.generated
        assert a.last_project is project
        assert b.last_project is project

    def test_generate_order(self):
        call_order: list[str] = []

        class OrderedGen(BaseGenerator):
            def __init__(self, tag: str) -> None:
                super().__init__(tag)
                self._tag = tag

            def generate(self, project: Project) -> None:  # type: ignore[override]
                call_order.append(self._tag)

            def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
                pass

        multi = MultiGenerator([OrderedGen("first"), OrderedGen("second")])
        multi.generate(Project("test"))

        assert call_order == ["first", "second"]

    def test_is_generator_protocol(self):
        multi = MultiGenerator([MockGenerator()])
        assert isinstance(multi, Generator)

    def test_repr(self):
        multi = MultiGenerator([MockGenerator(), MockGenerator()])
        assert "MultiGenerator" in repr(multi)
        assert "mock:mock" in repr(multi)


class TestDefaultGenerationContract:
    """A top-level project always gets a build generation unless a build
    generator was explicitly requested: auxiliary generators (dot,
    mermaid, metadata) are additive companions — adding a diagram must
    not cancel the build. Run an auxiliary generator alone via
    PCONS_GENERATOR / --generator (plans/plan-design-cleanup.md 4a)."""

    def test_auxiliary_generator_is_additive(self, tmp_path):
        from pcons.core.project import Project
        from pcons.generators.dot import DotGenerator
        from pcons.generators.generator import BaseGenerator

        project = Project("graphs", root_dir=tmp_path, build_dir=tmp_path)
        DotGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        assert (tmp_path / "deps.dot").exists()
        assert (tmp_path / "build.ninja").exists()  # build still happens

    def test_explicit_build_generator_no_double_default(self, tmp_path):
        from pcons.core.project import Project
        from pcons.generators.generator import BaseGenerator
        from pcons.generators.ninja import NinjaGenerator

        project = Project("explicit", root_dir=tmp_path, build_dir=tmp_path)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        assert (tmp_path / "build.ninja").exists()

    def test_no_generator_project_gets_default(self, tmp_path):
        from pcons.core.project import Project
        from pcons.generators.generator import BaseGenerator

        project = Project("plain", root_dir=tmp_path, build_dir=tmp_path)
        BaseGenerator._generate_pending(project)

        assert (tmp_path / "build.ninja").exists()


class TestRootSymlinkOptOut:
    """root_symlink=False keeps generation strictly inside build_dir
    (plans/plan-design-cleanup.md 4b)."""

    def test_root_symlink_disabled(self, tmp_path):
        from pcons.core.project import Project
        from pcons.generators.generator import BaseGenerator
        from pcons.generators.ninja import NinjaGenerator

        root = tmp_path / "src"
        build = tmp_path / "src" / "build"
        root.mkdir()
        project = Project("app", root_dir=root, build_dir=build)
        NinjaGenerator().generate(project, root_symlink=False)
        BaseGenerator._generate_pending(project)

        assert (build / "compile_commands.json").exists()
        assert not (root / "compile_commands.json").exists()

    def test_root_symlink_default_on(self, tmp_path):
        from pcons.core.project import Project
        from pcons.generators.generator import BaseGenerator
        from pcons.generators.ninja import NinjaGenerator

        root = tmp_path / "src"
        build = tmp_path / "src" / "build"
        root.mkdir()
        project = Project("app", root_dir=root, build_dir=build)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        assert (root / "compile_commands.json").is_symlink()

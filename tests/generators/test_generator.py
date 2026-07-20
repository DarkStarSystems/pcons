# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

import os

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator, Generator, MultiGenerator


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

    def test_generate_pending_forces_nonzero_exit_on_atexit_error(
        self, tmp_path, capsys, monkeypatch
    ):
        """A real generation failure in the atexit hook must report the error
        and force a nonzero exit. Python ignores exceptions raised at shutdown
        and would otherwise exit 0, silently hiding the failure."""

        class FailingGenerator(BaseGenerator):
            def __init__(self) -> None:
                super().__init__("failing")

            def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
                raise RuntimeError("atexit failure")

        gen = FailingGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        gen.generate(project)

        # os._exit would kill the test runner; sub a sentinel that records the
        # code and stops control flow the way a real _exit would.
        class _Exited(Exception):
            pass

        def fake_exit(code: int) -> None:
            raise _Exited(code)

        monkeypatch.setattr(os, "_exit", fake_exit)

        with pytest.raises(_Exited) as exc_info:
            BaseGenerator._generate_pending(project, _is_atexit=True)

        assert exc_info.value.args[0] == 1
        assert "atexit failure" in capsys.readouterr().err


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
    PCONS_GENERATOR / --generator (docs/plan-design-cleanup.md 4a)."""

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
    (docs/plan-design-cleanup.md 4b)."""

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

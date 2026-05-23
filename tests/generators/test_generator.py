# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

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

    def test_generate_pending_prints_to_stderr_on_atexit_error(self, tmp_path, capsys):
        class FailingGenerator(BaseGenerator):
            def __init__(self) -> None:
                super().__init__("failing")

            def _generate_impl(self, _project: Project, _output_dir: object) -> None:  # type: ignore[override]
                raise RuntimeError("atexit failure")

        gen = FailingGenerator()
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        gen.generate(project)

        with pytest.raises(RuntimeError, match="atexit failure"):
            BaseGenerator._generate_pending(project, _is_atexit=True)

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

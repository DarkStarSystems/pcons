# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator, Generator, MultiGenerator


class MockGenerator(BaseGenerator):
    """A mock generator for testing."""

    def __init__(self) -> None:
        super().__init__("mock")
        self.generated = False
        self.last_project: Project | None = None

    def generate(self, project: Project) -> None:
        self.generated = True
        self.last_project = project


class TestGeneratorProtocol:
    def test_base_generator_is_generator(self):
        gen = MockGenerator()
        assert isinstance(gen, Generator)


class TestBaseGenerator:
    def test_properties(self):
        gen = MockGenerator()
        assert gen.name == "mock"

    def test_generate_called(self, tmp_path):
        gen = MockGenerator()
        project = Project("test")

        gen.generate(project)

        assert gen.generated is True
        assert gen.last_project is project

    def test_repr(self):
        gen = MockGenerator()
        assert "MockGenerator" in repr(gen)
        assert "mock" in repr(gen)


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

            def generate(self, project: Project) -> None:
                call_order.append(self._tag)

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

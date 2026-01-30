# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator, Generator


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

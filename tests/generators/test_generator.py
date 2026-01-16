# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.generator."""

from pathlib import Path

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator, Generator


class MockGenerator(BaseGenerator):
    """A mock generator for testing."""

    def __init__(self) -> None:
        super().__init__("mock")
        self.generated = False
        self.last_project: Project | None = None
        self.last_output_dir: Path | None = None

    def generate(self, project: Project, output_dir: Path) -> None:
        self.generated = True
        self.last_project = project
        self.last_output_dir = output_dir


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

        gen.generate(project, tmp_path)

        assert gen.generated is True
        assert gen.last_project is project
        assert gen.last_output_dir is tmp_path

    def test_repr(self):
        gen = MockGenerator()
        assert "MockGenerator" in repr(gen)
        assert "mock" in repr(gen)

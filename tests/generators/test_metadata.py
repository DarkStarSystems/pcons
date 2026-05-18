# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.metadata."""

import json

from pcons.core.node import FileNode, Node
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.metadata import MetadataGenerator
from pcons.util.source_location import SourceLocation


def normalize_path(path: str) -> str:
    """Normalize path separators for cross-platform assertions."""
    return path.replace("\\", "/")


class TestMetadataGenerator:
    def test_is_generator(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = MetadataGenerator()

        gen.generate(project)

        assert gen.name == "metadata"
        assert (tmp_path / "pcons_metadata.json").exists()

    def test_empty_project(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = MetadataGenerator()

        gen.generate(project)

        content = json.loads((tmp_path / "pcons_metadata.json").read_text())
        assert content["schema_version"] == 1
        assert content["targets"] == []
        assert content["aliases"] == []

    def test_includes_targets_dependencies_and_aliases(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")

        lib = Target("mylib", target_type="static_library")
        lib.output_nodes.append(FileNode("build/libmylib.a"))
        lib.add_source("src/lib.c")
        project.add_target(lib)

        app = Target(
            "app",
            target_type="program",
            defined_at=SourceLocation(filename="build.py", lineno=10, function=None),
        )
        app.output_nodes.append(FileNode("build/app"))
        app.add_source("src/main.c")
        app.dependencies.append(lib)
        project.add_target(app)

        project.Default(app)
        project.Alias("all", app)
        project.Alias("all", FileNode("some/file.txt"))

        class IgnoredNode(Node):
            name = "ignored"

        project.Alias("all", IgnoredNode())  # Should be ignored in metadata

        gen = MetadataGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())

        assert content["project"]["name"] == "test"
        assert content["project"]["build_dir"] == "build"

        by_name = {target["name"]: target for target in content["targets"]}

        assert by_name["mylib"]["type"] == "static_library"
        assert normalize_path(by_name["mylib"]["sources"][0]) == "src/lib.c"
        assert normalize_path(by_name["mylib"]["outputs"][0]) == "build/libmylib.a"

        assert by_name["app"]["type"] == "program"
        assert by_name["app"]["dependencies"] == ["mylib"]
        assert by_name["app"]["is_default"] is True
        assert normalize_path(by_name["app"]["sources"][0]) == "src/main.c"
        assert normalize_path(by_name["app"]["outputs"][0]) == "build/app"
        assert by_name["app"]["defined_at"]["file"] == "build.py"
        assert by_name["app"]["defined_at"]["line"] == 10

        aliases = {alias["name"]: alias for alias in content["aliases"]}
        assert "all" in aliases
        assert len(aliases["all"]["entries"]) == 2
        assert "build/app" in aliases["all"]["entries"]
        assert "some/file.txt" in aliases["all"]["entries"]

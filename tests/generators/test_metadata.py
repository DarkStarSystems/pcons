# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.metadata."""

import json

from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.metadata import MetadataGenerator


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
        lib._sources.append(FileNode("src/lib.c"))
        project.add_target(lib)

        app = Target("app", target_type="program")
        app.output_nodes.append(FileNode("build/app"))
        app._sources.append(FileNode("src/main.c"))
        app.dependencies.append(lib)
        project.add_target(app)

        project.Default(app)
        project.Alias("all", app)

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

        aliases = {alias["name"]: alias for alias in content["aliases"]}
        assert "all" in aliases
        assert normalize_path(aliases["all"]["entries"][0]) == "build/app"

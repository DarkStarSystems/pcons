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
        assert content["schema_version"] == 2
        assert content["projects"][0]["name"] == "test"

    def test_includes_targets_dependencies_and_aliases(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")

        lib = Target("mylib", target_type="static_library")
        lib.output_nodes.append(FileNode("build/libmylib.a"))
        lib.add_source("src/lib.c")

        app = Target(
            "app",
            target_type="program",
            defined_at=SourceLocation(filename="build.py", lineno=10, function=None),
        )
        app.output_nodes.append(FileNode("build/app"))
        app.add_source("src/main.c")
        app.dependencies.append(lib)

        project.Default(app)
        project.Alias("all", app)
        project.Alias("all", FileNode("some/file.txt"))

        class IgnoredNode(Node):
            name = "ignored"

        project.Alias("all", IgnoredNode())  # Should be ignored in metadata

        gen = MetadataGenerator()
        gen.generate(project)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())

        assert content["projects"][0]["name"] == "test"
        assert content["projects"][0]["build_dir"] == "build"

        by_name = {
            target["name"]: target for target in content["projects"][0]["targets"]
        }

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

        aliases = {alias["name"]: alias for alias in content["projects"][0]["aliases"]}
        assert "all" in aliases
        assert len(aliases["all"]["entries"]) == 2
        assert "build/app" in aliases["all"]["entries"]
        assert "some/file.txt" in aliases["all"]["entries"]

    def test_target_qualified_name_and_sub_directory(self, tmp_path):
        """Targets include qualified_name and sub_directory fields."""
        project = Project("myproj", root_dir=tmp_path, build_dir="build")
        app = Target("app", target_type="program")
        app.output_nodes.append(FileNode("build/app"))

        MetadataGenerator().generate(project)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())
        by_name = {t["name"]: t for t in content["projects"][0]["targets"]}
        assert by_name["app"]["qualified_name"] == "myproj::app"
        assert by_name["app"]["sub_directory"] is None

    def test_target_sub_directory_set_in_subdir(self, tmp_path):
        """Targets created inside _enter_subdir have sub_directory set."""
        project = Project("myproj", root_dir=tmp_path, build_dir="build")
        with project._enter_subdir("lib"):
            lib = Target("mylib", target_type="static_library")
            lib.output_nodes.append(FileNode("build/lib/libmylib.a"))

        MetadataGenerator().generate(project)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())
        by_name = {t["name"]: t for t in content["projects"][0]["targets"]}
        assert by_name["mylib"]["sub_directory"] == "lib"
        assert by_name["mylib"]["qualified_name"] == "myproj::mylib"

    def test_child_projects_in_projects_list(self, tmp_path):
        """Child projects appear in the projects list with a parent reference."""
        parent = Project("parent", root_dir=tmp_path, build_dir="build")
        Project("child", root_dir=tmp_path / "sub")

        MetadataGenerator().generate(parent)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())
        assert len(content["projects"]) == 2
        proj_by_name = {p["name"]: p for p in content["projects"]}
        assert proj_by_name["parent"]["parent"] is None
        assert proj_by_name["child"]["parent"] == "parent"

    def test_test_target_embeds_testspec(self, tmp_path, gcc_toolchain):
        """Test() targets get an embedded `test` block for IDE integration."""
        src = tmp_path / "main.c"
        src.write_text("int main(void){return 0;}\n")

        project = Project("t", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        prog = project.Program("test_bin", env, sources=[str(src)])
        project.Test(
            "math.add",
            prog,
            args=["--quick"],
            labels=["unit", "fast"],
            timeout=30,
            should_fail=False,
        )

        MetadataGenerator().generate(project)

        content = json.loads((tmp_path / "build" / "pcons_metadata.json").read_text())
        by_name = {t["name"]: t for t in content["projects"][0]["targets"]}
        # The Test() target name is mangled internally; find by type.
        test_entries = [
            t for t in content["projects"][0]["targets"] if t["type"] == "test"
        ]
        assert len(test_entries) == 1
        ts = test_entries[0]["test"]
        # User-facing name preserved (target name is `test_math.add` but the
        # spec keeps the friendly form).
        assert ts["name"] == "math.add"
        assert ts["command"][-1] == "--quick"
        assert ts["labels"] == ["unit", "fast"]
        assert ts["timeout"] == 30
        assert ts["should_fail"] is False
        assert ts["disabled"] is False
        assert ts["serial"] is False
        assert ts["depends_on"] == []
        # `data` and `defined_at` are part of the full TestSpec dump so
        # IDEs (e.g., CodeLens) have the source location for the test.
        assert ts["data"] == []
        assert ts["defined_at"] != ""
        # Non-test targets have no `test` key
        assert "test" not in by_name["test_bin"]

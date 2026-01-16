# SPDX-License-Identifier: MIT
"""Tests for pcons.core.project."""

from pathlib import Path

import pytest

from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target


class TestProjectCreation:
    def test_basic_creation(self):
        project = Project("myproject")
        assert project.name == "myproject"
        assert project.root_dir == Path.cwd()
        assert project.build_dir == Path("build")

    def test_custom_directories(self, tmp_path):
        project = Project(
            "myproject",
            root_dir=tmp_path,
            build_dir="out",
        )
        assert project.root_dir == tmp_path
        assert project.build_dir == Path("out")

    def test_tracks_source_location(self):
        project = Project("myproject")
        assert project.defined_at is not None
        assert project.defined_at.lineno > 0


class TestProjectEnvironments:
    def test_create_environment(self):
        project = Project("myproject")
        env = project.Environment()

        assert env in project.environments
        assert env._project is project
        assert env.build_dir == project.build_dir

    def test_environment_with_variables(self):
        project = Project("myproject")
        env = project.Environment(variant="debug")

        assert env.variant == "debug"

    def test_multiple_environments(self):
        project = Project("myproject")
        env1 = project.Environment()
        env2 = project.Environment()

        assert len(project.environments) == 2
        assert env1 is not env2


class TestProjectNodes:
    def test_node_creation(self):
        project = Project("myproject")
        node = project.node("src/main.c")

        assert isinstance(node, FileNode)
        assert node.path == Path("src/main.c")

    def test_node_deduplication(self):
        project = Project("myproject")
        node1 = project.node("src/main.c")
        node2 = project.node("src/main.c")

        assert node1 is node2

    def test_dir_node_creation(self):
        project = Project("myproject")
        dir_node = project.dir_node("build")

        assert dir_node.path == Path("build")

    def test_node_type_mismatch_raises(self):
        project = Project("myproject")
        project.node("path")  # Create as FileNode

        with pytest.raises(TypeError):
            project.dir_node("path")  # Try to get as DirNode


class TestProjectTargets:
    def test_add_target(self):
        project = Project("myproject")
        target = Target("mylib")
        project.add_target(target)

        assert target in project.targets
        assert project.get_target("mylib") is target

    def test_duplicate_target_raises(self):
        project = Project("myproject")
        target1 = Target("mylib")
        target2 = Target("mylib")

        project.add_target(target1)
        with pytest.raises(ValueError) as exc_info:
            project.add_target(target2)

        assert "already exists" in str(exc_info.value)

    def test_get_nonexistent_target(self):
        project = Project("myproject")
        assert project.get_target("missing") is None


class TestProjectAliases:
    def test_create_alias(self):
        project = Project("myproject")
        target = Target("mylib")
        target.nodes.append(FileNode("lib.a"))
        project.add_target(target)

        alias = project.Alias("libs", target)

        assert "libs" in project.aliases
        assert len(alias.targets) == 1

    def test_alias_with_multiple_targets(self):
        project = Project("myproject")
        lib1 = Target("lib1")
        lib1.nodes.append(FileNode("lib1.a"))
        lib2 = Target("lib2")
        lib2.nodes.append(FileNode("lib2.a"))

        alias = project.Alias("all_libs", lib1, lib2)

        assert len(alias.targets) == 2


class TestProjectDefaults:
    def test_set_default_targets(self):
        project = Project("myproject")
        target = Target("app")
        project.add_target(target)

        project.Default(target)

        assert target in project.default_targets

    def test_default_by_name(self):
        project = Project("myproject")
        target = Target("app")
        project.add_target(target)

        project.Default("app")

        assert target in project.default_targets

    def test_default_avoids_duplicates(self):
        project = Project("myproject")
        target = Target("app")
        project.add_target(target)

        project.Default(target)
        project.Default(target)

        assert project.default_targets.count(target) == 1


class TestProjectValidation:
    def test_valid_project(self, tmp_path):
        # Create a valid project with existing source file
        project = Project("myproject", root_dir=tmp_path)
        source_file = tmp_path / "main.c"
        source_file.write_text("int main() { return 0; }")

        target = Target("app")
        target.sources.append(FileNode(source_file))
        project.add_target(target)

        errors = project.validate()
        assert errors == []

    def test_detect_missing_source(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path)
        target = Target("app")
        target.sources.append(FileNode("nonexistent.c"))
        project.add_target(target)

        errors = project.validate()
        assert len(errors) == 1
        assert "nonexistent.c" in str(errors[0])

    def test_detect_dependency_cycle(self):
        project = Project("myproject")
        a = Target("A")
        b = Target("B")
        a.link(b)
        b.link(a)
        project.add_target(a)
        project.add_target(b)

        errors = project.validate()
        assert len(errors) > 0
        assert any("cycle" in str(e).lower() for e in errors)


class TestProjectBuildOrder:
    def test_build_order(self):
        project = Project("myproject")
        lib = Target("lib")
        app = Target("app")
        app.link(lib)
        project.add_target(lib)
        project.add_target(app)

        order = project.build_order()

        assert order.index(lib) < order.index(app)


class TestProjectAllNodes:
    def test_all_nodes(self):
        project = Project("myproject")

        lib = Target("lib")
        lib.sources.append(FileNode("lib.c"))
        lib.nodes.append(FileNode("lib.o"))

        app = Target("app")
        app.sources.append(FileNode("main.c"))
        app.nodes.append(FileNode("app"))
        app.link(lib)

        project.add_target(lib)
        project.add_target(app)

        nodes = project.all_nodes()

        assert len(nodes) == 4

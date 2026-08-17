# SPDX-License-Identifier: MIT
"""Tests for pcons.core.project."""

from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target


class TestProjectCreation:
    def test_basic_creation(self):
        project = Project("myproject")
        assert project.name == "myproject"
        # root_dir defaults to directory containing the calling script
        assert project.root_dir == Path(__file__).parent
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

    def test_top_level_project(self):
        project1 = Project("project1")
        assert Project.top_level() is project1
        assert project1.is_top_level

        with project1._enter_subdir("sub"):
            project2 = Project("project2")
        assert Project.top_level() is project1
        assert not project2.is_top_level
        assert project2 in project1._children
        assert project2._parent is project1

    def test_root_dir_must_be_absolute(self):
        with pytest.raises(ValueError):
            Project("myproject", root_dir="relative/path")

    def test_retrieving_current_project_while_none_is_active_raises_a_value_error(self):
        with pytest.raises(ValueError):
            Project.current()

    def test_retrieving_top_level_project_while_none_is_active_raises_a_value_error(
        self,
    ):
        with pytest.raises(ValueError):
            Project.top_level()

    def test_retrieving_parent_project_while_no_parent_available_raises_a_value_error(
        self,
    ):
        project = Project("testproject")
        with pytest.raises(ValueError):
            _ = project.parent


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

    def test_default_environment(self):
        project = Project("myproject")
        env1 = project.Environment()
        project.Environment()

        assert project.default_environment is env1

    def test_missing_default_environment(self):
        project = Project("myproject")
        with pytest.raises(ValueError):
            _ = project.default_environment


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

        assert target in project.targets
        assert project.get_target("mylib") is target

    def test_duplicate_target_raises(self):
        Project("myproject")
        Target("mylib")
        with pytest.raises(ValueError) as exc_info:
            Target("mylib")

        assert "already exists" in str(exc_info.value)

    def test_get_nonexistent_target(self):
        project = Project("myproject")
        assert project.get_target("missing", raise_if_missing=False) is None

        with pytest.raises(KeyError):
            project.get_target("missing", raise_if_missing=True)


class TestSubproject:
    def test_target_lookup(self):
        root = Project("root")
        with root._enter_subdir("child1"):
            child1 = Project("child1")
            mylib1 = Target("mylib1")
        with root._enter_subdir("child2"):
            child2 = Project("child2")
            mylib2 = Target("mylib2")

        # may we support parent lookup ??? not currently implemented
        assert child1.get_target("mylib2", raise_if_missing=False) is None
        assert child2.get_target("mylib1", raise_if_missing=False) is None

        assert child1.get_target("mylib1") is mylib1
        assert child2.get_target("mylib2") is mylib2
        assert root.get_target("mylib1") is mylib1
        assert root.get_target("mylib2") is mylib2

        with pytest.raises(KeyError):
            child1.get_target("mylib2", raise_if_missing=True)

        with pytest.raises(KeyError):
            child2.get_target("mylib1", raise_if_missing=True)

    def test_subproject_build_dir_warns(self, tmp_path, test_project):
        with pytest.warns(
            UserWarning, match="build_dir argument is ignored for sub-projects"
        ):
            with test_project._enter_subdir("child"):
                Project("child", build_dir="ignored_build", root_dir=tmp_path / "child")


class TestSiblingProjects:
    """A second top-level Project is an independent sibling, never a merge."""

    def test_a_second_project_without_a_build_dir_raises(self):
        Project("first")
        with pytest.raises(PconsError, match="needs an explicit build_dir"):
            Project("second")

    def test_the_error_names_both_projects(self):
        Project("firmware")
        with pytest.raises(PconsError) as exc_info:
            Project("updater")
        message = str(exc_info.value)
        assert "'firmware'" in message
        assert "'updater'" in message
        assert "add_subdirectory" in message

    def test_a_distinct_build_dir_makes_a_sibling(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        assert first.is_top_level and second.is_top_level
        assert second._parent is None
        assert second not in first._children
        assert Project.current() is second
        assert Project.top_level() is first  # the run's default anchor
        assert first.build_dir == Path("build-a")
        assert second.build_dir == Path("build-b")

    def test_siblings_keep_separate_node_namespaces(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        node1 = first.node("src/main.c")
        node2 = second.node("src/main.c")
        assert node1 is not node2

    def test_a_shared_build_dir_raises(self, tmp_path):
        Project("first", root_dir=tmp_path, build_dir="out")
        with pytest.raises(PconsError, match="share the build directory"):
            Project("second", root_dir=tmp_path, build_dir="out")

    def test_a_shared_build_dir_is_compared_effectively(self, tmp_path):
        """The same directory spelled differently still collides."""
        Project("first", root_dir=tmp_path, build_dir="out")
        with pytest.raises(PconsError, match="share the build directory"):
            Project(
                "second", root_dir=tmp_path, build_dir=tmp_path / "sub" / ".." / "out"
            )

    def test_the_failed_project_is_not_registered(self):
        from pcons import get_registered_projects

        first = Project("first")
        with pytest.raises(PconsError):
            Project("second")
        assert get_registered_projects() == [first]

    def test_a_bare_project_after_a_subdirectory_still_raises(self):
        first = Project("first")
        with first._enter_subdir("sub"):
            Project("child")
        with pytest.raises(PconsError, match="needs an explicit build_dir"):
            Project("second")

    def test_a_fresh_tree_accepts_a_new_project(self):
        Project("first")
        Project._clear_tree()
        project = Project("second")
        assert Project.top_level() is project

    def test_targets_bind_to_their_own_sibling(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        t1 = Target("app", project=first)
        t2 = Target("app", project=second)  # same name, different project: fine
        assert t1.project is first
        assert t2.project is second
        assert first.get_target("app") is t1
        assert second.get_target("app") is t2

    def test_linking_across_siblings_raises(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        lib = Target("common", project=first)
        second = Project("second", root_dir=tmp_path, build_dir="build-b")
        app = Target("app", project=second)

        with pytest.raises(PconsError, match="build independently"):
            app.link(lib)

    def test_depending_across_siblings_raises(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        tool = Target("tool", project=first)
        second = Project("second", root_dir=tmp_path, build_dir="build-b")
        app = Target("app", project=second)

        with pytest.raises(PconsError, match="build independently"):
            app.depends(tool)

    def test_each_sibling_may_embed_the_same_subdirectory(self, tmp_path):
        first = Project("first", root_dir=tmp_path, build_dir="build-a")
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        with first._enter_subdir("lib"):
            lib1 = Project("lib", root_dir=tmp_path / "lib")
        with second._enter_subdir("lib"):
            lib2 = Project("lib", root_dir=tmp_path / "lib")

        assert lib1._parent is first
        assert lib2._parent is second
        assert lib1.build_dir == Path("build-a") / "lib"
        assert lib2.build_dir == Path("build-b") / "lib"


class TestExplicitBinding:
    """Targets attach to the project they were made through, not the
    most recently created one."""

    def _tree(self):
        top = Project("top")
        with top._enter_subdir("sub"):
            child = Project("child")
        return top, child

    def test_target_binds_to_the_project_passed(self):
        top, child = self._tree()
        # Project.current() is back to top, but the explicit project wins.
        target = Target("t", project=child)
        assert target.project is child
        assert top.get_target("t") is target  # lookup is recursive

    def test_builder_factory_binds_to_its_project(self):
        _top, child = self._tree()
        target = child.HeaderOnlyLibrary("hdrs")
        assert target.project is child

    def test_bare_target_still_binds_to_the_current_project(self):
        top, _child = self._tree()
        target = Target("t")
        assert target.project is top

    def test_top_walks_to_the_tree_root(self):
        top = Project("top")
        with top._enter_subdir("a"):
            child = Project("a")
            with child._enter_subdir("b"):
                grandchild = Project("b")
        assert top.top is top
        assert child.top is top
        assert grandchild.top is top
        assert top.is_top_level
        assert not child.is_top_level
        assert not grandchild.is_top_level


class TestProjectAliases:
    def test_create_alias(self):
        project = Project("myproject")
        target = Target("mylib")
        target.output_nodes.append(FileNode("lib.a"))

        alias = project.Alias("libs", target)

        assert "libs" in project.aliases
        assert len(alias.targets) == 1

    def test_alias_with_multiple_targets(self):
        project = Project("myproject")
        lib1 = Target("lib1")
        lib1.output_nodes.append(FileNode("lib1.a"))
        lib2 = Target("lib2")
        lib2.output_nodes.append(FileNode("lib2.a"))

        alias = project.Alias("all_libs", lib1, lib2)

        assert len(alias.targets) == 2

    def test_alias_resolves_targets_lazily(self):
        """Alias should pick up target output_nodes even if empty at Alias() time."""
        project = Project("myproject")
        target = Target("install_stuff")

        # Create alias while target has no nodes at all
        alias = project.Alias("install", target)
        assert alias.targets == []

        # Simulate resolve() populating output_nodes later
        node = FileNode("prefix/bin/app")
        target.output_nodes.append(node)

        # Now the alias should see the node
        assert alias.targets == [node]

    def test_alias_lazy_falls_back_to_nodes(self):
        """Alias deferred targets fall back to target.nodes when output_nodes is empty."""
        project = Project("myproject")
        target = Target("mylib")
        node = FileNode("lib.a")
        target.output_nodes.append(node)

        alias = project.Alias("libs", target)

        # output_nodes is empty, so should fall back to nodes
        assert alias.targets == [node]


class TestProjectDefaults:
    def test_set_default_targets(self):
        project = Project("myproject")
        target = Target("app")

        project.Default(target)

        assert target in project.default_targets

    def test_default_by_name(self):
        project = Project("myproject")
        target = Target("app")

        project.Default("app")

        assert target in project.default_targets

    def test_default_avoids_duplicates(self):
        project = Project("myproject")
        target = Target("app")

        project.Default(target)
        project.Default(target)

        assert project.default_targets.count(target) == 1

    def test_default_with_node(self):
        project = Project("myproject")
        target = Target("app")
        node = FileNode(Path("app.out"))
        target.output_nodes.append(node)

        project.Default(node)

        assert target in project.default_targets

    def test_default_with_node_not_owned_by_any_target_raises(self):
        project = Project("myproject")
        Target("app")
        orphan_node = FileNode(Path("orphan.out"))

        with pytest.raises(ValueError, match="not an output of any target"):
            project.Default(orphan_node)

    def test_default_with_alias(self):
        project = Project("myproject")
        target = Target("app")
        project.Alias("myalias", target)

        project.Default("myalias")

        assert target in project.default_targets

    def test_default_unknown_name_raises_clear_error(self):
        project = Project("myproject")

        with pytest.raises(KeyError, match="not a known alias or target"):
            project.Default("nonexistent")

    def test_default_wrong_type_raises_type_error(self):
        project = Project("myproject")

        with pytest.raises(TypeError):
            project.Default(42)  # type: ignore[arg-type]


class TestProjectValidation:
    def test_valid_project(self, tmp_path):
        # Create a valid project with existing source file
        project = Project("myproject", root_dir=tmp_path)
        source_file = tmp_path / "main.c"
        source_file.write_text("int main() { return 0; }")

        target = Target("app")
        target.add_source(FileNode(source_file))

        errors = project.validate()
        assert errors == []

    def test_detect_missing_source(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path)
        target = Target("app")
        target.add_source(FileNode("nonexistent.c"))

        errors = project.validate()
        assert len(errors) == 1
        assert "nonexistent.c" in str(errors[0])

    def test_detect_dependency_cycle(self):
        project = Project("myproject")
        a = Target("A")
        b = Target("B")
        a.private.link_libs.append(b)
        b.private.link_libs.append(a)

        errors = project.validate()
        assert len(errors) > 0
        assert any("cycle" in str(e).lower() for e in errors)


class TestProjectBuildOrder:
    def test_build_order(self):
        project = Project("myproject")
        lib = Target("lib")
        app = Target("app")
        app.private.link_libs.append(lib)

        order = project.build_order()

        assert order.index(lib) < order.index(app)


class TestProjectAllNodes:
    def test_all_nodes(self):
        project = Project("myproject")

        lib = Target("lib")
        lib.add_source(FileNode("lib.c"))
        lib.output_nodes.append(FileNode("lib.o"))

        app = Target("app")
        app.add_source(FileNode("main.c"))
        app.output_nodes.append(FileNode("app"))
        app.private.link_libs.append(lib)

        nodes = project.all_nodes()

        assert len(nodes) == 4


class TestNodeCanonicalization:
    def test_node_absolute_path_deduplicates_with_relative(self, tmp_path):
        """Absolute path under project root deduplicates with relative equivalent."""
        project = Project("myproject", root_dir=tmp_path)
        node_rel = project.node("src/main.c")
        node_abs = project.node(tmp_path / "src" / "main.c")

        assert node_rel is node_abs

    def test_build_dir_absolute_normalized_to_relative(self, tmp_path):
        """Absolute build_dir under root_dir is normalized to relative."""
        abs_build = tmp_path / "build"
        project = Project("myproject", root_dir=tmp_path, build_dir=abs_build)

        assert not project.build_dir.is_absolute()
        assert project.build_dir == Path("build")

    def test_build_dir_out_of_tree_stays_absolute(self, tmp_path):
        """Out-of-tree absolute build_dir stays absolute."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        out_of_tree = tmp_path / "builds" / "out"

        project = Project("myproject", root_dir=project_root, build_dir=out_of_tree)

        assert project.build_dir.is_absolute()
        assert project.build_dir == out_of_tree

    def test_dir_node_absolute_deduplicates(self, tmp_path):
        """Absolute dir_node path under project root deduplicates with relative."""
        project = Project("myproject", root_dir=tmp_path)
        dir1 = project.dir_node("build/output")
        dir2 = project.dir_node(tmp_path / "build" / "output")

        assert dir1 is dir2

    def test_node_dot_segments_normalized(self):
        """Paths with dot segments deduplicate after normalization."""
        project = Project("myproject")
        node1 = project.node("src/main.c")
        node2 = project.node("src/../src/main.c")

        assert node1 is node2


class TestGeneratePcFile:
    def test_basic_pc_file(self, tmp_path):
        """generate_pc_file() writes a valid .pc with target's public reqs."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        target = Target("mylib")
        target.public.include_dirs.append(Path("include"))
        target.public.defines.append("MYLIB_STATIC")
        target.public.link_libs.append("m")

        pc = project.generate_pc_file(target, version="2.1.0", description="My lib")

        assert pc.exists()
        content = pc.read_text()
        assert "Name: mylib" in content
        assert "Version: 2.1.0" in content
        assert "Description: My lib" in content
        assert "-lmylib" in content
        assert "-lm" in content
        assert "-I${includedir}" in content
        assert "-DMYLIB_STATIC" in content

    def test_pc_file_write_if_changed(self, tmp_path):
        """generate_pc_file() doesn't rewrite if content unchanged."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        target = Target("foo")

        pc = project.generate_pc_file(target, version="1.0")
        content1 = pc.read_text()
        mtime_ns1 = pc.stat().st_mtime_ns

        # Call again with identical content — should not rewrite the file.
        # Comparing st_mtime_ns (not st_mtime) and not sleeping avoids relying
        # on filesystem mtime granularity, which can be coarser than a
        # wrongly-rewritten file's actual write latency and mask a bug.
        project.generate_pc_file(target, version="1.0")
        content2 = pc.read_text()
        mtime_ns2 = pc.stat().st_mtime_ns

        assert content2 == content1
        assert mtime_ns2 == mtime_ns1

    def test_pc_file_requires_from_pkgconfig_deps(self, tmp_path):
        """Dependencies found via pkg-config become Requires: entries."""
        from pcons.packages.description import PackageDescription
        from pcons.packages.imported import ImportedTarget

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Simulate a pkg-config dependency
        pkg = PackageDescription(name="zlib", libraries=["z"])
        pkg.found_by = "pkg-config"
        zlib = ImportedTarget.from_package(pkg)

        target = Target("mylib")
        target.private.link_libs.append(zlib)

        pc = project.generate_pc_file(target, version="1.0")
        content = pc.read_text()
        assert "Requires: zlib" in content
        # zlib's -lz should NOT appear in Libs (it's in Requires)
        assert "-lz" not in content

    def test_pc_file_absolute_include_under_root_uses_includedir(self, tmp_path):
        """Absolute include dirs under root_dir map to ${includedir}."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        target = Target("mylib")
        target.public.include_dirs.append(tmp_path / "include")

        pc = project.generate_pc_file(target, version="1.0")
        content = pc.read_text()
        assert "-I${includedir}" in content
        assert str(tmp_path) not in content

    def test_pc_file_external_include_kept_absolute(self, tmp_path):
        """Absolute include dirs outside root_dir stay absolute."""
        import sys

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        target = Target("mylib")
        # Use a platform-appropriate absolute path outside the project root
        if sys.platform == "win32":
            ext_inc = Path("C:/opt/external/include")
        else:
            ext_inc = Path("/opt/external/include")
        target.public.include_dirs.append(ext_inc)

        pc = project.generate_pc_file(target, version="1.0")
        content = pc.read_text()
        assert f"-I{ext_inc}" in content

    def test_pc_file_inlines_header_only_dependency(self, tmp_path):
        """A dependency with no .pc of its own is inlined, not dropped.

        Header-only packages (glm is the motivating case) can't appear in
        Requires:, so their include dirs have to land in Cflags or anything
        compiling against us fails on a missing header.
        """
        from pcons.packages.description import PackageDescription
        from pcons.packages.imported import ImportedTarget

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        glm = ImportedTarget.from_package(
            PackageDescription(name="glm", include_dirs=["/opt/glm/include"])
        )

        target = Target("mylib")
        target.public.link_libs.append(glm)

        pc = project.generate_pc_file(target, version="1.0")
        content = pc.read_text()
        # Rendered through Path, so separators match the host (see
        # test_pc_file_external_include_kept_absolute).
        assert f"-I{Path('/opt/glm/include')}" in content
        assert "Requires:" not in content

    def test_pc_file_inlines_transitive_dependency(self, tmp_path):
        """Usage requirements are collected across the whole closure."""
        from pcons.packages.description import PackageDescription
        from pcons.packages.imported import ImportedTarget

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        deep = ImportedTarget.from_package(
            PackageDescription(name="deep", include_dirs=["/opt/deep/include"])
        )
        middle = ImportedTarget.from_package(PackageDescription(name="middle"))
        middle.link(deep)

        target = Target("mylib")
        target.public.link_libs.append(middle)

        pc = project.generate_pc_file(target, version="1.0")
        assert f"-I{Path('/opt/deep/include')}" in pc.read_text()

    def test_pc_file_names_sibling_library_dependency(self, tmp_path):
        """A sibling library in the same project is linked by name."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        base = Target("mxbase", target_type="shared_library")
        base.public.include_dirs.append(Path("include"))

        target = Target("mxgl")
        target.public.link_libs.append(base)

        pc = project.generate_pc_file(target, version="1.0")
        content = pc.read_text()
        assert "-lmxgl" in content
        assert "-lmxbase" in content

    def test_pc_file_skips_target_entries_in_link_libs(self, tmp_path):
        """Target objects in link_libs never leak into Libs: as -l<repr>."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        dep = Target("dep", target_type="shared_library")
        target = Target("mylib")
        target.public.link_libs.append(dep)

        content = project.generate_pc_file(target, version="1.0").read_text()
        assert "Target" not in content
        assert "object at" not in content


class TestAlias:
    def test_alias_accepts_list(self, tmp_path):
        """Alias() should accept a list of targets, not just varargs."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        t1 = Target("lib1")
        t2 = Target("lib2")

        alias = project.Alias("install", [t1, t2])
        assert len(alias._target_refs) == 2

    def test_alias_varargs_still_works(self, tmp_path):
        """Alias() should still accept varargs."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        t1 = Target("lib1")
        t2 = Target("lib2")

        alias = project.Alias("install", t1, t2)
        assert len(alias._target_refs) == 2

    def test_alias_raises_type_error_for_invalid_target(self, tmp_path):
        """Alias() should raise TypeError for invalid targets."""
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        invalid_target = 42

        with pytest.raises(TypeError):
            project.Alias("install", invalid_target)


class TestChildNodeIndex:
    """get_child_nodes/has_child_nodes answer from an index, not a scan.

    The contract the index has to honor: no filesystem access, build-dir-
    prefixed and unprefixed query paths agree, and a node equal to the query
    path is not its own child.
    """

    def test_nodes_registered_after_a_query_are_found(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")

        assert project.get_child_nodes("build/gen") == []
        node = project.node("build/gen/later.c")

        assert project.get_child_nodes("build/gen") == [node]

    def test_query_matches_with_and_without_build_dir_prefix(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")
        node = project.node("build/gen/thing.c")

        assert project.get_child_nodes("build/gen") == [node]
        assert project.get_child_nodes("gen") == [node]

    def test_node_equal_to_the_query_path_is_not_a_child(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")
        project.node("build/gen")

        assert project.get_child_nodes("build/gen") == []
        assert not project.has_child_nodes("build/gen")

    def test_grandchildren_count(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")
        deep = project.node("build/a/b/c/deep.c")

        assert project.get_child_nodes("build/a") == [deep]
        assert project.has_child_nodes("build/a")

    def test_external_absolute_paths(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="build")
        outside = project.node("/opt/vendor/lib/thing.a")

        assert project.get_child_nodes("/opt/vendor") == [outside]
        assert project.get_child_nodes("/opt/other") == []

    def test_has_child_nodes_counts_directory_nodes(self, tmp_path):
        """has_child_nodes asks "is anything registered under here", so a
        DirNode counts even though get_child_nodes returns FileNodes only."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        project.dir_node("build/assets/images")

        assert project.has_child_nodes("build/assets")
        assert project.get_child_nodes("build/assets") == []

    def test_directly_inserted_registry_entries_are_indexed(self, tmp_path):
        """The repair path: a build script may write project._nodes itself
        (examples/15_custom_builder does), bypassing node()."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        project.node("build/gen/first.c")

        smuggled = FileNode(Path("build/gen/smuggled.c"))
        project._nodes[Path("build/gen/smuggled.c")] = smuggled

        assert smuggled in project.get_child_nodes("build/gen")

    def test_index_survives_build_dir_reassignment(self, tmp_path):
        """build_dir is a public attribute and normalization depends on it."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        node = project.node("build/gen/thing.c")
        assert project.get_child_nodes("gen") == [node]

        project.build_dir = Path("other")

        # "build/gen" is no longer stripped, so it is a plain path now.
        assert project.get_child_nodes("build/gen") == [node]
        assert project.get_child_nodes("gen") == []

# SPDX-License-Identifier: MIT
"""Tests for pcons.core.target."""

from pathlib import Path

import pytest

from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import (
    ImportedTarget,
    Target,
    UsageRequirements,
    is_qualified_name,
    split_qualified_name,
)


class TestUsageRequirements:
    def test_creation(self):
        req = UsageRequirements()
        assert req.include_dirs == []
        assert req.link_libs == []
        assert req.defines == []

    def test_with_values(self):
        req = UsageRequirements(
            include_dirs=[Path("include")],
            link_libs=["foo"],
            defines=["DEBUG"],
        )
        assert req.include_dirs == [Path("include")]
        assert req.link_libs == ["foo"]
        assert req.defines == ["DEBUG"]

    def test_merge(self):
        req1 = UsageRequirements(
            include_dirs=[Path("inc1")],
            defines=["DEF1"],
        )
        req2 = UsageRequirements(
            include_dirs=[Path("inc2")],
            defines=["DEF2"],
        )
        req1.merge(req2)

        assert req1.include_dirs == [Path("inc1"), Path("inc2")]
        assert req1.defines == ["DEF1", "DEF2"]

    def test_merge_avoids_duplicates(self):
        req1 = UsageRequirements(
            include_dirs=[Path("inc")],
            defines=["DEF"],
        )
        req2 = UsageRequirements(
            include_dirs=[Path("inc")],  # Same
            defines=["DEF"],  # Same
        )
        req1.merge(req2)

        assert req1.include_dirs == [Path("inc")]
        assert req1.defines == ["DEF"]

    def test_clone(self):
        req = UsageRequirements(
            include_dirs=[Path("inc")],
            link_libs=["foo"],
        )
        clone = req.clone()

        assert clone.include_dirs == req.include_dirs
        assert clone.link_libs == req.link_libs

        # Modifying clone doesn't affect original
        clone.include_dirs.append(Path("other"))
        assert Path("other") not in req.include_dirs


class TestQualifiedName:
    def test_qualified_name(self):
        assert is_qualified_name("project::target")
        assert not is_qualified_name("target")
        assert not is_qualified_name("this::is::invalid")  # Only one '::' allowed
        with pytest.raises(ValueError):
            split_qualified_name("this::is::invalid")

        p, n = split_qualified_name("project::target")
        assert p == "project"
        assert n == "target"

        p, n = split_qualified_name("target")
        assert p is None
        assert n == "target"

    def test_qualified_name_property(self, test_project):  # noqa: F811
        target = Target("mylib")
        assert target.qualified_name == "test_project::mylib"


class TestTarget:
    def test_creation(self, test_project):  # noqa: F811
        target = Target("mylib")
        assert target.name == "mylib"
        assert target.nodes == []
        assert target.sources == []
        assert target.dependencies == []

    def test_tracks_source_location(self, test_project):  # noqa: F811
        target = Target("mylib")
        assert target.defined_at is not None
        assert target.defined_at.lineno > 0

    def test_link_adds_dependency(self, test_project):  # noqa: F811
        lib1 = Target("lib1")
        lib2 = Target("lib2")
        app = Target("app")

        app.link(lib1)
        app.link(lib2)

        assert lib1 in app.dependencies
        assert lib2 in app.dependencies

    def test_link_avoids_duplicates(self, test_project):  # noqa: F811
        lib = Target("lib")
        app = Target("app")

        app.link(lib)
        app.link(lib)  # Same lib again

        assert app.dependencies.count(lib) == 1

    def test_usage_requirements(self, test_project):  # noqa: F811
        lib = Target("lib")
        lib.public.include_dirs.append(Path("include"))
        lib.public.defines.append("LIB_API")
        lib.private.defines.append("LIB_BUILDING")

        assert lib.public.include_dirs == [Path("include")]
        assert lib.public.defines == ["LIB_API"]
        assert lib.private.defines == ["LIB_BUILDING"]

    def test_collect_usage_requirements(self, test_project):  # noqa: F811
        """Test transitive requirement collection."""
        # Create a dependency chain: app -> libB -> libA
        libA = Target("libA")
        libA.public.include_dirs.append(Path("libA/include"))
        libA.public.defines.append("LIBA_API")

        libB = Target("libB")
        libB.public.include_dirs.append(Path("libB/include"))
        libB.link(libA)

        app = Target("app")
        app.private.defines.append("APP_PRIVATE")
        app.link(libB)

        requirements = app.collect_usage_requirements()

        # Should have app's private, plus libB and libA's public
        assert Path("libA/include") in requirements.include_dirs
        assert Path("libB/include") in requirements.include_dirs
        assert "LIBA_API" in requirements.defines
        assert "APP_PRIVATE" in requirements.defines

    def test_collect_usage_requirements_cached(self, test_project):  # noqa: F811
        """Test that collection is cached."""
        lib = Target("lib")
        app = Target("app")
        app.link(lib)

        req1 = app.collect_usage_requirements()
        req2 = app.collect_usage_requirements()

        assert req1 is req2  # Same object (cached)

    def test_collect_usage_requirements_invalidated(self, test_project):  # noqa: F811
        """Test that cache is invalidated on new link."""
        lib1 = Target("lib1")
        lib2 = Target("lib2")
        lib2.public.defines.append("LIB2")
        app = Target("app")
        app.link(lib1)

        req1 = app.collect_usage_requirements()
        assert "LIB2" not in req1.defines

        app.link(lib2)
        req2 = app.collect_usage_requirements()

        assert req2 is not req1
        assert "LIB2" in req2.defines

    def test_get_all_languages(self, test_project):  # noqa: F811
        lib = Target("lib")
        lib.required_languages.add("c")

        app = Target("app")
        app.required_languages.add("cxx")
        app.link(lib)

        langs = app.get_all_languages()
        assert "c" in langs
        assert "cxx" in langs

    def test_equality_by_name(self, test_project):  # noqa: F811
        t1 = Target("mylib")
        t1.name = "fake"
        t2 = Target("mylib")
        t1.name = "mylib"  # Reset to original name for equality
        t3 = Target("other")

        assert t1 == t2
        assert t1 != t3

    def test_hashable(self, test_project):  # noqa: F811
        t1 = Target("mylib")
        t1.name = "fake"
        t2 = Target("mylib")
        t1.name = "mylib"  # Reset to original name for hashing

        targets = {t1, t2}
        assert len(targets) == 1  # Same name = same target

    def test_target_without_project(self):
        """Test that Target can be created without an active project."""
        with pytest.raises(ValueError):
            Target("orphan")


class TestImportedTarget:
    def test_creation(self, test_project):  # noqa: F811
        target = ImportedTarget("zlib", version="1.2.11")
        assert target.name == "zlib"
        assert target.is_imported is True
        assert target.package_name == "zlib"
        assert target.version == "1.2.11"

    def test_can_have_usage_requirements(self, test_project):  # noqa: F811
        target = ImportedTarget("zlib")
        target.public.include_dirs.append(Path("/usr/include"))
        target.public.link_libs.append("z")

        assert target.public.include_dirs == [Path("/usr/include")]
        assert target.public.link_libs == ["z"]

    def test_can_be_dependency(self, test_project):  # noqa: F811
        zlib = ImportedTarget("zlib")
        zlib.public.link_libs.append("z")

        app = Target("app")
        app.link(zlib)

        requirements = app.collect_usage_requirements()
        assert "z" in requirements.link_libs


class TestFluentAPI:
    """Tests for fluent API methods."""

    def test_link_returns_self(self, test_project):  # noqa: F811
        """link() returns self for chaining."""
        lib = Target("lib")
        app = Target("app")

        result = app.link(lib)

        assert result is app
        assert lib in app.dependencies

    def test_add_source_returns_self(self, tmp_path, test_project):  # noqa: F811
        """add_source() returns self for chaining."""
        target = Target("app")
        src = tmp_path / "main.c"
        src.touch()

        result = target.add_source(src)

        assert result is target
        assert len(target.sources) == 1

    def test_add_sources_returns_self(self, tmp_path, test_project):  # noqa: F811
        """add_sources() returns self for chaining."""
        target = Target("app")
        src1 = tmp_path / "main.c"
        src2 = tmp_path / "util.c"
        src1.touch()
        src2.touch()

        result = target.add_sources([src1, src2])

        assert result is target
        assert len(target.sources) == 2

    def test_add_sources_with_base(self, test_project):
        """add_sources() with base directory works."""
        target = Target("app")
        src_dir = test_project.root_dir / "src"
        src_dir.mkdir()
        (src_dir / "main.c").touch()
        (src_dir / "util.c").touch()

        target.add_sources(["main.c", "util.c"], base=src_dir)

        assert len(target.sources) == 2
        # Verify paths are resolved correctly
        paths = [n.path for n in target.sources if isinstance(n, FileNode)]
        rel_src_dir = src_dir.relative_to(test_project.root_dir)
        assert rel_src_dir / "main.c" in paths
        assert rel_src_dir / "util.c" in paths

    def test_public_private_requirements(self, test_project):  # noqa: F811
        """Usage requirements can be set directly on public/private."""
        target = Target("lib")

        target.public.include_dirs.append(Path("include"))
        target.public.defines.extend(["FOO", "BAR=1"])
        target.private.include_dirs.append(Path("src"))
        target.private.defines.append("BUILDING_LIB")

        assert Path("include") in target.public.include_dirs
        assert "FOO" in target.public.defines
        assert "BAR=1" in target.public.defines
        assert Path("src") in target.private.include_dirs
        assert "BUILDING_LIB" in target.private.defines

    def test_link_chain(self, tmp_path, test_project):  # noqa: F811
        """link() can be chained with other fluent methods."""
        lib = Target("lib")
        app = Target("app")
        src = tmp_path / "main.c"
        src.touch()

        result = app.add_source(src).link(lib)

        assert result is app
        assert len(app.sources) == 1
        assert lib in app.dependencies


class TestPostBuild:
    """Tests for post_build() functionality."""

    def test_post_build_adds_command(self, test_project):  # noqa: F811
        """post_build() adds a command to the list."""
        target = Target("app")

        target.post_build("install_name_tool -add_rpath @loader_path $out")

        post_build_cmds = target._builder_data.get("post_build_commands", [])
        assert len(post_build_cmds) == 1
        assert post_build_cmds[0] == "install_name_tool -add_rpath @loader_path $out"

    def test_post_build_fluent_returns_self(self, test_project):  # noqa: F811
        """post_build() returns self for chaining."""
        target = Target("app")

        result = target.post_build("echo done")

        assert result is target

    def test_post_build_multiple_commands(self, test_project):  # noqa: F811
        """Multiple post_build() calls accumulate commands in order."""
        target = Target("plugin")

        target.post_build("install_name_tool -add_rpath @loader_path $out")
        target.post_build("install_name_tool -change /old/path @rpath/lib.dylib $out")
        target.post_build("codesign --sign - $out")

        post_build_cmds = target._builder_data.get("post_build_commands", [])
        assert len(post_build_cmds) == 3
        assert post_build_cmds[0] == "install_name_tool -add_rpath @loader_path $out"
        assert (
            post_build_cmds[1]
            == "install_name_tool -change /old/path @rpath/lib.dylib $out"
        )
        assert post_build_cmds[2] == "codesign --sign - $out"

    def test_post_build_chain_with_other_methods(self, tmp_path, test_project):  # noqa: F811
        """post_build() can be chained with other fluent methods."""
        target = Target("app")
        src = tmp_path / "main.c"
        src.touch()

        result = target.add_source(src).post_build("chmod +x $out")
        target.private.defines.append("DEBUG")

        assert result is target
        assert len(target.sources) == 1
        post_build_cmds = target._builder_data.get("post_build_commands", [])
        assert len(post_build_cmds) == 1
        assert "DEBUG" in target.private.defines

    def test_post_build_empty_by_default(self, test_project):  # noqa: F811
        """Target has no post_build commands by default."""
        target = Target("app")

        post_build_cmds = target._builder_data.get("post_build_commands", [])
        assert post_build_cmds == []


class TestTargetDepends:
    """Tests for target.depends() implicit dependency support."""

    def test_depends_with_file_node(self, test_project):  # noqa: F811
        """depends() accepts FileNode objects."""
        target = Target("app")
        dep = FileNode("tools/codegen.py")

        target.depends(dep)

        assert dep in target._extra_implicit_deps

    def test_depends_with_string_no_project(self, test_project):  # noqa: F811
        """depends() with string creates FileNode when no project."""
        target = Target("app")

        target.depends("tools/codegen.py")

        assert len(target._extra_implicit_deps) == 1
        assert target._extra_implicit_deps[0].path == Path("tools/codegen.py")

    def test_depends_with_target(self, test_project):  # noqa: F811
        """depends() with Target adds to implicit target deps, not link deps."""
        target = Target("app")
        lib = Target("mylib")

        target.depends(lib)

        assert lib in target._implicit_target_deps
        assert len(target.dependencies) == 0
        assert len(target._extra_implicit_deps) == 0

    def test_depends_mixed_args(self, test_project):  # noqa: F811
        """depends() handles mixed Target and file args."""
        target = Target("app")
        lib = Target("mylib")
        config = FileNode("config.yaml")

        target.depends(lib, config, "tools/script.py")

        assert lib in target._implicit_target_deps
        assert lib not in target.dependencies
        assert config in target._extra_implicit_deps
        assert len(target._extra_implicit_deps) == 2

    def test_depends_fluent(self, test_project):  # noqa: F811
        """depends() returns self for chaining."""
        target = Target("app")

        result = target.depends("a.txt").depends("b.txt")

        assert result is target
        assert len(target._extra_implicit_deps) == 2

    def test_depends_applied_during_resolve(self, tmp_path):
        """depends() deps are applied to output nodes during resolve."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        cmd = env.Command(
            target="output.txt",
            source="input.txt",
            command="tool $SOURCE $TARGET",
        )
        cmd.depends("tools/codegen.py")

        # Before resolve, output nodes don't have the implicit dep yet
        assert len(cmd.output_nodes[0].implicit_deps) == 0

        project.resolve()

        # After resolve, the dep is on the output node
        assert len(cmd.output_nodes[0].implicit_deps) == 1

    def test_apply_extra_implicit_deps_propagated(self, test_project):  # noqa: F811
        """Propagated deps go on both object nodes and output nodes."""
        target = Target("app")
        obj = FileNode("build/main.o")
        exe = FileNode("build/app")
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(exe)
        dep = FileNode("version.h")
        target._extra_implicit_deps.append(dep)

        target._apply_extra_implicit_deps()

        assert dep in obj.implicit_deps
        assert dep in exe.implicit_deps

    def test_apply_extra_implicit_deps_output_only(self, test_project):  # noqa: F811
        """Output-only deps go on output nodes but not object nodes."""
        target = Target("app")
        obj = FileNode("build/main.o")
        exe = FileNode("build/app")
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(exe)
        dep = FileNode("data.bin")
        target._extra_implicit_deps_output_only.append(dep)

        target._apply_extra_implicit_deps()

        assert dep not in obj.implicit_deps
        assert dep in exe.implicit_deps

    def test_depends_propagate_false(self, test_project):  # noqa: F811
        """depends(propagate=False) stores in output-only lists."""
        target = Target("app")
        lib = Target("mylib")

        target.depends(lib, "config.yaml", propagate=False)

        assert lib in target._implicit_target_deps_output_only
        assert lib not in target._implicit_target_deps
        assert len(target._extra_implicit_deps) == 0
        assert len(target._extra_implicit_deps_output_only) == 1

    def test_apply_no_duplicates(self, test_project):  # noqa: F811
        """_apply_extra_implicit_deps doesn't add duplicates."""
        target = Target("app")
        output = FileNode("build/app")
        target.output_nodes.append(output)
        dep = FileNode("version.txt")
        target._extra_implicit_deps.append(dep)

        target._apply_extra_implicit_deps()
        target._apply_extra_implicit_deps()  # Apply twice

        assert output.implicit_deps.count(dep) == 1

    def test_depends_with_project(self, tmp_path):
        """depends() uses project.node() when project is available."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        target = Target("app")

        target.depends("tools/codegen.py")

        dep = target._extra_implicit_deps[0]
        # project.node() canonicalizes the path
        assert dep is project.node("tools/codegen.py")


class TestTargetSubdir:
    def test_directories(self):
        root = Path.cwd().resolve()
        project = Project("test_project", root_dir=root, build_dir="/build")
        with project._enter_subdir("lib"):
            target = Target("mylib")

        assert target.source_dir == (root / "lib")
        assert target.build_dir.as_posix() == Path("/build/lib").as_posix()

        assert target.qualified_name == "test_project::mylib"
        assert project.get_target("test_project::mylib") == target
        assert project.get_target("mylib") == target  # Unqualified lookup should work

    def test_collision(self, test_project):
        with test_project._enter_subdir("lib1"):
            Project("sub1", root_dir=test_project.root_dir / "lib1")
            target1 = Target("mylib")
        with test_project._enter_subdir("lib2"):
            Project("sub2", root_dir=test_project.root_dir / "lib2")
            target2 = Target("mylib")

        assert target1 is not target2
        assert target1.qualified_name == "sub1::mylib"
        assert target2.qualified_name == "sub2::mylib"

        assert test_project.get_target("sub1::mylib") == target1
        assert test_project.get_target("sub2::mylib") == target2

        with pytest.raises(KeyError):
            # Unqualified lookup should fail due to collision
            test_project.get_target("mylib")

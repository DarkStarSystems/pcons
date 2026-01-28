# SPDX-License-Identifier: MIT
"""Tests for pcons.core.builder."""

from pathlib import Path

from pcons.core.builder import BaseBuilder, Builder, CommandBuilder
from pcons.core.environment import Environment
from pcons.core.node import FileNode, Node


class TestBuilderProtocol:
    def test_command_builder_is_builder(self):
        builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )
        assert isinstance(builder, Builder)


class TestBaseBuilder:
    def test_properties(self):
        class TestBuilder(BaseBuilder):
            def _build(self, env, targets, sources, **kwargs):
                return []

            def default_vars(self):
                return {}

        builder = TestBuilder(
            "Object",
            "cc",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
        )

        assert builder.name == "Object"
        assert builder.tool_name == "cc"
        assert builder.src_suffixes == [".c"]
        assert builder.target_suffixes == [".o"]
        assert builder.language == "c"

    def test_normalize_sources(self):
        class TestBuilder(BaseBuilder):
            def _build(self, env, targets, sources, **kwargs):
                return sources

        builder = TestBuilder("Test", "test", target_suffixes=[".out"])
        env = Environment()

        # Mix of strings, Paths, and Nodes
        sources = ["file.c", Path("other.c"), FileNode("third.c")]
        result = builder(env, "out.o", sources)

        assert len(result) == 3
        assert all(isinstance(n, Node) for n in result)


class TestCommandBuilder:
    def test_creation(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
        )
        assert builder.name == "Object"
        assert builder.tool_name == "cc"

    def test_single_source_mode(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmdline = "$cc.cmd -c -o $TARGET $SOURCE"
        env.cc.cmd = "gcc"

        result = builder(env, None, ["a.c", "b.c"])

        # Should create two targets (one per source)
        assert len(result) == 2
        assert all(isinstance(n, FileNode) for n in result)

    def test_multi_source_mode(self):
        builder = CommandBuilder(
            "Program",
            "link",
            "cmdline",
            src_suffixes=[".o"],
            target_suffixes=[""],
            single_source=False,
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmdline = "$link.cmd -o $TARGET $SOURCES"
        env.link.cmd = "gcc"

        result = builder(env, "app", ["a.o", "b.o"])

        # Should create one target with multiple sources
        assert len(result) == 1
        assert isinstance(result[0], FileNode)
        assert result[0].path == Path("app")

    def test_default_target_path(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.build_dir = Path("out")

        result = builder(env, None, ["src/foo.c"])

        # Default target should be in build_dir with .o suffix
        assert len(result) == 1
        assert isinstance(result[0], FileNode)
        assert result[0].path == Path("out/foo.o")

    def test_explicit_target_path(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        result = builder(env, "custom/output.o", ["foo.c"])

        assert len(result) == 1
        assert isinstance(result[0], FileNode)
        assert result[0].path == Path("custom/output.o")

    def test_target_has_dependencies(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        source = FileNode("foo.c")
        result = builder(env, "foo.o", [source])

        assert len(result) == 1
        target = result[0]
        assert source in target.explicit_deps

    def test_target_has_builder_reference(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        result = builder(env, "foo.o", ["foo.c"])
        target = result[0]

        assert target.builder is builder
        assert target.is_target
        assert not target.is_source

    def test_target_has_build_info(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        result = builder(env, "foo.o", ["foo.c"])
        target = result[0]

        # Check build info is stored
        assert isinstance(target, FileNode)
        assert target._build_info is not None
        info = target._build_info
        assert info["tool"] == "cc"
        assert info["command_var"] == "cmdline"
        assert info["language"] == "c"


class TestCommandBuilderDepfile:
    def test_depfile_and_deps_style_stored(self):
        from pcons.core.subst import PathToken, TargetPath

        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            language="c",
            single_source=True,
            depfile=TargetPath(suffix=".d"),
            deps_style="gcc",
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        result = builder(env, "foo.o", ["foo.c"])
        target = result[0]

        assert isinstance(target, FileNode)
        assert target._build_info is not None
        info = target._build_info
        # depfile is resolved to PathToken with the target path
        assert isinstance(info["depfile"], PathToken)
        assert info["depfile"].suffix == ".d"
        assert info["depfile"].path == "foo.o"
        assert info["deps_style"] == "gcc"

    def test_msvc_deps_style_no_depfile(self):
        builder = CommandBuilder(
            "Object",
            "cc",
            "cmdline",
            src_suffixes=[".c"],
            target_suffixes=[".obj"],
            language="c",
            single_source=True,
            deps_style="msvc",
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "cl.exe"

        result = builder(env, "foo.obj", ["foo.c"])
        target = result[0]

        assert isinstance(target, FileNode)
        assert target._build_info is not None
        info = target._build_info
        assert info["depfile"] is None
        assert info["deps_style"] == "msvc"

    def test_no_deps_by_default(self):
        builder = CommandBuilder(
            "Program",
            "link",
            "cmdline",
            src_suffixes=[".o"],
            target_suffixes=[""],
            single_source=False,
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "gcc"

        result = builder(env, "app", ["a.o", "b.o"])
        target = result[0]

        assert isinstance(target, FileNode)
        assert target._build_info is not None
        info = target._build_info
        assert info["depfile"] is None
        assert info["deps_style"] is None

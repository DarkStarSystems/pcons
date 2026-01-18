# SPDX-License-Identifier: MIT
"""Tests for env.Command() functionality."""

from pathlib import Path

import pytest

from pcons.core.builder import GenericCommandBuilder
from pcons.core.environment import Environment
from pcons.core.node import FileNode


class TestGenericCommandBuilder:
    """Tests for GenericCommandBuilder class."""

    def test_creation_with_string_command(self):
        """Builder can be created with a string command."""
        builder = GenericCommandBuilder("echo hello > $TARGET")
        assert builder.name == "Command"
        assert builder.tool_name == "command"
        assert builder.command == "echo hello > $TARGET"

    def test_creation_with_list_command(self):
        """Builder can be created with a list command."""
        builder = GenericCommandBuilder(["python", "script.py", "$SOURCE", "$TARGET"])
        assert builder.command == "python script.py $SOURCE $TARGET"

    def test_unique_rule_names(self):
        """Each builder gets a unique rule name."""
        builder1 = GenericCommandBuilder("cmd1")
        builder2 = GenericCommandBuilder("cmd2")
        assert builder1.rule_name != builder2.rule_name

    def test_custom_rule_name(self):
        """Builder can have a custom rule name."""
        builder = GenericCommandBuilder("cmd", rule_name="my_custom_rule")
        assert builder.rule_name == "my_custom_rule"

    def test_requires_explicit_target(self):
        """Builder raises error if no target is provided."""
        builder = GenericCommandBuilder("echo hello")
        env = Environment()
        with pytest.raises(ValueError, match="requires explicit target"):
            builder(env, None, ["source.txt"])

    def test_creates_target_node(self):
        """Builder creates target node with proper dependencies."""
        builder = GenericCommandBuilder("cp $SOURCE $TARGET")
        env = Environment()

        result = builder(env, "output.txt", ["input.txt"])

        assert len(result) == 1
        assert isinstance(result[0], FileNode)
        assert result[0].path == Path("output.txt")
        assert result[0].builder is builder

    def test_target_depends_on_sources(self):
        """Target node depends on all sources."""
        builder = GenericCommandBuilder("cat $SOURCES > $TARGET")
        env = Environment()

        source1 = FileNode("a.txt")
        source2 = FileNode("b.txt")
        result = builder(env, "combined.txt", [source1, source2])

        target = result[0]
        assert source1 in target.explicit_deps
        assert source2 in target.explicit_deps

    def test_build_info_contains_command(self):
        """Target node contains build info with command."""
        builder = GenericCommandBuilder("process $SOURCE > $TARGET")
        env = Environment()

        result = builder(env, "out.txt", ["in.txt"])
        target = result[0]

        assert hasattr(target, "_build_info")
        assert target._build_info["tool"] == "command"
        assert target._build_info["command"] == "process $SOURCE > $TARGET"
        assert target._build_info["rule_name"] == builder.rule_name


class TestEnvironmentCommand:
    """Tests for Environment.Command() method."""

    def test_command_with_single_target_and_source(self):
        """Command with single target and source."""
        env = Environment()

        nodes = env.Command("output.txt", "input.txt", "cp $SOURCE $TARGET")

        assert len(nodes) == 1
        assert nodes[0].path == Path("output.txt")

    def test_command_with_multiple_sources(self):
        """Command with multiple sources."""
        env = Environment()

        nodes = env.Command(
            "combined.txt", ["a.txt", "b.txt", "c.txt"], "cat $SOURCES > $TARGET"
        )

        assert len(nodes) == 1
        target = nodes[0]
        assert len(target.explicit_deps) == 3

    def test_command_with_multiple_targets(self):
        """Command with multiple targets."""
        env = Environment()

        nodes = env.Command(
            ["output.h", "output.c"], "input.y", "bison -d -o ${TARGETS[0]} $SOURCE"
        )

        assert len(nodes) == 2
        paths = [n.path for n in nodes]
        assert Path("output.h") in paths
        assert Path("output.c") in paths

    def test_command_with_no_sources(self):
        """Command with no source dependencies."""
        env = Environment()

        nodes = env.Command("timestamp.txt", None, "date > $TARGET")

        assert len(nodes) == 1
        assert len(nodes[0].explicit_deps) == 0

    def test_command_with_path_objects(self):
        """Command accepts Path objects."""
        env = Environment()

        nodes = env.Command(
            Path("build/output.txt"),
            [Path("src/input.txt")],
            "process $SOURCE > $TARGET",
        )

        assert len(nodes) == 1
        assert nodes[0].path == Path("build/output.txt")

    def test_command_registers_nodes(self):
        """Command registers nodes with environment."""
        env = Environment()

        nodes = env.Command("out.txt", "in.txt", "cmd")

        assert nodes[0] in env.created_nodes

    def test_command_returns_file_nodes(self):
        """Command returns only FileNode objects."""
        env = Environment()

        nodes = env.Command(["a.txt", "b.txt"], "source.txt", "split $SOURCE")

        assert all(isinstance(n, FileNode) for n in nodes)


class TestGenericCommandNinja:
    """Tests for Ninja generation of generic commands."""

    def test_generates_rule_for_command(self, tmp_path):
        """Ninja generator creates rule for command."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("out.txt", "in.txt", "process $SOURCE > $TARGET")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        # Should have a command rule
        assert "rule command_" in content
        # Should have the actual command with $in/$out
        assert "process $in > $out" in content

    def test_generates_build_statement(self, tmp_path):
        """Ninja generator creates build statement."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("output.txt", "input.txt", "cp $SOURCE $TARGET")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "build output.txt:" in content
        assert "input.txt" in content

    def test_handles_multiple_sources(self, tmp_path):
        """Ninja generator handles multiple sources."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("out.txt", ["a.txt", "b.txt"], "cat $SOURCES > $TARGET")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        # Build statement should list all sources
        assert "a.txt" in content
        assert "b.txt" in content

    def test_handles_multiple_targets(self, tmp_path):
        """Ninja generator handles multiple targets."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command(["out.c", "out.h"], "grammar.y", "bison -d $SOURCE")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        # Build statement should list multiple outputs
        assert "out.c" in content
        assert "out.h" in content

    def test_converts_source_variable(self, tmp_path):
        """$SOURCE is converted to $in."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("out.txt", "in.txt", "process $SOURCE")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "process $in" in content
        # Original $SOURCE should not appear
        assert "$SOURCE" not in content

    def test_converts_target_variable(self, tmp_path):
        """$TARGET is converted to $out."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("out.txt", "in.txt", "process > $TARGET")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "> $out" in content
        # Original $TARGET should not appear
        assert "$TARGET" not in content

    def test_converts_sources_variable(self, tmp_path):
        """$SOURCES is converted to $in."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command("out.txt", ["a.txt", "b.txt"], "cat $SOURCES > $TARGET")

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "cat $in > $out" in content

    def test_converts_indexed_source(self, tmp_path):
        """${SOURCES[n]} is converted to $source_n."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command(
            "out.txt",
            ["first.txt", "second.txt"],
            "diff ${SOURCES[0]} ${SOURCES[1]} > $TARGET",
        )

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "$source_0" in content
        assert "$source_1" in content
        # Should have indexed source variables
        assert "source_0 = " in content
        assert "source_1 = " in content

    def test_converts_indexed_target(self, tmp_path):
        """${TARGETS[n]} is converted to $target_n."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path)
        env = project.Environment()

        env.Command(
            ["out.c", "out.h"],
            "grammar.y",
            "bison -o ${TARGETS[0]} -H ${TARGETS[1]} $SOURCE",
        )

        gen = NinjaGenerator()
        gen.generate(project, tmp_path)

        content = (tmp_path / "build.ninja").read_text()
        assert "$target_0" in content
        assert "$target_1" in content
        # Should have indexed target variables
        assert "target_0 = " in content
        assert "target_1 = " in content


class TestCommandVariableConversion:
    """Tests for _convert_command_variables helper."""

    def test_convert_source(self):
        """Converts $SOURCE to $in."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("process $SOURCE")
        assert result == "process $in"

    def test_convert_target(self):
        """Converts $TARGET to $out."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("process > $TARGET")
        assert result == "process > $out"

    def test_convert_sources(self):
        """Converts $SOURCES to $in."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("cat $SOURCES")
        assert result == "cat $in"

    def test_convert_targets(self):
        """Converts $TARGETS to $out."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("touch $TARGETS")
        assert result == "touch $out"

    def test_convert_indexed_source(self):
        """Converts ${SOURCES[n]} to $source_n."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("diff ${SOURCES[0]} ${SOURCES[1]}")
        assert result == "diff $source_0 $source_1"

    def test_convert_indexed_target(self):
        """Converts ${TARGETS[n]} to $target_n."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("gen ${TARGETS[0]} ${TARGETS[1]}")
        assert result == "gen $target_0 $target_1"

    def test_convert_mixed(self):
        """Converts mixed variables."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables(
            "process ${SOURCES[0]} $SOURCE $TARGET ${TARGETS[1]}"
        )
        assert result == "process $source_0 $in $out $target_1"

    def test_preserves_other_variables(self):
        """Preserves other $ variables."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        result = gen._convert_command_variables("echo $HOME $SOURCE > $TARGET")
        assert result == "echo $HOME $in > $out"

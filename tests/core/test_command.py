# SPDX-License-Identifier: MIT
"""Tests for env.Command() functionality."""

from pathlib import Path

import pytest

from pcons.core.builder import GenericCommandBuilder
from pcons.core.environment import Environment
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator


class TestGenericCommandBuilder:
    """Tests for GenericCommandBuilder class."""

    def test_creation_with_string_command(self):
        """Builder can be created with a string command."""
        from pcons.core.subst import TargetPath

        builder = GenericCommandBuilder("echo hello > $TARGET")
        assert builder.name == "Command"
        assert builder.tool_name == "command"
        # Command is tokenized with $TARGET converted to TargetPath()
        assert builder.command == ["echo", "hello", ">", TargetPath()]

    def test_creation_with_list_command(self):
        """Builder can be created with a list command."""
        from pcons.core.subst import SourcePath, TargetPath

        builder = GenericCommandBuilder(["python", "script.py", "$SOURCE", "$TARGET"])
        # $SOURCE and $TARGET are converted to typed markers
        assert builder.command == ["python", "script.py", SourcePath(), TargetPath()]

    def test_unique_rule_names(self, test_project):  # noqa: F811
        """Each builder gets a unique rule name."""
        builder1 = GenericCommandBuilder("cmd1")
        builder2 = GenericCommandBuilder("cmd2")
        assert builder1.rule_name != builder2.rule_name

    def test_custom_rule_name(self, test_project):  # noqa: F811
        """Builder can have a custom rule name."""
        builder = GenericCommandBuilder("cmd", rule_name="my_custom_rule")
        assert builder.rule_name == "my_custom_rule"

    def test_requires_explicit_target(self, test_project):  # noqa: F811
        """Builder raises error if no target is provided."""
        builder = GenericCommandBuilder("echo hello")
        env = Environment()
        with pytest.raises(ValueError, match="requires explicit target"):
            builder(env, None, ["source.txt"])

    def test_creates_target_node(self, test_project):  # noqa: F811
        """Builder creates target node with proper dependencies."""
        builder = GenericCommandBuilder("cp $SOURCE $TARGET")
        env = Environment()

        result = builder(env, "output.txt", ["input.txt"])

        assert len(result) == 1
        assert isinstance(result[0], FileNode)
        assert result[0].path == Path("output.txt")
        assert result[0].builder is builder

    def test_target_depends_on_sources(self, test_project):  # noqa: F811
        """Target node depends on all sources."""
        builder = GenericCommandBuilder("cat $SOURCES > $TARGET")
        env = Environment()

        source1 = FileNode("a.txt")
        source2 = FileNode("b.txt")
        result = builder(env, "combined.txt", [source1, source2])

        target = result[0]
        assert source1 in target.explicit_deps
        assert source2 in target.explicit_deps

    def test_build_info_contains_command(self, test_project):  # noqa: F811
        """Target node contains build info with command."""
        from pcons.core.subst import SourcePath, TargetPath

        builder = GenericCommandBuilder("process $SOURCE > $TARGET")
        env = Environment()

        result = builder(env, "out.txt", ["in.txt"])
        target = result[0]

        assert isinstance(target, FileNode)
        assert target._build_info is not None
        assert target._build_info.get("tool") == "command"
        # Command is tokenized list with markers
        assert target._build_info.get("command") == [
            "process",
            SourcePath(),
            ">",
            TargetPath(),
        ]
        assert target._build_info.get("rule_name") == builder.rule_name

    def test_srcdir_preserved_in_tokens(self, test_project):  # noqa: F811
        """$SRCDIR is preserved as a plain string token (generators handle it)."""
        builder = GenericCommandBuilder("python $SRCDIR/scripts/gen.py $SOURCE $TARGET")
        from pcons.core.subst import SourcePath, TargetPath

        assert builder.command == [
            "python",
            "$SRCDIR/scripts/gen.py",
            SourcePath(),
            TargetPath(),
        ]


class TestEnvironmentCommand:
    """Tests for Environment.Command() method.

    Note: As of v0.2.0, env.Command() returns a Target object instead of
    list[FileNode], and uses keyword-only arguments.
    """

    def test_command_with_single_target_and_source(self, test_project):  # noqa: F811
        """Command with single target and source."""
        env = Environment()

        result = env.Command(
            target="output.txt", source="input.txt", command="cp $SOURCE $TARGET"
        )

        # Returns Target, not list
        from pcons.core.target import Target

        assert isinstance(result, Target)
        assert len(result.output_nodes) == 1
        assert result.output_nodes[0].path == Path("output.txt")

    def test_command_with_multiple_sources(self, test_project):  # noqa: F811
        """Command with multiple sources."""
        env = Environment()

        result = env.Command(
            target="combined.txt",
            source=["a.txt", "b.txt", "c.txt"],
            command="cat $SOURCES > $TARGET",
        )

        assert len(result.output_nodes) == 1
        output_node = result.output_nodes[0]
        assert len(output_node.explicit_deps) == 3

    def test_command_with_multiple_targets(self, test_project):  # noqa: F811
        """Command with multiple targets."""
        env = Environment()

        result = env.Command(
            target=["output.h", "output.c"],
            source="input.y",
            command="bison -d -o ${TARGETS[0]} $SOURCE",
        )

        assert len(result.output_nodes) == 2
        paths = [n.path for n in result.output_nodes]
        assert Path("output.h") in paths
        assert Path("output.c") in paths

    def test_command_with_no_sources(self, test_project):  # noqa: F811
        """Command with no source dependencies."""
        env = Environment()

        result = env.Command(
            target="timestamp.txt", source=None, command="date > $TARGET"
        )

        assert len(result.output_nodes) == 1
        assert len(result.output_nodes[0].explicit_deps) == 0

    def test_command_with_path_objects(self, test_project):  # noqa: F811
        """Command accepts Path objects."""
        env = Environment()

        result = env.Command(
            target=Path("build/output.txt"),
            source=[Path("src/input.txt")],
            command="process $SOURCE > $TARGET",
        )

        assert len(result.output_nodes) == 1
        assert result.output_nodes[0].path == Path("build/output.txt")

    def test_command_registers_nodes(self, test_project):  # noqa: F811
        """Command registers nodes with environment."""
        env = Environment()

        result = env.Command(target="out.txt", source="in.txt", command="cmd")

        assert result.output_nodes[0] in env.created_nodes

    def test_command_returns_target(self, test_project):  # noqa: F811
        """Command returns Target object (not list[FileNode])."""
        env = Environment()

        result = env.Command(
            target=["a.txt", "b.txt"], source="source.txt", command="split $SOURCE"
        )

        from pcons.core.target import Target

        assert isinstance(result, Target)
        assert all(isinstance(n, FileNode) for n in result.output_nodes)

    def test_command_name_derived_from_target(self, test_project):  # noqa: F811
        """Command target name is derived from first target file if not specified."""
        env = Environment()

        result = env.Command(target="my_output.txt", source="in.txt", command="cmd")

        assert result.name == "my_output"

    def test_command_explicit_name(self, test_project):  # noqa: F811
        """Command can have an explicit name."""
        env = Environment()

        result = env.Command(
            target="out.txt", source="in.txt", command="cmd", name="my_custom_name"
        )

        assert result.name == "my_custom_name"


class TestGenericCommandNinja:
    """Tests for Ninja generation of generic commands."""

    def test_generates_rule_for_command(self, tmp_path):
        """Ninja generator creates rule for command."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target="out.txt", source="in.txt", command="process $SOURCE > $TARGET"
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        # Should have a command rule
        assert "rule command_" in content
        # Should have the actual command with $in/$out
        assert "process $in > $out" in content

    def test_generates_build_statement(self, tmp_path):
        """Ninja generator creates build statement."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target="output.txt", source="input.txt", command="cp $SOURCE $TARGET"
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "build output.txt:" in content
        assert "input.txt" in content

    def test_handles_multiple_sources(self, tmp_path):
        """Ninja generator handles multiple sources."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target="out.txt",
            source=["a.txt", "b.txt"],
            command="cat $SOURCES > $TARGET",
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        # Build statement should list all sources
        assert "a.txt" in content
        assert "b.txt" in content

    def test_handles_multiple_targets(self, tmp_path):
        """Ninja generator handles multiple targets."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target=["out.c", "out.h"], source="grammar.y", command="bison -d $SOURCE"
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        # Build statement should list multiple outputs
        assert "out.c" in content
        assert "out.h" in content

    def test_converts_source_variable(self, tmp_path):
        """$SOURCE is converted to $in."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(target="out.txt", source="in.txt", command="process $SOURCE")

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "process $in" in content
        # Original $SOURCE should not appear
        assert "$SOURCE" not in content

    def test_converts_target_variable(self, tmp_path):
        """$TARGET is converted to $out."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(target="out.txt", source="in.txt", command="process > $TARGET")

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "> $out" in content
        # Original $TARGET should not appear
        assert "$TARGET" not in content

    def test_converts_sources_variable(self, tmp_path):
        """$SOURCES is converted to $in."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target="out.txt",
            source=["a.txt", "b.txt"],
            command="cat $SOURCES > $TARGET",
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "cat $in > $out" in content

    def test_converts_indexed_source(self, tmp_path):
        """${SOURCES[n]} is converted to $source_n."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target="out.txt",
            source=["first.txt", "second.txt"],
            command="diff ${SOURCES[0]} ${SOURCES[1]} > $TARGET",
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        env.Command(
            target=["out.c", "out.h"],
            source="grammar.y",
            command="bison -o ${TARGETS[0]} -H ${TARGETS[1]} $SOURCE",
        )

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "$target_0" in content
        assert "$target_1" in content
        # Should have indexed target variables
        assert "target_0 = " in content
        assert "target_1 = " in content


class TestTargetAsSources:
    """Tests for using Targets as sources in builders."""

    def test_add_source_accepts_target(self, tmp_path):
        """Target.add_source() accepts another Target."""
        from pcons.core.project import Project

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create a command target that generates code
        generated = env.Command(
            target="generated.cpp",
            source="generator.y",
            command="yacc -o $TARGET $SOURCE",
        )

        # Create a program target that uses the generated source
        program = project.Program("myapp", env)
        program.add_source(generated)

        # The generated target should be in pending sources
        assert program._pending_sources is not None
        assert generated in program._pending_sources

        # The generated target should also be a dependency
        assert generated in program.dependencies

    def test_add_sources_accepts_targets(self, tmp_path):
        """Target.add_sources() accepts Targets mixed with paths."""
        from pcons.core.project import Project

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create command targets
        gen1 = env.Command(target="gen1.cpp", source="gen1.y", command="cmd1")
        gen2 = env.Command(target="gen2.cpp", source="gen2.y", command="cmd2")

        # Create a program with mixed sources
        program = project.Program("myapp", env)
        program.add_sources([gen1, "main.cpp", gen2])

        # Both generated targets should be in pending sources
        assert program._pending_sources is not None
        assert gen1 in program._pending_sources
        assert gen2 in program._pending_sources

        # main.cpp should be in _sources
        source_paths = [s.path for s in program._sources]
        assert Path("main.cpp") in source_paths

    def test_command_accepts_target_source(self, tmp_path):
        """env.Command() accepts Target as source."""
        from pcons.core.project import Project

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # First command produces output
        step1 = env.Command(
            target="intermediate.txt",
            source="input.txt",
            command="process1 $SOURCE > $TARGET",
        )

        # Second command uses first's output
        step2 = env.Command(
            target="final.txt",
            source=[step1],
            command="process2 $SOURCE > $TARGET",
        )

        # step2 should have step1 in pending sources
        assert step2._pending_sources is not None
        assert step1 in step2._pending_sources

        # step2 should depend on step1
        assert step1 in step2.dependencies

    def test_command_with_mixed_sources(self, tmp_path):
        """env.Command() accepts mix of Targets and paths."""
        from pcons.core.project import Project

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # First command
        gen = env.Command(target="gen.h", source="gen.y", command="cmd")

        # Second command with mixed sources
        result = env.Command(
            target="out.txt",
            source=[gen, "config.h", "version.txt"],
            command="combine $SOURCES > $TARGET",
        )

        # Target source should be in pending sources
        assert result._pending_sources is not None
        assert gen in result._pending_sources

        # Path sources should be in output_nodes' explicit_deps
        output_node = result.output_nodes[0]
        source_paths = [d.path for d in output_node.explicit_deps]
        assert Path("config.h") in source_paths
        assert Path("version.txt") in source_paths

    def test_resolved_target_sources(self, tmp_path):
        """Target.sources includes resolved Target outputs before pending sources cleared."""
        from pcons.core.project import Project
        from pcons.core.target import Target

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create a command target that generates code
        # (Command targets are resolved immediately - output_nodes are populated)
        generated = env.Command(
            target="generated.cpp",
            source="gen.y",
            command="echo generated > $TARGET",
        )

        # Verify the generated target has output_nodes
        assert len(generated.output_nodes) == 1
        assert generated.output_nodes[0].path == Path("generated.cpp")

        # Create a target that uses the generated source
        consumer = Target("consumer", target_type="program")
        consumer.add_source("main.cpp")
        consumer.add_source(generated)

        # Before anything, _sources has main.cpp, _pending_sources has generated
        assert len(consumer._sources) == 1
        assert consumer._pending_sources is not None
        assert generated in consumer._pending_sources

        # The sources property should include both because generated has output_nodes
        all_sources = consumer.sources
        source_paths = [s.path for s in all_sources]
        assert Path("main.cpp") in source_paths
        assert Path("generated.cpp") in source_paths

    def test_command_pending_resolution(self, tmp_path):
        """Command target's pending sources are resolved correctly."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create source file
        (tmp_path / "input.txt").write_text("input")

        # First command produces output
        step1 = env.Command(
            target="intermediate.txt",
            source="input.txt",
            command="step1 $SOURCE > $TARGET",
        )

        # Second command uses first's output
        step2 = env.Command(
            target="final.txt",
            source=[step1],
            command="step2 $SOURCE > $TARGET",
        )

        # Verify step2 has step1 in pending sources
        assert step2._pending_sources is not None
        assert step1 in step2._pending_sources

        # Resolve the project
        project.resolve()

        # After resolution, step2's output nodes should depend on step1's output
        final_node = step2.output_nodes[0]
        intermediate_node = step1.output_nodes[0]

        # Check that intermediate_node is in final_node's dependencies
        assert intermediate_node in final_node.explicit_deps

        # Generate and verify ninja output
        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()

        # Both targets should be in the ninja file
        assert "intermediate.txt" in content
        assert "final.txt" in content


class TestCommandDepends:
    """Tests for the depends= parameter on env.Command()."""

    def test_depends_single_file(self, tmp_path):
        """depends= with a single file adds implicit dep."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        result = env.Command(
            target="output.txt",
            source="input.txt",
            command="python $SRCDIR/tools/gen.py $SOURCE $TARGET",
            depends="tools/gen.py",
        )

        assert len(result._extra_implicit_deps) == 1
        # Applied to output nodes during resolve
        project.resolve()
        assert len(result.output_nodes[0].implicit_deps) == 1

    def test_depends_multiple_files(self, tmp_path):
        """depends= with a list adds multiple implicit deps."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        result = env.Command(
            target="output.txt",
            source="input.txt",
            command="tool $SOURCE $TARGET",
            depends=["tools/gen.py", "config.yaml"],
        )

        assert len(result._extra_implicit_deps) == 2
        project.resolve()
        assert len(result.output_nodes[0].implicit_deps) == 2

    def test_depends_appears_in_ninja_after_pipe(self, tmp_path):
        """depends= files appear after | in ninja build statements."""
        from pcons.generators.ninja import NinjaGenerator

        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="tool $SOURCE $TARGET",
            depends="tools/gen.py",
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        # The dep should appear after | (implicit deps section)
        assert "| " in content
        assert "gen.py" in content
        # The dep should NOT be in $in (explicit sources)
        # Find the build line for output.txt
        for line in content.splitlines():
            if "build output.txt:" in line:
                # Sources (before |) should only have input.txt
                before_pipe = line.split("|")[0]
                assert "gen.py" not in before_pipe
                break

    def test_depends_not_in_sources(self, tmp_path):
        """depends= files don't appear in $SOURCE/$SOURCES."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        result = env.Command(
            target="output.txt",
            source="input.txt",
            command="tool $SOURCE $TARGET",
            depends="tools/gen.py",
        )

        # The build_info sources should only contain input.txt
        build_info = result.output_nodes[0]._build_info
        source_paths = [str(s.path) for s in build_info["sources"]]
        assert "tools/gen.py" not in source_paths


class TestDeclaredSourceOrder:
    """`$SOURCE` and `${SOURCES[n]}` mean nothing if declaration order isn't
    kept. Target sources used to be appended after plain paths regardless of
    where they were written, so a command that ran its own built tool as
    `${SOURCES[0]}` executed a data file instead.
    """

    def _project(self, tmp_path, gcc_toolchain):
        project = Project("order", root_dir=tmp_path, build_dir="build")
        (tmp_path / "tool.c").write_text("int main(void){return 0;}\n")
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("data\n")
        env = project.Environment(toolchain=gcc_toolchain)
        tool = project.Program("mytool", env, sources=["tool.c"])
        return project, env, tool

    @staticmethod
    def _source_names(command_target):
        """Source names, stems only: the program picks up .exe on Windows."""
        build_info = command_target.output_nodes[0]._build_info
        return [
            Path(s.path).stem
            if Path(s.path).suffix in ("", ".exe")
            else Path(s.path).name
            for s in build_info["sources"]
        ]

    def test_target_first(self, tmp_path, gcc_toolchain):
        project, env, tool = self._project(tmp_path, gcc_toolchain)

        cmd = env.Command(
            target=project.build_dir / "out.txt",
            source=[tool, "a.txt", "b.txt"],
            command="$SOURCE ${SOURCES[1]} ${SOURCES[2]} > $TARGET",
        )
        project.resolve()

        assert self._source_names(cmd) == ["mytool", "a.txt", "b.txt"]

    def test_target_in_the_middle(self, tmp_path, gcc_toolchain):
        project, env, tool = self._project(tmp_path, gcc_toolchain)

        cmd = env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt", tool, "b.txt"],
            command="$SOURCES > $TARGET",
        )
        project.resolve()

        assert self._source_names(cmd) == ["a.txt", "mytool", "b.txt"]

    def test_multi_output_target_splices_all_its_outputs(self, tmp_path, gcc_toolchain):
        project, env, _tool = self._project(tmp_path, gcc_toolchain)
        generator = env.Command(
            target=[project.build_dir / "one.c", project.build_dir / "two.c"],
            source=None,
            command="generate $TARGETS",
            name="gen",
        )

        cmd = env.Command(
            target=project.build_dir / "out.txt",
            source=[generator, "a.txt"],
            command="$SOURCES > $TARGET",
            name="consume",
        )
        project.resolve()

        assert self._source_names(cmd) == ["one.c", "two.c", "a.txt"]

    def test_edge_inputs_follow_declared_order(self, tmp_path, gcc_toolchain):
        """Not just substitution: $in order matters to anything order-sensitive."""
        from pcons.generators.ninja import NinjaGenerator

        project, env, tool = self._project(tmp_path, gcc_toolchain)
        env.Command(
            target=project.build_dir / "out.txt",
            source=[tool, "a.txt"],
            command="$SOURCES > $TARGET",
        )

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        edge = next(
            line for line in content.splitlines() if line.startswith("build out.txt:")
        )
        assert edge.index("mytool") < edge.index("a.txt")

    def test_commands_without_target_sources_are_unchanged(
        self, tmp_path, gcc_toolchain
    ):
        project, env, _tool = self._project(tmp_path, gcc_toolchain)

        cmd = env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt", "b.txt"],
            command="$SOURCES > $TARGET",
        )
        project.resolve()

        assert self._source_names(cmd) == ["a.txt", "b.txt"]


class TestSourceSlices:
    """`${SOURCES[n:]}` -- "the tool, then however many data files there are"
    is the normal shape for a code-generation rule."""

    def _command(self, tmp_path, gcc_toolchain, template):
        project = Project("slices", root_dir=tmp_path, build_dir="build")
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text("data\n")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt", "b.txt", "c.txt"],
            command=template,
        )
        from pcons.generators.ninja import NinjaGenerator

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = (tmp_path / "build" / "build.ninja").read_text()
        return next(
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("command =") and "out.txt" not in line
        )

    def test_open_ended_slice(self, tmp_path, gcc_toolchain):
        command = self._command(tmp_path, gcc_toolchain, "gen ${SOURCES[1:]} > $TARGET")

        assert "$source_1 $source_2" in command
        assert "$source_0" not in command

    def test_bounded_slice(self, tmp_path, gcc_toolchain):
        command = self._command(
            tmp_path, gcc_toolchain, "gen ${SOURCES[0:2]} > $TARGET"
        )

        assert "$source_0 $source_1" in command
        assert "$source_2" not in command

    def test_open_start_slice(self, tmp_path, gcc_toolchain):
        command = self._command(tmp_path, gcc_toolchain, "gen ${SOURCES[:2]} > $TARGET")

        assert "$source_0 $source_1" in command

    def test_slice_mixes_with_an_index(self, tmp_path, gcc_toolchain):
        command = self._command(
            tmp_path, gcc_toolchain, "${SOURCES[0]} --json ${SOURCES[1:]} > $TARGET"
        )

        assert "$source_0 --json $source_1 $source_2" in command


class TestUnknownSubstitutionsRaise:
    """An unrecognized ${...} used to reach build.ninja as an escaped literal
    and run as nonsense -- the opposite of pcons's fail-fast rule."""

    def _command(self, tmp_path, template):
        project = Project("bad", root_dir=tmp_path, build_dir="build")
        (tmp_path / "a.txt").write_text("data\n")
        env = project.Environment()
        return env.Command(
            target=project.build_dir / "out.txt", source=["a.txt"], command=template
        )

    def test_unknown_marker_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unrecognized substitution"):
            self._command(tmp_path, "gen ${SOURCE[0]} > $TARGET")

    def test_message_lists_the_supported_forms(self, tmp_path):
        with pytest.raises(ValueError) as excinfo:
            self._command(tmp_path, "gen ${SOURCES[a:b]} > $TARGET")

        assert "${SOURCES[n:m]}" in str(excinfo.value)

    def test_empty_subscript_raises(self, tmp_path):
        with pytest.raises(ValueError, match="empty subscript"):
            self._command(tmp_path, "gen ${SOURCES[]} > $TARGET")

    def test_plain_variables_still_pass_through(self, tmp_path):
        project = Project("vars", root_dir=tmp_path, build_dir="build")
        (tmp_path / "a.txt").write_text("data\n")
        env = project.Environment()
        env.MYVAR = "value"

        cmd = env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt"],
            command="gen ${MYVAR} $SOURCE > $TARGET",
        )

        assert cmd is not None


class TestWorkingDirectory:
    """`cwd=` for a tool that only works from somewhere else -- the source
    root, typically, because it opens an input by a path relative to it.

    Ninja and make run from the build directory and pcons writes every path in
    a command relative to there, so moving the command has to move its paths
    with it, or the tool is handed paths that mean nothing where it runs."""

    def _project(self, tmp_path, gcc_toolchain, **command_args):
        project = Project("cwd", root_dir=tmp_path, build_dir="build")
        (tmp_path / "in.txt").write_text("data\n")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=project.build_dir / "gen/out.txt",
            source=["in.txt"],
            **command_args,
        )
        return project

    def _ninja(self, tmp_path, gcc_toolchain, **command_args):
        from pcons.generators.ninja import NinjaGenerator

        project = self._project(tmp_path, gcc_toolchain, **command_args)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    def _makefile(self, tmp_path, gcc_toolchain, **command_args):
        from pcons.generators.makefile import MakefileGenerator

        project = self._project(tmp_path, gcc_toolchain, **command_args)
        MakefileGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "Makefile").read_text()

    def _command_line(self, content, marker="command ="):
        return next(
            line.strip()
            for line in content.splitlines()
            if marker in line and "out.txt" not in line.split(marker)[0]
        )

    def test_absolute_cwd_is_stored_on_the_edge(self, tmp_path, gcc_toolchain):
        project = self._project(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )
        node = project.targets[0].output_nodes[0]

        assert node._build_info["cwd"] == tmp_path

    def test_relative_cwd_is_anchored_at_the_project_root(
        self, tmp_path, gcc_toolchain
    ):
        project = self._project(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd="tools"
        )
        node = project.targets[0].output_nodes[0]

        assert node._build_info["cwd"] == tmp_path / "tools"

    def test_no_cwd_leaves_the_command_alone(self, tmp_path, gcc_toolchain):
        content = self._ninja(tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET")

        command = self._command_line(content)
        assert "cd " not in command
        assert "gen $in $out" in command

    def test_ninja_changes_directory_and_back(self, tmp_path, gcc_toolchain):
        content = self._ninja(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        command = self._command_line(content)
        # Back to the build dir, so anything wrapped around the command (a
        # post-build step, write_if_different) still finds its files.
        assert command.endswith("&& cd build")
        assert "cd .. && " in command

    def test_ninja_paths_are_relative_to_the_working_directory(
        self, tmp_path, gcc_toolchain
    ):
        content = self._ninja(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        # $in/$out are ninja's own, build-relative view of the edge, so a
        # moved command uses the per-edge variables instead.
        assert "gen $source_0 $target_0" in content
        assert "  source_0 = in.txt\n" in content
        assert "  target_0 = build/gen/out.txt\n" in content

    def test_a_bare_target_name_still_resolves_to_the_build_dir(
        self, tmp_path, gcc_toolchain
    ):
        """`target="out.txt"` gives a node path with no build_dir prefix.

        It is still execution-relative -- ninja writes `build out.txt:` and
        the file lands in the build directory -- but it looks exactly like a
        source path, so anchoring it at the project root sent the command's
        output one directory up, where nothing would ever look for it.
        """
        from pcons.generators.ninja import NinjaGenerator

        project = Project("cwd", root_dir=tmp_path, build_dir="build")
        (tmp_path / "in.txt").write_text("data\n")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target="out.txt",
            source=["in.txt"],
            command="gen $SOURCE $TARGET",
            cwd=tmp_path,
        )
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = (tmp_path / "build" / "build.ninja").read_text()

        assert "  target_0 = build/out.txt\n" in content

    def test_ninja_srcdir_follows_the_working_directory(self, tmp_path, gcc_toolchain):
        content = self._ninja(
            tmp_path,
            gcc_toolchain,
            command="$SRCDIR/tools/gen $TARGET",
            cwd=tmp_path / "sub",
        )

        assert "../tools/gen $target_0" in content
        assert "$topdir/tools/gen" not in content

    def test_ninja_stays_relocatable(self, tmp_path, gcc_toolchain):
        content = self._ninja(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        # No absolute path from this checkout anywhere in the moved edge.
        assert str(tmp_path) not in self._command_line(content)
        assert str(tmp_path) not in content.split("# Build statements")[1]

    def test_makefile_changes_directory_and_back(self, tmp_path, gcc_toolchain):
        content = self._makefile(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        recipe = next(
            line.strip()
            for line in content.splitlines()
            if line.startswith("\t") and " gen " in line
        )
        assert recipe.startswith(f"cd {tmp_path} && ")
        assert recipe.endswith(f"&& cd {tmp_path / 'build'}")

    def test_makefile_paths_are_absolute_in_a_moved_command(
        self, tmp_path, gcc_toolchain
    ):
        content = self._makefile(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        # A Makefile already spells sources absolutely; the output has to
        # follow, or it lands wherever the command was told to run.
        assert (
            f"gen {tmp_path / 'in.txt'} {tmp_path / 'build' / 'gen' / 'out.txt'}"
            in (content)
        )

    def test_write_if_different_wrapper_is_not_moved(self, tmp_path, gcc_toolchain):
        content = self._ninja(
            tmp_path,
            gcc_toolchain,
            command="gen $SOURCE $TARGET",
            cwd=tmp_path,
            write_if_different=True,
        )

        command = self._command_line(content)
        before, after = command.split(" && cd .. && ", 1)
        # Both halves of the stash wrapper run where ninja put us: the build
        # directory, which is where $out is relative to.
        assert "stable_output --pre $out" in before
        assert after.split(" && cd build && ", 1)[1].endswith(
            "stable_output --post $out"
        )

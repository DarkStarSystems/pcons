# SPDX-License-Identifier: MIT
"""Tests for env.Command() functionality."""

import shlex
import sys
from pathlib import Path

import pytest

from pcons.core.builder import GenericCommandBuilder
from pcons.core.environment import Environment
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.subst import SourcePath, TargetPath
from pcons.generators.generator import BaseGenerator

# cmd.exe reads a leading "/" as a switch and needs /d to change drive, so a
# `cd` in a generated command is spelled differently per platform.
CD = "cd /d" if sys.platform == "win32" else "cd"


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

    def test_no_rule_name_by_default(self, test_project):  # noqa: F811
        """No name pinned here means the generator names the rule after its
        contents, so identical commands share one."""
        assert GenericCommandBuilder("cmd1").rule_name is None

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
    kept. A Target source reordered among the plain paths would leave a command
    that runs its own built tool as `${SOURCES[0]}` executing a data file.
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


class TestHandQuoting:
    """pcons quotes each token itself, so quoting one by hand quotes it twice.

    Wrapping a path that might contain spaces is the reflex in every other
    build system, and the resulting failure names the quoted string as if it
    were a filename: `/bin/sh: "/Applications/.../Rez": No such file`.
    """

    def test_a_quoted_token_raises(self):
        with pytest.raises(ValueError, match="quotes each token"):
            GenericCommandBuilder('"/opt/my tools/rez" $SOURCE $TARGET')

    def test_the_message_shows_the_bare_spelling(self):
        with pytest.raises(ValueError, match=r"Write it bare: '/opt/rez'"):
            GenericCommandBuilder('"/opt/rez" $SOURCE')

    def test_single_quotes_too(self):
        with pytest.raises(ValueError, match="quotes each token"):
            GenericCommandBuilder("'/opt/rez' $SOURCE")

    def test_list_form_is_checked_as_well(self):
        with pytest.raises(ValueError, match="quotes each token"):
            GenericCommandBuilder(["rez", '"/opt/sdk"', "$SOURCE"])

    def test_an_unquoted_token_is_untouched(self):
        builder = GenericCommandBuilder("rez /opt/rez $SOURCE")

        assert builder.command[1] == "/opt/rez"

    def test_a_quote_inside_a_token_is_left_alone(self):
        """Only a leading quote is the mistake; one in the middle may well be
        a literal the program wants."""
        builder = GenericCommandBuilder(["say", 'it"s', "-x"])

        assert builder.command[1] == 'it"s'

    def test_a_trailing_quote_alone_is_fine(self):
        """`-DNAME="value"` wants its quotes delivered -- that is how a C
        string macro is spelled, and pcons quoting the whole token preserves
        them."""
        builder = GenericCommandBuilder('cc -DFOO="bar" --msg="hi" $SOURCE')

        assert builder.command[1] == '-DFOO="bar"'
        assert builder.command[2] == '--msg="hi"'

    def test_verbatim_says_the_quotes_are_meant(self):
        from pcons.core.subst import Verbatim

        builder = GenericCommandBuilder(["awk", Verbatim("'{print $1}'"), "$SOURCE"])

        assert builder.command[1] == "'{print $1}'"

    def test_the_message_names_the_escape_hatch(self):
        with pytest.raises(ValueError, match="Verbatim"):
            GenericCommandBuilder('"/opt/rez" $SOURCE')

    def test_a_quoted_path_with_spaces_is_caught_after_the_split(self):
        """The case quoting exists for: a string command is split on
        whitespace first, so neither half is a matched pair."""
        with pytest.raises(ValueError, match="quotes each token"):
            GenericCommandBuilder('"/opt/my tools/rez" $SOURCE $TARGET')


class TestEmbeddedMarkers:
    """A marker may be part of an argument rather than all of one.

    The spelling that matters is `./${SOURCES[0]}`: a bare `${SOURCES[0]}`
    expands to a build-directory name with no directory in it, and /bin/sh
    reads that as something to look up on $PATH, where a program this build
    just produced is not."""

    def test_relative_prefix_on_an_index(self):
        builder = GenericCommandBuilder("./${SOURCES[0]} $TARGET")

        assert builder.command[0] == SourcePath(index=0, prefix="./")

    def test_flag_welded_to_a_target(self):
        builder = GenericCommandBuilder("cl /Fo$TARGET $SOURCE")

        assert builder.command[1] == TargetPath(prefix="/Fo", start=0)

    def test_suffix_after_a_marker(self):
        builder = GenericCommandBuilder("gen $TARGET.tmp")

        assert builder.command[1] == TargetPath(suffix=".tmp", start=0)

    def test_a_bare_marker_is_left_alone(self):
        """Nothing attached means nothing to distribute: a plain $in/$out."""
        builder = GenericCommandBuilder("cp $SOURCES $TARGET")

        assert builder.command == ["cp", SourcePath(), TargetPath()]

    def test_braced_bare_marker(self):
        assert GenericCommandBuilder("cp ${SOURCE} x").command[1] == SourcePath()

    def test_affix_on_a_multi_path_form_becomes_a_slice(self):
        """A prefix on something that expands to several paths belongs to each
        of them, which is what a full slice says."""
        builder = GenericCommandBuilder("tar ./$SOURCES")

        assert builder.command[1] == SourcePath(start=0, prefix="./")

    def test_affix_on_a_slice_is_kept(self):
        builder = GenericCommandBuilder("gen -i${SOURCES[1:]}")

        assert builder.command[1] == SourcePath(start=1, prefix="-i")

    def test_two_markers_in_one_token_raise(self):
        with pytest.raises(ValueError, match="more than one"):
            GenericCommandBuilder("cp $SOURCE:$TARGET")

    def test_unbraced_subscript_raises(self):
        with pytest.raises(ValueError, match="needs braces"):
            GenericCommandBuilder("tool $SOURCES[0]")

    def test_a_longer_name_is_not_a_marker(self):
        """$SOURCEDIR is an ordinary variable, not $SOURCE plus text."""
        builder = GenericCommandBuilder("tool $SOURCEDIR/x $TARGET")

        assert builder.command[1] == "$SOURCEDIR/x"

    def test_unknown_substitution_beside_a_marker_still_raises(self):
        with pytest.raises(ValueError, match="Unrecognized substitution"):
            GenericCommandBuilder("tool ${nope[1]}$SOURCE")


class TestEmbeddedMarkersInNinja:
    """What the embedded forms come out as, for ninja to expand."""

    def _command(self, tmp_path, template, sources=("a.txt", "b.txt")):
        from pcons.generators.ninja import NinjaGenerator

        project = Project("embedded", root_dir=tmp_path, build_dir="build")
        for name in sources:
            (tmp_path / name).write_text("data\n")
        env = project.Environment()
        env.Command(
            target=project.build_dir / "out.txt",
            source=list(sources),
            command=template,
        )
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = (tmp_path / "build" / "build.ninja").read_text()
        return next(
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("command =")
        )

    def test_prefix_stays_next_to_the_path(self, tmp_path):
        command = self._command(tmp_path, "./${SOURCES[0]} $TARGET ${SOURCES[1:]}")

        assert "./$source_0" in command
        assert "$source_1" in command

    def test_prefix_repeats_over_a_slice(self, tmp_path):
        command = self._command(tmp_path, "gen -i${SOURCES[0:]} $TARGET")

        assert "-i$source_0" in command
        assert "-i$source_1" in command

    def test_prefix_repeats_over_a_bare_marker(self, tmp_path):
        command = self._command(tmp_path, "gen -i$SOURCES $TARGET")

        assert "-i$source_0" in command
        assert "-i$source_1" in command
        assert "-i$in" not in command

    def test_suffix_reaches_the_command(self, tmp_path):
        command = self._command(tmp_path, "gen $SOURCE $TARGET.tmp")

        assert "$target_0.tmp" in command


class TestUnknownSubstitutionsRaise:
    """An unrecognized ${...} that reached build.ninja as an escaped literal
    would run as nonsense -- the opposite of pcons's fail-fast rule."""

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


class TestCommandVariablesAndDollars:
    """A Command's own $variables, and $$ for a dollar that isn't one."""

    def _command(self, tmp_path, template, **vars_):
        project = Project("vars", root_dir=tmp_path, build_dir="build")
        (tmp_path / "a.txt").write_text("data\n")
        env = project.Environment()
        for name, value in vars_.items():
            setattr(env, name, value)
        result = env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt"],
            command=template,
        )
        return result.output_nodes[0]._build_info["command"]

    def test_a_variable_is_expanded(self, tmp_path):
        command = self._command(tmp_path, "$MYTOOL $SOURCE $TARGET", MYTOOL="mytool")

        assert command[0] == "mytool"

    def test_a_list_variable_becomes_several_arguments(self, tmp_path):
        command = self._command(tmp_path, "tool $MYFLAGS $TARGET", MYFLAGS=["-a", "-b"])

        assert command[:3] == ["tool", "-a", "-b"]

    def test_a_variable_beside_a_marker_is_expanded(self, tmp_path):
        command = self._command(
            tmp_path, "tool $OUTFLAG$TARGET $SOURCE", OUTFLAG="--out="
        )

        assert command[1].prefix == "--out="

    def test_an_undefined_variable_still_fails_fast(self, tmp_path):
        from pcons.core.errors import MissingVariableError

        with pytest.raises(MissingVariableError):
            self._command(tmp_path, "$NOPE $TARGET")

    def test_double_dollar_collapses_to_one(self, tmp_path):
        """Resolved here, once. Left doubled, the generator cannot tell an
        escaped dollar from a real one and escapes half of it."""
        command = self._command(tmp_path, "awk $$1 $SOURCE > $TARGET")

        assert command[1] == "$1"

    def test_srcdir_is_left_for_the_generators(self, tmp_path):
        command = self._command(tmp_path, "python $SRCDIR/gen.py $TARGET")

        assert "$SRCDIR/gen.py" in command

    def test_ninja_manifest_parses_with_a_literal_dollar(self, tmp_path):
        r"""The doubled form "$\$$" is not a valid ninja escape; ninja rejects
        the whole file, so nothing builds at all."""
        from pcons.generators.ninja import NinjaGenerator

        project = Project("dollar", root_dir=tmp_path, build_dir="build")
        (tmp_path / "a.txt").write_text("data\n")
        env = project.Environment()
        env.Command(
            target=project.build_dir / "out.txt",
            source=["a.txt"],
            command="tool --stamp=$$Rev$$ $TARGET",
        )
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = (tmp_path / "build" / "build.ninja").read_text()

        command = next(
            line
            for line in content.splitlines()
            if line.strip().startswith("command =")
        )
        assert "$\\$$" not in command
        assert "Rev" in command


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
        line = next(
            line.strip()
            for line in content.splitlines()
            if marker in line and "out.txt" not in line.split(marker)[0]
        )
        # A moved command contains "&&", so on Windows it is routed through
        # cmd.exe. Unwrap it: these tests are about the cd, not the routing.
        prefix, _, rest = line.partition('cmd.exe /s /c "')
        return prefix + rest[:-1] if rest else line

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
        assert command.endswith(f"&& {CD} build")
        assert f"{CD} .. && " in command

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
        assert f"  target_0 = {Path('build/gen/out.txt')}\n" in content

    def test_moved_paths_use_native_separators(self, tmp_path, gcc_toolchain):
        """A moved edge is routed through cmd.exe on Windows, and cmd.exe
        reads `build/tool.exe` as the command `build` with a switch."""
        content = self._ninja(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )
        target_line = next(
            line for line in content.splitlines() if line.strip().startswith("target_0")
        )

        expected = str(Path("build/gen/out.txt"))
        assert target_line.strip() == f"target_0 = {expected}"

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

        assert f"  target_0 = {Path('build/out.txt')}\n" in content

    def test_ninja_keeps_an_embedded_prefix_on_a_moved_path(
        self, tmp_path, gcc_toolchain
    ):
        """A moved command names its inputs one by one in place of $in; text
        attached to the marker has to come along with each of them."""
        content = self._ninja(
            tmp_path,
            gcc_toolchain,
            command="./${SOURCES[0]} --out=$TARGET",
            cwd=tmp_path,
        )

        assert "./$source_0" in content
        assert "--out=$target_0" in content
        assert "  source_0 = in.txt\n" in content

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
        assert recipe.startswith(f"cd {shlex.quote(str(tmp_path))} && ")
        assert recipe.endswith(f"&& cd {shlex.quote(str(tmp_path / 'build'))}")

    def test_makefile_paths_are_absolute_in_a_moved_command(
        self, tmp_path, gcc_toolchain
    ):
        content = self._makefile(
            tmp_path, gcc_toolchain, command="gen $SOURCE $TARGET", cwd=tmp_path
        )

        # A Makefile already spells sources absolutely; the output has to
        # follow, or it lands wherever the command was told to run.
        source = shlex.quote(str(tmp_path / "in.txt"))
        output = shlex.quote(str(tmp_path / "build" / "gen" / "out.txt"))
        assert f"gen {source} {output}" in content

    def test_write_if_different_wrapper_is_not_moved(self, tmp_path, gcc_toolchain):
        content = self._ninja(
            tmp_path,
            gcc_toolchain,
            command="gen $SOURCE $TARGET",
            cwd=tmp_path,
            write_if_different=True,
        )

        command = self._command_line(content)
        before, after = command.split(f" && {CD} .. && ", 1)
        # Both halves of the stash wrapper run where ninja put us: the build
        # directory, which is where $out is relative to.
        assert "stable_output --pre $out" in before
        assert after.split(f" && {CD} build && ", 1)[1].endswith(
            "stable_output --post $out"
        )


class TestRuleSharing:
    """Identical commands share one ninja rule, and a manifest is reproducible.

    A rule name pinned per edge would bypass the generator's deduplication, so
    N identical commands would write N identical rules; a random one would also
    make every run produce a different build.ninja.
    """

    def _ninja(self, tmp_path, commands):
        from pcons.generators.ninja import NinjaGenerator

        project = Project("share", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        for i, command in enumerate(commands):
            (tmp_path / f"in{i}.txt").write_text("x\n")
            env.Command(target=f"out{i}.txt", source=[f"in{i}.txt"], command=command)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    @staticmethod
    def _rules(manifest: str) -> list[str]:
        return [ln for ln in manifest.splitlines() if ln.startswith("rule ")]

    def test_identical_commands_share_a_rule(self, tmp_path):
        content = self._ninja(tmp_path, ["cp $SOURCE $TARGET"] * 20)

        command_rules = [r for r in self._rules(content) if "command" in r]
        assert len(command_rules) == 1

    def test_different_commands_keep_their_own(self, tmp_path):
        content = self._ninja(tmp_path, ["cp $SOURCE $TARGET", "mv $SOURCE $TARGET"])

        assert len({r for r in self._rules(content) if "command" in r}) == 2

    def test_a_pinned_rule_name_reaches_the_generator(self, test_project):  # noqa: F811
        """A caller that wants an edge on a rule of its own says so, and the
        name travels on the edge for the generator to use verbatim."""
        builder = GenericCommandBuilder("cp $SOURCE $TARGET", rule_name="my_rule")
        env = Environment()

        node = builder(env, "out.txt", ["in.txt"])[0]

        assert builder.rule_name == "my_rule"
        assert node._build_info["rule_name"] == "my_rule"


class TestTargetOutsideTheBuildDirectory:
    """A destination the script names outside the build tree stays there.

    Node paths are stored relative to the project root, so a generator cannot
    tell `outdir/copy.txt` from an ordinary build output by looking at it —
    and reading it as build-relative writes the file into `build/` instead.
    Nothing about the build reveals that: ninja tracks the path it wrote, so
    the mistake is self-consistent and rebuilds cleanly.
    """

    def _ninja(self, tmp_path, target):
        from pcons.generators.ninja import NinjaGenerator

        (tmp_path / "src.txt").write_text("x\n")
        project = Project("outside", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        env.Command(target=target, source=["src.txt"], command="cp $SOURCE $TARGET")
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    def test_absolute_target_outside_the_build_dir_says_where_it_is(self, tmp_path):
        content = self._ninja(tmp_path, tmp_path / "outdir" / "copy.txt")

        assert "build $topdir/outdir/copy.txt:" in content

    def test_it_matches_what_install_does(self, tmp_path):
        """Install already emits its destinations this way."""
        from pcons.generators.ninja import NinjaGenerator

        (tmp_path / "src.txt").write_text("x\n")
        project = Project("both", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        env.Command(
            target=tmp_path / "outdir" / "copy.txt",
            source=["src.txt"],
            command="cp $SOURCE $TARGET",
        )
        project.Install(tmp_path / "outdir2", ["src.txt"])
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = (tmp_path / "build" / "build.ninja").read_text()

        assert "build $topdir/outdir/copy.txt:" in content
        assert "build $topdir/outdir2/src.txt:" in content

    def test_a_target_inside_the_build_dir_is_unchanged(self, tmp_path):
        content = self._ninja(tmp_path, tmp_path / "build" / "gen" / "out.txt")

        assert "build gen/out.txt:" in content

    def test_a_relative_target_is_unchanged(self, tmp_path):
        """A bare name is build-dir relative, which is the ordinary case."""
        content = self._ninja(tmp_path, "out.txt")

        assert "build out.txt:" in content


class TestBuildDirPathWarning:
    """A command runs *in* the build directory, but `project.build_dir` is
    relative to the project root, so interpolating it names one level too
    deep -- `build/tool` looks for `build/build/tool`. Nothing checks it."""

    def _generate(self, tmp_path, command, **kwargs):
        from pcons.generators.ninja import NinjaGenerator

        (tmp_path / "in.txt").write_text("x\n")
        project = Project("bd", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        env.Command(target="out.txt", source=["in.txt"], command=command, **kwargs)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

    def test_warns(self, tmp_path, caplog):
        self._generate(tmp_path, ["tool", "-Wl,build/libfoo.dylib", "$SOURCE"])

        assert "runs *in* the build directory" in caplog.text

    def test_a_cwd_edge_is_left_alone(self, tmp_path, caplog):
        """With cwd= the frame is explicit and deliberate."""
        self._generate(
            tmp_path, ["tool", "build/libfoo.dylib", "$SOURCE"], cwd=tmp_path
        )

        assert "runs *in* the build directory" not in caplog.text

    def test_a_similar_name_is_not_flagged(self, tmp_path, caplog):
        self._generate(tmp_path, ["tool", "-Lrebuild/x", "$SOURCE"])

        assert "runs *in* the build directory" not in caplog.text

    def test_it_can_be_switched_off(self, tmp_path, caplog, monkeypatch):
        monkeypatch.setenv("PCONS_WARN_BUILD_DIR_PATHS", "0")
        self._generate(tmp_path, ["tool", "-Wl,build/libfoo.dylib", "$SOURCE"])

        assert "runs *in* the build directory" not in caplog.text

    @pytest.mark.parametrize("raw", ["", "silent", "quiet", "2"])
    def test_a_value_it_does_not_know_still_warns(
        self, tmp_path, caplog, monkeypatch, raw
    ):
        """A warning knob must not be able to fail generation."""
        monkeypatch.setenv("PCONS_WARN_BUILD_DIR_PATHS", raw)
        self._generate(tmp_path, ["tool", "-Wl,build/libfoo.dylib", "$SOURCE"])

        assert "runs *in* the build directory" in caplog.text


class TestOutsideBuildDirIndexedTargets:
    """A `${TARGETS[n]}` naming an outside-build destination must agree with
    the build statement. Pointing them at different places leaves the edge
    dirty after every run, or writes the file where nothing looks for it."""

    def _ninja(self, tmp_path, targets, command):
        from pcons.generators.ninja import NinjaGenerator

        (tmp_path / "src.txt").write_text("x\n")
        project = Project("idx", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        env.Command(target=targets, source=["src.txt"], command=command)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    def test_indexed_target_matches_the_build_statement(self, tmp_path):
        content = self._ninja(
            tmp_path,
            tmp_path / "outdir" / "copy.txt",
            ["cp", "$SOURCE", "${TARGETS[0]}"],
        )

        assert "build $topdir/outdir/copy.txt:" in content
        assert "  target_0 = $topdir/outdir/copy.txt\n" in content

    def test_multiple_outside_targets_agree_too(self, tmp_path):
        content = self._ninja(
            tmp_path,
            [tmp_path / "outdir" / "a.txt", tmp_path / "outdir" / "b.txt"],
            ["cp", "$SOURCE", "${TARGETS[0]}", "&&", "cp", "$SOURCE", "${TARGETS[1]}"],
        )

        assert "build $topdir/outdir/a.txt $topdir/outdir/b.txt:" in content
        assert "  target_0 = $topdir/outdir/a.txt\n" in content
        assert "  target_1 = $topdir/outdir/b.txt\n" in content

    def test_a_build_dir_target_is_unchanged(self, tmp_path):
        content = self._ninja(
            tmp_path,
            tmp_path / "build" / "gen" / "out.txt",
            ["cp", "$SOURCE", "${TARGETS[0]}"],
        )

        assert "  target_0 = gen/out.txt\n" in content

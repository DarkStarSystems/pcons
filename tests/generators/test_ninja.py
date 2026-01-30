# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.ninja."""

from pathlib import Path

from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.ninja import NinjaGenerator


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison."""
    return p.replace("\\", "/")


class TestNinjaGenerator:
    def test_is_generator(self):
        gen = NinjaGenerator()
        assert gen.name == "ninja"

    def test_creates_build_ninja(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = NinjaGenerator()

        gen.generate(project)

        ninja_file = tmp_path / "build.ninja"
        assert ninja_file.exists()

    def test_header_contains_project_name(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path, build_dir=".")
        gen = NinjaGenerator()

        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "myproject" in content

    def test_writes_builddir_variable(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="out")
        gen = NinjaGenerator()

        gen.generate(project)

        content = (tmp_path / "out" / "build.ninja").read_text()
        # builddir is always "." since the ninja file is inside the build directory
        assert "builddir = ." in content


class TestNinjaBuildStatements:
    def test_writes_build_for_target(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        # Create a target with a node that has build info
        target = Target("app")
        output_node = FileNode("build/app.o")
        source_node = FileNode("src/main.c")

        # Simulate what a builder would do
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )

        # Use object_nodes for .o file outputs
        target.object_nodes.append(output_node)
        target._sources.append(source_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        assert "build build/app.o:" in content
        assert "cc_cmdline" in content
        assert "src/main.c" in content

    def test_writes_rule_for_builder(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "language": "c",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )

        # Use object_nodes for .o file outputs
        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "rule cc_cmdline" in content
        assert "command = " in content


class TestNinjaAliases:
    def test_writes_aliases(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("mylib")
        lib_node = FileNode("build/libmy.a")
        # Use output_nodes for final library outputs
        target.output_nodes.append(lib_node)
        project.add_target(target)

        project.Alias("libs", target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        assert "build libs: phony" in content
        assert "build/libmy.a" in content


class TestNinjaDefaults:
    def test_writes_defaults(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        app_node = FileNode("build/app")
        # Use output_nodes for final executable outputs
        target.output_nodes.append(app_node)
        project.add_target(target)

        project.Default(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # Check for 'all' phony target and user-specified default
        assert "build all: phony build/app" in content
        # User called project.Default(), so default is user-specified, not 'all'
        assert "default build/app" in content


class TestNinjaEscaping:
    def test_escapes_spaces_in_paths(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("path with spaces/file.c"))
        # Normalize for cross-platform comparison
        assert normalize_path(escaped) == "path$ with$ spaces/file.c"

    def test_escapes_dollar_signs(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("$HOME/file.c"))
        # Normalize for cross-platform comparison
        assert normalize_path(escaped) == "$$HOME/file.c"

    def test_escapes_colons(self, tmp_path):
        gen = NinjaGenerator()
        escaped = gen._escape_path(Path("C:/path/file.c"))
        # Normalize for cross-platform comparison
        assert normalize_path(escaped) == "C$:/path/file.c"


class TestNinjaPostBuild:
    def test_post_build_commands_in_ninja_output(self, tmp_path):
        """Post-build commands are baked into the rule command."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app")
        source_node = FileNode("build/main.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": None,
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "progcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        # Use output_nodes for final program outputs
        target.output_nodes.append(output_node)
        target.post_build("install_name_tool -add_rpath @loader_path $out")
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # Post-build commands are now baked directly into the rule's command line
        # (not as a separate post_build variable)
        assert "post_build =" not in content
        # The command should include the post-build commands
        assert "&& install_name_tool -add_rpath @loader_path build/app" in content

    def test_post_build_multiple_commands_chained(self, tmp_path):
        """Multiple post-build commands are chained with && in the rule."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("plugin")
        output_node = FileNode("build/plugin.so")
        source_node = FileNode("build/plugin.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": None,
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            src_suffixes=[".o"],
            target_suffixes=[".so"],
        )

        # Use output_nodes for final library outputs
        target.output_nodes.append(output_node)
        target.post_build("install_name_tool -add_rpath @loader_path $out")
        target.post_build("codesign --sign - $out")
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # Post-build commands are baked into the rule's command line
        assert "post_build =" not in content
        # Both commands should be in the rule, chained with &&
        assert "&& install_name_tool -add_rpath @loader_path build/plugin.so" in content
        assert "&& codesign --sign - build/plugin.so" in content

    def test_post_build_variable_substitution(self, tmp_path):
        """$out and $in are substituted in post-build commands."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/myapp")
        source_node = FileNode("build/main.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": None,
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "progcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        # Use output_nodes for final program outputs
        target.output_nodes.append(output_node)
        target.post_build("echo Built $out from $in")
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # $out should be substituted with the actual output path
        # $in should be substituted with the input files
        # Post-build commands are baked into the rule's command line
        assert "post_build =" not in content
        assert "&& echo Built build/myapp from build/main.o" in content

    def test_no_post_build_when_empty(self, tmp_path):
        """No post_build commands in rule when target has none."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app")
        source_node = FileNode("build/main.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": None,
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "progcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        # Use output_nodes for final program outputs
        target.output_nodes.append(output_node)
        # No post_build() calls
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        # Should not have post_build variable
        assert "post_build =" not in content


class TestNinjaDepsDirectives:
    def test_gcc_deps_style_emits_depfile_and_deps(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        from pcons.core.subst import PathToken, TargetPath

        target = Target("app")
        output_node = FileNode("build/app.o")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "objcmd",
            "language": "c",
            "sources": [source_node],
            "depfile": PathToken(path="build/app.o", path_type="build", suffix=".d"),
            "deps_style": "gcc",
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cc",
            "objcmd",
            src_suffixes=[".c"],
            target_suffixes=[".o"],
            depfile=TargetPath(suffix=".d"),
            deps_style="gcc",
        )

        # Use object_nodes for .o file outputs
        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "depfile = $out.d" in content
        assert "deps = gcc" in content

    def test_msvc_deps_style_emits_deps_msvc(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app.obj")
        source_node = FileNode("src/main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "objcmd",
            "language": "c",
            "sources": [source_node],
            "depfile": None,
            "deps_style": "msvc",
        }
        output_node.builder = CommandBuilder(
            "Object",
            "cc",
            "objcmd",
            src_suffixes=[".c"],
            target_suffixes=[".obj"],
            deps_style="msvc",
        )

        # Use object_nodes for .obj file outputs
        target.object_nodes.append(output_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "deps = msvc" in content
        # MSVC doesn't use depfile
        assert "depfile" not in content

    def test_no_deps_style_emits_no_deps_directive(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("app")
        output_node = FileNode("build/app")
        source_node = FileNode("build/main.o")
        output_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": None,
            "sources": [source_node],
            "depfile": None,
            "deps_style": None,
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "progcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        # Use output_nodes for final program outputs
        target.output_nodes.append(output_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()
        # Should not have any deps directives for linker
        assert "deps = gcc" not in content
        assert "deps = msvc" not in content
        assert "depfile" not in content

# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.ninja."""

from pathlib import Path

import pytest

from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
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
        BaseGenerator._generate_pending(project)

        ninja_file = tmp_path / "build.ninja"
        assert ninja_file.exists()

    def test_header_contains_project_name(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path, build_dir=".")
        gen = NinjaGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "myproject" in content

    def test_writes_builddir_variable(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir="out")
        gen = NinjaGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        # Use intermediate_nodes for .o file outputs
        target.intermediate_nodes.append(output_node)
        target._sources.append(source_node)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        # Use intermediate_nodes for .o file outputs
        target.intermediate_nodes.append(output_node)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        project.Alias("libs", target)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        project.Default(target)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # Post-build commands are now baked directly into the rule's command line
        # (not as a separate post_build variable)
        assert "post_build =" not in content
        # The command should include the post-build commands with literal $out
        # for ninja to expand at build time (build-dir-relative)
        assert "&& install_name_tool -add_rpath @loader_path $out" in content

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

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # Post-build commands are baked into the rule's command line
        assert "post_build =" not in content
        # Both commands should be in the rule with literal $out for ninja
        assert "&& install_name_tool -add_rpath @loader_path $out" in content
        assert "&& codesign --sign - $out" in content

    def test_post_build_variable_substitution(self, tmp_path):
        """$out and $in are passed through as literals for ninja to expand."""
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

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())
        # $out and $in are left as literals for ninja to expand at build time
        assert "post_build =" not in content
        assert "&& echo Built $out from $in" in content

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

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        # Should not have post_build variable
        assert "post_build =" not in content


class TestNinjaDepsDirectives:
    def test_same_command_but_different_dep_modes_use_distinct_rules(self, tmp_path):
        """Commands with the same command line but different deps_style should get different rules with correct deps directives."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        from pcons.core.subst import PathToken, TargetPath

        target = Target("app")

        module_obj = FileNode("build/mod.cppm.o")
        module_src = FileNode("src/mod.cppm")
        module_obj._build_info = {
            "tool": "cxx",
            "command_var": "objcmd",
            "language": "cxx_module",
            "sources": [module_src],
            "command": "g++ -c -o $out $in",
            "depfile": None,
            "deps_style": None,
        }
        module_obj.builder = CommandBuilder(
            "Object",
            "cxx",
            "objcmd",
            src_suffixes=[".cppm"],
            target_suffixes=[".o"],
        )

        regular_obj = FileNode("build/main.cpp.o")
        regular_src = FileNode("src/main.cpp")
        regular_obj._build_info = {
            "tool": "cxx",
            "command_var": "objcmd",
            "language": "cxx",
            "sources": [regular_src],
            "command": "g++ -c -o $out $in",
            "depfile": PathToken(
                path="build/main.cpp.o", path_type="build", suffix=".d"
            ),
            "deps_style": "gcc",
        }
        regular_obj.builder = CommandBuilder(
            "Object",
            "cxx",
            "objcmd",
            src_suffixes=[".cpp"],
            target_suffixes=[".o"],
            depfile=TargetPath(suffix=".d"),
            deps_style="gcc",
        )

        target.intermediate_nodes.extend([module_obj, regular_obj])

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()

        rule_headers = [
            line for line in content.splitlines() if line.startswith("rule cxx_objcmd_")
        ]
        assert len(rule_headers) == 2, content
        assert "depfile = $out.d" in content
        assert "deps = gcc" in content

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

        # Use intermediate_nodes for .o file outputs
        target.intermediate_nodes.append(output_node)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

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

        # Use intermediate_nodes for .obj file outputs
        target.intermediate_nodes.append(output_node)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        assert "deps = msvc" in content
        # MSVC doesn't use depfile
        assert "depfile" not in content
        # The prefix must be pinned explicitly rather than relying on
        # ninja's built-in default, which is the English cl.exe string and
        # silently matches nothing (dropping header deps) on a localized
        # (e.g. German/Japanese) cl.exe.
        assert "msvc_deps_prefix = Note: including file: " in content

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

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build.ninja").read_text()
        # Should not have any deps directives for linker
        assert "deps = gcc" not in content
        assert "deps = msvc" not in content
        assert "depfile" not in content


class TestNinjaSrcDir:
    def test_srcdir_replaced_with_topdir(self, tmp_path):
        """$SRCDIR in Command() commands is replaced with $topdir for ninja."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="python $SRCDIR/scripts/generate.py $SOURCE $TARGET",
            name="gen",
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        # $SRCDIR should become $topdir in the ninja file
        assert "$topdir/scripts/generate.py" in content
        # Original $SRCDIR should not appear
        assert "$SRCDIR" not in content

    def test_command_depends_in_ninja(self, tmp_path):
        """Command with depends= generates implicit deps in ninja.

        Source-file deps live outside the build dir, so they must be
        emitted with the $topdir/ prefix — ninja runs from the build
        dir and otherwise can't find them.
        """
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="python $SRCDIR/tools/gen.py $SOURCE -o $TARGET",
            depends=["tools/gen.py", "config.yaml"],
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "build.ninja").read_text())
        # Both deps should appear after | in the build statement,
        # each prefixed with $topdir/ since they're source files.
        for line in content.splitlines():
            if "build output.txt:" in line:
                assert "| " in line
                after_pipe = line.split("| ", 1)[1]
                assert "$topdir/tools/gen.py" in after_pipe
                assert "$topdir/config.yaml" in after_pipe
                break
        else:
            raise AssertionError("build output.txt line not found")

    def test_implicit_dep_inside_build_dir_is_bare(self, tmp_path):
        """Implicit deps inside build_dir must use the build-relative
        path (no $topdir/ prefix), so they match references like
        the `dyndep = ...` directive that uses build-relative paths.
        Regression for cxx_modules failing with
        "dyndep 'cxx_modules.dyndep' is not an input".
        """
        from pcons.core.node import FileNode

        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="cp $SOURCE $TARGET",
        )
        project.resolve()

        # Simulate a toolchain (e.g. C++ modules) that writes a dyndep
        # file directly to the build dir during after_resolve and adds
        # it as an implicit dep without setting _build_info.
        dyndep_node = FileNode("build/cxx_modules.dyndep")
        for tgt in project.targets:
            for n in tgt.output_nodes:
                n.implicit_deps.append(dyndep_node)

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "build.ninja").read_text())
        for line in content.splitlines():
            if "build output.txt:" in line and "| " in line:
                after_pipe = line.split("| ", 1)[1]
                assert "cxx_modules.dyndep" in after_pipe
                assert "$topdir/build/cxx_modules.dyndep" not in after_pipe
                break
        else:
            raise AssertionError("build output.txt line with implicit dep not found")

    def test_srcdir_in_middle_of_token(self, tmp_path):
        """$SRCDIR works when embedded in a token (e.g., --config=$SRCDIR/cfg)."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="tool --config=$SRCDIR/my.cfg $SOURCE $TARGET",
            name="cfg_tool",
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "--config=$topdir/my.cfg" in content
        assert "$SRCDIR" not in content

    def test_restat_in_ninja_rule(self, tmp_path):
        """Command with restat=True generates restat = 1 in the ninja rule."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="generated.h",
            source="spec.yml",
            command="python gen.py $SOURCE $TARGET",
            restat=True,
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        # Find the rule block and verify restat is present
        lines = content.splitlines()
        in_rule = False
        found_restat = False
        for line in lines:
            if line.startswith("rule "):
                in_rule = True
            elif in_rule and not line.startswith("  "):
                in_rule = False
            if in_rule and line.strip() == "restat = 1":
                found_restat = True
                break
        assert found_restat, f"restat = 1 not found in ninja rules:\n{content}"

    def test_no_restat_by_default(self, tmp_path):
        """Command without restat should not generate restat = 1."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="cp $SOURCE $TARGET",
        )
        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "restat" not in content

    def test_target_depends_creates_implicit_dep_on_all_steps(self, tmp_path):
        """target.depends(gen) adds | dep to both compile and link steps."""
        from pcons import find_c_toolchain

        try:
            toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("No C toolchain available")
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=toolchain)

        gen = env.Command(
            target="build/generated.h",
            source="spec.yml",
            command="python gen.py $SOURCE $TARGET",
        )

        app = project.Program("app", env, sources=["main.c"])
        app.depends(gen)

        project.resolve()
        ninja_gen = NinjaGenerator()
        ninja_gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "build.ninja").read_text())
        lines = content.splitlines()

        # Compile step should have generated.h as implicit dep
        compile_line = next((ln for ln in lines if ln.startswith("build obj.")), None)
        assert compile_line is not None, "compile line not found"
        assert "| " in compile_line, f"No implicit dep on compile: {compile_line}"
        assert "generated.h" in compile_line.split("| ", 1)[1]

        # Link step should also have generated.h as implicit dep, not in $in
        link_line = next(
            (
                ln
                for ln in lines
                if ln.startswith("build app:") or ln.startswith("build app.exe:")
            ),
            None,
        )
        assert link_line is not None, "link line not found"
        assert "| " in link_line, f"No implicit dep on link: {link_line}"
        before_pipe = link_line.split("| ", 1)[0]
        after_pipe = link_line.split("| ", 1)[1]
        assert "generated.h" not in before_pipe, "generated.h should not be in $in"
        assert "generated.h" in after_pipe


class TestNinjaTestRule:
    def test_test_rule_quotes_spaced_python_exe(
        self, tmp_path, gcc_toolchain, monkeypatch
    ):
        """sys.executable must survive as a single argument even with a
        space in the path. _escape_path's "$ " (dollar-space) is unescaped
        by ninja to a bare space before the shell sees it, which would
        otherwise split the interpreter path into two arguments.
        """
        import sys

        fake_python = "/opt/my tools/bin/python3"
        monkeypatch.setattr(sys, "executable", fake_python)

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        src = tmp_path / "main.c"
        src.write_text("int main(void){return 0;}\n")
        prog = project.Program("prog", env, sources=[str(src)])
        project.Test("prog.smoke", prog)

        project.resolve()

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert '"/opt/my tools/bin/python3"' in content
        # The old "$ "-escaped form (unquoted, ninja un-escapes to a bare
        # space) must not appear.
        assert "/opt/my$ tools/bin/python3" not in content

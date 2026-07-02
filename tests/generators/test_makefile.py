# SPDX-License-Identifier: MIT
"""Tests for pcons.generators.makefile."""

from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison."""
    return p.replace("\\", "/")


class TestMakefileGenerator:
    def test_is_generator(self):
        gen = MakefileGenerator()
        assert gen.name == "makefile"

    def test_creates_makefile(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        makefile = tmp_path / "Makefile"
        assert makefile.exists()

    def test_header_contains_project_name(self, tmp_path):
        project = Project("myproject", root_dir=tmp_path, build_dir=".")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "Makefile").read_text()
        assert "myproject" in content

    def test_writes_builddir_variable(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "out")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "out" / "Makefile").read_text()
        # Build dir should be the absolute path
        assert "BUILDDIR :=" in content

    def test_disables_builtin_rules(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "Makefile").read_text()
        assert ".SUFFIXES:" in content
        assert "--no-builtin-rules" in content

    def test_writes_phony_targets(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "Makefile").read_text()
        assert ".PHONY:" in content
        assert "all" in content
        assert "clean" in content

    def test_writes_clean_target(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        gen = MakefileGenerator()

        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        assert "clean:" in content
        assert "rm -rf" in content


class TestMakefileBuildStatements:
    def test_writes_build_rule_for_target(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create a target with a node that has build info
        target = Target("app")
        output_node = FileNode(tmp_path / "build" / "app.o")
        source_node = FileNode(tmp_path / "src" / "main.c")

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

        target.intermediate_nodes.append(output_node)
        target.add_source(source_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "Makefile").read_text())
        # Check that a build rule exists for the output
        assert "build/app.o:" in content
        assert "main.c" in content

    def test_writes_directory_rules(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app")
        output_node = FileNode(tmp_path / "build" / "obj" / "main.o")
        source_node = FileNode(tmp_path / "src" / "main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )

        target.intermediate_nodes.append(output_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "Makefile").read_text())
        # Directory rule should exist
        assert "mkdir -p" in content

    def test_order_only_prerequisites(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app")
        output_node = FileNode(tmp_path / "build" / "obj" / "main.o")
        source_node = FileNode(tmp_path / "src" / "main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )

        target.intermediate_nodes.append(output_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "Makefile").read_text())
        # Order-only prerequisite syntax: target: prereqs | order-only
        assert " | " in content


class TestMakefileAliases:
    def test_writes_aliases(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=".")

        # Create a simple target
        target = Target("mylib")
        output_node = FileNode(tmp_path / "build" / "libmylib.a")
        target.output_nodes.append(output_node)

        # Create an alias
        project.Alias("all_libs", output_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "Makefile").read_text()
        assert "all_libs:" in content


class TestMakefileDefaultTarget:
    def test_writes_default_goal(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app", target_type="program")
        output_node = FileNode(tmp_path / "build" / "app")
        target.output_nodes.append(output_node)
        target._resolved = True

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        assert ".DEFAULT_GOAL" in content


class TestMakefileEscaping:
    def test_escapes_dollar_signs(self, tmp_path):
        gen = MakefileGenerator()
        result = gen._escape_path("path/with$dollar")
        assert result == "path/with$$dollar"

    def test_handles_normal_paths(self, tmp_path):
        gen = MakefileGenerator()
        result = gen._escape_path("/some/normal/path.o")
        assert result == "/some/normal/path.o"


class TestMakefileDepfiles:
    def test_includes_depfiles(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app")
        output_node = FileNode(tmp_path / "build" / "obj" / "main.o")
        source_node = FileNode(tmp_path / "src" / "main.c")
        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )

        target.intermediate_nodes.append(output_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        # Should include .d files for incremental builds
        assert "-include" in content
        assert "*.d" in content


class TestMakefileImplicitDeps:
    def test_implicit_deps_added_to_prereqs(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app")
        output_node = FileNode(tmp_path / "build" / "main.o")
        source_node = FileNode(tmp_path / "src" / "main.c")
        header_node = FileNode(tmp_path / "include" / "header.h")

        output_node._build_info = {
            "tool": "cc",
            "command_var": "cmdline",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Object", "cc", "cmdline", src_suffixes=[".c"], target_suffixes=[".o"]
        )
        # Add implicit dependency (e.g., from header scanner)
        output_node.implicit_deps.append(header_node)

        target.intermediate_nodes.append(output_node)

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "Makefile").read_text())
        # Implicit dep should be in prerequisites
        assert "header.h" in content


class TestMakefilePostBuild:
    def test_post_build_commands_appended(self, tmp_path):
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app", target_type="program")
        output_node = FileNode(tmp_path / "build" / "app")
        source_node = FileNode(tmp_path / "build" / "main.o")

        output_node._build_info = {
            "tool": "link",
            "command_var": "linkcmd",
            "sources": [source_node],
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "linkcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        # Create a mock environment with the link tool
        class MockLinkTool:
            linkcmd = "gcc -o $TARGET $SOURCES"

        class MockEnv:
            link = MockLinkTool()

            def subst(self, template, **kwargs):
                return template

        target._env = MockEnv()
        target.intermediate_nodes.append(output_node)
        target.output_nodes.append(output_node)
        target._builder_data["post_build_commands"] = [
            "chmod +x $out",
            "echo 'Built $out'",
        ]
        project._resolved = True  # Skip auto-resolve since we set up nodes manually

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        # Post-build commands should be chained with &&
        assert "&&" in content
        assert "chmod +x" in content
        assert "echo" in content


class TestMakefileRecipeDollarEscaping:
    def test_rpath_origin_survives_make(self, tmp_path):
        """A literal $ORIGIN rpath flag must be doubled ($$ORIGIN) so Make's
        own $-expansion pass doesn't mangle it before the shell ever runs
        the recipe (Make would otherwise read $O as variable O, an empty
        variable, turning $ORIGIN/../lib into RIGIN/../lib).

        ``build_info["command"]`` here mirrors exactly what the resolver
        stores for a real build (see Resolver._expand_single_node_command):
        a token list where the user's ``$$ORIGIN`` has already been
        collapsed by subst() to a literal single-dollar ``$ORIGIN``,
        protected from the *shell* by quoting but not yet from Make.
        """
        from pcons.core.subst import SourcePath, TargetPath

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        target = Target("app", target_type="program")
        output_node = FileNode(tmp_path / "build" / "app")
        source_node = FileNode(tmp_path / "build" / "main.o")

        output_node._build_info = {
            "tool": "link",
            "command_var": "linkcmd",
            "sources": [source_node],
            "command": [
                "gcc",
                "-o",
                TargetPath(),
                SourcePath(),
                "-Wl,-rpath,$ORIGIN/../lib",
            ],
        }
        output_node.builder = CommandBuilder(
            "Program", "link", "linkcmd", src_suffixes=[".o"], target_suffixes=[""]
        )

        target.intermediate_nodes.append(output_node)
        target.output_nodes.append(output_node)
        project._resolved = True  # Skip auto-resolve since nodes are hand-built

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        assert "$$ORIGIN/../lib" in content
        # A bare single-dollar $ORIGIN would mean Make's own pass wasn't
        # escaped away; make sure it doesn't appear un-doubled.
        assert "$ORIGIN" not in content.replace("$$ORIGIN", "")


class TestMakefileSrcDir:
    def test_srcdir_replaced_with_project_root(self, tmp_path):
        """$SRCDIR in Command() commands is replaced with project root for make."""
        project = Project("test", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        env.Command(
            target="output.txt",
            source="input.txt",
            command="python $SRCDIR/scripts/generate.py $SOURCE $TARGET",
            name="gen",
        )
        project.resolve()

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = normalize_path((tmp_path / "build" / "Makefile").read_text())
        # $SRCDIR should become the absolute project root path
        project_root = normalize_path(str(tmp_path))
        assert f"{project_root}/scripts/generate.py" in content
        # Original $SRCDIR should not appear
        assert "$SRCDIR" not in content


class TestMakefileTestRecipe:
    def test_test_recipe_quotes_and_escapes_python_exe(
        self, tmp_path, gcc_toolchain, monkeypatch
    ):
        """sys.executable is quoted for the shell and $-escaped for Make, so
        an interpreter path containing a space or $ survives both a shell
        argument split and Make's own $-expansion pass over the recipe.
        """
        import sys

        fake_python = "/opt/my tools/py$thon/bin/python3"
        monkeypatch.setattr(sys, "executable", fake_python)

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        src = tmp_path / "main.c"
        src.write_text("int main(void){return 0;}\n")
        prog = project.Program("prog", env, sources=[str(src)])
        project.Test("prog.smoke", prog)

        project.resolve()

        gen = MakefileGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        # Shell-quoted (single quotes, so the embedded space is part of one
        # argument) and $-escaped ($$ so Make doesn't consume $t as a
        # variable reference before the shell ever sees the line).
        assert "'/opt/my tools/py$$thon/bin/python3'" in content
        assert "test-build" in content

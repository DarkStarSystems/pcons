# SPDX-License-Identifier: MIT
"""Tests for ninja generator multi-output support."""

from pathlib import Path

from pcons.core.builder import MultiOutputBuilder, OutputSpec
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.ninja import NinjaGenerator


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison."""
    return p.replace("\\", "/")


class TestNinjaMultiOutput:
    def test_multi_output_build_statement(self, tmp_path):
        """Test that multi-output builds generate correct ninja syntax."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("shlib")
        dll_node = FileNode("build/mylib.dll")
        lib_node = FileNode("build/mylib.lib")
        exp_node = FileNode("build/mylib.exp")
        source_node = FileNode("src/mylib.obj")

        # Simulate what MultiOutputBuilder would do
        dll_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": None,
            "sources": [source_node],
            "outputs": {
                "primary": {
                    "path": Path("build/mylib.dll"),
                    "suffix": ".dll",
                    "implicit": False,
                    "required": True,
                },
                "import_lib": {
                    "path": Path("build/mylib.lib"),
                    "suffix": ".lib",
                    "implicit": False,
                    "required": True,
                },
                "export_file": {
                    "path": Path("build/mylib.exp"),
                    "suffix": ".exp",
                    "implicit": True,
                    "required": True,
                },
            },
            "all_output_nodes": {
                "primary": dll_node,
                "import_lib": lib_node,
                "export_file": exp_node,
            },
        }
        dll_node.builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
                OutputSpec("export_file", ".exp", implicit=True),
            ],
            src_suffixes=[".obj"],
        )

        # Secondary nodes reference primary
        lib_node._build_info = {"primary_node": dll_node, "output_name": "import_lib"}
        lib_node.builder = dll_node.builder

        exp_node._build_info = {"primary_node": dll_node, "output_name": "export_file"}
        exp_node.builder = dll_node.builder

        target.output_nodes.extend([dll_node, lib_node, exp_node])
        target.add_source(source_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())

        # Should have explicit outputs followed by implicit outputs after |
        # Format: build explicit1 explicit2 | implicit1: rule deps
        assert "build/mylib.dll" in content
        assert "build/mylib.lib" in content
        assert "build/mylib.exp" in content

        # Check for implicit output syntax
        assert " | build/mylib.exp" in content

    def test_multi_output_variables(self, tmp_path):
        """Test that multi-output builds include out_<name> variables."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("shlib")
        dll_node = FileNode("build/mylib.dll")
        source_node = FileNode("src/mylib.obj")

        dll_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": None,
            "sources": [source_node],
            "outputs": {
                "primary": {
                    "path": Path("build/mylib.dll"),
                    "suffix": ".dll",
                    "implicit": False,
                    "required": True,
                },
                "import_lib": {
                    "path": Path("build/mylib.lib"),
                    "suffix": ".lib",
                    "implicit": False,
                    "required": True,
                },
            },
        }
        dll_node.builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        target.output_nodes.append(dll_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())

        # Check for output variables
        assert "out_primary = build/mylib.dll" in content
        assert "out_import_lib = build/mylib.lib" in content

    def test_secondary_nodes_not_written(self, tmp_path):
        """Test that secondary nodes don't get their own build statements."""
        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("shlib")
        dll_node = FileNode("build/mylib.dll")
        lib_node = FileNode("build/mylib.lib")
        source_node = FileNode("src/mylib.obj")

        dll_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": None,
            "sources": [source_node],
            "outputs": {
                "primary": {
                    "path": Path("build/mylib.dll"),
                    "suffix": ".dll",
                    "implicit": False,
                    "required": True,
                },
                "import_lib": {
                    "path": Path("build/mylib.lib"),
                    "suffix": ".lib",
                    "implicit": False,
                    "required": True,
                },
            },
        }
        dll_node.builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        # Secondary node references primary
        lib_node._build_info = {"primary_node": dll_node, "output_name": "import_lib"}
        lib_node.builder = dll_node.builder

        target.output_nodes.extend([dll_node, lib_node])
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())

        # Count build statements - should only have one for the actual build
        # (plus mkdir statements for directories)
        build_lines = [
            line for line in content.split("\n") if line.startswith("build ")
        ]
        # Filter out mkdir statements and phony targets (e.g., 'all')
        non_mkdir_builds = [
            line
            for line in build_lines
            if ": mkdir" not in line and ": phony" not in line
        ]
        # Should have just one build statement (for the multi-output)
        assert len(non_mkdir_builds) == 1


class TestNinjaSingleOutput:
    def test_single_output_unchanged(self, tmp_path):
        """Test that single-output builds still work normally."""
        from pcons.core.builder import CommandBuilder

        project = Project("test", root_dir=tmp_path, build_dir=".")

        target = Target("prog")
        exe_node = FileNode("build/app.exe")
        source_node = FileNode("build/main.obj")

        exe_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": None,
            "sources": [source_node],
        }
        exe_node.builder = CommandBuilder(
            "Program",
            "link",
            "progcmd",
            src_suffixes=[".obj"],
            target_suffixes=[".exe"],
        )

        target.output_nodes.append(exe_node)
        project.add_target(target)

        gen = NinjaGenerator()
        gen.generate(project)

        content = normalize_path((tmp_path / "build.ninja").read_text())

        # Should have normal single output
        assert "build build/app.exe:" in content
        # Should NOT have multi-output syntax
        assert " | " not in content.split("link_progcmd")[1].split("\n")[0]

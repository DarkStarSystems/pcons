# SPDX-License-Identifier: MIT
"""Tests for external/user-defined tools.

This test demonstrates how a user can create their own tool
without modifying pcons source code. We create a simple "concat"
tool that combines multiple text files into one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcons.core.builder import CommandBuilder
from pcons.core.environment import Environment
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


# ============================================================================
# User-defined tool: A simple file concatenator
# This could live in the user's project, not in pcons
# ============================================================================


class ConcatTool(BaseTool):
    """A simple tool that concatenates text files.

    This demonstrates how a user can create a custom build tool.
    The actual concatenation is done by a simple shell command.

    Usage:
        env.concat.Bundle("output.txt", ["file1.txt", "file2.txt"])
    """

    def __init__(self) -> None:
        super().__init__("concat")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cat",  # Unix cat command
            "flags": [],
            "header": "",  # Optional header text
            "footer": "",  # Optional footer text
            # Command template: cat all inputs and redirect to output
            # Uses typed markers (SourcePath/TargetPath) instead of string patterns
            "bundlecmd": [
                "$concat.cmd",
                "$concat.flags",
                SourcePath(),
                ">",
                TargetPath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Bundle": CommandBuilder(
                "Bundle",
                "concat",
                "bundlecmd",
                src_suffixes=[".txt", ".js", ".css", ".sql"],
                target_suffixes=[".txt", ".js", ".css", ".sql", ".bundle"],
                single_source=False,  # Multiple inputs allowed
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Check if cat command is available."""
        from pcons.core.toolconfig import ToolConfig

        # Simple check - try to run cat --version
        try:
            subprocess.run(
                ["cat", "--version"],
                capture_output=True,
                timeout=5,
            )
            # cat --version returns 0 on GNU coreutils, but may fail on BSD
            # Either way, if we get here without exception, cat exists
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

        return ToolConfig("concat", cmd="cat")


class ConcatToolchain(BaseToolchain):
    """A minimal toolchain containing just the concat tool.

    Users can create toolchains for their custom tools.
    """

    def __init__(self) -> None:
        super().__init__("concat")

    def _configure_tools(self, config: object) -> bool:
        concat = ConcatTool()
        concat_config = concat.configure(config)
        if concat_config is None:
            return False

        self._tools = {"concat": concat}
        return True


# ============================================================================
# Tests
# ============================================================================


class TestExternalTool:
    """Test that external tools can be created and used."""

    def test_tool_can_be_instantiated(self) -> None:
        """Test that we can create the tool."""
        tool = ConcatTool()
        assert tool.name == "concat"

    def test_tool_provides_builders(self) -> None:
        """Test that the tool provides builders."""
        tool = ConcatTool()
        builders = tool.builders()
        assert "Bundle" in builders
        assert builders["Bundle"].name == "Bundle"

    def test_tool_has_default_vars(self) -> None:
        """Test that the tool has default variables."""
        tool = ConcatTool()
        defaults = tool.default_vars()
        assert "cmd" in defaults
        assert defaults["cmd"] == "cat"
        assert "bundlecmd" in defaults

    def test_tool_can_setup_environment(self) -> None:
        """Test that the tool can be added to an environment."""
        env = Environment()
        tool = ConcatTool()
        tool.setup(env)

        # Tool namespace should exist
        assert env.has_tool("concat")

        # Tool variables should be accessible
        assert env.concat.cmd == "cat"

    def test_tool_builder_attached_to_environment(self) -> None:
        """Test that builders are attached to the tool namespace."""
        env = Environment()
        tool = ConcatTool()
        tool.setup(env)

        # Builder should be callable via tool namespace
        assert hasattr(env.concat, "Bundle")

    def test_builder_creates_node(self, tmp_path: Path) -> None:
        """Test that the builder creates target nodes."""
        env = Environment()
        env.build_dir = tmp_path

        tool = ConcatTool()
        tool.setup(env)

        # Create source files
        src1 = tmp_path / "file1.txt"
        src2 = tmp_path / "file2.txt"
        src1.write_text("Hello\n")
        src2.write_text("World\n")

        # Call the builder
        target = env.concat.Bundle(
            str(tmp_path / "output.txt"),
            [str(src1), str(src2)],
        )

        # Should return a node
        assert target is not None

    def test_tool_variables_can_be_modified(self) -> None:
        """Test that tool variables can be customized."""
        env = Environment()
        tool = ConcatTool()
        tool.setup(env)

        # Modify variables
        env.concat.flags = ["-n"]  # Number lines
        env.concat.header = "# Generated file"

        assert env.concat.flags == ["-n"]
        assert env.concat.header == "# Generated file"


class TestExternalToolchain:
    """Test that external toolchains work."""

    def test_toolchain_can_be_instantiated(self) -> None:
        """Test that we can create the toolchain."""
        toolchain = ConcatToolchain()
        assert toolchain.name == "concat"

    def test_toolchain_can_configure(self) -> None:
        """Test that the toolchain can configure its tools."""
        toolchain = ConcatToolchain()

        # Configure with a mock config object
        # The concat tool just needs to find 'cat'
        result = toolchain.configure(None)

        # On Unix systems, this should succeed
        # On Windows without cat, it might fail - that's OK
        if result:
            assert "concat" in toolchain.tools

    def test_environment_with_toolchain(self) -> None:
        """Test creating an environment with the toolchain."""
        toolchain = ConcatToolchain()
        if not toolchain.configure(None):
            pytest.skip("cat command not available")

        env = Environment(toolchain=toolchain)

        # Tool should be available
        assert env.has_tool("concat")
        assert hasattr(env.concat, "Bundle")


class TestMultipleExternalTools:
    """Test combining multiple external tools."""

    def test_multiple_tools_in_environment(self) -> None:
        """Test adding multiple custom tools to an environment."""
        env = Environment()

        # Add concat tool
        concat = ConcatTool()
        concat.setup(env)

        # We could add more tools...
        # For now, just verify concat works alongside built-in tools

        # Add a mock "cc" tool manually
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        # Both should coexist
        assert env.has_tool("concat")
        assert env.has_tool("cc")
        assert env.concat.cmd == "cat"
        assert env.cc.cmd == "gcc"


class TestNinjaGeneration:
    """Test that external tools integrate with ninja generation."""

    def test_generates_ninja_rule(self, tmp_path: Path) -> None:
        """Test that the tool generates valid ninja rules."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        # Create a project with the concat tool
        project = Project("test_concat", build_dir=tmp_path)
        env = project.Environment()

        concat = ConcatTool()
        concat.setup(env)

        # Create source files
        src1 = tmp_path / "file1.txt"
        src2 = tmp_path / "file2.txt"
        src1.write_text("Hello\n")
        src2.write_text("World\n")

        # Create a bundle target
        env.concat.Bundle(
            str(tmp_path / "output.txt"),
            [str(src1), str(src2)],
        )

        # Generate ninja file
        generator = NinjaGenerator()
        generator.generate(project)

        # Check that ninja file was created
        ninja_file = tmp_path / "build.ninja"
        assert ninja_file.exists()

        # Read and verify content
        content = ninja_file.read_text()

        # Should have a rule for the concat command
        assert "rule" in content
        # Should have a build statement for output.txt
        assert "output.txt" in content

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Uses cat command with shell redirection which doesn't work on Windows",
    )
    def test_end_to_end_build(self, tmp_path: Path) -> None:
        """Full end-to-end test: create files, generate ninja, run ninja, verify output."""
        # Check if ninja is available
        try:
            result = subprocess.run(
                ["ninja", "--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                pytest.skip("ninja not available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("ninja not available")

        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator

        # === Step 1: Create source files ===
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        file1 = src_dir / "header.txt"
        file2 = src_dir / "body.txt"
        file3 = src_dir / "footer.txt"

        file1.write_text("=== HEADER ===\n")
        file2.write_text("This is the body.\nIt has multiple lines.\n")
        file3.write_text("=== FOOTER ===\n")

        # === Step 2: Create pcons project and configure ===
        build_dir = tmp_path / "build"
        project = Project("concat_test", build_dir=build_dir)
        env = project.Environment()

        # Add our custom concat tool
        concat = ConcatTool()
        concat.setup(env)

        # Define the build target
        output_file = build_dir / "combined.txt"
        env.concat.Bundle(
            str(output_file),
            [str(file1), str(file2), str(file3)],
        )

        # === Step 3: Generate ninja file ===
        generator = NinjaGenerator()
        generator.generate(project)

        ninja_file = build_dir / "build.ninja"
        assert ninja_file.exists(), "build.ninja should be generated"

        # Print ninja file for debugging if test fails
        print("Generated build.ninja:")
        print(ninja_file.read_text())

        # === Step 4: Run ninja ===
        result = subprocess.run(
            ["ninja", "-f", str(ninja_file)],
            cwd=build_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        print("Ninja stdout:", result.stdout)
        print("Ninja stderr:", result.stderr)

        assert result.returncode == 0, f"Ninja failed: {result.stderr}"

        # === Step 5: Verify output ===
        assert output_file.exists(), "Output file should be created"

        content = output_file.read_text()
        print("Output file content:")
        print(content)

        # Verify content is concatenation of all inputs
        assert "=== HEADER ===" in content
        assert "This is the body." in content
        assert "It has multiple lines." in content
        assert "=== FOOTER ===" in content

        # Verify order (header should come before body, body before footer)
        header_pos = content.find("=== HEADER ===")
        body_pos = content.find("This is the body.")
        footer_pos = content.find("=== FOOTER ===")

        assert header_pos < body_pos < footer_pos, (
            "Files should be concatenated in order"
        )


class TestToolIntegration:
    """Test that external tools integrate with the rest of pcons."""

    def test_tool_with_variant(self) -> None:
        """Test that external tools work with variants."""
        env = Environment()

        concat = ConcatTool()
        concat.setup(env)

        # Set a variant (no effect on concat, but shouldn't break)
        env.variant = "release"

        assert env.concat.cmd == "cat"
        assert env.variant == "release"

    def test_tool_environment_clone(self) -> None:
        """Test that cloned environments preserve external tools."""
        env = Environment()

        concat = ConcatTool()
        concat.setup(env)
        env.concat.flags = ["-n"]

        # Clone the environment
        env2 = env.clone()

        # Tool should be in cloned env
        assert env2.has_tool("concat")
        assert env2.concat.flags == ["-n"]

        # Modifying clone shouldn't affect original
        env2.concat.flags.append("-v")
        assert "-v" not in env.concat.flags

    def test_variable_substitution(self) -> None:
        """Test that tool variables work with substitution."""
        env = Environment()

        concat = ConcatTool()
        concat.setup(env)

        # Test substitution
        result = env.subst("$concat.cmd")
        assert result == "cat"

        env.concat.flags = ["-n", "-v"]
        result = env.subst("$concat.cmd $concat.flags")
        assert result == "cat -n -v"

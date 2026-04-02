#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for the concat example.

This example demonstrates how to create and use a custom tool
that concatenates multiple text files into one.

Uses Python for cross-platform file concatenation.
"""

import sys

from pcons import Project
from pcons.core.builder import Builder, CommandBuilder
from pcons.tools.tool import BaseTool

# =============================================================================
# Custom Tool Definition
# =============================================================================


class ConcatTool(BaseTool):
    """A custom tool that concatenates text files.

    This demonstrates how users can create their own tools
    without modifying pcons source code.
    """

    def __init__(self) -> None:
        super().__init__("concat")

    def default_vars(self) -> dict[str, object]:
        # Use pcons helper for cross-platform file concatenation
        # This handles forward slashes and spaces in paths on all platforms
        python_cmd = sys.executable.replace("\\", "/")
        return {
            # Command as list of tokens for proper handling of spaces in paths
            "cmd": [python_cmd, "-m", "pcons.util.commands", "concat"],
            "flags": [],
            # Template expands $concat.cmd list into separate tokens
            "bundlecmd": ["$concat.cmd", "$concat.flags", "$$in", "$$out"],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Bundle": CommandBuilder(
                "Bundle",
                "concat",
                "bundlecmd",
                src_suffixes=[".txt"],
                target_suffixes=[".txt", ".bundle"],
                single_source=False,
            ),
        }


# =============================================================================
# Build Script
# =============================================================================

# Create project
project = Project("concat_example")

# Directories
src_dir = project.root_dir / "src"
build_dir = project.build_dir

# Create environment and add our custom tool
env = project.Environment()
concat_tool = ConcatTool()
concat_tool.setup(env)

# Define the build: combine all txt files into one
env.concat.Bundle(
    build_dir / "combined.txt",
    [
        src_dir / "header.txt",
        src_dir / "content.txt",
        src_dir / "footer.txt",
    ],
)

project.generate()

print(f"Generated {build_dir}")

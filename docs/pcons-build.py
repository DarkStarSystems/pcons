#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for pcons documentation.

This build script demonstrates pcons dogfooding by using pcons to build
its own documentation. It showcases:
- Custom tool creation (GitInfo for version injection)
- Multi-step build pipelines
- Variable substitution in commands

Usage:
    python docs/pcons-build.py
    ninja -C docs/build
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pcons.core.builder import Builder, CommandBuilder
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.tools.tool import BaseTool

# =============================================================================
# Custom Tools
# =============================================================================


class GitInfoTool(BaseTool):
    """Tool that extracts version information from git.

    This demonstrates creating a custom tool that generates files
    from external commands (git). The generated version info can
    be used in documentation footers, about dialogs, etc.

    Builders:
        VersionFile: Generates a text file with git version info
    """

    def __init__(self) -> None:
        super().__init__("gitinfo")

    def default_vars(self) -> dict[str, object]:
        return {
            "git": "git",
            # Command that outputs version info to stdout, redirected to file
            # Escaping: $$$$ in pcons -> $$ in ninja -> $ in shell
            "versioncmd": (
                "/bin/sh -c '"
                'echo "pcons $$$$(git describe --tags --always 2>/dev/null || echo dev) '
                '| $$$$(git log -1 --format=%cd --date=short 2>/dev/null || date +%Y-%m-%d)"'
                "' > $$out"
            ),
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "VersionFile": CommandBuilder(
                "VersionFile",
                "gitinfo",
                "versioncmd",
                src_suffixes=[],  # No source files needed
                target_suffixes=[".txt"],
                single_source=False,
            ),
        }


class PandocTool(BaseTool):
    """Tool for converting Markdown to HTML using Pandoc.

    Pandoc is a universal document converter. This tool wraps it
    for markdown-to-HTML conversion with template support.

    Builders:
        Html: Converts .md files to .html
    """

    def __init__(self) -> None:
        super().__init__("pandoc")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "pandoc",
            "flags": ["--standalone", "--toc", "--toc-depth=2"],
            "template": "",  # Set to --template=path if using template
            "metadata": [],  # Additional --metadata flags
            "variables": [],  # Additional --variable flags
            "htmlcmd": (
                "$pandoc.cmd $pandoc.flags $pandoc.template "
                "$pandoc.metadata $pandoc.variables "
                "-f markdown -t html -o $$out $$in"
            ),
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Html": CommandBuilder(
                "Html",
                "pandoc",
                "htmlcmd",
                src_suffixes=[".md"],
                target_suffixes=[".html"],
                single_source=True,
            ),
        }


class InsertFooterTool(BaseTool):
    """Tool for inserting content into HTML files.

    Replaces a placeholder in an HTML file with content from another file.
    Used to inject version info into the documentation footer.

    Builders:
        Insert: Replaces placeholder in HTML with content from a file
    """

    def __init__(self) -> None:
        super().__init__("insertfooter")

    def default_vars(self) -> dict[str, object]:
        return {
            "placeholder": "{{VERSION_INFO}}",
            # Use awk instead of sed to avoid delimiter issues
            # Escaping: $$$$ -> $$ in ninja -> $ in shell
            # $$in/$$out -> $in/$out in ninja (ninja variables)
            "insertcmd": (
                "awk -v ver=\"$$$$(cat $$$$(echo $$in | cut -d' ' -f2))\" "
                "'{gsub(/{{VERSION_INFO}}/,ver)}1' "
                "$$$$(echo $$in | cut -d' ' -f1) > $$out"
            ),
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Insert": CommandBuilder(
                "Insert",
                "insertfooter",
                "insertcmd",
                src_suffixes=[".html", ".txt"],
                target_suffixes=[".html"],
                single_source=False,
            ),
        }


# =============================================================================
# Build Configuration
# =============================================================================


def get_git_info() -> str:
    """Get git version info for display purposes during build."""
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        version = tag.stdout.strip() if tag.returncode == 0 else "dev"

        date = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=short"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        date_str = date.stdout.strip() if date.returncode == 0 else "unknown"

        return f"pcons {version} | {date_str}"
    except Exception:
        return "pcons dev"


def main() -> None:
    # Directories
    docs_dir = Path(__file__).parent
    build_dir = docs_dir / "build"
    template_file = docs_dir / "template.html"

    # Create project
    project = Project("pcons-docs", build_dir=build_dir)
    env = project.Environment()

    # Set up custom tools
    gitinfo_tool = GitInfoTool()
    gitinfo_tool.setup(env)

    pandoc_tool = PandocTool()
    pandoc_tool.setup(env)

    footer_tool = InsertFooterTool()
    footer_tool.setup(env)

    # Configure pandoc with our template
    env.pandoc.template = f"--template={template_file}"
    env.pandoc.metadata = ["--metadata=title:'pcons User Manual'"]
    # Note: We don't pass version-info to pandoc since we inject it separately

    # ==========================================================================
    # Build Rules
    # ==========================================================================

    # Step 1: Generate version info file
    # This creates build/version.txt with git tag/sha and date
    env.gitinfo.VersionFile(
        build_dir / "version.txt",
        [],  # No inputs - reads from git
    )

    # Step 2: Convert markdown to HTML (with placeholder for version)
    env.pandoc.Html(
        build_dir / "index.tmp.html",
        docs_dir / "index.md",
    )

    # Step 3: Insert version info into the HTML footer
    env.insertfooter.Insert(
        build_dir / "index.html",
        [build_dir / "index.tmp.html", build_dir / "version.txt"],
    )

    # ==========================================================================
    # Generate Ninja Build File
    # ==========================================================================

    generator = NinjaGenerator()
    generator.generate(project)

    # Print status
    git_info = get_git_info()
    print(f"Generated {build_dir / 'build.ninja'}")
    print(f"Version: {git_info}")
    print()
    print("To build the documentation:")
    print(f"  ninja -C {build_dir}")
    print()
    print(f"Output will be at: {build_dir / 'index.html'}")


if __name__ == "__main__":
    main()

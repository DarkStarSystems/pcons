#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for pcons documentation.

This build script demonstrates pcons dogfooding by using pcons to build
its own documentation. It showcases:
- Custom tool creation (GitInfo for version injection)
- Multi-step build pipelines
- Variable substitution in commands

Usage:
    pcons -C docs
"""

from __future__ import annotations

import sys

from pcons.core.builder import Builder, CommandBuilder
from pcons.core.project import Project
from pcons.core.subst import PathToken, SourcePath, TargetPath
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
        # A Python one-liner instead of shell substitution: commands kept
        # as token lists need no quoting or $-escaping, and work
        # identically on Windows. (Single line: ninja commands can't
        # contain newlines.)
        script = (
            "import subprocess, sys, pathlib, datetime; "
            "g = lambda *a: subprocess.run(a, capture_output=True, text=True)"
            ".stdout.strip(); "
            "ver = g('git', 'describe', '--tags', '--always') or 'dev'; "
            "date = g('git', 'log', '-1', '--format=%cd', '--date=short') "
            "or datetime.date.today().isoformat(); "
            "pathlib.Path(sys.argv[1]).write_text("
            "f'pcons {ver} | {date}\\n', encoding='utf-8')"
        )
        return {
            "python": sys.executable,
            "versioncmd": ["$gitinfo.python", "-c", script, TargetPath()],
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
        # sys.argv: [html, version_file, output]. Token-list command:
        # no shell, no escaping, cross-platform.
        script = (
            "import sys, pathlib; "
            "html = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'); "
            "ver = pathlib.Path(sys.argv[2]).read_text(encoding='utf-8').strip(); "
            "pathlib.Path(sys.argv[3]).write_text("
            "html.replace('{{VERSION_INFO}}', ver), encoding='utf-8')"
        )
        return {
            "python": sys.executable,
            # $in expands to both inputs in order (html, version file),
            # then the output: argv[1..3] line up with the script.
            "insertcmd": [
                "$insertfooter.python",
                "-c",
                script,
                SourcePath(),
                TargetPath(),
            ],
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
# Build Description
# =============================================================================

project = Project("pcons-docs")
env = project.Environment()

GitInfoTool().setup(env)
PandocTool().setup(env)
InsertFooterTool().setup(env)

# Configure pandoc with our template. A PathToken keeps the flag relocatable:
# the generator relativizes the path for the build file it writes.
env.pandoc.template = PathToken(
    prefix="--template=", path="template.html", path_type="project"
)
env.pandoc.metadata = ["--metadata=title:'pcons User Manual'"]

# Target paths are relative to the build directory, sources to the project
# root (this file's directory). Build files are generated automatically once
# the script finishes.

# Step 1: version info from git, at build time
version = env.gitinfo.VersionFile("version.txt", [])  # no inputs: reads git

# Step 2: markdown to HTML, with a placeholder where the version goes
page = env.pandoc.Html("index.tmp.html", "index.md")

# Step 3: inject the version info into the HTML footer
env.insertfooter.Insert("index.html", [*page, *version])

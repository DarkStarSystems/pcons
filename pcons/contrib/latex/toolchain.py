# SPDX-License-Identifier: MIT
"""LaTeX toolchain for pcons.

Provides PDF document compilation from LaTeX sources using latexmk.
The toolchain handles multi-pass compilation, bibliography processing
(bibtex/biber), index generation (makeindex), and dependency tracking
automatically via latexmk.

Toolchain tools:
- latex: Provides the Pdf builder for .tex → .pdf compilation

The latex tool variables can be customized per-environment::

    env.latex.engine = "xelatex"        # pdflatex, xelatex, lualatex
    env.latex.flags.append("-shell-escape")
    env.latex.cmd = "/usr/local/bin/latexmk"  # custom latexmk path
"""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING

from pcons.core.builder import CommandBuilder
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain, toolchain_registry

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


class LatexTool(BaseTool):
    """LaTeX document tool using latexmk.

    Variables:
        cmd: latexmk command (default: ``latexmk``)
        engine: LaTeX engine — ``pdflatex``, ``xelatex``, or ``lualatex``
        flags: Additional latexmk flags (list)
        pdfcmd: Command template for .tex → .pdf compilation
    """

    def __init__(self) -> None:
        super().__init__("latex")

    def default_vars(self) -> dict[str, object]:
        python_cmd = sys.executable.replace("\\", "/")
        return {
            "cmd": "latexmk",
            "engine": "pdflatex",
            "flags": [],
            "pdfcmd": [
                python_cmd,
                "-m",
                "pcons.util.latex_deps",
                "--engine",
                "$latex.engine",
                "--latexmk",
                "$latex.cmd",
                "--depfile",
                TargetPath(suffix=".d"),
                "-o",
                TargetPath(),
                "$latex.flags",
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Pdf": CommandBuilder(
                "Pdf",
                "latex",
                "pdfcmd",
                src_suffixes=[".tex"],
                target_suffixes=[".pdf"],
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
                restat=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """No per-tool configuration needed; latexmk handles detection."""
        return None


class LatexToolchain(BaseToolchain):
    """LaTeX toolchain for building PDF documents.

    Uses latexmk for multi-pass compilation and automatic auxiliary tool
    management (bibtex/biber, makeindex, etc.).

    Usage::

        from pcons.contrib.latex import find_latex_toolchain

        toolchain = find_latex_toolchain()
        env = project.Environment(toolchain=toolchain)
        env.latex.Pdf(build_dir / "paper.pdf", src_dir / "paper.tex")
    """

    def __init__(self) -> None:
        super().__init__("latex")

    def _configure_tools(self, config: object) -> bool:
        """Configure tools if latexmk is available."""
        # Check for latexmk
        if not shutil.which("latexmk"):
            return False

        self._tools = {"latex": LatexTool()}
        return True


toolchain_registry.register(
    LatexToolchain,
    aliases=["latex", "latexmk"],
    check_command="latexmk",
    tool_classes=[LatexTool],
    category="latex",
    description="LaTeX document compilation via latexmk",
)

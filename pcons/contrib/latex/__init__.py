# SPDX-License-Identifier: MIT
"""LaTeX toolchain for pcons.

Provides PDF document compilation from LaTeX sources using latexmk.

Usage::

    from pcons.contrib.latex import find_latex_toolchain

    project = Project("my_paper")
    env = project.Environment(toolchain=find_latex_toolchain())
    env.latex.Pdf(build_dir / "paper.pdf", src_dir / "paper.tex")

    # Customize engine (default: pdflatex)
    env.latex.engine = "xelatex"
    env.latex.flags.append("-shell-escape")

Requirements:
    A TeX distribution with ``latexmk`` in PATH (TeX Live, MiKTeX).
"""

from __future__ import annotations

from pcons.contrib.latex.toolchain import LatexTool, LatexToolchain

__all__ = ["LatexTool", "LatexToolchain", "find_latex_toolchain"]


def find_latex_toolchain() -> LatexToolchain:
    """Find and return a configured LaTeX toolchain.

    Returns:
        A configured LatexToolchain instance.

    Raises:
        RuntimeError: If latexmk is not found in PATH.
    """
    toolchain = LatexToolchain()
    # Use a lightweight configure — just check for latexmk
    toolchain._configured = toolchain._configure_tools(None)
    if not toolchain._configured:
        msg = (
            "LaTeX toolchain not found. Install a TeX distribution "
            "(TeX Live or MiKTeX) with 'latexmk' in PATH."
        )
        raise RuntimeError(msg)
    return toolchain

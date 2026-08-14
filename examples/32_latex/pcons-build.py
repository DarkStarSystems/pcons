# SPDX-License-Identifier: MIT
"""Build script for a LaTeX project.

Demonstrates building a PDF from LaTeX sources using latexmk.
The LaTeX toolchain handles multi-pass compilation, bibliography
processing, and dependency tracking automatically.

Requirements:
    TeX distribution with latexmk (TeX Live, MiKTeX)

Usage:
    uvx pcons          # configure + generate + build
"""

import sys

from pcons import Project
from pcons.contrib.latex import find_latex_toolchain

# Before the project, not after: a script that stops here describes no build,
# and pcons expects a script that describes one to run it.
try:
    toolchain = find_latex_toolchain()
except RuntimeError as e:
    print(f"Skipping: {e}", file=sys.stderr)
    sys.exit(0)

project = Project("latex_example")
src_dir = project.root_dir / "src"
build_dir = project.build_dir

env = project.Environment(toolchain=toolchain)

# Build PDF — latexmk handles bibtex, multi-pass, etc. automatically
env.latex.Pdf(build_dir / "main.pdf", src_dir / "main.tex")

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write a C file returning the text of version.txt, read from beside this
script."""

import sys
from pathlib import Path

here = Path(__file__).resolve().parent
text = (here / "version.txt").read_text(encoding="utf-8").strip()

out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    f'const char *bar_version(void) {{ return "{text}"; }}\n',
    encoding="utf-8",
)

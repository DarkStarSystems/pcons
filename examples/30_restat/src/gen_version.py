#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate version.h from version.txt.

Writes the output only if the content changed, demonstrating
how restat avoids rebuilding downstream targets when a code
generator produces identical output.
"""

import sys
from pathlib import Path

version = Path(sys.argv[1]).read_text().strip()
output = Path(sys.argv[2])
content = f'#define VERSION "{version}"\n'

# Write-if-changed: only update the file when the content differs.
# Combined with restat=True in the build script, Ninja will skip
# recompiling main.c when the generated header hasn't changed.
if output.exists() and output.read_text() == content:
    pass  # no change needed
else:
    output.write_text(content)

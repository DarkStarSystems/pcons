#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating Ninja's restat feature.

This example uses a code generator (gen_version.py) that produces a
version.h header from version.txt. The generator writes the file only
when the content changes.

With ``restat=True`` on the Command, Ninja re-checks the output
timestamp after running the generator. If version.h wasn't actually
modified, Ninja skips recompiling main.c and relinking — saving
potentially expensive rebuilds in large projects.
"""

import os
import sys
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

project = Project("restat_example", build_dir=build_dir)
env = project.Environment(toolchain=find_c_toolchain())

# Generate version.h from version.txt, with restat so unchanged
# output doesn't trigger downstream rebuilds.
python = sys.executable.replace("\\", "/")
gen_script = src_dir / "gen_version.py"
gen = env.Command(
    target=build_dir / "version.h",
    source=[src_dir / "version.txt", gen_script],
    command=f"{python} ${{SOURCES[1]}} ${{SOURCES[0]}} $TARGET",
    restat=True,
)

# Compile the program — depends on the generated header.
env.cc.includes.append(str(build_dir))
app = project.Program("app", env, sources=[src_dir / "main.c"])
app.depends(gen)  # implicit dep: build gen first, but don't link version.h

Generator().generate(project)
print(f"Generated {build_dir}")

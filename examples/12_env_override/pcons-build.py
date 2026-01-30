#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating env.override() for per-file settings.

This example demonstrates using env.override() to compile specific
source files with different flags - like extra defines or includes.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

toolchain = find_c_toolchain()
project = Project("override_example", build_dir=build_dir)

env = project.Environment(toolchain=toolchain)

# Get correct suffixes for this toolchain (.o/.obj for objects, .exe on Windows)
obj_suffix = toolchain.get_object_suffix()
prog_name = toolchain.get_program_name("demo")

# Compile main.c with standard settings
# Object() returns a list, use [0] to get the node
main_obj = env.cc.Object(build_dir / f"main{obj_suffix}", src_dir / "main.c")[0]

# Compile extra.c with additional define and include path using override()
with env.override() as extra_env:
    extra_env.cc.defines.append("HAS_EXTRA_FEATURE=1")
    extra_env.cc.includes.append(include_dir)
    extra_obj = extra_env.cc.Object(
        build_dir / f"extra{obj_suffix}", src_dir / "extra.c"
    )[0]

# Link both objects into the program
env.link.Program(build_dir / prog_name, [main_obj, extra_obj])

Generator().generate(project)
print(f"Generated {build_dir}")

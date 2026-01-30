#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating paths with spaces.

This example shows that pcons correctly handles:
- Source directories with spaces: "src with spaces/"
- Source filenames with spaces: "my program.c"
- Include directories with spaces: "My Headers/"
- Preprocessor defines with spaces in values: GREETING_MESSAGE="Hello World"

The build system properly escapes/quotes these for:
- Ninja build files (spaces escaped as "$ ")
- Makefile (shell quoting with single quotes)
- compile_commands.json (shlex quoting for IDEs)

This is important for projects on systems where paths commonly
contain spaces (Windows, macOS with iCloud, etc.).
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories - note the spaces in names!
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
example_dir = Path(__file__).parent
src_dir = example_dir / "src with spaces"
include_dir = example_dir / "My Headers"

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("paths_with_spaces", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Create program target
# - Source file has spaces in both directory and filename
# - Include directory has spaces
# - Define has a string value with spaces
program = project.Program("my_program", env)
program.add_sources([src_dir / "my program.c"])

# Add include directory with spaces
program.public.include_dirs.append(include_dir)

# Add define with a string value containing spaces
# The quotes are part of the C string literal
program.public.defines.append('GREETING_MESSAGE="Hello from pcons!"')

# Add warning flags
if toolchain.name in ("msvc", "clang-cl"):
    program.private.compile_flags.extend(["/W4"])
else:
    program.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve and generate
project.resolve()

generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")

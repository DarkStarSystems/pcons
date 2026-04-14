#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example demonstrating paths embedded in flags.

This example shows how to use PathToken to embed a file path
inside an arbitrary linker flag, ensuring it gets properly
relativized by the generator.

On macOS, -Wl,-force_load,<path> tells the linker to load all
symbols from a static library (similar to --whole-archive on Linux).
This is a case where the path is embedded inside a flag, not a
standalone argument.

This example just checks that the generated build.ninja has the
correct path relativization — it doesn't require macOS-specific
linker behavior.
"""

import sys

from pcons import Project, find_c_toolchain
from pcons.core.subst import PathToken

project = Project("path_in_flags")
env = project.Environment(toolchain=find_c_toolchain())

src_dir = project.root_dir / "src"

# Build a static library
lib = project.StaticLibrary("mylib", env)
lib.add_sources([src_dir / "mylib.c"])

# Build main program, linking the library
prog = project.Program("main", env)
prog.add_sources([src_dir / "main.c"])

# Instead of plain string: prog.public.link_flags.append(f"-Wl,-force_load,{lib_path}")
# Use PathToken so the path gets properly relativized in the generated build file.
# The library output path is relative to build_dir.
lib_output = "libmylib.a"
if sys.platform == "win32":
    lib_output = "mylib.lib"

prog.private.link_flags.append(
    PathToken(prefix="-Wl,-force_load,", path=lib_output, path_type="build")
)

# Also link normally so the linker finds the symbols
prog.link(lib)

project.generate()

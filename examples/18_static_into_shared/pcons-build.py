#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script testing static library linked into shared library.

This example tests the pattern:
1. Static library (libcore.a) with public includes
2. Shared library (libwrapper.so/dylib) that links the static library
   - Should inherit public includes from core_lib
3. Executable that links the shared library

Expected: core.h is found via public include propagation,
and core_value() from static lib is available in shared lib.
"""

from pcons import Project, install_dir

# Create project
project = Project("static_into_shared")

# Directories
src_dir = project.root_dir / "src"
env = project.Environment(toolchain="c")

# 1. Create static library from core.c with PUBLIC include directory
#    This should propagate to any target that links core_lib
core_lib = project.StaticLibrary("core", env, sources=[src_dir / "core.c"])
core_lib.public.include_dirs.append(src_dir)  # So dependents can #include "core.h"

# 2. Create shared library from wrapper.c, linking the static library
#    wrapper.c includes "core.h" which should be found via core_lib's public includes
# This example deliberately uses the low-level link_libs lists (the power form)
# instead of link()/link_private(), to keep that API exercised in CI.
wrapper_lib = project.SharedLibrary("wrapper", env, sources=[src_dir / "wrapper.c"])
wrapper_lib.public.link_libs.append(core_lib)

# 3. Create executable that links the shared library
prog = project.Program("demo", env, sources=[src_dir / "main.c"])
prog.private.link_libs.append(wrapper_lib)

# install_dir() resolves the conventional subdir from the env's toolchain:
# shared libs land in "lib" (or "bin" on DLL platforms), archives in "lib",
# programs in "bin".
installed_libs = project.Install(
    install_dir(env, "shared_library"),
    [wrapper_lib],
    name="install-libraries",
)
installed_archives = project.Install(
    install_dir(env, "static_library"),
    [core_lib],
    name="install-archives",
)
installed_bins = project.Install(
    install_dir(env, "program"),
    [prog],
    name="install-binaries",
)

project.Alias("install", [installed_libs, installed_archives, installed_bins])

# Resolve to inspect resolved state (debug purposes only)
project.resolve()

# Debug output
print(f"core_lib output_nodes: {core_lib.output_nodes}")
print(f"core_lib public.include_dirs: {list(core_lib.public.include_dirs)}")
print(f"wrapper_lib output_nodes: {wrapper_lib.output_nodes}")
print(f"wrapper_lib dependencies: {wrapper_lib.dependencies}")
print(f"prog output_nodes: {prog.output_nodes}")

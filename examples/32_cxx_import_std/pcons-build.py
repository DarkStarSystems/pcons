#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++23 program that uses `import std;`.

This exercises pcons's standard-library module support across toolchains:
  - On MSVC, pcons synthesizes a build node for
    `%VCToolsInstallDir%/modules/std.ixx` and links the resulting `.obj`.
  - On clang/libc++, pcons consults `libc++.modules.json` (queried via
    `clang++ -stdlib=libc++ -print-file-name=c++/libc++.modules.json`),
    locates `std.cppm`, builds it, and links the resulting `.o`.

The user code itself is portable: a single-module library that exposes
`greet()`, and a `main` that uses `std::println` (C++23). Both files use
`import std;` — pcons + the toolchain do the rest.
"""

from pcons import Project
from pcons.toolchains import find_c_toolchain

project = Project("cxx_import_std")
# Prefer MSVC on Windows (its `import std;` lives in std.ixx and works
# out of the box). Elsewhere, prefer LLVM/Clang with libc++.
toolchain = find_c_toolchain(prefer=["msvc", "llvm"])
env = project.Environment(toolchain=toolchain)

if toolchain.name == "msvc":
    env.cxx.flags.extend(["/std:c++latest", "/EHsc", "/permissive-"])
else:
    env.cxx.flags.extend(["-std=c++23", "-stdlib=libc++"])

project.Program(
    "hello",
    env,
    sources=["src/Greet.cppm", "src/main.cpp"],
)

project.generate()

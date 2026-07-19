#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++23 program that uses `import std;`.

This exercises pcons's standard-library module support across toolchains:
  - On MSVC, pcons synthesizes a build node for
    `%VCToolsInstallDir%/modules/std.ixx` and links the resulting `.obj`.
  - On clang/libc++, pcons consults `libc++.modules.json` (queried via
    `clang++ -stdlib=libc++ -print-file-name=c++/libc++.modules.json`),
    locates `std.cppm`, builds it, and links the resulting `.o`.
  - On GCC/libstdc++ (>= 15), pcons probes `#include <bits/std.cc>` via
    `-E -x c++ - -H`, compiles the discovered source with `-fmodules`,
    and links the resulting `.o`. GCC writes `gcm.cache/std.gcm` next to
    the build directory automatically.

The user code itself is portable: a single-module library that exposes
`greet()`, and a `main` that uses `std::println` (C++23). Both files use
`import std;` — pcons + the toolchain do the rest.
"""

from pcons import Project, get_var
from pcons.toolchains import find_c_toolchain

project = Project("cxx_import_std")

# Optional override: pcons build TOOLCHAIN=gcc|llvm|msvc
toolchain_override = get_var("TOOLCHAIN", None)
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    # Prefer MSVC on Windows (its `import std;` lives in std.ixx and works
    # out of the box). Elsewhere, prefer LLVM/Clang with libc++, then GCC.
    toolchain = find_c_toolchain(prefer=["msvc", "llvm", "gcc"])
env = project.Environment(toolchain=toolchain)

# import std needs C++23 (MSVC has no /std:c++23, so it maps to /std:c++latest).
env.cxx.set_standard("c++23")
if toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")  # libc++ ships the std module
    env.link.libs.append("c++")

project.Program(
    "hello",
    env,
    sources=["src/Greet.cppm", "src/main.cpp"],
)

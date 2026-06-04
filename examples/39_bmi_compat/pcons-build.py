#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating C++20 module interface (BMI) reuse across targets.

A Binary Module Interface (`.gcm` on GCC) can only be consumed by translation
units compiled with matching BMI-sensitive flags (C++ dialect, ABI knobs, ...).
pcons keys each BMI by a hash of those flags and stores it under
`cxx_modules/<hash>/<module>.gcm`, wiring every translation unit to the right
BMI via a per-key GCC module mapper file. As a result:

  - lib1 and lib2 compile `provider.cppm` with identical flags (`-std=c++23`),
    so they share one compiled interface (`cxx_modules/<hashA>/provider.gcm`).
    pcons's object cache already collapses the two identical compiles into a
    single object + BMI.
  - lib3 compiles `provider.cppm` with `-std=c++26` - a BMI-incompatible
    dialect - so it gets its own interface (`cxx_modules/<hashB>/provider.gcm`).

Status by toolchain:
  - GCC:   supported (per-key BMI dirs + module mapper files).
  - clang: supported (per-key dirs + -fmodule-output / -fprebuilt-module-path).
  - MSVC:  not yet wired for cross-target BMI reuse.
"""

from pcons import Project, find_c_toolchain, get_var

project = Project("bmi-compat")

toolchain_override = get_var("TOOLCHAIN")
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

# lib1 and lib2: identical (-std=c++23) -> share one compiled interface.
lib1 = project.StaticLibrary("lib1", env, sources=["provider.cppm", "consumer.cpp"])
lib1.private.compile_flags.append("-std=c++23")

lib2 = project.StaticLibrary("lib2", env, sources=["provider.cppm", "consumer.cpp"])
lib2.private.compile_flags.append("-std=c++23")

# lib3: -std=c++26 is a BMI breaker -> gets its own compiled interface.
lib3 = project.StaticLibrary("lib3", env, sources=["provider.cppm", "consumer.cpp"])
lib3.private.compile_flags.append("-std=c++26")

# A program to prove the c++23 interface links and runs end to end.
app = project.Program("app", env, sources=["main.cpp"])
app.private.compile_flags.append("-std=c++23")
app.link(lib1)

project.generate()

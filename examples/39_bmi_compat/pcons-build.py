#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating C++20 module interface (BMI) reuse across targets.

A Binary Module Interface (`.gcm` on GCC, `.pcm` on clang, `.ifc` on MSVC) can
only be consumed by translation units compiled with matching BMI-sensitive
flags (C++ dialect, ABI knobs, ...). pcons keys each BMI by a hash of those
flags and stores it under `cxx_modules/<hash>/<module>.<ext>`, wiring every
translation unit to the right BMI. As a result:

  - lib1 and lib2 compile `provider.cppm` with identical flags, so they share
    one compiled interface (`cxx_modules/<hashA>/provider.<ext>`). pcons's
    object cache already collapses the two identical compiles into a single
    object + BMI.
  - lib3 compiles `provider.cppm` with a BMI-incompatible dialect, so it gets
    its own interface (`cxx_modules/<hashB>/provider.<ext>`).

Dialect flags are toolchain-specific (GCC/clang use `-std=`, MSVC uses
`/std:`), so the script selects a "shared" and a BMI-breaking dialect per
toolchain.

Status by toolchain:
  - GCC:   supported (per-key BMI dirs + module mapper files).
  - clang: supported (per-key dirs + -fmodule-output / -fprebuilt-module-path).
  - MSVC:  supported (per-key dirs + /ifcOutput / /ifcSearchDir).
"""

from pcons import Project, find_c_toolchain, get_var

project = Project("bmi-compat")

toolchain_override = get_var("TOOLCHAIN")
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

# Pick a shared dialect and a BMI-incompatible "breaker" dialect per toolchain.
if toolchain.name == "msvc":
    env.cxx.flags.append("/EHsc")
    shared_dialect = ["/std:c++20"]
    breaker_dialect = ["/std:c++latest"]
else:
    shared_dialect = ["-std=c++20"]
    breaker_dialect = ["-std=c++23"]

# lib1 and lib2: identical flags -> share one compiled interface.
lib1 = project.StaticLibrary("lib1", env, sources=["provider.cppm", "consumer.cpp"])
lib1.private.compile_flags.extend(shared_dialect)

lib2 = project.StaticLibrary("lib2", env, sources=["provider.cppm", "consumer.cpp"])
lib2.private.compile_flags.extend(shared_dialect)

# lib3: a BMI-breaking dialect -> gets its own compiled interface.
lib3 = project.StaticLibrary("lib3", env, sources=["provider.cppm", "consumer.cpp"])
lib3.private.compile_flags.extend(breaker_dialect)

# A program to prove the shared interface links and runs end to end.
app = project.Program("app", env, sources=["main.cpp"])
app.private.compile_flags.extend(shared_dialect)
app.link(lib1)

project.generate()

# SPDX-License-Identifier: MIT
"""Precompiled headers: headers compiled once per target, not once per source.

`app` lists the headers its sources include, C++ ones for its C++ sources and
C ones for its C source. pcons compiles each set once, with exactly the flags
those sources compile with, and every source compiles against the result.
The sources don't change: each still includes what it uses.

`tests` compiles with the same settings as `app`, so it reuses app's
precompiled C++ header rather than making its own. A target that compiled
differently would be an error at generate time, not a compiler refusal
halfway through the build.

Turn them off with `pcons PCONS_PRECOMPILE_HEADERS=0`, to check that every
source includes what it needs.
"""

from pcons import Project

project = Project("precompiled_headers")
env = project.Environment(toolchain="c")
env.cxx.set_standard("c++17")

app = project.Program(
    "app", env, sources=["src/main.cpp", "src/words.cpp", "src/checksum.c"]
)
app.set_option(
    "precompile_headers",
    {"cxx": ["src/words.h", "<iostream>"], "c": ["<string.h>"]},
)

tests = project.Program("tests", env, sources=["tests/test_words.cpp", "src/words.cpp"])
tests.private.include_dirs.append("src")
tests.set_option("precompile_headers", app)

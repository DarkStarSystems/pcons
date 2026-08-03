#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compiling one file differently from its neighbours.

Two ways, for two different situations:

1. ``target.add_sources([...], env=other_env)`` — the file stays part of the
   target, so it keeps the target's include dirs, defines, and everything
   inherited from its dependencies. Only the environment layer changes. This
   is what you want for a per-file flag tweak: the one source that miscompiles
   at -O2, the one that needs -fno-strict-aliasing, the one that wants a
   define its neighbours must not see.

2. ``env.cc.Object()`` — compile a source to a standalone object node and use
   it as a source. Use this when the *object itself* is the thing you want to
   share: several targets can link the same object without recompiling it.
   It's outside any target, so the target's usage requirements don't apply.
"""

from pcons import Project

project = Project("object_sources")

src_dir = project.root_dir / "src"
build_dir = project.build_dir
env = project.Environment(toolchain="c")

# (2) A standalone object, compiled once, usable by any target.
obj_suffix = env.toolchain.get_object_suffix()  # .o on Unix, .obj on Windows
helper_obj = env.cc.Object(
    build_dir / f"helper{obj_suffix}",
    src_dir / "helper.c",
)

prog = project.Program("demo", env)
prog.add_sources([src_dir / "main.c", helper_obj[0]])

# The target's own requirements reach every source it owns...
prog.private.defines.append("IN_DEMO=1")

# ...including this one, which additionally gets -DTUNED.
with env.override() as tuned_env:
    tuned_env.cc.defines.append("TUNED")
    prog.add_sources([src_dir / "tuned.c"], env=tuned_env)

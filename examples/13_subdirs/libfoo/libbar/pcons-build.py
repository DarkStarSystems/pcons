#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for libbar - a library nested two levels down.

Like libfoo, this builds either on its own or as part of a parent build.
Nothing here is written differently for the two cases: `project.root_dir`
and `project.build_dir` always refer to this directory and this library's
own build output, wherever it sits in a larger tree.
"""

from pcons import Project

project = Project("libbar")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

libbar = project.StaticLibrary("bar", env, sources=["src/bar.c"])
libbar.public.include_dirs.append("include")

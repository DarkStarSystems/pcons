# SPDX-License-Identifier: MIT
"""A small compression library, vendored as its own project."""

from pcons import Project

project = Project("zlite")
if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

lib = project.StaticLibrary("zlite", env, sources=["src/zlite.c"])
lib.public.include_dirs.append("include")

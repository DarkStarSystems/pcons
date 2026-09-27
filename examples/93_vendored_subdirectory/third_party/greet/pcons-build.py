# SPDX-License-Identifier: MIT
"""A small library with a demo program: builds on its own or as a dependency."""

from pcons import Project

project = Project("greet")
if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

lib = project.StaticLibrary("greet", env, sources=["src/greet.c"])
lib.public.include_dirs.append("include")

demo = project.Program("greet-demo", env, sources=["src/demo.c"])
demo.link(lib)

# Built on its own, this project's plain `ninja` builds the demo.
project.Default(demo)

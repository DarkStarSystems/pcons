# SPDX-License-Identifier: MIT
"""An image library that depends on zlite, vendored as its own project.

It finds zlite the way it would anywhere: with find_package().
"""

from pcons import Project

project = Project("pngish")
if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

zlite = project.find_package("zlite", version=">=1.2")

lib = project.StaticLibrary("pngish", env, sources=["src/pngish.c"])
lib.public.include_dirs.append("include")
lib.link(zlite)

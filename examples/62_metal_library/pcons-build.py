#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Metal shaders compiled and linked into the .metallib an app loads.

`project.MetalLibrary` is the whole pipeline: each `.metal` source compiles
to an `.air`, and the `.air` files link into one `.metallib`. It returns a
Target, so the library can be a default target, an alias member, or
something to Install -- the same as a program or a shared library.

`env.metal.Object` and `env.metal.Library` drive the two steps by hand and
return nodes instead, like every other tool-namespace builder. Reach for
those only when you need an intermediate `.air` for its own sake.

macOS only: Metal is an Apple toolchain.
"""

from pcons import Project

project = Project("metal_library")
env = project.Environment(toolchain="c")

shaders = project.MetalLibrary(
    "effects", env, sources=["src/blur.metal", "src/warp.metal"]
)
project.Default(shaders)

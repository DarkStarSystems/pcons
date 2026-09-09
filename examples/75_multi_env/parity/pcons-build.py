# SPDX-License-Identifier: MIT
"""One directory, described once, built once per environment.

Nothing here names an environment. The script asks its parent for the default
one, and the parent's ``add_subdirectory("parity", env=...)`` decides what that
answers. Including this directory twice, once per environment, builds it twice:

    build/host/parity/lib/libparity.a
    build/strict/parity/lib/libparity.a

Built on its own it makes its own environment, like any other subdirectory.
"""

from pcons import Project

project = Project("parity")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

parity = project.StaticLibrary("parity", env, sources=["src/parity.c"])
parity.public.include_dirs.append("src")

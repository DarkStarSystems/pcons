#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for libfoo - can be built standalone or as part of a parent.

This demonstrates a subdir that works both:
- Standalone: `cd libfoo && python pcons-build.py`
- As subdir: called from parent pcons-build.py

It also pulls in its own subdirectory, so libbar ends up one level down when
libfoo is built directly and two levels down when the top-level project
builds everything. Neither script says anything about where it sits.
"""

from pcons import Project, add_subdirectory

project = Project("libfoo")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    # take parent environment
    env = project.parent.default_environment

bar = add_subdirectory("libbar")

# Assigning to a module-level name exports it: the parent can access this
# target as `ns.libfoo` after `ns = add_subdirectory("libfoo")`.
libfoo = project.StaticLibrary("foo", env)
libfoo.add_sources(["src/foo.c"])
libfoo.public.include_dirs.append("include")
libfoo.link(bar.libbar)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for libfoo - can be built standalone or as part of parent project.

This demonstrates a subdir that works both:
- Standalone: `cd libfoo && python pcons-build.py`
- As subdir: called from parent pcons-build.py
"""

from pcons import Project

project = Project("libfoo")
assert project is not None

if not project.is_top_level:
    # take parent environment
    env = project.parent.environments[-1]
else:
    from pcons import find_c_toolchain

    env = project.Environment(toolchain=find_c_toolchain())

libfoo = project.StaticLibrary("foo", env)
libfoo.add_sources(["src/foo.c"])
libfoo.public.include_dirs.append("include")

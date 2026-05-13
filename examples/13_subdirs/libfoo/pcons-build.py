#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for libfoo - can be built standalone or as part of parent project.

This demonstrates a subdir that works both:
- Standalone: `cd libfoo && python pcons-build.py`
- As subdir: called from parent pcons-build.py
"""

from pcons import Project

project = Project.current()
assert project is not None
env = project.environments[0]

libfoo = project.StaticLibrary("foo", env)
libfoo.add_sources(["src/foo.c"])
libfoo.public.include_dirs.append("include")

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Grandchild sub-project (third nesting level).

Creates its own Project and one Program. Reached via add_subdirectory from
the child project.
"""

from pcons import Project, find_c_toolchain

project = Project("nested_grandchild")

if project.is_top_level:
    env = project.Environment(toolchain=find_c_toolchain())
else:
    # Reuse the top-level toolchain rather than re-detecting one per level.
    env = Project.top_level().default_environment

grandchild_app = project.Program("grandchild_app", env, sources=["src/grandchild.c"])

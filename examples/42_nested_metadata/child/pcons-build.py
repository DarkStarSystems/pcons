#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Child sub-project (second nesting level).

Creates its own Project, one Program, and nests a grandchild project via
add_subdirectory.
"""

from pcons import Project, add_subdirectory, find_c_toolchain

project = Project("nested_child")

if project.is_top_level:
    env = project.Environment(toolchain=find_c_toolchain())
else:
    # Reuse the top-level toolchain rather than re-detecting one per level.
    env = Project.top_level().default_environment

child_app = project.Program("child_app", env, sources=["src/child.c"])

add_subdirectory("grandchild")

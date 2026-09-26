# SPDX-License-Identifier: MIT
"""Grandchild sub-project (third nesting level).

Creates its own Project and one Program. Reached via add_subdirectory from
the child project.
"""

from pcons import Project

project = Project("nested_grandchild")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    # Reuse the enclosing project's environment rather than detecting a
    # toolchain again at every level.
    env = project.parent.default_environment

grandchild_app = project.Program("grandchild_app", env, sources=["src/grandchild.c"])

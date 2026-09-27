# SPDX-License-Identifier: MIT
"""Child sub-project (second nesting level).

Creates its own Project, one Program, and nests a grandchild project via
add_subdirectory.
"""

from pcons import Project, add_subdirectory

project = Project("nested_child")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    # Reuse the enclosing project's environment rather than detecting a
    # toolchain again at every level.
    env = project.parent.default_environment

child_app = project.Program("child_app", env, sources=["src/child.c"])

add_subdirectory("grandchild")

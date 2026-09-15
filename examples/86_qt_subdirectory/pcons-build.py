# SPDX-License-Identifier: MIT
"""Qt targets declared by add_subdirectory() scripts.

Two ways a subdirectory script can get the environment it builds in, both
of which have to produce the same Qt codegen edges as a top-level script:

- ``own_env`` makes its own Project, its own Environment and its own
  find_qt(), the way a library written to build standalone does.
- ``shared_env`` makes its own Project but builds in this one's
  environment, reached through ``project.parent.default_environment``.

Each subdirectory's generated Qt files land in its own build directory:
``build/own_env/qt.own_env_app/`` beside ``build/own_env/obj.own_env_app/``.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qt_subdirectory")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)  # Qt 6 requires C++17 or later

find_qt(project, env, modules=["Core"])

project.add_subdirectory("own_env")
project.add_subdirectory("shared_env")

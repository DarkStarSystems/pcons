# SPDX-License-Identifier: MIT
"""A static library linked whole.

``plugins`` holds a plugin that registers itself from a static constructor.
Nothing calls into it, so a linker pulling objects out of the archive only
when something needs them would leave it out, and the program would find no
plugins. ``whole_archive`` makes every link that pulls the library in take
all of its objects: ``-Wl,-force_load,`` on Apple, ``-Wl,--whole-archive``
around it with GNU ld and lld, ``/WHOLEARCHIVE:`` with MSVC's link.

Usage:
  pcons
  ./build/main
"""

from pcons import Project

project = Project("whole_archive")
env = project.Environment(toolchain="c")

plugins = project.StaticLibrary("plugins", env, sources=["src/hello_plugin.cpp"])
plugins.public.include_dirs.append("include")
plugins.set_option("whole_archive", True)

main = project.Program("main", env, sources=["src/main.cpp", "src/registry.cpp"])
main.link(plugins)

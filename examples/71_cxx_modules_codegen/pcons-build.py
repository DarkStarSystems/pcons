# SPDX-License-Identifier: MIT
"""A generated C++ source in a build that also owns a C++20 module.

This is issue #105's reproduction, and it now just builds. The interesting
part is what *isn't* here: no staging, no second configure pass, no separate
environment for the generated file.

1. **Scanning is per target, not per project.** The old modules pass built one
   scan node for the whole project, taking every scanned source as an input
   and hanging every scanned object off it. That made `use`'s source an input
   to the node that `gen.cpp.o` waits on -- and `generated.cpp` is written by
   `gen`, so the graph closed: `generated.cpp -> gen -> gen.cpp.o ->
   cxx_modules.dyndep -> generated.cpp`. Each target now gets its own scan and
   collate, so `gen` (which imports nothing and is scanned in its own scope)
   is nowhere near `use`'s scan.

2. **A generated source is just an input to a scan edge.** Nothing scans it at
   configure time -- it doesn't exist yet, and asking would be the second
   symptom in #105 (`error: no such file or directory`). The scan runs at
   build time, after ninja has run the edge that writes the file, because the
   file is an ordinary input to the scan edge.

`use` never mentions module `m` in this script. It says `import m;` in the
generated source; the link to `m` is what puts the module's exports in scope,
and the compile order between the two is discovered, not declared.
"""

from pcons import Project, get_var

project = Project("cxx_modules_codegen")
# The test harness selects a toolchain per platform via TOOLCHAIN;
# without it, prefer whatever this host has.
env = project.Environment(
    toolchain=get_var("TOOLCHAIN", "") or ["llvm", "msvc", "gcc", "c"]
)
env.cxx.set_standard("c++20")

# The module interface, and the generator that writes the source importing it.
mod = project.StaticLibrary("m", env, sources=["src/mod.cppm"])
gen = project.Program("gen", env, sources=["src/gen.cpp"])

generated = env.Command(
    target=project.build_dir / "generated.cpp",
    tool=gen,
    command="$TOOL $TARGET",
    name="generate",
)

use = project.Program("use", env, sources=[generated.output_nodes[0]])
use.link(mod)

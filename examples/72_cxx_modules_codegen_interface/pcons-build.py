# SPDX-License-Identifier: MIT
"""A C++20 module interface written by a program this build compiles.

Not just a generated source that *imports* a module (see
`71_cxx_modules_codegen`) -- here the `.cppm` that *exports* one is itself a
build product, and `app` imports it. That works with zero staging because
every decision pcons has to make at configure time is already available:

1. **The suffix is a static fact.** `gen/iface.cppm` doesn't exist when the
   build script runs, and pcons never asks the filesystem. It reads `.cppm`
   off the declared path, and that alone is enough to compile the file as a
   module interface and to enrol it in `genmod`'s scan scope.

2. **The scan waits for the generator.** The generated file is an ordinary
   input to the scan edge, so ninja orders the scan after the `COMMAND` edge
   that writes it. Nothing is scanned before it exists.

3. **The BMI is discovered, not declared.** Which module `iface.cppm` exports
   is decided by the program that writes it. The scan reports `gen`, the
   collate writes it into `genmod`'s dyndep file as an implicit output of that
   compile, and `app`'s modmap picks it up at build time -- so the flags that
   point the compiler at the BMI arrive without a reconfigure.
"""

import platform

from pcons import Project, get_var

project = Project("cxx_modules_codegen_interface")
# The test harness selects a toolchain per platform via TOOLCHAIN;
# without it, prefer whatever this host has.
env = project.Environment(
    toolchain=get_var("TOOLCHAIN", "") or ["llvm", "msvc", "gcc", "c"]
)
env.cxx.set_standard("c++20")

gen_dir = project.build_dir / "gen"

geniface = project.Program("geniface", env, sources=["src/geniface.cpp"])

# A POSIX shell looks a bare name up on $PATH, where the build directory is
# not; cmd.exe searches the current directory and has no `./`.
run = "" if platform.system() == "Windows" else "./"

iface = env.Command(
    target=gen_dir / "iface.cppm",
    source=[geniface],
    command=f"{run}${{SOURCES[0]}} $TARGET",
)

genmod = project.StaticLibrary("genmod", env, sources=[iface.output_nodes[0]])
app = project.Program("app", env, sources=["src/main.cpp"])
app.link(genmod)

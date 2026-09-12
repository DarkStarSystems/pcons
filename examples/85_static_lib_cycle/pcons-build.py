# SPDX-License-Identifier: MIT
"""Two static libraries that need each other.

``parser`` calls into ``lexer`` and ``lexer`` calls back into ``parser``.
Static libraries may link each other this way: neither has to be built
before the other (compiling one needs only the other's headers), and every
linker can resolve the cycle. Both libraries say what they link, and
``main`` links ``parser`` alone.

The link line gets the archives in the form the linker wants. GNU ld and
lld search an archive once, so on Linux the archives are wrapped in
``-Wl,--start-group ... -Wl,--end-group``; Apple's ld and MSVC's link
rescan on their own and get the archives as they are.

Only static libraries, object libraries and header-only libraries may be
in such a cycle. A program or shared library in one is an error, since it
would have to be built before its own dependency.

Usage:
  pcons
  ./build/main
"""

from pcons import Project

project = Project("static_lib_cycle")
env = project.Environment(toolchain="c")

lexer = project.StaticLibrary("lexer", env, sources=["src/lexer.c"])
lexer.public.include_dirs.append("include")

parser = project.StaticLibrary("parser", env, sources=["src/parser.c", "src/delim.c"])
parser.public.include_dirs.append("include")

lexer.link(parser)
parser.link(lexer)

main = project.Program("main", env, sources=["src/main.c"])
main.link(parser)

project.Default(main)

# SPDX-License-Identifier: MIT
"""A static library's public frameworks propagate to a program that links it.

`fwlib.public.frameworks.append("CoreFoundation")` is the target-level usage
requirement (added in #186). It differs from `env.Framework()`: that call
puts `-framework` on the *environment* the caller owns, so every target
built with that environment links the framework whether it uses it or not.
`target.public.frameworks` is scoped to the target and merges, dedupes, and
propagates transitively like `link_libs` and `link_dirs` -- so `fwlib` alone
declares the dependency, and `app` links CoreFoundation without ever naming
it.

macOS only: frameworks are an Apple linker concept.
"""

from pcons import Project

project = Project("macos_frameworks")
env = project.Environment(toolchain="c")

fwlib = project.StaticLibrary("fwlib", env)
fwlib.add_sources(["src/fwlib.c"])
fwlib.public.include_dirs.append("src")
fwlib.public.frameworks.append("CoreFoundation")

app = project.Program("app", env)
app.add_sources(["src/main.c"])
app.link(fwlib)

# SPDX-License-Identifier: MIT
"""A dependency built from its own source tree, kept out of your default build.

`third_party/greet` is someone else's project with its own `pcons-build.py`.
Built on its own, its `Default()` builds a demo program. Pulled in here as a
dependency, neither its demo nor its library should be something a plain
`ninja` asks for: this project's app is.

`add_subdirectory(..., build_tier="manual")` says so. Nothing the included
tree declares goes into a wider tier than "manual", whatever its own script
chose, so its targets build only when something needs them (the app links the
library, so ninja builds that) or when asked for by name. It's CMake's
`EXCLUDE_FROM_ALL`. `build_tier="all"` would leave them out of plain `ninja`
but in `ninja all`.

A `Default()` in this script could still name one of them. The dependency's
own `Default()` calls govern only its own tree, with or without the option:
they never push this project's app out of the default build.

`pcons explain` shows each target's tier and the line that decided it.
"""

from pcons import Project, add_subdirectory

project = Project("vendored_subdirectory")
env = project.Environment(toolchain="c")

greet = add_subdirectory("third_party/greet", build_tier="manual")

app = project.Program("app", env, sources=["src/main.c"])
app.link(greet.lib)

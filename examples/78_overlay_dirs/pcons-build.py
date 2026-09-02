# SPDX-License-Identifier: MIT
"""Two source trees merged into one staging directory, later source winning.

`InstallDir` copies a directory *as* a directory: `InstallDir(dest, "shared")`
produces `dest/shared/...`. Two trees named the same way land on top of each
other, and the two calls fight over one destination. `OverlayDir` copies the
trees' *contents* into one destination, which is a different job:

1. **Argument order is the whole conflict rule.** `config.txt` exists in both
    trees. `app` comes second, so `app`'s version is the one that lands.
    Nothing else participates: not modification time, not which tree looks
    more specific, not depth. Swap the two names and the answer swaps.

2. **Relative paths survive.** `src/com/example/Thing.java` arrives at
    `stage/src/com/example/Thing.java`. A builder that flattened to the base
    name would produce `stage/Thing.java`, and a Java package would stop
    resolving -- the kind of breakage that shows up at run time rather than
    at build time.

3. **A shared directory takes a child from each tree.** `res/` is in both,
    holding `res/xml` in one and `res/drawable` in the other. Both children
    end up under one `stage/res/`. This is the case that defeats splitting the
    work per subdirectory.

4. **`exclude=` drops what should not ship.** Patterns are matched against the
    path relative to *each source root*, so both trees' `notes.md` go.

One target owns the destination and stages all of it with a single build edge,
so which files win is decided when that edge runs rather than when pcons runs.
The edge reports every directory it walked and every file it copied in a
depfile. That is what makes a file added anywhere -- including deep under
`src/com/example/` -- appear in `stage/` on the next `ninja`, with no hand-run
of pcons, and it is why a file removed from a source tree takes its staged copy
with it. A file another build edge generates into one of these trees is staged
by the same build that writes it.
"""

from pcons import Project

project = Project("overlay_dirs")
env = project.Environment()

stage = project.OverlayDir(
    env,
    "stage",
    sources=["shared", "app"],
    exclude=["notes.md"],
)

project.Default(stage)

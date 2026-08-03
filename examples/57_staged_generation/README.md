# Staged generation: discovering targets from the build's own output

Some projects can't know their target list until something has run. A
definition language, an IDL, a schema, a plugin manifest — the set of things to
build is *data*, and the program that reads it is often built by the same
build.

pcons describes the graph and then hands it to Ninja; it never creates targets
while the build runs. Instead it stages, and the build system drives the
staging:

```
pass 1   pcons describes what produces the manifest
         ninja compiles list-plugins, runs it -> gen/plugins-list.txt
         build.ninja lists that manifest among its own inputs, so it is now
         out of date -> ninja re-runs pcons and re-reads build.ninja
pass 2   the manifest exists, the per-plugin targets join the graph
         ninja builds them
```

All of that happens inside a single `ninja` invocation from a clean tree:

```
$ pcons generate && ninja -C build
[1/4] CC obj.list-plugins/src/list-plugins.c.o
[2/4] LINK list-plugins
[3/4] COMMAND gen/plugins-list.txt
[3/4] Regenerating build.ninja
[1/10] COMMAND gen/S_blur.c gen/S_glow.c gen/S_sharpen.c gen/plugins.h
...
[6/10] LINK demo
```

Add a line to `plugins.def`, run `ninja`, and the new plugin appears — with
only the new plugin compiled:

```
[1/2] Regenerating build.ninja
[1/9] COMMAND gen/S_blur.c gen/S_glow.c gen/S_sharpen.c gen/S_emboss.c gen/plugins.h
[2/6] CC obj.demo/build/gen/S_emboss.c.o
[3/6] CC obj.demo/src/main.c.o      # includes the regenerated registry header
[4/6] LINK demo
```

## What makes it work

**`project.when_generated(path)`** runs a block only once *path* exists, and
registers it as an input of the generated build files either way. The
underlying primitive is `project.generated_input(path)`, which returns the path
or `None`. This is the one sanctioned filesystem check in a build script:
it asks whether a declared build input has been produced yet, and records the
answer as a dependency. (Deciding whether something is a *target* by looking at
the filesystem is still wrong.) A staged input that no rule produces is an
error — it could never appear.

**The self-regeneration edge.** Every generated `build.ninja` carries a
`generator = 1` edge listing the build script, every project-local Python
module it imported, and every registered configure dependency. Editing any of
them re-runs pcons before anything is built. Add your own with
`project.add_configure_dependency(path)`.

**`write_if_different=True`** on `env.Command`. `restat` only pays off if the
generator leaves byte-identical outputs alone, and most generators rewrite
everything every run. This option stashes the outputs, runs the command, and
restores whatever came back identical — timestamp included — so one added
plugin doesn't recompile the other 278.

**Per-source dependencies.** `main.c` is the only file that includes the
generated registry header, so only it declares that dependency
(`project.node("src/main.c").depends(...)`). Hanging it on the whole target
would recompile every plugin whenever the registry changed.

## Portability

Ninja handles this natively. GNU make 4.x does too (it remakes a makefile, then
restarts). GNU make 3.81 — still `/usr/bin/make` on macOS — compares makefile
prerequisite timestamps at whole-second granularity and can miss a manifest
written in the same second, so use ninja or a modern GNU make for staged
builds.

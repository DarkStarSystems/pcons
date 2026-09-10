# A three-step builder, and what `depends()` means to each step

`AssetBundle` is a custom builder with three steps in one target: compile
each scene to a `.abin`, pack them into a `.pak`, write a `.manifest`. The
step logic is in one place, `AssetBundleFactory` in `pcons-build.py`, and
each step is a `GenericCommandBuilder`, the same thing `env.Command` is made
of. The tool, `tools/assetc.py`, is a few lines of Python, just for this contrived example.

The build script declares one dependency:

```python
bundle.depends(palette)
```

and the generated ninja build file shows what that meant to each step:

```
build abin/forest.abin: command $topdir/scenes/forest.scene | $topdir/assetc.opts || palette.txt
build abin/cave.abin:   command $topdir/scenes/cave.scene   | $topdir/assetc.opts || palette.txt
build level.pak:        command abin/forest.abin abin/cave.abin  | palette.txt
build level.manifest:   command level.pak                        | palette.txt
```
(Regular dependencies are after `|`, order-only deps after `||`.)

## Discovered, order-only, and explicit dependencies

The build script's half of the rule: `bundle.depends(palette)` means
pcons will build the palette (or ensure it exists if it's a source)
before any step of the bundle. The script doesn't need to care which step of the builder reads it.

In the builder, each step then decides for itself. The compile step *discovers* its
dependencies: the tool reports what it read in a depfile, like a compiler
reporting the headers it included. So it holds the palette order-only (`||`):
the palette exists before the first compile, and after that a palette change
recompiles a scene only if that scene's depfile lists it. The pack and
manifest steps discover nothing, so they hold the palette as a regular implicit
dependency (`|`) and rerun whenever it changes.

That is slightly over-cautious on the first build since the pack does
not read the palette (but it doesn't build anything extra and in this
case it costs nothing), and it's exact from then on.

## What if a build step can't discover its dependencies?

In this contrived example, we've set it up so `assetc compile` also
reads `assetc.opts`, but doesn't report it: the options arrive as a
flag, the way a compiler reads a response file. If the build script
said `bundle.depends("assetc.opts")`, the compile step would hold the
file order-only, its depfile would never mention it, and changing the
scale would rebuild nothing.

Only the builder knows which step reads the options file, so the
builder declares it on that step's node:

```python
abin.depends(options)   # in AssetBundleFactory.resolve
```

That is the `| $topdir/assetc.opts` on the ninja compile edges above, and the reason
the last rebuild test in `test.toml` recompiles every scene when the options
change. The user never has to know which step is which.

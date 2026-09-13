# Content-discovered build order, over sources the build generates

Four scene packs, no compiler. A `.scene` file names what it provides and
what it references; compiling one into a `.pack` embeds a digest of every pack
it references, so a referenced pack has to be built first. Which pack that is
lives in the scene text, and nothing in `pcons-build.py` says it.

```python
scene_refs = Scanner(
    "scene-refs",
    source_suffixes=[".scene"],
    scan_command=[python, "$SRCDIR/tools/scan_scene.py", NodeVar("PACK"),
                  "$SOURCES", "$TARGET"],
    scan_deps=["tools/scan_scene.py"],
    scan_vars=pack_of_edge,
    on_unresolved="error",
    edge_args=EdgeArgsSpec(suffix=".refs", var="SCENE_REFS", token="$SCENE_REFS",
                           format=ArgsFormat(line="{name} {path}"),
                           include="requires"),
)
scene_refs.attach(pack_common, pack_extras, pack_level1, pack_level2)
```

**Only the scanner reads a `ref` line.** `tools/scan_scene.py` reports each
pack's provides and requires as JSON; pcons collates those into a ninja dyndep
file. The build script declares which packs `level2` *may* reference, with
`depends(pack_common, pack_level1, pack_extras, on_change=False)`: those land
on the build statement as order-only deps, so every candidate is built first
but none of them makes `level2` repack by itself. The only implicit dep the
script contributes is the packer:

```
# build.ninja, one statement, rule name and bindings elided
build packs/level2.pack: <cmdline rule> gen/generated2.scene | $
    $topdir/tools/pack_scene.py || $
    packs/common.pack packs/level1.pack packs/extras.pack $
    scan/scene-refs/scene_packs.pack_level2.dyndep
```

What the scene text decides is *which* of those packs is an input, and that
arrives at build time, in the dyndep:

```
# scan/scene-refs/scene_packs.pack_level2.dyndep
build packs/level2.pack: dyndep | packs/level1.pack
```

So editing `assets/extras.scene` repacks `extras` and stops there: `level2`
lists it as a candidate but does not reference it, and the dyndep is what
ninja consults. (The scan of `level2` re-runs, since a candidate it may read
changed, and its unchanged dyndep ends the cascade.) Add `ref extras` to
`level2`'s scene and the dyndep gains `packs/extras.pack` on the next build,
with no change to the build script: the declared dependencies say where a name
may be found, the scene says which ones are used.

**The discovered facts reach the command line too.** `edge_args` has collate
write each pack edge a `.refs` file — `common packs/common.pack` — and appends
`$SCENE_REFS` to that edge's command. The path is fixed at configure time, the
content decided at build time, so `tools/pack_scene.py` is *told* which packs
to read rather than parsing a scene for refs.

**Target dependencies carry the exports.** A scope resolves a required name
only against the scopes it depends on, so `pack_level2` needs a `depends()`
on the pack that provides `level1` to see that name at all. The dependency
says where to look (and builds the candidates first, as any dependency does);
the scene content decides what gets used, and when.

## Generated sources, twice over

The scanned sources don't all exist when the build starts:

```
src/gen_head.pyfrag + src/gen1_payload.pyfrag  ->  bin/genscene1.py
        run it  ->  gen/generated1.scene   (packed into level1)
                +   gen/gen2_payload.pyfrag (part of the next generator)
src/gen_head.pyfrag + gen/gen2_payload.pyfrag  ->  bin/genscene2.py
        run it  ->  gen/generated2.scene   (packed into level2)
```

A scan edge takes a generated scene as an input like any other, so its
producer's ordering comes from the node graph. There are no phase
declarations, no staging, no reconfigure, and no existence checks anywhere in
the build script — pcons runs once and ninja builds it all:

```
$ pcons
[1/16] SCAN[scene-refs] packs/common.pack.scaninfo.json
[2/16] COLLATE[scene-refs] scan/scene-refs/scene_packs.pack_common.dyndep
[3/16] COMMAND packs/common.pack
[4/16] COMMAND bin/genscene1.py
[5/16] COMMAND gen/generated1.scene gen/gen2_payload.pyfrag
[6/16] SCAN[scene-refs] packs/level1.pack.scaninfo.json
...
[16/16] COMMAND packs/level2.pack
```

A scanner that scanned the whole project in one pass could not: the scan of
`generated2.scene` would have to run before the generator that writes it,
which the generator's own scan would have to precede. (Issue #105 is the C++
modules version of the same deadlock.) Here each scan is just another edge, so
there is nothing to break.

## Two details worth copying

**A pack serves every scene inside it.** `scan_vars` gives each scan edge the
pack it is scanning for, and the scan info gives every name that pack's path.
The alternative, `provide_template`, suits a scanner whose every logical name
gets an artifact of its own — a C++ module's BMI — but here several names
share one file, and a template would promise artifacts that never get built.

**`write_if_different=True` on the generator steps.** Editing a fragment to
the same text reassembles `bin/genscene1.py` and stops: identical output, so
nothing downstream re-runs and no pack is rebuilt.

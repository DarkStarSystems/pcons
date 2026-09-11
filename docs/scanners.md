# Discovered Dependencies: Scanners

Some edges' real dependencies live only inside their input files. In
C++, `import m;` orders one compile after another. A scene file that
references another scene has to be packed after the second scene is
made up-to-date. A Fortran `USE` picks a `.mod` some other compile
writes. And sometimes those source files are _generated_, so they
don't exist when the build starts, and may be updated as they're
rebuilt in an incremental build. Users shouldn't have to hand-create
these dependencies in their build scripts.

A **Scanner** declares that a set of build edges has content-derived
dependencies, and allows users to specify the command that finds them.
A scanner supplies a tool that reads one edge's sources and reports
what it found; pcons wires the graph and turns those reports into a
Ninja [dyndep](https://ninja-build.org/manual.html#_dyndep) file while
the build runs.

## Static vs. Dynamic content

> Configure may depend only on static facts: file names, suffixes,
> flags, the target DAG. Content-derived facts, which may or may not
> exist or be up-to-date when the build starts, flow through scan →
> collate at build time.

- A scanned source is never read at configure time, so a *generated* source is
  treated like other sources. The scan edge takes it as an input, and ninja runs the producer
  first.
- Scanned sources aren't configure dependencies. Change an `import` and pcons
  doesn't need to re-run: that one file is rescanned and recompiled directly by ninja.
- Some discovered dependencies can't reach a command line directly through dyndep, which carries
  only deps and outputs. Those get written to a file by the new `collate` step, to a
  fixed path. See [Discovered flags](#discovered-flags).

## Declaring a Scanner

Here's a simple example using "scene" files, which for this example are text files with name-value pairs like name and properties, and `ref`s which refer to other scenes by name. The `scan_scene.py` tool collects provides/requires for each scene based on its `name` and `refs`. That allows pcons to dynamically build a dependency graph for the whole hierarchy. This even works for scenes that are generated during the build, as shown in example 70 (`examples/70_scene_packs`).

```python
from pcons import Scanner

scene_refs = Scanner(
    "scene-refs",                    # lowercase-hyphenated, like a preset
    source_suffixes=[".scene"],      # which sources get scanned
    scan_command=[python, "$SRCDIR/tools/scan_scene.py", "$SOURCES", "$TARGET"],
    scan_deps=["tools/scan_scene.py"],   # re-scan when the tool changes
    on_unresolved="error",           # a ref nothing provides is a broken asset
)
scene_refs.attach(pack_common, pack_level1, pack_level2)
```

`attach()` is the only API for a Scanner, and it takes targets (not files). Each attached
target is one **scope**. Call it before `project.resolve()`; a toolchain may
also attach from its `after_resolve` hook.

A **governed edge** is any build edge of an attached target with at least one
source matching `source_suffixes`. Attaching a scanner that governs nothing is
an error.

Per scope, the resolver wires:

- **one scan edge per governed edge**: that edge's scanned sources in, one
  scan-info JSON file out. All the scan edges of a scanner share one ninja
  rule, whatever their count.
- **one collate edge per target**: inputs are the scan infos, a fixed manifest pcons writes at
  configure time, and the exports of scanned dependency scopes. Outputs are a dyndep file,
  an exports file, and any args files. It runs with `restat`, so a collate
  that changes nothing dirties nothing.
- **`dyndep = <the scope's dyndep file>` on every governed edge**, with that
  file in *order-only* position. The edge waits for it to exist; the loaded
  dyndep supplies the real deps. Rewriting it doesn't by itself rebuild
  anything.

`scan_depfile` / `scan_deps_style` give the scan edge its own
dependency tracking, so a scan that reads a header re-runs when the
header changes. Styles are `"gcc"` for a make-style depfile, `"msvc"`
for `/showIncludes` on the scan's output.

## The scan-info contract

The scan tool writes one JSON file per governed edge. Every field but
`version` is optional:

```json
{"version": 1,
 "provides": [{"name": "level1", "path": "packs/level1.pack"}],
 "requires": ["common"],
 "extra_deps": ["assets/shared.inc"],
 "extra_outputs": ["packs/level1.index"]}
```

All paths are relative to the build directory, with forward slashes.

- **`provides`** says where a logical name lives, so requiring edges can
  depend on it. Give no `path` and the scanner's `provide_template`
  (`{name}`, `{scanner}`, `{scope}`) supplies one.
- **`requires`** lists logical names this edge needs. Collate resolves each
  one to the artifact that provides it, own scope first, then imports.
  Anything left over is handled per `on_unresolved`: `"ignore"` (something
  outside the build may satisfy it), `"warn"`, or `"error"`.
- **`extra_deps`** are discovered files this edge reads.
- **`extra_outputs`** are files this edge writes beyond its declared outputs,
  and they're the only thing that becomes a dyndep implicit *output*.

Hence the rule collate enforces: **every provide path must be backed by one
of the edge's declared outputs or by one of its `extra_outputs`.** Anything
else is rejected.

## Exports travel along dependencies

A scope resolves a required name against its own provides and against the
exports of the scopes it *depends on*. So `pack_level2` needs
`depends(pack_level1)` (or `link()`, for a library) before it can see
the name `level1` at all.

The dependency carries the exports, and builds the dependency first, as any
`depends()` does. It does not decide what a scope uses: which artifacts
a pack or a compile needs, and in what order, comes out of the scan.

## Discovered flags

In Ninja, dyndep can reorder a build; it can't edit a command. Pcons uses `edge_args` to
close that gap. Collate writes with each governed edge a small file listing the artifacts its
`requires` resolved to, and the edge's command refers to that file by a path
fixed at configure time, and typically used in the compilation line as `@file`.

```python
edge_args=EdgeArgsSpec(
    suffix=".refs",                  # appended to the edge's primary output
    var="SCENE_REFS",                # per-edge ninja variable holding the path
    token="$SCENE_REFS",             # appended to the command ("@$SCENE_REFS" for a response file)
    format=ArgsFormat(line="{name} {path}"),
    include="requires",              # or "requires+provides"
)
```

`link_args` is the same idea per scope instead of per edge, wired to the
target's final link edges. It carries extra link inputs that only collate can
know about — the standard library module's object, when some translation unit
turns out to `import std;`. Use `link_args_target_types` to keep it off
targets whose linker won't take a response file, such as an archiver.

## Generated sources need no phases

`examples/70_scene_packs` builds two generations of generated scenes: it
assembles a generator from checked-in fragments, runs it to get a scanned
scene *and* a fragment of the next generator, assembles that one, and runs it
for another scanned scene. One `pcons` run, one `ninja` run, no staging
declarations and no existence checks anywhere.

That works because a scan edge is just an edge. Its input is the generated
scene, so the producer's ordering comes from the node graph, and any number of
generation stages compose for free.

A scanner that scanned the whole project in one pass can't do this: the scan
of the second generated scene would have to run before the generator that
writes it, which that generator's own scan would have to precede. Issue #105
is the C++ modules version of the same deadlock.

## Scanner or staged generation?

Both discover things mid-build, and they compose. The question they answer is
different:

| Build-time question | Mechanism |
|---|---|
| What does this file's *content* imply — deps, ordering, extra outputs, per-edge flags? | **Scanner**. Resolved during the build, no reconfigure. |
| What *set* of files or targets exists — names, counts, output paths? | **Staged generation** (`generated_input` / `when_generated`). pcons re-runs to describe the new targets. |

A staged pass can add scanned targets, since the scanner wiring runs at every
configure. Scans of generated files need no staging. See
[Staged Generation](user-guide.md#staged-generation-targets-discovered-mid-build)
and `examples/57_staged_generation`.

## Ninja only

Only ninja can express dyndep. The Makefile and Xcode generators
refuse a project that uses a scanner. `build.ninja` declares
`ninja_required_version = 1.11` as soon as a scope exists: that's the
minimum version where cross-file dyndep references resolve reliably.

## A complete example

Scenes reference each other by name. For this contrived example,
"packing" one embeds a digest of every pack it references, so a
referenced pack must be built first — and which one that is lives in
the scene text.

```python
def pack(name: str, *scenes: str) -> Target:
    return env.Command(
        target=packs_dir / f"{name}.pack",
        source=list(scenes),
        command=[python, "$SRCDIR/tools/pack_scene.py", "$TARGET", "$SOURCES"],
        depends=["tools/pack_scene.py"],
        name=f"pack_{name}",
    )


pack_common = pack("common", "assets/common.scene")
pack_level1 = pack("level1", "assets/level1.scene")
pack_level1.depends(pack_common)  # carries common's exports

scene_refs = Scanner(
    "scene-refs",
    source_suffixes=[".scene"],
    scan_command=[python, "$SRCDIR/tools/scan_scene.py", "$SOURCES", "$TARGET"],
    scan_deps=["tools/scan_scene.py"],
    provide_template="packs/{name}.pack",   # one scene per pack, here
    on_unresolved="error",
    edge_args=EdgeArgsSpec(suffix=".refs", var="SCENE_REFS", token="$SCENE_REFS",
                           format=ArgsFormat(line="{name} {path}"),
                           include="requires"),
)
scene_refs.attach(pack_common, pack_level1)
```

The `provides` carry no path, so `provide_template` supplies one.

```python
info = {
    "version": 1,
    "provides": [{"name": n} for n in provides],
    "requires": sorted(set(requires) - set(provides)),
}
Path(out).write_text(json.dumps(info))
```

`packs/level1.pack`'s build statement carries no ordering at all. The ordering
arrives at build time, from what the scenes said:

```
# scan/scene-refs/scene_packs.pack_level1.dyndep
build packs/level1.pack: dyndep | packs/common.pack
```

The full version is `examples/70_scene_packs`: several scenes per pack (so the
scan edge gets its pack through `scan_vars` instead of a template), the
generated scenes, and the rebuild behavior.

## Seeing it in the graph

`pcons generate --graph` (DOT) and `--mermaid` draw the build. A scanner's
own files — scan infos, manifest, dyndep, exports, argument files — are
machinery rather than anything you asked to build, so the graph leaves them
out; the edges they govern carry a `scanned: <scanner>` line under their
name instead.

`--graph-detail` asks for more, comma-separated:

| Item | Draws |
|---|---|
| `scan` | the machinery itself: sources → scan info → collate → dyndep, exports and args files, including each args file reaching the command that reads it |
| `discovered` | what the last build's dyndep files record: the requires that ordered each edge, and the artifacts it turned out to provide |
| `headers` | header edges from the compiler's `.d` files |

```bash
pcons generate --graph=deps.dot --graph-detail=discovered
dot -Tpng deps.dot -o deps.png
```

All three read files a build writes, so build first: before that there is
nothing to report and they draw nothing. `discovered` is the interesting one,
since those edges are precisely what no build script can state — in
`examples/26_fortran_modules` it draws `greetings.f90.o` providing
`modules/greetings.mod` and `main.f90.o` waiting on it.

## How the C++ and Fortran toolchains use it

C++20 modules and Fortran modules use this method internally, so it should be fully
transparent for users.

Every target that owns module sources gets a scanner attached automatically.
For C++ the scan edge is `clang-scan-deps`, `cl /scanDependencies`, or GCC's
own preprocess-only pass, all emitting P1689; a C++-specific collate maps
logical module names to keyed BMIs and writes each compile a modmap
(`edge_args`) carrying `-fmodule-file=`, GCC's mapper lines, or MSVC's
`/reference`. `import std;` rides `link_args`: configure describes dormant
standard-library edges, and nothing builds or links unless some translation
unit's scan reports the import. Fortran declares a `"fortran-modules"` scanner
over its `MODULE`/`USE` scan and uses the generic collate unchanged.

The examples: `examples/29_cxx_modules`, `30_cxx_partitions`,
`32_cxx_import_std`, `35_cxx_modules_deps`, `36_cxx_modules_multi_level_subdirs`,
`39_bmi_compat`, `71_cxx_modules_codegen`, `72_cxx_modules_codegen_interface`,
and `26_fortran_modules`. The user-facing rules are in
[C++20 modules](user-guide.md#c20-modules).

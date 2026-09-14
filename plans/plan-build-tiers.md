# Build tiers: what an invocation builds

**Status: implemented 2026-09-13.** Core in `pcons/core/tiers.py`
(`decide_build_tiers`), `Target.build_tier` / `place_in_tier`, the
`build_tier=` declaration on `@builder`. Replaces the implicit "programs and
libraries" default set and the boolean `build_by_default`. Settles issue #121.

## The problem

Two accidents in the current design:

1. Plain `ninja` builds every program and library and nothing else. That
   rule names two kinds of C-family target in core, against the principle
   that pcons builds anything: a LaTeX document, a scene pack, an objcopy'd
   `.bin` are products too, and today they build only when named or when a
   script calls `Default()`.
2. `Default()` replaces the implicit set, so the moment any script calls it,
   every `build_by_default` on every other target stops meaning anything.
   Whether a flag has an effect depends on what else was called, and on the
   order.

## The concept

Every target sits in one of three nested tiers, by which invocation reaches
it:

| tier | reached by | holds |
|---|---|---|
| `default` | plain `ninja`, and everything below | the products: programs, libraries, commands, documents, packs, bundles of the build's own making |
| `all` | `ninja all`, naming, and as a dependency | steps that operate on products: installs, overlays, archives, installers |
| `manual` | naming only | test runs (`ninja test`: a Test target has no output for `all` to name), and targets that must not run unasked: one that rewrites sources (Qt lupdate), one too slow or too destructive for routine builds |

`default` ⊂ `all` ⊂ everything. Location never matters: a subdirectory's
target and a top-level one are placed the same way. (SCons built only
what lived under the current directory; pcons has no such rule.)

## The attribute: `target.build_tier`

- A `str` with the values above. The builder that creates a target places
  it: `Program`, `StaticLibrary`, `SharedLibrary`, `Command`, `Object` and
  every product-making builder (LaTeX, Qt program, custom builders by
  default) say `"default"`; `Install`, `InstallAs`, `InstallDir`,
  `OverlayDir`, `Tarfile`/`Zipfile`, the installer helpers say `"all"`;
  `Test`, Qt's lupdate and deploy say `"manual"`.
- The script may set it: `bench.build_tier = "all"`,
  `firmware.build_tier = "default"`, `lupdate.build_tier = "manual"`.
- `build_by_default` is kept one release as a deprecated alias:
  `True` reads/writes `"default"`, `False` writes `"manual"`: the old False
  kept a target out of `all` too, which is what manual means. (Qt's two
  utility targets say `"manual"` outright.)
- Core knows the three words and nothing about what makes a Program a
  product; that is the builder's declaration.

## `Default()`

Keeps its SCons meaning as the way to *name the default tier outright*:
"the default tier is exactly these". It is recorded, with the call site,
and applied at generate, never at the call. Multiple Default() calls append.

## Decision, at generate, order-agnostic

Once, from the final state, for each target, first rule that applies:

1. The script set `build_tier` explicitly: that value.
2. `Default()` was called somewhere: `"default"` if the target was named
   as a default target, else `"all"` if the builder placed it in
   `"default"`, else the builder's value. (A `Default()` call demotes the
   unnamed products to `all`; it never touches steps or manual targets.)
3. The builder's value.

A target named in `Default()` and also set by the script to `"all"` or
`"manual"` is a contradiction: refused at generate, with both source
locations. Nothing depends on the order of calls.

The default set is every target whose decided tier is `"default"`. `all`
is every target whose decided tier is not `"manual"`. Named targets
always build.

## Debuggable

`pcons explain` gains a "build tiers" section, and `-v` at generate logs
the same lines, one per target, in the voice of the flag provenance:

```
build tiers:
  app          default   product (Program)
  firmware     default   product (Command)
  bench        all       build_tier = "all"            pcons-build.py:41
  install_bin  all       an install is a step; `ninja all` or `ninja install`
  tool         default   named in Default()            pcons-build.py:60
  lib          all       not named in Default()        pcons-build.py:60
  lupdate      manual    placed by QtTranslations
```

"Why isn't X building?" is one command, and the answer names the line.

## What changes for existing scripts

- A `Command` (or any product-making custom builder) whose output nothing
  consumes now builds on plain `ninja`. The intended fix; the one-line
  opt-out is `build_tier = "all"`.
- Scripts that call `Default()` see no change in what plain `ninja` builds.
- Installs, archives, tests: in `all`, not default, as today.
- Changelog: Changed, with the migration line. Ships in 0.29 alongside the
  builders it affects rather than baking the old rule into a new flag.

## Implementation notes

- `Target.build_tier` (str) with validation; the builder registry passes
  the placement (default `"default"`) so custom builders get it for free
  and the step builders override it; `build_by_default` property as alias.
- One function in core, `decide_build_tiers(project) -> dict[Target, (tier,
  reason, location)]`, used by both generators' default/all writers, by
  `explain`, and by the verbose log. No generator re-derives it.
- Tests: the precedence table row by row, order-independence (Default()
  before and after target creation, in a subdirectory), the contradiction
  error, the explain section, and the alias. `tests/generators/
  test_build_by_default.py` becomes `test_build_tiers.py`.
- Examples: the ones that called `Default()` only to get a Command built
  drop the call; their test.toml stays as the check.
- Docs: rewrite the "Default and Alias Targets" section of the user guide
  around the three tiers, most common case first: what plain `ninja`
  builds, `Default()` to name a subset, `build_tier` to move one target,
  and the unusual layout at the end.

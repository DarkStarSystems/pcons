# Presets

A **preset** is a named, declarative bundle of build settings. Variants
(`debug`/`release`), feature bundles (`warnings`, `lto`), and cross-compilation
targets (`emscripten`, `pyodide`) are all presets — they reduce to one
tool-agnostic primitive, a `Preset` (a set of per-tool `ToolContribution`s),
applied by the core via `env.apply(preset)`.

This document is the **decided convention** for how presets are named, applied,
and authored. Agents and contributors should follow it. The whole convention is
implemented (see the status table at the end); any future additions marked
**(planned)** describe an agreed-but-unbuilt target.

## The three shapes

Presets come in three shapes. The right surface matches the shape — don't force
one verb onto all of them.

| Shape | What it is | How you apply it | User-extensible? |
|-------|------------|------------------|------------------|
| **Knob** | one orthogonal axis that takes a value | a `set_*` method | no — curated |
| **Feature** | additive, named, toolchain-resolved flag bundle | `env.apply_preset("name")` | **yes** |
| **Target** | parameterized cross-compile descriptor | `env.apply_cross_preset(factory())` | yes |

### Knobs

General settings that apply to any build live on the environment:

```python
env.set_variant("release")
env.set_target_arch("arm64")
```

**Domain-specific** settings live on the relevant *tool namespace*, not on core, so the
core stays tool-agnostic and each language brings its own:

```python
env.cxx.set_standard("c++20")     # C++ setting, on the cxx namespace
# a Fortran toolchain would offer env.fc.set_standard("f2018"), etc.
```

> **How it works:** there is no C/C++ vocabulary on core `Environment`. A tool
> namespace (`env.cxx`) resolves an unknown attribute like `set_standard` through
> the environment's toolchain via `Toolchain.tool_setting(tool, name)`, which
> returns a callable bound to the env at *access* time (so it's clone-safe and
> reuses no captured state). The realization stays per-toolchain
> (`make_cxx_standard_preset`); a toolchain that doesn't realize a setting simply
> no-ops. New domain settings are added by overriding `tool_setting` — no core
> change.

### Features

Additive, named bundles, resolved per-toolchain:

```python
env.apply_preset("warnings")      # built-in
env.apply_preset("werror")        # compose freely with any warning set
env.apply_preset("mycorp/strict") # contributed (registry)
```

### Targets

Parameterized cross-compilation descriptors are factory functions, namespaced by
Python import:

```python
from pcons.toolchains.presets import emscripten, pyodide, android
env.apply_cross_preset(pyodide("2026_0"))
```

## Naming & namespacing

- **Names are lowercase, hyphenated, short**: `warnings`, `werror`, `asan`, `lto`.
- **Categories are metadata, never encoded in the name** (a preset's category is
  `feature`/`variant`/`target`/…, not part of its name).
- **Prefer small, orthogonal, composable presets over mega-bundles.** Application
  is additive and `explain()` shows provenance, so composition is the idiom.
  This is why `warnings` does **not** include `-Werror` — apply `werror` too if
  you want it.
- **Namespacing — `scope/name`** (for contributed features):
  - **Bare names are reserved for pcons built-ins** (`warnings`, `lto`).
  - **Contributed presets must carry a scope**: `mycorp/strict`, `qt/widgets`
    (`register_preset` warns on a bare name).
  - Targets and value presets are namespaced by Python import instead
    (`from mycorp.pcons import strict`).

## Where realizations live (locality)

The **identity** of a preset (name, category, description) is tool-agnostic. Its
**realization** (the actual flags) is toolchain-specific and lives as close to
the toolchain as possible:

- **Built-in realizations live *in* the toolchain** — e.g. each toolchain's
  `FEATURE_PRESETS` dict and `*_VARIANTS` tables. Flags are never relocated to a
  central place.
- **Contributed realizations live with the contributor** (in the registry) as a
  resolver `(toolchain) -> contributions | None`. This is the only place
  external presets *can* live.
- The **core registry holds identity + a resolver pointer + metadata — never raw
  flags.**

Resolution order: the active **toolchain answers first** (built-ins, near their
flags), then the **registry** (contributed). `explain()` records which source
realized each flag (`warnings <- gcc (toolchain)` vs `mycorp/strict <-
registry`), so indirection never becomes a mystery.

## Tool-agnostic by construction

pcons core knows nothing about compilers. Presets must not reintroduce that
knowledge:

- The `Preset`/`ToolContribution` model and the categories are tool-free.
- A feature resolver **receives the toolchain and returns contributions**, so it
  works for *any* domain — C/C++, Fortran, WASM, LaTeX, asset pipelines.
- Domain settings live on tool namespaces (above), not on core.

Feature presets are realized for whatever compile tools a toolchain declares via
`_feature_preset_tools()` (e.g. `("cc", "cxx")` for C/C++, `("fc",)` for
Fortran), so the same `warnings`/`werror` names map to the right tool per
toolchain. WASM toolchains (`emscripten`/`wasi`) are clang-based and inherit the
C/C++ realizations on `cc`/`cxx` directly.

## Authoring a feature preset

**Built-in (in a toolchain) — the common case.** Add an entry to the toolchain's
`FEATURE_PRESETS`; it is realized on that toolchain's compile tools:

```python
class UnixToolchain(BaseToolchain):
    FEATURE_PRESETS = {
        "warnings": {"compile_flags": ["-Wall", "-Wextra", "-Wpedantic"]},
        "werror":   {"compile_flags": ["-Werror"]},
        ...
    }
```

A Fortran toolchain owns its own flags and target tool:

```python
class GfortranToolchain(UnixToolchain):
    FEATURE_PRESETS = {
        "warnings": {"compile_flags": ["-Wall", "-Wextra"]},
        "werror":   {"compile_flags": ["-Werror"]},
    }
    def _feature_preset_tools(self):
        return ("fc",)
```

**Contributed (external).** Register a resolver under a scope. The resolver
receives the toolchain and returns contributions, or `None` when the preset
doesn't apply to that toolchain (a silent no-op, not an error):

```python
from pcons import preset, ToolContribution   # or register_preset(name, fn, ...)

@preset("acme/draft", description="LaTeX draft mode")
def draft(tc):
    if tc.name != "latex":
        return None                       # not applicable to this toolchain
    return [ToolContribution("latex", flags=("-draftmode",))]

# build script (resolution is toolchain-first, then registry):
env.apply_preset("acme/draft")
# pcons.list_presets() lists registered contributed presets.
```

Bare (unscoped) names are reserved for pcons; `register_preset` warns if a
contributed preset omits its `scope/`.

A declarative resolver can already make **compiler-version-specific choices** —
it receives the toolchain, so it may branch on `tc.name`/version and return
different contributions.

**Imperative escape hatch.** Most presets only *add* flags, which the
declarative form handles. For the rare preset that must do something else —
**remove** or **override** a flag, or anything not expressible as additive
contributions — register it with `imperative=True`. Its function receives the
*environment* and may do anything; it must self-describe (the `description` is
what `explain()` reports, since an imperative change can't be attributed
token-by-token):

```python
@preset("acme/no-rtti", imperative=True, description="drop -frtti, force -fno-rtti")
def no_rtti(env):
    if "-frtti" in env.cxx.flags:
        env.cxx.flags.remove("-frtti")
    env.cxx.flags.append("-fno-rtti")
```

`explain()` then appends: `imperative presets (ran; effect not attributable):
acme/no-rtti - drop -frtti, force -fno-rtti`.

## Status summary

| Piece | State |
|-------|-------|
| `Preset`/`ToolContribution`, `env.apply()`, `explain()` provenance | implemented |
| `env.apply_preset("name")`, per-toolchain `FEATURE_PRESETS` | implemented |
| `warnings` + `werror` (orthogonal), Fortran/WASM coverage | implemented |
| `env.set_variant` / `env.set_target_arch` | implemented |
| Cross-preset factories (`emscripten`/`pyodide`/…) | implemented |
| `env.cxx.set_standard` (tool-namespace setting via `tool_setting`) | implemented |
| Registry, `scope/name` namespacing, `register_preset`/`preset`/`list_presets` | implemented |
| Imperative escape hatch (`register_preset(..., imperative=True)`) | implemented |

# Presets

A **preset** is a named, declarative bundle of build settings. Variants
(`debug`/`release`), feature bundles (`warnings`, `lto`), and cross-compilation
targets (`emscripten`, `pyodide`) are all presets — they reduce to one
tool-agnostic primitive, a `Preset` (a set of per-tool `ToolContribution`s),
applied by the core via `env.apply(preset)`.

This document is the **decided convention** for how presets are named, applied,
and authored. Agents and contributors should follow it. Sections marked
**(planned)** describe the agreed target that is not yet fully implemented.

## The three shapes

Presets come in three shapes. The right surface matches the shape — don't force
one verb onto all of them.

| Shape | What it is | How you apply it | User-extensible? |
|-------|------------|------------------|------------------|
| **Knob** | one orthogonal axis that takes a value | a `set_*` method | no — curated |
| **Feature** | additive, named, toolchain-resolved flag bundle | `env.apply_preset("name")` | **yes** |
| **Target** | parameterized cross-compile descriptor | `env.apply_cross_preset(factory())` | yes |

### Knobs

General knobs that apply to any build live on the environment:

```python
env.set_variant("release")
env.set_target_arch("arm64")
```

**Domain-specific** knobs live on the relevant *tool facet*, not on core, so the
core stays tool-agnostic and each language brings its own:

```python
env.cxx.set_standard("c++20")     # C++ knob, on the cxx facet  (planned home)
env.cxx.set_stdlib("libc++")      # (planned)
# a Fortran toolchain would offer env.fc.set_standard("f2018"), etc.
```

> **Status:** `set_variant`/`set_target_arch` are on core today. `set_cxx_standard`
> currently lives on core `Environment` as well; it is **(planned)** to move to
> `env.cxx.set_standard` so no C/C++ vocabulary remains in core. A toolchain
> attaches its facet knobs at setup, resolving through the tool's owning
> environment at call time (clone-safe).

### Features

Additive, named bundles, resolved per-toolchain:

```python
env.apply_preset("warnings")      # built-in
env.apply_preset("werror")        # compose freely with any warning set
env.apply_preset("mycorp/strict") # contributed (planned registry)
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
- **Namespacing — `scope/name`** *(planned for contributed features)*:
  - **Bare names are reserved for pcons built-ins** (`warnings`, `lto`).
  - **Contributed presets must carry a scope**: `mycorp/strict`, `qt/widgets`.
  - Targets and value presets are namespaced by Python import instead
    (`from mycorp.pcons import strict`).

## Where realizations live (locality)

The **identity** of a preset (name, category, description) is tool-agnostic. Its
**realization** (the actual flags) is toolchain-specific and lives as close to
the toolchain as possible:

- **Built-in realizations live *in* the toolchain** — e.g. each toolchain's
  `FEATURE_PRESETS` dict and `*_VARIANTS` tables. Flags are never relocated to a
  central place.
- **Contributed realizations live with the contributor** *(planned registry)* as
  a resolver `(toolchain) -> contributions | None` (or a family-keyed table as
  sugar). This is the only place external presets *can* live.
- The **core registry holds identity + a resolver pointer + metadata — never raw
  flags.**

Resolution order: the active **toolchain answers first** (built-ins, near their
flags), then the **registry** (contributed). `explain()` records which source
realized each flag (`warnings ← gcc (toolchain)` vs `mycorp/strict ←
registry`), so indirection never becomes a mystery.

## Tool-agnostic by construction

pcons core knows nothing about compilers. Presets must not reintroduce that
knowledge:

- The `Preset`/`ToolContribution` model and the categories are tool-free.
- A feature resolver **receives the toolchain and returns contributions**, so it
  works for *any* domain — C/C++, Fortran, WASM, LaTeX, asset pipelines.
- Domain knobs live on tool facets (above), not on core.

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

**Contributed (external) — *(planned)*.** Register a resolver under a scope:

```python
@pcons.preset("acme/draft", category="feature")
def draft(tc):
    if tc.name != "latex":
        return None                       # not applicable to this toolchain
    return [ToolContribution("latex", flags=("-draftmode",))]
```

## Status summary

| Piece | State |
|-------|-------|
| `Preset`/`ToolContribution`, `env.apply()`, `explain()` provenance | implemented |
| `env.apply_preset("name")`, per-toolchain `FEATURE_PRESETS` | implemented |
| `warnings` + `werror` (orthogonal), Fortran/WASM coverage | implemented |
| `env.set_variant` / `env.set_target_arch` | implemented |
| Cross-preset factories (`emscripten`/`pyodide`/…) | implemented |
| `env.cxx.set_standard` / `set_stdlib` (knob re-homing) | planned |
| Registry, `scope/name` namespacing, `register_preset`, `list_presets` | planned |

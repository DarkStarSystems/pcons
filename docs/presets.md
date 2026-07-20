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

## Preset application: the contract

How a preset lands on an environment is itself a contract: **a preset either
applies fully or fails loudly, and every bookkeeping field has exactly one
writer.** Silent partial application is the same defect as GCC ignoring a
cross triple — the build proceeds looking configured while part of the
request evaporated.

This section is a specification of what the **core apply machinery
enforces**, not a checklist for preset or toolchain authors. Contributed
and site-specific presets flow through the same `apply` path as built-ins,
so they get these guarantees (and these errors) without doing anything.
The entire authoring obligation remains the two rules already documented
above: return `None` when a preset doesn't apply to a toolchain, and
return contributions when it does — the machinery treats what a resolver
returns as the complete realization. Custom *toolchains* that override the
realization hooks (`_arch_contributions`, `apply_cross_preset`, …) keep
their freedom, but the base classes default to fail-fast — e.g. an empty
arch realization raises from the base `apply_target_arch` unless the
toolchain explicitly declares the no-op — so an extension that simply
doesn't handle a case is safe by default rather than silently wrong.

### Apply fully or raise

When a preset is applied, exactly one of three things happens:

1. **It applies** — its contributions land on the tools they name.
2. **It is inapplicable** — the resolver (toolchain or registry) returned
   `None` for this toolchain. This is the *only* sanctioned silent no-op,
   and it is a decision made by the realizer, never by the apply machinery.
3. **It fails** — anything else raises at apply time with a message naming
   what was missing. In particular:
   - An **unknown preset name** (no toolchain built-in, no registry entry)
     is an error, not a warning — a typo'd `apply_preset("waarnings")` must
     not produce a quietly less-strict build.
   - A **`cmd` contribution to a tool the environment doesn't have** is an
     error: a command swap (`cc` → `emcc`) *is* the retargeting mechanism,
     and dropping it silently un-crosses the build. Validation happens
     before any contribution is applied, so application is atomic.
   - A preset **none of whose contributions landed** is an error — it
     "applied" and changed nothing, which is indistinguishable from a
     no-op the user didn't ask for. Flag/define contributions to a subset
     of missing tools are tolerated (broadcast semantics); losing *all* of
     them is not.
   - A **knob value the toolchain cannot realize** is an error, enforced in
     the *base* `apply_target_arch` when the realization comes back empty —
     so custom toolchains inherit fail-fast without remembering to raise.
     A toolchain that legitimately wants a no-op declares it explicitly.
     Concretely: on Linux, `set_target_arch` cannot retarget GCC/LLVM (use
     `linux_cross(triple=...)`) and says so; wasm toolchains reject any
     arch other than `wasm32` rather than silently coercing.

### One writer per bookkeeping field

`env.variant` is written only by variant presets (enforced by their
exclusive group). `env.target_arch` is written **only by the
`set_target_arch` knob**: cross presets carry `arch` as metadata (see the
cross-target contract below) and must not stamp it onto the environment —
otherwise the field's value depends on call order and can disagree with the
flags actually in effect.

### One application per resolution

The `set_*`/`apply_*` surfaces resolve a preset against each configured
toolchain, but toolchains share tools (`cc`, `cxx`, `link`), so resolution
and application are distinct steps: a resolved preset is applied **at most
once per environment**, however many toolchains resolved it. Applying per
toolchain would double flags (`-arch arm64 -arch arm64`, `-Werror
-Werror`) on any env with `add_toolchain()`.

Deliberate *user* re-application is unaffected: applying the same feature
preset twice on purpose remains additive, as composition requires.

## Cross-compilation targets: the contract

A `CrossPreset` describes **what to build for**; each toolchain decides **how**
to get there. This section is the contract between the two — what each field
means, which mechanisms exist, and what a toolchain must do when it can't
honor a preset. The goal is *no silent misbuilds and no hidden magic*: every
realized flag is attributable via `explain()`, every auto-detected value is
overridable, and every unsupported combination is a loud error.

### Two surfaces, one distinction

pcons has two target-related surfaces. They answer different questions and
must not be conflated:

| Surface | Question it answers | Example |
|---------|--------------------|---------|
| `env.set_target_arch(arch)` (knob) | *which CPU*, same platform as the toolchain's default | macOS universal builds (`-arch`), MSVC arm64-on-x64 (cross toolset + `/MACHINE:`) |
| `env.apply_cross_preset(p)` (target) | *which platform* — different OS/SDK/libc | iOS, Android, WASI, Linux-on-ARM |

A cross preset is realized **only** from its own fields (triple, sysroot,
env_vars, extra flags). It never routes through the arch knob — the knob's
vocabulary is per-toolchain-and-platform, while a preset's `arch` uses its
ecosystem's names (`arm64-v8a`, `wasm32`), and mixing the two produces flags
like `-arch arm64-v8a`. When a triple is present it already encodes the CPU;
a separate arch flag is at best redundant.

### Exactly two retarget mechanisms

Every toolchain reaches a foreign target in one (or both) of two ways:

1. **Flag-retargeted** — one driver binary, target selected by flags.
   Clang-family (`--target=<triple>`, sysroot flags) and swiftc
   (`-target`, `-sdk`).
2. **Binary-retargeted** — a different tool binary per target. GCC cross
   binaries (`aarch64-linux-gnu-gcc`), Emscripten (`emcc`), wasi-sdk's
   bundled clang. Selected via the preset's `tool_cmds`, keyed by pcons
   tool name (`cc`, `cxx`, `link`, `ar`, ...) so any tool can be
   repointed; `env_vars` (`CC`/`CXX`/`LD`/`AR`) survives as a deprecated
   alias.

A preset may carry both (Android does: per-triple clang wrappers *and* a
triple); each toolchain consumes the mechanism it understands. Some
targets are *toolchain-shaped*, not preset-shaped: WebAssembly needs the
dedicated `emscripten`/`wasi` toolchains, which own output suffixes,
shared-library rules, and the link driver — applying a wasm cross preset
to a native toolchain raises rather than half-applying (compile with
emcc, link with the host driver). A toolchain
that can realize **neither** mechanism from a given preset must **raise at
apply time** with a message naming what's missing — never partially apply
(GCC rejects triple-only presets, telling you to provide cross binaries or
use clang).

MSVC has no different-platform targets at all — everything it can build for
is Windows, so arch selection there is the *knob's* job (below), and
`apply_cross_preset` on MSVC is always an error directing you to
`set_target_arch`.

### The knob can retarget binaries too

The knob/preset split is by *question*, not mechanism — and the knob's
realization is per-toolchain like everything else. Answering "which CPU" may
itself require different binaries: building for arm64 on x64 Windows needs
the cross toolset (`bin/Hostx64/arm64/cl.exe` and the matching `lib/arm64`
directories, all in the same VC install). MSVC's `set_target_arch` resolves
those paths itself — the same way CMake's `-A ARM64` does — rather than only
emitting `/MACHINE:` and relying on the user having run the right `vcvars`
variant. clang-cl keeps its one binary (`--target` retargets it) but gets
the cross VC/SDK library directories the same way, since the dev shell's
`LIB` covers only the host arch. A missing cross toolset is a hard error
naming the Visual Studio Installer component to add. `explain()` keeps it
transparent: every repointed `cmd` and added `/LIBPATH:` is attributed to
the arch preset.

### Field contract

| Field | Meaning | Realized as |
|-------|---------|-------------|
| `name` | preset identity; appears in `explain()` provenance | — |
| `triple` | **canonical target identity** for flag-retargeted drivers; encodes CPU, vendor, OS, ABI (and for Apple, min version) | clang `--target=`, swiftc `-target`; ignored by binary-retargeted drivers (their binary *is* the triple) |
| `arch` | CPU name in the target ecosystem's own vocabulary (`arm64`, `arm64-v8a`, `wasm32`); metadata for naming and platform-suffix decisions | **nothing** — never a flag source |
| `sysroot` | root of target headers/libraries (sysroot, SDK, NDK sysroot) | `--sysroot=` (GNU-style), `-isysroot` (Apple clang), `-sdk` (swiftc) |
| `tool_cmds` | per-tool command overrides keyed by pcons tool name — the binary-retarget mechanism | replaces `<tool>.cmd` for every named tool |
| `env_vars` | *(deprecated alias for `tool_cmds`: CC→cc, CXX→cxx, LD→link, AR→ar)* | merged into `tool_cmds`; `tool_cmds` wins |
| `extra_compile_flags` / `extra_link_flags` | verbatim escape hatch for target-required flags (`-mios-version-min=`, `-sSIDE_MODULE=1`) | appended to `cc`+`cxx` / `link` as-is |

### Bounded auto-detection

Factories and toolchains may auto-detect paths (the iOS SDK via `xcrun`,
`find_wasi_sdk()`, NDK layout), because requiring users to paste SDK paths is
worse. But detection is bounded by three rules, which keep it transparent
rather than magic:

1. **Always overridable** — every detected value has an explicit parameter
   (`ios(sdk=...)`, `wasi_sdk(sdk_path=...)`) that bypasses detection
   entirely.
2. **Always attributable** — detected values land in ordinary contributions
   under the preset's name, so `env.explain()` shows exactly what was
   resolved and by whom.
3. **Loud on failure** — failed detection is a warning or error naming the
   tool it tried (`xcrun`, `WASI_SDK_PATH`), never a silent omission.

### Host independence

Which flags a preset realizes depends only on the **target descriptor and the
toolchain** — never on the host OS. The host may affect *detection* (whether
`xcrun` exists) but not *semantics*: the same `ios()` preset on the same
toolchain must produce the same command lines on any host that has the SDK.


### Authoring checklist

For a new target factory in `pcons/toolchains/presets.py`:

- Pick the **triple** first; derive `name` and `arch` from it, not the
  reverse. Use the ecosystem's own arch vocabulary.
- Carry the platform's required flags in `extra_*_flags`; don't invent new
  fields for them.
- If the target needs specific binaries, set `env_vars` — with paths derived
  from one user-supplied root parameter, not guessed.
- Auto-detect only per the bounded rules above.

For toolchain realization, override `_target_contributions()` (see
`UnixToolchain` for the flag-retargeted pattern, `SwiftToolchain` for a
driver with its own flag spelling, `WasmToolchain` for narrowing to extra
flags only) and keep the fail-fast rule: realize a mechanism or raise.

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
| Cross-preset field contract: triple/sysroot/env_vars realization, bounded auto-detection (xcrun, wasi-sdk) | implemented |
| Preset application contract: apply-fully-or-raise (unknown names, missing-tool cmd, zero-effect presets, unrealizable knobs) | implemented |
| `env.target_arch` single-writer (knob only; cross presets are metadata) | implemented |
| One application per resolution (no per-toolchain double-apply) | implemented |
| `CrossPreset.arch` decoupled from flag emission (host-independent) | implemented |
| Fail fast on unrealizable cross presets (MSVC + any, GCC + triple-only) | implemented |
| MSVC/clang-cl `set_target_arch` selects the cross toolset (cl/lib dirs), not just `/MACHINE:` | implemented |
| `tool_cmds` binary-retarget (any tool; `env_vars` deprecated alias); wasm presets require the wasm toolchains | implemented |
| `env.cxx.set_standard` (tool-namespace setting via `tool_setting`) | implemented |
| Registry, `scope/name` namespacing, `register_preset`/`preset`/`list_presets` | implemented |
| Imperative escape hatch (`register_preset(..., imperative=True)`) | implemented |

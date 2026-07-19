# Plan: Swift Toolchain Support

Status: **in progress** (drafted 2026-06-04, revised 2026-07-19 after review
against current main; implementation started 2026-07-19)

## Summary

Add a `SwiftToolchain` to pcons supporting Swift programs and libraries,
cross-module imports, and bidirectional C/C++ interop. Swift fits pcons'
architecture well — it's essentially "gfortran plus C++ modules," both
already solved. The toolchain is a pure add-on under `pcons/toolchains/`,
with zero Swift knowledge in core.

Selection is by name, like every toolchain now: `Environment(toolchain="swift")`
via `toolchain_registry.register()` + `register_finder(["swift"],
find_swift_toolchain)`. The generated `KnownToolchain` Literal, the docs
toolchain table, and `env.add_toolchain("swift")` all pick it up
automatically from the registry.

The closest existing template is `pcons/toolchains/gfortran.py`: a non-C
language with its own compiler-as-link-driver, module artifacts, and
mixed-language runtime injection (`get_runtime_libs` / `get_runtime_libdirs`
hooks exist for exactly this).

## The one real architectural gap: module-at-once compilation

pcons compiles strictly per-source: `CompileLinkFactory._create_object_node()`
(`pcons/tools/compile_link.py`) maps one source → one object, with an object
cache keyed by source path. Swift breaks this — files in a Swift module see
each other freely, so **the compilation unit is the module (= pcons Target),
not the file**. CMake and SwiftPM reached the same conclusion.

The clean fix: compile each target's Swift sources in one whole-module
invocation:

```
swiftc -emit-object -wmo -module-name Foo \
  -emit-module -emit-module-path Foo.swiftmodule \
  -o obj.foo/foo.o src/*.swift
```

N sources → 1 object + 1 `.swiftmodule`. To support it, add a tool-agnostic
`group_sources: bool = False` flag to `SourceHandler`
(`pcons/tools/toolchain.py`). When set, `CompileLinkFactory` collects all
matching sources in a target into a single compile node
(`_build_info["sources"]` already holds a list — link nodes do this today).
Cache key becomes (sorted sources, cmd, effective requirements). This stays
generic — any future module-at-once language reuses it.

**compile_commands.json wrinkle (do not defer):** the compile-commands
generator emits one entry per compile node. A grouped node covers N sources,
but sourcekit-lsp needs an entry *per file* (each carrying the whole file
list in its command, as CMake does for Swift). The `group_sources` work must
include a per-source fan-out in `pcons/generators/compile_commands.py`, or
IDE support silently covers one file per module.

Nice consequence: **no dyndep scanner needed**, unlike Fortran. Since one
target = one module, inter-module `.swiftmodule` ordering falls out of
ordinary target dependencies. Intra-module ordering is solved by WMO. Much
simpler than the Fortran or C++20-modules machinery.

## Phase 1 — Pure Swift programs (MVP)

- `pcons/toolchains/swift.py`:
  - `SwiftCompiler` (tool name `swiftc`, language `"swift"`)
  - `SwiftLinker` — swiftc as link driver, like `GfortranLinker`; it handles
    Swift runtime libs automatically
  - `SwiftToolchain(UnixToolchain)`, `TOOL_NAMES` declared for the env stub
    generator
  - Registry entry (category `"swift"`, check command `swiftc`) **plus**
    `register_finder(["swift"], find_swift_toolchain)` so
    `Environment(toolchain="swift")` works
  - Run `python -m pcons._gen_stubs` — the `KnownToolchain` Literal and env
    tool-namespace stubs regenerate from the registry (freshness test
    enforces this)
- The `group_sources` mechanism in `SourceHandler` + `CompileLinkFactory`,
  including the compile_commands per-source fan-out
- Module name derived from target name (sanitized to a valid Swift
  identifier), passed as `-module-name`
- Dependency tracking via `-emit-dependencies` (Makefile-format `.d`, works
  with ninja `deps = gcc`)
- **Presets, not ad-hoc variant code**: realize the built-in variant presets
  (debug → `-Onone -g`, release → `-O`) and the feature presets `werror`
  (`-warnings-as-errors`) through the toolchain preset table, exactly as
  gfortran does — this also makes `env.explain()` attribution work for free
- `examples/46_swift_hello/` in current minimal style
  (`Environment(toolchain="swift")`, no explicit generate call), with
  `test.toml` gated on `requires = ["swiftc"]`
- Unit tests for the toolchain and the grouped-compile mechanism

## Phase 2 — Swift libraries & cross-module imports

- Static/shared libraries of Swift code; emit `.swiftmodule` into a shared
  `swiftmodules/` dir in the build tree
- Propagate the module search path as a usage requirement: `target.public`
  gets `-I <swiftmodules dir>` so dependents' `import Foo` resolves — the
  existing usage-requirements machinery does the rest
- Language priority: `"swift": 3` (above cxx), mirroring
  `GfortranToolchain.language_priority`
- `.swiftmodule` compatibility note: module binaries are compiler-version-
  and flag-locked (same class of problem as C++ BMIs). Within one build this
  can't bite (one toolchain, one flag set per target); state the assumption.
  `-enable-library-evolution` + `.swiftinterface` is the escape hatch for
  distributable libraries (optional, later)
- Example with a Swift library + program importing it, including a `Test()`
  target so `pcons test` covers the grouped-compile path

## Phase 3 — C/C++ interop (the headline feature)

- **Swift → C/C++ (importing)**: Swift consumes C headers via clang module
  maps. Provide a helper that generates a `module.modulemap` from a target's
  public headers (CMake does this). Pass the C target's effective
  includes/defines through as `-Xcc -I…` / `-Xcc -D…` — the
  `${pairwise(...)}` subst function (already used for `-framework`) handles
  the two-token form.
- **Interop mode is a tool-namespace setting**, per the presets doctrine
  (like `env.cxx.set_standard`): `env.swiftc.set_cxx_interop("c++20")` →
  `-cxx-interoperability-mode=default -Xcc -std=c++20`. Consider
  `env.swiftc.set_language_mode("6")` for Swift language modes on the same
  pattern.
- **C++ → Swift (reverse)**: `swiftc -emit-clang-header-path Foo-Swift.h` as
  an additional output of the module compile (`MultiOutputBuilder` /
  `OutputSpec` pattern), propagated as a public include dir. C++ code does
  `#include "Foo-Swift.h"`.
- **Mixed linking**: override `get_runtime_libs` / `get_runtime_libdirs`
  exactly as gfortran does. When a C++ linker links Swift objects, inject the
  Swift runtime paths — `swiftc -print-target-info` emits JSON with
  `runtimeLibraryPaths` (the analog of `_find_gfortran_libdir()`). When
  swiftc drives the link of C++ objects, it mostly handles it; may need
  `-lc++` / `-lstdc++` on Linux.
- **Verify against the fixed link ordering**: `_merge_with_base_libs` now
  (correctly) puts `env.link.libs` after usage-requirement libs, and runtime
  libs must land after the Swift objects/libs that need them — add a test
  that pins the mixed-link line order (this exact bug shape just got fixed
  for Rust in the PR #48 review).

## Phase 4 — First-class polish

- **`pcons init` adoption**: detect `.swift` sources (like C/C++ today) and
  generate a working `toolchain="swift"` script; scaffolding stays C++
- **Cross-preset synergy as a named goal**: pcons already ships `ios()` /
  `android()` cross presets — "Swift + C++ for iOS in two lines" is a story
  neither CMake nor SwiftPM tells cleanly. Add an example once Phase 3
  lands; verify cross-preset flags pass through `-Xcc` for the clang
  importer.
- **CI reality**: macOS runners have swiftc via Xcode — Phase 1 examples run
  there for free with `requires = ["swiftc"]` gating. Ubuntu runners do NOT
  ship Swift: add `swift-actions/setup-swift` to one Linux job (the only CI
  infra change in this plan). Windows: swift.org installer, lld-link, `.obj`
  suffix; defer until Unix is solid, then test on tower1.
- `apply_target_arch` (`-target <triple>`)
- `compile_commands.json` entries feed sourcekit-lsp (depends on the Phase 1
  fan-out)

## Risks / open questions

1. **Incremental granularity**: WMO recompiles the whole module when any
   file changes. Acceptable for v1 (ninja-level incrementality is
   per-module, same as CMake's default). True per-file incremental Swift
   under ninja requires output-file-maps and frontend-level invocations (the
   Bazel approach) — significant complexity; defer unless users ask.
2. **C++ interop coverage**: Swift imports a large but bounded subset of
   C++; templates and some idioms don't cross. Document rather than solve.
3. **Windows Swift maturity**: real but lags; gate in CI like the
   conan/xcode examples.
4. **Object cache semantics**: grouped compiles need their own cache key
   shape — small, contained change in `compile_link.py`.

## Effort

Roughly a week of focused work to a genuinely useful state (phases 1–3);
phase 1 alone yields a demoable `46_swift_hello` example. Swift 6.3.3 is
available locally (arm64-apple-macosx) for spiking.

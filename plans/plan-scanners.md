# Scanners: Tool-Agnostic Discovered Dependencies

**Status: implemented** on `feature/scanners`. Core in `pcons/core/scan.py` and
`pcons/core/collate.py`; C++20 modules (LLVM, GCC, MSVC) and Fortran migrated
onto it. User docs: `docs/scanners.md`. Closes #105.

## Context

Issue #105: a project that generates a C++ source with a program it builds
itself stops building as soon as any environment owns a module interface.
Ninja reports a cycle through `cxx_modules.dyndep`.

The root cause was structural. The module pass wired **one global dyndep node**
whose inputs were *every* scanned source and which gated *every* scanned
object, so any scanned source produced by the build closed a cycle through its
own generator. The obvious fix fails: drop generated sources from the scan's
inputs and the cycle goes, but the scan then runs before the generator and dies
on the missing file. One fact, two symptoms — **a generated source cannot be
scanned until its producer has run.** Only structure fixes that.

The same global-dyndep structure existed a second time in `gfortran.py`, with
the same latent cycle and no cache, depfile or restat. When the same machinery
appears twice with the same defect, it's a core primitive wearing toolchain
costumes.

## The invariant

> **Configure may depend only on static facts** — file names, suffixes, flags,
> the target DAG. **Content-derived facts flow through scan → collate at build
> time.**

Corollaries:

- A scan edge's input is the source node itself, so a generated source
  inherits its producer's ordering from the node graph. No phases, no
  existence checks, N generation stages free.
- Scanned sources are **not** configure dependencies.
- Dyndep carries deps and outputs, never command lines, so content-derived
  *flags* move into per-edge args files written by collate and referenced
  through a token whose path is fixed at configure time.
- Acyclicity holds by construction: scan(s) ← s ← producer target ← its own
  collate/scans, all along existing DAG direction. Cross-scope artifact
  references still resolve, because dyndep implicit outputs are global to
  ninja.

## Architecture

`Scanner` is a frozen value object; `scanner.attach(*targets)` is the only
surface. One attached target is one **scope**. `ScannerResolver` runs inside
`Resolver.resolve()`, after the toolchain `after_resolve` hooks (so
toolchain-attached scanners are visible) and before command expansion (so
per-edge variables exist when compile templates expand). Per scope:

1. **Governed edges** — build edges with at least one source matching
   `source_suffixes`. None ⇒ hard error; an attach that governs nothing is a
   mistake.
2. **One scan edge per governed edge**: scanned sources in, one scan-info JSON
   out, optional depfile (`gcc`) or `/showIncludes` (`msvc`). Marker tokens
   and a constant description keep all scans of a scanner on **one ninja
   rule**; per-edge values ride ninja variables.
3. **A configure-written manifest** (static facts only), written
   if-changed.
4. **One collate edge**: scan infos + manifest + dependency scopes' exports
   in; dyndep + exports + args files out, `restat`.
5. **Stamping**: `dyndep = <scope dyndep>` on every governed edge, that file in
   **order-only** position, plus the edge's args-file variable and token.

`pcons/core/collate.py` holds the generic collate CLI and the three versioned
JSON schemas (scan info / manifest / exports), plus the shared dyndep writer
and `write_text_if_changed`. A toolchain that needs more supplies its own
`collate_command` and passes facts through `manifest_extra` / `edge_extra`;
`pcons/toolchains/cxx_collate.py` is the only such client.

## Decisions

**Named `Scanner`.** It fills the slot `ARCHITECTURE.md` documented as
"Partial" for years, and it's what every other build system calls this. The
old protocol sketch (a `scan()` returning nodes at configure time) is gone: it
described the very thing the invariant forbids.

**Dyndep in order-only position.** The governed edge must wait for the file to
exist, but a rewritten dyndep must not by itself dirty every edge in the scope
— the *loaded* dyndep supplies the real deps. Ninja accepts a dyndep file in
order-only position; an implicit dep would rebuild the world on every collate.

**Per-scope BMI directories** (`cxx_modules/<scope>/<key>/`). Cross-scope
implicit-output collisions become structurally impossible instead of
error-checked. Within-scope collisions keep the old provider-collision error,
now raised from collate.

**`import std;` dormant, not synthesized.** Configure describes the std /
std.compat edges and a static exports file; nothing references them until some
TU's collate reports a real import, whose dyndep then requires the std BMI and
whose scope's link-extras file (`link_args`) gains the std object. A project
that never imports std builds and links nothing extra, and a toolchain with no
std module is an error only when something imports it. `wire_std_into_targets`
dies.

**Exports travel along target dependencies.** A scope resolves a required name
against its own provides and the exports of the scopes it depends on — not
against anything anywhere in the build. The dependency carries the exports;
content decides the order. This is a behavior change for Fortran: a
cross-target `USE` now needs a declared `link()` / `depends()`.

**Generators refuse rather than mislead.** `Generator.find_dyndep_use()` plus
a `_reject_dyndep()` call in the Makefile and Xcode generators converts
silently wrong output into a clear error.

**Delivered atomically** on one feature branch: the old machinery is deleted
there, with no coexistence code.

## Migration notes

- clang: `clang-scan-deps -format=p1689` scan edges; modmap response file
  placed *before* the source so `-x c++-module` applies.
- GCC: the compiler is its own scanner (`-fdirectives-only` p1689, format
  version 0); the modmap is a libcody module-mapper file. Module interface
  compiles still carry no depfile (#102), so they take an implicit dep on
  their own scan output instead — same coverage, no cycle.
- MSVC: `cl /scanDependencies` with `deps=msvc`; the modmap is a response
  file, where a flag and its argument must share one line (cl.exe won't join
  them across lines — D8004).
- Fortran: `fortran_scanner.py --scan-one` emits the neutral schema; the
  generic collate does the rest. `restat` on the compiles fixes a
  pre-existing forever-rebuild bug (an unchanged `.mod` as a dyndep output).

## Out of scope

- **Qt automoc** onto the Scanner primitive. Its collate would need a
  "verify + re-run pcons" variant; the manifest and args schemas leave room.
- **Scopes without a target.** `attach()` takes targets only, so a bare node
  can't be scanned. No use case has needed it.
- **MSVC mixed-provides nuance.** `/interface` and `/internalPartition` are
  mutually exclusive (D8016), so the scan's classification decides; a TU that
  somehow provided both would need a rule the standard doesn't ask for.
- Per-`.mod` restat for gfortran's unchanged-module outputs (pre-existing).
- Staged-generation regeneration-edge bugs (separate work).

# Design cleanup plan: contracts over accidents

Working list from the 2026-07-20 design review, done in the style of the
cross-target contract work (docs/presets.md "Cross-compilation targets: the
contract", v0.22.x): preserve the public API surface, replace accidental
semantics with stated contracts, and turn silent misbehavior into loud
errors. Refer to and update this file as items land; mark status inline.

Statuses: **todo** / **in progress** / **done (commit)** / **dropped (why)**.

---

## Theme 1 — Preset application contract (silent partial application)

**Status: implemented** (docs/presets.md "Preset application"). Bonus find
while implementing: gfortran inherited Unix's cc/cxx variant realization, so
Fortran debug/release variants silently did nothing — fixed by realizing
variants on `_feature_preset_tools()` (fc), with regression tests.

The core apply path should guarantee: a preset either applies fully or fails
loudly, and every bookkeeping field has a single writer. Draft the contract
as a "Preset application" section in docs/presets.md first, then implement.

### 1a. Contributions to an absent tool are silently dropped — **done**
- `Environment._apply_contribution` (environment.py:614) returns quietly when
  the env lacks `c.tool`; the preset is still recorded in `_applied_presets`
  and shows in `explain()` as applied.
- Symptom: a cross preset swapping `cc → emcc` on a cxx-only env leaves C
  compiled by the host compiler, reported as success.
- Direction: `ToolContribution.required: bool` — `cmd` swaps required by
  default, broadcast flag contributions optional; `_apply_contribution`
  raises when a required tool is missing.

### 1b. `env.target_arch` has two writers, last wins — **done**
- `Environment.apply` stamps `target_arch` from any preset carrying `arch`
  (environment.py:543); both `set_target_arch` and `apply_cross_preset`
  produce such presets. Order of calls changes the field, which can then
  disagree with the actual flags.
- Direction: cross presets stop populating `Preset.arch` (it's metadata per
  the cross-target contract); only the knob writes `env.target_arch`.

### 1c. Multi-toolchain fan-out double-applies presets — **done**
- `set_target_arch` / `apply_preset` / `apply_cross_preset` loop over
  `self.toolchains` (environment.py:687, 769, 802) but toolchains share
  cc/cxx/link, so with `add_toolchain` the same contribution lands twice
  (`-arch arm64 -arch arm64`, `-Werror -Werror`). Duplicate paired flags can
  be hard link errors.
- Direction: apply each resolved preset once (dedup by identity across the
  loop) or target only the toolchain owning each contributed tool.

### 1d. Unknown names and unrealizable knobs only warn or no-op — **done**
- `apply_preset("waarnings")` logs a warning and continues
  (environment.py:783). `set_target_arch` on Linux gcc/llvm applies an empty
  arch preset (unix.py:266 returns []); on wasm toolchains any arch silently
  coerces to `wasm32` (wasm_common.py:83).
- Direction: raise on unknown preset names (keep the deliberate
  resolver-returned-None no-op for inapplicable toolchains); raise on
  unrealizable arch, pointing at `linux_cross(triple=...)`; wasm rejects
  arch != wasm32 instead of coercing. Enforce in the *base*
  `apply_target_arch` (empty realization raises unless the toolchain
  explicitly declares the no-op) so custom toolchains inherit fail-fast
  by default — decided 2026-07-20 in response to the extension-author
  burden concern.
- Note: `cuda`/`cython` toolchains silently no-op the whole preset machinery;
  same rule should cover them if they ever front a cross build.

## Theme 2 — One concept, two mechanisms

### 2a. emscripten()/wasi_sdk() presets under-realize vs dedicated toolchains — **todo**
- `_cmd_contributions` (toolchain.py:998) repoints only cc/cxx from
  `env_vars`; the dedicated toolchains also repoint `link`, change output
  suffixes to .js/.wasm, and reject shared libs (emscripten.py:312,
  wasm_common.py:46, wasi.py:331). The `emscripten()` docstring blesses use
  with a native LLVM/GCC toolchain, but that path compiles with emcc and
  links with host clang — no diagnostic. On GCC the fail-fast guard passes
  (env_vars present) and g++ links emcc objects.
- Direction: either presets learn to fully retarget (needs 2b), or applying
  a wasm preset to a non-wasm toolchain raises, directing to the dedicated
  toolchain. Decide which; don't leave both half-working.

### 2b. `CrossPreset.env_vars` — env-var vocabulary for tool-cmd overrides — **todo**
- The field is documented as "CC, CXX commands" (presets.py:36) and consumed
  only for cc/cxx (toolchain.py:1002); no way to express link/ar/ranlib.
  GCC's fail-fast also matches the literal strings "CC"/"CXX" (gcc.py:386).
- Direction: add `tool_cmds: dict[str, str]` keyed by pcons tool names;
  keep `env_vars` as a deprecated alias (CC→cc, CXX→cxx, LD→link, AR→ar);
  `_cmd_contributions` iterates the merged map over declared tools.

### 2c. WASM toolchains bypass the contribution model — **todo**
- `WasiToolchain.setup` / `EmscriptenToolchain.setup` imperatively mutate
  `env.cc.cmd`, sysroot flags, etc. (wasi.py:328-349, emscripten.py:308-320)
  — invisible to `explain()`, violating "every realized flag is
  attributable". WASI's triple also lives in tool `default_vars`
  (wasi.py:152,176) — a third location for one concept.
- Direction: toolchain setup realizes SDK cmd/sysroot/triple as an ordinary
  applied `Preset` (reusing `_cmd_contributions`/`_sysroot_contributions`),
  so `explain()` shows `cc.cmd <- wasi-sdk`.

### 2d. `env.use(package)` vs `target.link(imported)` — **todo**
- Two paths for "consume this dependency": `use()` flattens onto tool vars
  with ad-hoc dedup and `libraries`/`library_dirs` vocabulary
  (environment.py:873-959); `target.link()` routes through
  `UsageRequirements`/`UniqueList` with transitive propagation and
  `link_libs`/`link_dirs` (target.py:531-578). Different dedup, different
  propagation, different names.
- Direction: reimplement `use()` over the same UsageRequirements merge path
  targets use; map vocabulary in one place.

### 2e. SwiftToolchain inheritance / IS_CLANG_DRIVER drift — **todo** (small)
- `SwiftToolchain(UnixToolchain)` inherits `IS_CLANG_DRIVER=False`; its
  `_target_contributions` comment (swift.py:416) claims to keep cc/cxx
  contributions for mixed builds, but those are never emitted (gated on
  IS_CLANG_DRIVER) and Swift declares no cc/cxx tools at all.
- Direction: delete the dead branch/comment; document IS_CLANG_DRIVER as
  "cc/cxx accept --target" so False means "no cc/cxx", not "not clang".

## Theme 3 — Configure cache conflates host and target

### 3a. `check_sizeof` host fallback + target-blind cache keys — **todo**
- `_get_sizeof_ctypes` returns the host Python's ctypes sizes
  (config.py:394-413); cache key is `sizeof:<type>` with no toolchain/target
  discriminator (config.py:372). Cross builds can bake host `SIZEOF_*` into
  config.h; switching presets in one build_dir reuses the other target's
  answers.
- Direction: namespace cache keys by a toolchain/target signature (triple +
  compiler id); refuse or clearly mark the ctypes fallback under a cross
  preset.

### 3b. `find_program` cache staleness — **todo**
- Cached under `program:<name>` (config.py:175), invalidated only by
  `path.exists()` (config.py:200) — not by PATH changes or toolchain switch.
  Similarly `PkgConfigFinder.is_available()` caches its path once per
  process (pkgconfig.py:59).
- Direction: include a PATH hash (or toolchain signature) in the key, or
  re-verify on mismatch.

## Theme 4 — Contracts that exist only as folklore

### 4a. Surprise default-Ninja generation — **todo**
- `_generate_pending` calls `project.generate()` (generator.py:217), which
  runs default Ninja generation unless a *build* generator already ran
  (`_is_build_generator` → `_mark_generated`, generator.py:133). A
  dot/mermaid/metadata-only script — reachable via the documented
  `PCONS_GENERATOR=metadata` CLI — silently also emits build.ninja +
  compile_commands + root symlink.
- Direction: default generation fires only when *no* generator ran at all.

### 4b. compile_commands root symlink: undocumented, no opt-out — **todo**
- `_create_root_symlink` (compile_commands.py:76) writes into the project
  root (outside build_dir) unconditionally; multi-preset builds fight over
  the single link, last writer wins.
- Direction: `root_symlink: bool = True` keyword threaded through
  `generate()`; document ownership under multi-config builds.

### 4c. clone() vs exclusive-group presets contradicts documented workflow — **todo**
- `clone()` copies `_applied_presets` (environment.py:433); the
  exclusive-group guard raises on a second same-group preset
  (environment.py:523-534) while its error text says "clone the environment
  to build multiple variants" — which only works if you clone *before* the
  first `set_variant`. `base.set_variant("release"); dbg = base.clone();
  dbg.set_variant("debug")` raises.
- Direction: re-applying within an exclusive group replaces the previous
  preset (the natural "switch variant" op) — imperative contributions of the
  replaced preset may need thought; or clone drops group members.

### 4d. Path relativization re-derived per generator — **todo**
- Three independent implementations of "paths relative to execution dir":
  ninja (ninja.py:800-931 area), makefile (makefile.py:716-773),
  compile_commands (compile_commands.py:203, 283) — plus a fourth label
  scheme in graph.py. Contract stated only as scattered prose.
- Direction: one `BaseGenerator` relativize helper + a written contract
  (architecture.md), all generators call it.

### 4e. Package-finder chain: precedence, found_by, negative caching — **todo**
- Precedence is list-insertion order, undocumented (project.py:1064);
  a finder that almost-matched falls through silently (the msys2 pkg-config
  incident was this shape); `required=False` misses skip the cache
  (project.py:1069-1074) so repeat probes re-shell-out; `add_package_finder`
  bypasses `is_available()` filtering (project.py:1108).
- Direction: document chain precedence + found_by as a contract; cache
  negative results (sentinel); route insertion through availability
  filtering; debug-log which finder won/was skipped.

### 4f. Stack-frame root_dir inference as quiet fallback — **todo** (small)
- project.py:130-135 infers root from the caller's `co_filename`, guarded by
  `exists()` — exactly what stale .pyc paths defeat (bit us on tower1, and
  in tests until the conftest guard). PCONS_SOURCE_DIR is already checked
  first.
- Direction: keep inference (it's the right UX for build scripts) but log at
  debug when the frame path doesn't exist and cwd fallback engages, so the
  failure is diagnosable.

---

## Sequencing (agreed 2026-07-20)

1. Theme 1 (apply-path contract) — draft contract section in presets.md
   first, then implement. Completes the cross-target work.
2. Theme 2a+2b (binary-retarget generalization) — fixes a real documented
   user path.
3. Theme 3 (configure-cache target-keying) — makes the cross story
   trustworthy end-to-end.
4. Theme 4 items as independent cleanups; 4c (clone/variant) first since it
   contradicts the docs' own advice.

All items preserve the public API surface; behavior changes are
silent-wrong → loud-error only.

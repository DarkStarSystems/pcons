# Plan: intents for mixed-toolchain environments

Status: **decided, ready to implement** (decisions 2026-10-17; see the end). Stacks on PR #221
(`fortran-compilers`) until that merges.

An environment can hold several toolchains: gcc with gfortran, clang-cl
with flang, MSVC with ifx, swift with clang, any of them with CUDA. Each
one has its own flags for the same request (`debug`, `lto`, `sanitize`),
and they share a link. This plan replaces today's rules for who puts which
flags where with one model, the one Meson uses.

## The problem

Mixed environments got working one failure at a time, most recently in
PR #221. Each rule has a reason, but together they're a set of patches rather than one idea:

| Question | Today's rule |
|---|---|
| Who may change a tool? | Setup presets: only the toolchain that brought it. Knobs: that toolchain, or another one if both write flags the same way (`LINKER_STYLE`) |
| How does a knob work? | Every toolchain realizes it; identical presets apply once (`_dedup_fanout`), and group members from one fan-out don't replace each other (`_fanout_applied`) |
| A toolchain added later? | Gets the last `set_variant()`, and nothing else |
| Per-target link flags? | The link tool's owner, plus same-style toolchains that compiled objects |
| Per-target compile flags? | The primary toolchain plus the one compiling that source |
| Which program drives the link? | The link language, but the switch to `g++` or `gfortran` happens by comparing `link.cmd` with `cc.cmd`/`cxx.cmd` |
| Link language? | The strongest language by the *primary* toolchain's priorities, so it changes with which toolchain came first |

The same question ("may toolchain A put flags on tool B?") gets two
answers, depending on whether a setup preset or a knob is asking.
`LINKER_STYLE` stands in for the real question, which is "can tool B read
A's flags?" And the fan-out pushes every toolchain's version of every
knob at the environment, then filters, deduplicates and reconciles.

It also leaves real bugs. These four are reproduced on this branch:

- **Cross presets and `set_target_arch` never reach `fc`.** llvm plus
  flang or gfortran, with `linux_cross("aarch64-linux-gnu")`: `cc` gets
  `--target=aarch64-linux-gnu`, and `fc` gets nothing, whichever order the
  calls come in. The same goes for `-arch arm64` on macOS. The Fortran
  objects are built for the host, with no error: the silent misbuild the
  cross-target contract exists to prevent. `UnixToolchain` realizes both
  on `cc`, `cxx` and `link` by name, so a Fortran toolchain never puts
  them on `fc`.
- **A feature applied before `add_toolchain()` misses the new toolchain.**
  `apply_preset("warnings")`, then `add_toolchain("gfortran")`: `fc` gets
  no `-Wall`. Only the variant is replayed.
- **Duplicate link flags.** gcc plus gfortran with `fast-math` puts
  `-ffast-math` on the link twice: the presets differ, so the dedup misses
  them. Harmless for this flag, but a paired flag can break a link.
- **A runtime from a library built elsewhere is missed.** A Fortran
  program in a gfortran environment, linking a C++ static library built in
  a C++ environment, fails on macOS with undefined `std::string` symbols.
  The link language is right (C++, from the library), but runtime
  injection looks only at the program's own objects and its own
  environment's toolchains, so nothing adds `-lc++`. The same goes the
  other way, for a C++ program linking a Fortran static library from a
  Fortran environment.

A fourth one was found on tower1 (Windows) while writing this plan:

- **clang (MSVC target) plus ifx can't link a Fortran program.** With
  `toolchain="llvm"` and `add_toolchain("ifx")`, a Fortran main plus a C
  helper fails with `LNK1561: entry point must be defined`. The link
  language comes out as C (by llvm's priorities, Fortran ranks 0), so
  clang drives link.exe. ifx's `/SUBSYSTEM:CONSOLE` never reaches it,
  because clang's `LINKER_STYLE` is "gnu". Adding
  `-Wl,/SUBSYSTEM:CONSOLE` by hand links it, but with `LNK4098`: clang
  links the static C runtime (`libcmt`) by default, while ifx's objects ask
  for the DLL one. clang-cl plus ifx links and runs. Rule 5 (the console
  need, realized by clang as `-Wl,/SUBSYSTEM:CONSOLE`) fixes the first
  half. The second half is that clang targeting MSVC should use the DLL
  runtime (`-fms-runtime-lib=dll`), as pcons' MSVC, clang-cl and flang
  builds do.

## How others do it

- **Meson.** Built-in options (`buildtype`, `b_sanitize`, `b_lto`) are
  project-wide intents. Each compiler turns them into its own compile
  flags. The linker belongs to the link language's compiler, and turns the
  same intents into link flags. The link language comes from a fixed
  priority order (C++ first; Fortran is last, below C). Other languages add their runtime libraries. No compiler
  writes another one's flags.
- **CMake.** Flags are per language (`CMAKE_<LANG>_FLAGS_<CONFIG>`). A
  link uses the link language's rule, chosen by `LINKER_PREFERENCE` (C++
  30, Fortran 20, C 10). Other languages' runtimes come from
  `CMAKE_<LANG>_IMPLICIT_LINK_LIBRARIES`, probed at configure time. There
  are few built-in features: LTO (`INTERPROCEDURAL_OPTIMIZATION`) is
  realized per language, and a sanitizer is per-language compile options
  plus separate link options.
- **SCons.** One flat environment that every tool writes into, a link
  that picks `$CXX` when there are C++ objects, and no variant or feature
  abstraction, so the user carries all of this.

Pcons already has Meson's ingredients: tool ownership (`_tool_owners`),
the toolchain that compiled each object (`build_info["toolchain"]`), and
a recorded request for the one knob that's replayed (`_variant_request`).

## The model

Five rules replace the table above.

1. **A knob or feature is an intent, recorded once.** `set_variant`,
   `set_target_arch`, `apply_preset`, `apply_cross_preset` and the tool
   settings (`env.cxx.set_standard`, `env.swiftc.set_cxx_interop`) record
   what was asked, in order. A toolchain added later realizes every
   recorded intent for its tools, the same as if it had been there from
   the start.
2. **Each tool is realized by its owner, and only its owner.** A
   toolchain's realization of an intent lands on the tools it brought.
   Contributions to other tools are dropped. Setup presets already work
   this way; now everything does.
3. **An intent applies fully or raises.** Every toolchain that takes part
   (below) realizes the intent, declares it a deliberate no-op, or the
   apply raises, naming the toolchain. No borrowing: gfortran gets its own
   `sanitize`, rather than gcc's link flag reaching a gfortran link by luck.
   This covers cross targets and arch too: flang retargets `fc` with
   `--target`, and gfortran, which needs a different binary, raises unless
   the preset's `tool_cmds` repoints `fc`, as GCC does for `cc`.
4. **A target is linked by the toolchain of its strongest language.** The
   link language comes from one fixed priority order. If the toolchain
   that compiled those objects has its own link driver (`g++`,
   `gfortran`, `flang`), that driver links, with that toolchain's
   realization of the link-side intents. If its objects link as they are
   (MSVC-ABI objects carry `/DEFAULTLIB`), the environment's link tool
   links them.
5. **Objects state what they need, and the linker writes the flags.** A
   compiling toolchain reports neutral link needs: runtime libraries,
   their directories, a console entry point. That covers every object the
   link pulls in, including those in static libraries from other
   environments. The linking toolchain turns them into its own flags.
   Nobody else writes link flags.

`LINKER_STYLE`, the fan-out dedup, the cross-toolchain group
reconciliation, the variant-only replay and the command comparison all go
away.

## Design

### 1. Intents

```python
@dataclass(frozen=True)
class Intent:
    kind: str                  # "variant", "arch", "feature", "target", "setting"
    name: str                  # "debug", "arm64", "lto", the cross preset's name, "cxx.standard"
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)  # set_variant(extra_flags=...)
    group: str | None = None   # "build_variant", "target_arch", "cross_target", "cxx.standard"
```

`Environment._intents: list[Intent]` replaces `_variant_request`. An
intent with a `group` replaces the group's earlier intent; a feature
(no group) adds to the list, so applying a feature twice still adds it
twice, as composition requires. `clone()` copies the list.

Recording is core and tool-agnostic: the core stores what was asked and
asks each toolchain to realize it. What a variant or a feature means stays
in the toolchains, as docs/presets.md requires.

`apply_imperative_preset` stays outside the model: it runs once against
the whole environment and isn't replayed. Its `explain()` line already
says so.

### 2. Realization and ownership

One core method does every knob:

```python
def _realize(self, intent: Intent, toolchains: Sequence[Toolchain]) -> None:
    for toolchain in toolchains:
        preset = toolchain.realize(intent)        # None: can't; empty: deliberate no-op
        ...completeness check (section 3)...
        own = tuple(c for c in preset.contributions if self._owner(c.tool) is toolchain)
        self.apply(replace(preset, contributions=own, realized_by=toolchain.name))
```

- `Toolchain.realize(intent)` dispatches to the hooks that exist today
  (`make_variant_preset`, `make_feature_preset`, `make_target_preset`,
  `_arch_contributions`, `make_cxx_standard_preset`). Those keep their
  signatures, so toolchains change only where a realization is missing.
- `set_variant` and the rest record the intent and call `_realize` over
  all toolchains. `add_toolchain` calls `_realize` for each recorded intent,
  in order, over just the new toolchain.
- Since each tool takes one toolchain's contributions, nothing is applied
  twice and no dedup is needed. Group replacement is per toolchain: a
  toolchain's new group preset un-applies that toolchain's previous one in
  the group (a variant replaces its `default` CRT preset). That's well
  defined once presets carry `realized_by`, and the `_fanout_applied`
  exception goes away.
- `apply()` keeps the existing contract (validation, `cmd` refused in
  groups, and so on). The "none of its contributions landed" check moves to
  the intent level: an intent that changed no tool in any toolchain is the
  error, not one toolchain's empty share.
- A tool no toolchain brought (the script's own `env.add_tool("lint")`)
  takes the primary toolchain's contributions, so it behaves like a single-
  toolchain environment. (Decision D3.)
- `Preset` gains `realized_by: str | None`, and `explain()` shows it:
  `-O0 <- debug (variant, gfortran)`.

`apply_from` and `own_tools_only` go away; `swift.py` and `_cxx_set_standard`
call the same intent path.

### 3. Which toolchains take part, and completeness

A toolchain takes part in an intent if it owns at least one of its
`_feature_preset_tools()`. That's gfortran for `fc`, CUDA for `cuda` once
it declares that (today it inherits `("cc", "cxx")`, owns neither, and so
sits out, as it effectively does now). Qt and Cython own none of theirs
and sit out. The same rule covers every intent kind, so the variant, arch,
cross-target and feature realizations all move onto
`_feature_preset_tools()` plus `link`, instead of naming `cc`, `cxx` and
`link`.

A toolchain that takes part answers in one of three ways:

| Answer | How it's declared | Effect |
|---|---|---|
| Realized | contributions, as today | applied to its tools |
| Deliberate no-op | an empty entry (`"warnings": {}` on swift) or an empty preset | nothing, shown in `explain()` |
| Unavailable | no entry, or `make_feature_preset` returns None (openmp without libomp) | **error**, naming the toolchain and suggesting `has_preset` |

Today's `"openmp": {}` entries mean "realized dynamically" and must keep
meaning that, so the deliberate no-op needs its own marker. A module-level
`NO_OP: dict = {}` sentinel compared by identity is enough.

`has_preset(name)` becomes "every toolchain that takes part realizes it or
declares it a no-op", so it still answers exactly "would `apply_preset`
raise?". Registry (contributed) presets keep their rule: a resolver
returning None is the sanctioned no-op, for that toolchain.

This is the strict version of rule 3 (D1). The escape hatch is explicit
and per call: `env.apply_preset("sanitize", allow_partial=True)` applies
the feature wherever it's available and skips the toolchains that can't,
and `explain()` lists each one it skipped. It's for features only:
variants, arch and cross targets have no partial form, because a partly
retargeted build is a misbuild. The other escapes stay as they are:
`has_preset()` to guard, a flag appended to one tool by hand, and the
imperative presets.

### 4. Link language and link driver

**Fixed priority.** One order for every environment, highest first:

`swift > cuda > objcxx > cxx > fortran > objc > c`

Today the order comes from the primary toolchain, and Fortran only ranks
when a Fortran toolchain is primary. So gcc plus gfortran links a Fortran
and C program with gcc plus `-lgfortran`, and gfortran plus gcc links the
same objects with gfortran. With a fixed order, the program decides, not
which toolchain came first. C++ over Fortran over C is CMake's order;
Meson agrees on C++ first but puts Fortran last.
Swift stays above C++, since `swiftc` handles the Swift runtime. (Decision
D2.)

**The driver.** Let *L* be the link language and *T* the toolchain that
compiled the *L* objects (from `build_info["toolchain"]`; the primary for a
target with no objects of its own).

```python
def link_driver(self, language: str, env: Environment) -> str | None:
    """The command that links a program led by *language* objects this
    toolchain compiled, or None if the environment's link tool links them
    as they are."""
```

- If *T* is the link tool's owner, the link tool links. Its command may
  still change: `UnixToolchain.link_driver("cxx")` returns `$cxx.cmd`
  when the link tool still runs the C driver. That comparison is the one
  that's in `CompileLinkContext` today. It moves into the toolchain, which
  is where knowledge of drivers belongs.
- If *T* isn't the owner and `T.link_driver(L)` is a command, *T* links:
  that command, with *T*'s realization of the link-side intents (below).
  gfortran and flang return their compiler, and so do gcc and clang for
  C++ (`g++`, `clang++`).
- If it returns None, the link tool links. MSVC, clang-cl and Windows ifx
  return None, because their objects name their runtimes, and so does flang
  when it targets the MSVC ABI.

That gives:

| Environment | Program | Linked by |
|---|---|---|
| gcc + gfortran | Fortran main | gfortran, with gfortran's link flags (today: gcc's flags, gfortran's command) |
| gcc + gfortran | C++ main | g++, plus `-lgfortran` (unchanged) |
| gfortran + gcc | C++ main | **g++, plus `-lgfortran`** (today: gfortran plus `-lstdc++`; D2) |
| llvm + flang (Linux, macOS) | Fortran main | flang, with flang's link flags (today: clang's flags) |
| clang-cl + flang | Fortran main | link.exe (unchanged) |
| flang + clang-cl | C++ main | flang (unchanged) |
| MSVC + ifx | Fortran main | link.exe, plus ifx's console need (unchanged) |
| clang (MSVC ABI) + ifx | Fortran main | **clang, plus ifx's console need** (today: clang without it, `LNK1561`) |
| swift + llvm | Swift main | swiftc (unchanged) |

**Which object holds `main`.** Pcons doesn't know, and mostly doesn't
need to. The driver is chosen by language, for its runtime, and `main` is
an ordinary symbol to whichever driver links. gfortran and flang (checked:
gfortran 16, flang 23) emit `main` into the object of the file holding the
`PROGRAM`, so `g++` links a Fortran main program as readily as `gfortran`
does, given `-lgfortran`. ifx is the exception: its `main` lives in its
runtime. On Linux the ifx driver adds `for_main.o`, which is why
`-nofor-main` exists for a C main. On Windows `main` is in `libifcore`,
found through `/DEFAULTLIB`, which is why the link needs a subsystem.

Finding the `PROGRAM` would mean reading sources at configure time, so
every edit would reconfigure. The scanner reads them, but only at build
time. So for ifx on Linux:

- When ifx drives (Fortran is the strongest language), it adds
  `for_main.o` itself, as now.
- When another driver links ifx objects (a C++ helper in the program, so
  C++ wins), ifx's needs don't include `for_main.o`, and a Fortran main
  program fails with an undefined `main`. The script says
  `prog.link_language = "fortran"` (below), and ifx drives.
- When ifx drives a program with a C main, it needs `-nofor-main`. That
  follow-up from #221 becomes the same choice in reverse: with
  `link_language` set to C or C++, the C/C++ driver links, and with no
  `for_main.o`.

On Windows, ifx's `console` need (§5) covers both cases, since the runtime
`main` is pulled in only when no object defines one.

**Choosing the link language (D2's escape hatch).**
`target.link_language = "fortran"` overrides the fixed order for one
target, like CMake's `LINKER_LANGUAGE`. It must be a language some
toolchain in the linking environment compiles, else it's an error. It
picks *T* by the same rule as above, so the driver and its link flags
follow. An environment-wide default isn't needed: the fixed order is the
default.

**As built (Phase 3).** Two refinements came out of implementing this:

- A driver takes over a link only if it reads the link tool's command
  line: its own link tool has the same `progcmd` and `sharedcmd`
  templates. Every GNU-style driver shares gcc's (`gnu_link_vars`), so
  gfortran, flang and Linux ifx can take over gcc's or clang's link;
  swiftc and link.exe can't, so clang keeps a link led by Swift objects,
  as before.
- The languages are tried strongest first, and the first with a driver
  wins, the compiling toolchain's before the owner's. MSVC-ABI compilers
  return none (their objects name their runtimes), so clang targeting MSVC
  links with `clang`, not `clang++`. A link tool the script set to
  something other than a compiler driver links as it is.

**Link-side realization per driver.** `env.link.flags` stays what it is
today: the link tool owner's realization plus the script's own flags, so
`env.link.explain()` and `"-flto" in env.link.flags` keep working. When a
target is linked by another toolchain *T*, its link flags are
`env.link.flags` with the owner's intent contributions removed (by count,
as group replacement already does) and *T*'s added. *T*'s realization is
computed from the recorded intents when the target is linked
(`Environment.flags_realized_by`), so no state is kept for it and
nothing is applied to `env.link`. `pcons explain <target>` shows the flags the target actually
gets. (Decision D4.)

### 5. Link needs

```python
@dataclass(frozen=True)
class LinkNeeds:
    libs: tuple[str, ...] = ()            # runtime libraries, by name
    libdirs: tuple[Path, ...] = ()
    frameworks: tuple[str, ...] = ()      # macOS
    framework_dirs: tuple[Path, ...] = ()
    rpaths: tuple[Path, ...] = ()         # where the runtime is found when the program runs
    objects: tuple[Path, ...] = ()        # files linked as they are (Swift's swiftrt.o)
    console: bool = False                 # the entry point is a console main
```

These are the link-side fields of `UsageRequirements`, minus raw
`link_flags`, plus what only a runtime needs. Raw flags are left out on
purpose: a need is something every linker can be told in its own way,
which is the point. Each field has a known user today, or will have
within this plan:

- `libs`, `libdirs`: the Fortran, C++ and Swift runtimes
  (`get_runtime_libs`, `get_runtime_libdirs`).
- `frameworks`, `framework_dirs`: a Swift or Objective-C toolchain whose
  objects need a framework, when another driver links on macOS.
- `rpaths`: Swift on Linux, when `clang++` links Swift objects. Today it
  gets `-L` but no rpath, so the program finds `libswiftCore.so` only if
  it's installed system-wide. Check this in Phase 4.
- `objects`: Swift's `swiftrt.o` on Linux, which `swiftc` adds when it
  links and `clang++` doesn't (to confirm in Phase 4, with the rpath). ifx's `for_main.o` would be one too, but
  see §4.
- `console`: ifx on Windows.
- Found in Phase 2, from the audit (`mixed-toolchain-audit.md`): objects
  built with a feature can need that feature's runtime whoever links them.
  ifx's ASan objects on Windows need the ASan libraries (`/INFERASANLIBS`
  on its own link), gfortran's coverage objects need `-lgcov` under
  clang++, and gfortran's OpenMP objects `GOMP_*` where flang's and ifx's
  need `__kmpc_*`. These are `libs` needs, stated by the compiling
  toolchain for the features it realized.

A need with no realization on the linking toolchain is an error, never a
dropped flag: `frameworks` on link.exe, for example.

`Toolchain.link_needs(target, language, linker)` replaces
`get_runtime_libs` and `get_runtime_libdirs`. It also replaces the part of
`get_link_flags_for_target` that a non-linking toolchain contributes
today (ifx's `/SUBSYSTEM:CONSOLE`). The linking toolchain realizes the
needs: `-l`/`-L` or `.lib`/`/LIBPATH:` through its link context, as now,
and `console` as `/SUBSYSTEM:CONSOLE` (link.exe), `-Wl,/SUBSYSTEM:CONSOLE`
(clang or flang for the MSVC ABI) or nothing (GNU ld, where it's the
default). A subsystem the script set in `env.link.flags` still wins.

`get_link_flags_for_target` becomes the linker's own business only: the
install name, SONAME, import library, `exported_symbols` and the console
need. Then `LINKER_STYLE` has no uses left and goes.

### 6. Per-target compile flags

`get_compile_flags_for_target_type` (`-fPIC`, ifx's `-fPIE`) comes from the
compiling toolchain only, by rule 2. Today the primary's flags are added
too. That's harmless for gfortran beside gcc (both `-fPIC`), but it hands
nvcc gcc's flags raw. `CudaToolchain` then needs its own (`-Xcompiler
-fPIC`), which the Phase 2 audit checks.

### 7. Upstream dependencies: packages and other targets

Usage requirements are data, not intents. They come from
`target.public`, `env.use()`, `ImportedTarget`, and the pkg-config and
Conan finders. The intent machinery never writes or reads them, and they
keep flowing as they do today. The plan has to keep four things working,
and fixes three things that don't work today.

**Kept working:**

- **`env.use(pkg)`** puts a package's includes and defines on `cc`,
  `cxx` and `fc`, its compile flags on `cc` and `cxx`, and its link fields
  on `env.link`. To the intent machinery, that's the script's own layer,
  like a flag the script appended. So when another toolchain's driver
  links a target (§4), only the owner's *intent* contributions are swapped
  out: package and script flags ride along to whichever driver links.
  Phase 0 pins this, with a package used in a mixed environment and a
  target linked by the other driver.
- **A dependency's `public.link_flags`, `link_libs` and `link_dirs`** reach
  the link as today, whoever drives it. One fix: the libraries are written
  by the *linking* toolchain's context (`-lz` or `z.lib`). Today that's
  `_context_class_for(env)`, which asks the primary toolchain. That's the
  same answer in every environment that links today, but not once §4 can
  pick another driver. Phase 3.
- **Conan's `build_type`** comes from `env.variant`. That stays the variant
  intent's name, with one writer, as now. `compiler.cppstd` is read from
  `env.cxx.flags`, which still carries `-std=` from the `cxx` owner. Reading
  the recorded `cxx.standard` intent instead is more direct, and it's a
  small change worth making in Phase 1.
- **The Conan profile's compiler** comes from the toolchain the script
  passes to `sync_profile()`. That's usually `env.toolchain`, the primary.
  In a Fortran-primary environment, that gives a profile with no
  `CC`/`CXX` and a guessed compiler. `sync_profile(env=env)` should take the
  toolchain that owns `cxx`, else `cc`, from the environment. This is
  small, and could go in Phase 1.

**Link languages through dependencies.** Today's link language counts
every linked target's languages, shared libraries included. Runtime
injection counts only the program's own objects. The model makes the two
agree:

- The link needs (rule 5) come from every object the link pulls in: the
  target's own, plus those of the static and object libraries it links,
  transitively, from whichever environment compiled them. Each object's
  `build_info["toolchain"]` says who to ask. That fixes the fourth bug
  above.
- A shared library already carries its runtime dependencies, so it adds
  neither a link language nor needs. CMake does the same: link languages
  propagate through static libraries only. This changes one case: a C
  program linking only a C++ *shared* library links with `cc`, not `cxx`.
  (Decision D6.)
- The driver (§4) is always a toolchain of the *linking* environment.
  Objects from another environment's toolchain contribute needs, never the
  driver: that toolchain's flags were realized for another environment's
  intents. A gfortran environment linking a C++ static library links with
  gfortran, plus the C++ runtime that gcc's objects say they need.
- An imported library (a package) has no objects to ask. It can declare
  its languages (`ImportedTarget(languages={"cxx"})`, like CMake's
  `IMPORTED_LINK_INTERFACE_LANGUAGES`), which also scopes its compile
  flags (below); the Conan finder could set `cxx`
  for a package with a `compiler.libcxx`. A declared language's runtime
  comes from the linking environment's toolchain for that language, else
  from the linking toolchain's knowledge of it (gfortran knows `-lstdc++`,
  as `FortranToolchainBase.get_runtime_libs` does today). If neither
  knows, it's an error that says to add that toolchain or list the
  library. Undeclared, nothing is injected, as now.

**Package compile flags on Fortran sources.** `env.use()` keeps a
package's compile flags off `fc`, but a package linked as a target
doesn't. `public.compile_flags` reaches every source of every dependent,
whatever its language. pkg-config's and Conan's flags are C-family flags
(the `-I` and `-D` parts are split out, the rest isn't), so `-pthread` or
`-std=c++17` from a package can reach gfortran or ifx.

A package's compile flags apply to the compiles of its declared languages
(the `languages` above). Undeclared, that's the C family (C, C++,
Objective-C), as `env.use()` does today. A target's own
`public.compile_flags` are the script's to scope and aren't changed. (D5.)

This doesn't block packages for other languages. A Fortran library's
`.pc` file (netCDF-Fortran, HDF5's Fortran API, json-fortran) mostly
carries `-I` for its `.mod` files and `-l` for its libraries. Those are
split out of the flags and reach every compiler, `fc` included, as they
do now. Only its other flags (an `-fopenmp`, say) would need it declared
as Fortran. A CUDA package is the same, with `languages={"cxx", "cuda"}`.
One declaration covers both the flags and the runtime need. The
pkg-config finder can't tell a package's language from its `.pc` file,
so it takes a `languages=` argument where the script knows better.

**C runtime agreement on Windows.** Conan's profile for clang on Windows
asks for the dynamic runtime (`compiler.runtime=dynamic`). Per tower1,
pcons' clang links the static one by default, so Conan packages and
clang's own objects already disagree, before any Fortran is involved. The
clang DLL-runtime follow-up from #221 fixes both. It's a separate small PR,
ahead of this work.

## Toolchain audit (Phase 2)

Strict completeness needs every toolchain to answer every built-in feature
it might take part in. Where one is missing today:

| Toolchain | Missing | Proposed |
|---|---|---|
| gfortran | sanitize, profile, lto, hardened, pthread | GCC's own flags: it *is* GCC |
| flang | sanitize, profile, lto, hardened, pthread, coverage | check each against flang 21-23; `-flto` and `-pthread` likely; the rest unavailable |
| ifx (Linux) | sanitize, profile, lto, hardened, pthread, coverage | check against ifx 2025: `-ipo` for lto, `-fsanitize=address` is documented; the rest unavailable |
| ifx (Windows) | sanitize, profile, lto, hardened, pthread, coverage | `/Qipo` for lto; MSVC's `/guard:cf` etc. for hardened go on link.exe, which ifx doesn't own; the rest unavailable |
| cuda | everything (no table) | declare `_feature_preset_tools() = ("cuda",)`; `-Xcompiler` forms where nvcc passes through; `-G`/`-lineinfo` already in variants |
| swift | sanitize, lto, and the rest | `-sanitize=address`, `-lto=llvm-full` exist; check |
| clang-cl | coverage, pthread | unavailable (as MSVC) |
| wasm (emscripten, wasi) | openmp | unavailable (as now) |
| gfortran | cross target (triple only), arch | raise, as GCC does: needs `tool_cmds["fc"]` |
| flang | cross target, arch | `--target=` and `--sysroot=` on `fc`, like clang; arch on macOS only if flang takes `-arch` (check) |
| ifx | cross target, arch | raise: ifx has no cross targets |

"Unavailable" for a toolchain that takes part means the feature raises in
that mixed environment. A script that wants it anyway keeps the other
toolchain's sources in their own environment. The audit also writes the
missing link-side realizations: the `UnixToolchain` debug variant puts no
`-g` on the link today, which is the MSVC-ABI `/DEBUG` follow-up from
#221. In this model it's an ordinary gap in a toolchain's table.

## What goes away

- `BaseToolchain.LINKER_STYLE` and both overrides.
- `Environment.apply_from`, `own_tools_only`, `_dedup_fanout`,
  `_fanout_seen`, `_fanout_applied`, `_variant_request`, and the
  variant-only replay in `add_toolchain`.
- The command comparison and the same-style `compilers` loop in
  `CompileLinkContext.from_effective_requirements`.
- `get_runtime_libs` and `get_runtime_libdirs` (folded into `link_needs`).
- The primary-relative `language_priority` property, and the three places
  in `compile_link.py` that read it.

## User-visible changes

- Cross presets and `set_target_arch` retarget `fc`, or raise when the
  Fortran compiler can't be retargeted that way. **Fix.**
- Features and cross presets applied before `add_toolchain()` reach the
  added toolchain. **Fix.**
- No duplicate link flags in mixed environments. **Fix.**
- `sanitize`, `lto` and the rest reach gfortran's sources too. **Fix**,
  through the audit.
- A feature a toolchain in the environment can't realize raises, where
  today it silently skipped that toolchain's sources. **Behavior change**
  (D1). `has_preset` follows.
- A Fortran-primary environment linking C++ objects uses the C++ driver
  plus the Fortran runtime (D2). **Behavior change**; examples 27 and 28
  cover both directions.
- A program linking a static library from another environment gets that
  library's runtime (the Fortran-and-C++ case above). **Fix.**
- A package's compile flags stay off Fortran sources when the package is
  linked as a target, as they already do with `env.use()` (D5).
  **Behavior change.**
- A C program linking only a C++ shared library links with `cc` (D6).
  **Behavior change**, and probably invisible.
- `sync_profile(env=env)` picks the environment's C/C++ toolchain.
- `explain()` shows which toolchain realized each flag.

None of this changes single-toolchain environments, apart from the
`explain()` column. That's the guard for the pcons-tests sweep: almost
every project there has a single toolchain.

## Phases

Each phase is a commit with tests. Phases 1 and 2 are independently
useful and could ship alone.

**Phase 0: pin today's behavior.** A table-driven test over mixed
environments (gcc+gfortran, gfortran+gcc, llvm+flang, clang-cl+flang,
flang+clang-cl, msvc+ifx, clang+ifx, gcc+cuda, swift+llvm) and knobs
(each variant, each feature, mac arch, a cross preset, before and after
`add_toolchain`). It also covers upstream inputs: a package applied with
`env.use()`, an `ImportedTarget` with link flags and libraries, a static
library from another environment, and the Conan profile text for the
mixed environments. It records every tool's flags and each sample target's
link command into a checked-in expectations file. It builds toolchains
from tool objects, as `test_fortran_compilers.py` does, so it runs on
every host without the compilers. Each later phase changes that file, and
the diff is the review of what changed.

**Phase 1: record intents, and replay them all.** `Intent`, `_intents`,
`add_toolchain` replays every intent. The fan-out stays for now. Conan's
`cppstd` reads the recorded intent, and `sync_profile(env=env)` picks the
C/C++ toolchain (§7). Fixes the replay bug above. Small.

**Phase 2: ownership and completeness.** Audit and fill the toolchain
tables (above), then switch `_realize` to owner-only with the
completeness check and `allow_partial=True`. Remove `apply_from`, the dedup and the reconciliation.
Fixes the duplicates and the Fortran cross-target bug. Medium; most of it is toolchain tables and their
tests.

**Phase 3: link language and driver.** Fixed priority,
`target.link_language`, `link_driver`, per-driver link realization, the
link context class from the linking toolchain. Remove the command comparison. Needs tower1
for the clang+ifx and flang cases. Medium to large.

**Phase 4: link needs.** `LinkNeeds`, `link_needs`, the console need,
collected from every object the link pulls in (§7); declared languages on
imported libraries, scoping both their runtime needs and their compile
flags; package compile flags scoped to C-family compiles
(D5); compile-side target-type flags from the compiling toolchain only.
Remove `LINKER_STYLE`. Medium.

**Phase 5: docs.** A "Mixed toolchains: the contract" section in
docs/presets.md (the rules above, as decided), its status table,
ARCHITECTURE.md, the user guide's mixed-language section, CHANGELOG.

Testing for every phase: the full suite, the Fortran examples with
gfortran and flang here, tower1 on Windows (MinGW gfortran, conda flang,
clang64 flang, ifx, with clang-cl and MSVC), and CI's flang and ifx
jobs. The pcons-tests sweep runs after Phases 2, 3 and 4. Several of its
projects use Conan or pkg-config, so it's the real-world check for §7,
alongside example 07 (Conan) and the pkg-config examples.

## Decisions (2026-10-17)

- **D1. Completeness: strict.** Escape hatch:
  `apply_preset(name, allow_partial=True)`, for features only (§3).
- **D2. Fixed language priority.** Escape hatch: `target.link_language`
  (§4).
- **D3. Unowned tools take the primary's realization.**
- **D4. Per-driver link realization**, in Phase 3, with care.
- **D5. Package compile flags go to the package's declared languages**,
  the C family when undeclared (§7). Not for a pcons target's own
  `public.compile_flags`.
- **D6. Link language through static libraries only.**
- **D7. Two PRs:** Phases 0-2, then 3-5. Both stack on #221 until it
  merges.

## Out of scope

- A cross-environment ABI or architecture check (declined 2026-10-16).
- The ifx `.mod` rewrite cascade, `-nofor-main`, and the runtime libdir
  lookup using `$FC` and `PATH`: separate follow-ups from #221.
- Per-tool requirements (`env.use()` and includes per compiler): already
  per tool since #221.

# Gary's notes/questions
- How do we know which language contains `main`? (link driver)
- Shouldn't `link_needs` also include frameworks, framework dirs, and possibly other things?
- D1: let's go strict for now. Do users need any escape hatch?
- D2: agreed, fixed. But could users need an escape hatch? (Explicit link tool selection)
- D3: Yes
- D4: Yes, that's important. Will take careful work.
- D5: It's possible in principle for a Fortran lib to have a pkg-config with Fortran includes & libs (or CUDA or some other language). Would this prohibit such packages from working? I don't want to overcomplicate things for a hypothetical, but consider it.
- D6: Agreed.
- D7: two is OK.

## Responses

- **Which language holds `main`?** Pcons doesn't know, and mostly doesn't
  need to: gfortran and flang put `main` in the `PROGRAM`'s object, so any
  driver links it. ifx is the exception. Its `main` is in its runtime, so a
  program whose strongest language isn't Fortran needs
  `link_language = "fortran"`. That and `-nofor-main` turn out to be one
  choice. Folded into §4.
- **More in `link_needs`?** Yes: frameworks and framework dirs, rpaths,
  and objects linked as they are (Swift's `swiftrt.o`). The fields are
  the link half of `UsageRequirements` without raw flags. Folded into §5,
  which says who needs each one, including a likely Swift-on-Linux rpath
  gap to check.
- **D1 escape hatch:** `allow_partial=True`, per call, features only. §3.
- **D2 escape hatch:** `target.link_language`. §4.
- **D5 and non-C packages:** they keep working. A Fortran package's `-I`
  and `-l` are split out of its flags and reach `fc` as now; only its
  other flags need `languages={"fortran"}`, which is the same declaration
  §7 uses for runtime needs. §7.

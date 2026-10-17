# Audit: what each compiler realizes, for the mixed-toolchain plan

Evidence for Phase 2 of `plan-mixed-toolchain-intents.md`, gathered
2026-10-17 by running each compiler: macOS arm64 (gfortran 16.2, flang
23.1.3, Swift 6.4), Linux in Docker (flang 21.1.8, gfortran 13.3, ifx
2025.3.2, Swift 6.2.4, nvcc 13.4), and Windows 11 on tower1 (ifx 2026.1.1
with link.exe, conda flang 23.1.3 for the MSVC ABI, MinGW gfortran 16.2).

Verdicts: **realize** (flags given), **no-op** (deliberately nothing),
**unavailable** (applying the feature raises).

## gfortran (all platforms)

It's GCC, so it realizes what GCC does with the same flags, except
`-Wpedantic` (noisy for legal Fortran) and `-D_FORTIFY_SOURCE` (does
nothing for Fortran). Verified working: sanitize (ASan catches an
overflow; UBSan reports an integer overflow), lto, hardened (PIE,
BIND_NOW, RELRO, stack protector), pthread, coverage, fast-math, openmp;
profile on Linux.

Platform limits it shares with GCC's C and C++, so they're not
Fortran-specific: `-pg` fails on macOS (no `gcrt1.o`), the ELF `-z` link
flags fail on macOS, and MinGW has no sanitizer runtime.

`-arch x86_64` is ignored with a warning and builds arm64, so arch
must raise. `--target=` is rejected: it's binary-retargeted.

## flang (21 and 23, every platform)

| feature | compile | link | verdict |
|---|---|---|---|
| warnings | | | no-op: `-Wall` is ignored with a driver warning, `-Wextra` unsupported, `-Wpedantic` an error. `-pedantic` gives portability warnings, which gfortran's preset leaves out too |
| werror | `-Werror` | | realize |
| sanitize | | | unavailable: `-fsanitize` unknown |
| profile | | | unavailable: `-pg` unknown. For the MSVC ABI, link-only `-Wl,/PROFILE`, as MSVC's preset |
| lto | `-flto` | `-flto`; for the MSVC ABI also `-fuse-ld=lld` | realize |
| hardened | | | unavailable: no stack protector option |
| pthread | | `-pthread` | realize on the link only (the compile warns "unused"); unavailable for the MSVC ABI, as MSVC |
| coverage | | | unavailable |
| fast-math | `-ffast-math` | `-ffast-math` | realize |
| openmp | `-fopenmp` | `-fopenmp` | realize |

`-arch` is rejected; `--target=`, `--sysroot=` and `-isysroot` work. The
runtime is single-arch, so only host-arch links succeed.

The MSVC-ABI debug link needs `-g` to write a PDB. On macOS and Linux `-g`
on the link line changes nothing.

## ifx on Linux

| feature | compile | link | verdict |
|---|---|---|---|
| warnings | `-warn all` | | realize |
| werror | `-warn errors` | | realize |
| sanitize | `-fsanitize=address -fno-omit-frame-pointer` | `-fsanitize=address` | realize; `address,undefined` silently drops every sanitizer |
| profile | `-pg -g` | `-pg` | realize |
| lto | `-flto` | `-flto` | realize (only when ifx drives the link) |
| hardened | `-fstack-protector-strong -fPIE` | `-pie -Wl,-z,relro,-z,now` | realize |
| pthread | `-pthread` | `-pthread` | realize |
| coverage | | | unavailable: `--coverage` is accepted and does nothing |
| fast-math | `-ffast-math` | `-ffast-math` | realize |
| openmp | `-qopenmp` | `-qopenmp` | realize |

No cross targets or arch.

## ifx on Windows (link.exe)

| feature | compile | link | verdict |
|---|---|---|---|
| warnings | `/warn:all` | | realize |
| werror | `/warn:errors` | | realize |
| sanitize | `/fsanitize=address` | `/INFERASANLIBS` | realize; without the link flag, `__asan_init` is unresolved |
| profile | | `/PROFILE` | realize (MSVC's meaning) |
| lto | | | unavailable: the objects are bitcode only ifx's own lld-link can link |
| hardened | `/GS /guard:cf` | `/guard:cf /DYNAMICBASE /NXCOMPAT` | realize |
| pthread | | | unavailable, as MSVC |
| coverage | | | unavailable: `/Qcov-gen` is gone |
| fast-math | `/fp:fast` | | realize |
| openmp | `/Qopenmp` | | realize |

x64 only.

## Swift

| feature | compile | link | verdict |
|---|---|---|---|
| warnings | | | no-op: Swift 6 has warning groups only |
| werror | `-warnings-as-errors` | | realize (bare `-Werror` takes a group argument) |
| sanitize | `-sanitize=address` | `-sanitize=address` | realize; `undefined` is unsupported on Linux |
| profile | | | unavailable |
| lto | `-lto=llvm-full` | `-lto=llvm-full` | realize |
| hardened | | Linux: `-Xlinker -z -Xlinker relro -Xlinker -z -Xlinker now` | realize (stack protector and PIE are the defaults) |
| pthread | | | no-op: Glibc autolinks it |
| coverage | `-profile-generate -profile-coverage-mapping` | `-profile-generate` | realize (LLVM's format, not gcov's) |
| fast-math | | | unavailable |
| openmp | | | unavailable |

When clang++ links Swift objects on Linux it also needs `swiftrt.o` (or
runtime metadata silently isn't registered), `-Wl,-rpath,/usr/lib/swift/linux`,
and the libraries Swift's autolink section lists (`swift-autolink-extract`).
On macOS the objects autolink; below deployment target 26 they also need
`-Wl,-rpath,/usr/lib/swift`. Phase 4's link needs.

## CUDA (nvcc, GCC or Clang host)

nvcc rejects bare host flags (`-fPIC`, `-Wall`, `-pthread`, ...). Each goes
through as one token, `-Xcompiler=<flag>`; `-Xcompiler` splits on commas.

| feature | compile | verdict |
|---|---|---|
| warnings | `-Xcompiler=-Wall -Xcompiler=-Wextra` | realize (`-Wpedantic` floods nvcc's generated code) |
| werror | `-Werror=all-warnings` | realize |
| sanitize | `-Xcompiler=-fsanitize=address -Xcompiler=-fsanitize=undefined -Xcompiler=-fno-omit-frame-pointer` | realize, host code only |
| profile | `-pg` | realize |
| lto | | unavailable: device LTO needs `-dlink`; host `-flto` breaks on `fatbinData` |
| hardened | `-Xcompiler=-fstack-protector-strong -D_FORTIFY_SOURCE=2`, `-Xcompiler=-fPIE` for programs | realize |
| pthread | `-Xcompiler=-pthread` | realize |
| coverage | `-Xcompiler=--coverage` | realize, host code only |
| fast-math | `-use_fast_math -Xcompiler=-ffast-math` | realize |
| openmp | `-Xcompiler=-fopenmp` | realize, host code only |

The link side is the host toolchain's. Position-independent code is
`-Xcompiler=-fPIC`; gcc's bare `-fPIC` reaching nvcc today is a bug.

### CUDA with cl.exe as the host compiler (Windows)

Verified on tower1 with nvcc 12.5 and cl 19.44 (VS 2022), a Quadro RTX
6000 running the kernel:

| feature | compile | verdict |
|---|---|---|
| warnings | `-Xcompiler=/W4` | realize: cl reports C4101 |
| werror | `-Werror=all-warnings` | realize: nvcc passes `-WX` to cl itself, so `/WX` isn't needed |
| sanitize | `-Xcompiler=/fsanitize=address` | realize: catches a host heap overflow; nvcc's host object carries `/InferAsanLibs`, so the link needs nothing |
| profile | | no-op: MSVC's `profile` is link-only `/PROFILE`, the host linker's |
| hardened | `-Xcompiler=/GS -Xcompiler=/guard:cf` | realize: GUARD_CF set (the link flags are MSVC's) |
| fast-math | `-use_fast_math -Xcompiler=/fp:fast` | realize |
| openmp | `-Xcompiler=/openmp` | realize: 32 threads |
| lto, pthread, coverage | | unavailable, as with MSVC |

A DLL from a `.cu` needs no PIC flag. `-Xcompiler=/W4,/WX` splits on the
comma, so a flag whose value has a comma needs its own `-Xcompiler=`.

## Mixed links (Fortran objects, C++ driver), for Phase 4

- Coverage: gfortran objects linked by clang++ need `-lgcov`.
- OpenMP: gfortran objects need `GOMP_*`, flang's and ifx's `__kmpc_*`. The
  C++ driver's `-fopenmp` supplies its own runtime only.
- LTO doesn't cross compilers: flang 23 bitcode vs Apple clang, gfortran's
  GIMPLE vs clang, ifx's bitcode vs g++.
- ASan: gfortran objects linked by Apple clang++ hit an ASan version
  mismatch.

## Verified on Windows after Phase 2 (tower1, 2026-10-17)

- clang-cl + flang and flang + clang-cl, `debug`: each links with `-g` or
  `/DEBUG` and writes a PDB.
- msvc + ifx, `sanitize`: links and runs, because cl's objects carry
  `/InferAsanLibs` and `/DEFAULTLIB:"VCASAN"` themselves. An all-Fortran
  program in an MSVC environment has no such object, so it needs ifx's
  runtime stated as a link need (Phase 4). At run time, conda flang's own
  `clang_rt.asan_dynamic-x86_64.dll` ahead of MSVC's on PATH makes the
  program exit with 127.
- msvc + ifx, `hardened`: GUARD_CF set, runs.
- clang-cl, `sanitize`: **doesn't link** with lld-link, Fortran or not.
  clang-cl's objects name no ASan runtime (the clang-cl driver adds it
  when it links, and pcons links with lld-link directly), and lld-link
  ignores `/inferasanlibs`. It links with
  `clang_rt.asan_dynamic-x86_64.lib
  /wholearchive:clang_rt.asan_dynamic_runtime_thunk-x86_64.lib`.

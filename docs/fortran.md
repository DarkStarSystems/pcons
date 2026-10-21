# Fortran

Pcons builds Fortran with gfortran and LLVM's flang on Linux, macOS and
Windows, and with Intel's ifx on Linux and Windows, and mixes it with C
and C++ in one program.
A Fortran program is three lines of build script:

```python
from pcons import Project

project = Project("hello")
env = project.Environment(toolchain="fortran")
project.Program("hello", env, sources=["src/main.f90", "src/greetings.f90"])
```

List the sources in any order. Pcons works out which files define and use
which modules, and compiles them in the right order.

## Choosing a compiler

`toolchain="fortran"` finds whichever compiler is installed: gfortran
first, then flang, then ifx. Set `$FC` to choose one without editing the
script, as you'd set `$CC` or `$CXX` for C and C++:

```console
$ FC=flang-21 pcons
$ FC=/opt/intel/oneapi/compiler/latest/bin/ifx pcons
```

To require a particular compiler, ask for it by name:
`toolchain="gfortran"`, `"flang"` or `"ifx"`. A `$FC` that contradicts
the toolchain the script asks for is an error, not a hint.

Each compiler has its requirements:

- **flang** needs LLVM 21 or newer.
- **ifx** needs the oneAPI environment: `source
  /opt/intel/oneapi/setvars.sh` on Linux, `setvars.bat` on Windows. Intel
  doesn't make ifx for macOS.
- **gfortran** needs nothing special. On Windows it's MSYS2's MinGW build.

Fortran builds need the ninja generator (Ninja 1.10 or newer), which is
the default. The Make and Xcode generators can't express the
compile-time module ordering, and say so.

## Sources, modules and includes

All the usual extensions work: free form (`.f90`, `.f95`, `.f03`, `.f08`,
`.f18`), fixed form (`.f`, `.for`, `.ftn`), and preprocessed (`.F`,
`.F90`).

Pcons scans each source for the modules it declares and uses, and ninja
orders the compiles from that (with
[dyndep](https://ninja-build.org/manual.html#ref_dyndep)). Submodules
compile after the module or submodule they extend. A source is recompiled
when a module it uses changes its interface: with gfortran and flang,
editing only a module's implementation doesn't recompile its users. (ifx
is different; see [Limitations](#limitations).)

Files included with `#include` or Fortran's `INCLUDE` are found the way
the compiler finds them: beside the including file, then in
`env.fc.includes` and the include directories of linked targets and
packages. Editing an included file recompiles every source that includes
it.

```python
env.fc.includes.append(project.root_dir / "include")
```

## Calling C and C++

To mix languages, add a toolchain to the environment:

```python
env = project.Environment(toolchain="c++")
env.add_toolchain("fortran")

project.Program("solver", env, sources=["src/main.cpp", "src/kernel.f90"])
```

Each compiler compiles its own sources, with its own flags for the variant
and every preset. The order of the calls doesn't matter: a variant or
preset set before `add_toolchain()` reaches the added compiler too.

A program is linked by the driver of its strongest language: C++, then C,
then Fortran. So the program above links with `g++` or `clang++`, and
pcons adds the Fortran runtime. A program with only Fortran sources links
with the Fortran compiler. The rule also counts objects in static
libraries, even ones built in another environment, so a C++ program that
links a Fortran static library gets the Fortran runtime too.

A program whose `main` is in Fortran, with C or C++ helpers, can ask for
the Fortran driver, which then adds the C++ runtime:

```python
sim = project.Program("sim", env, sources=["src/main.f90", "src/helper.cpp"])
sim.link_language = "fortran"
```

With ifx on Linux this is required: ifx keeps a Fortran program's `main`
in its runtime, which only its own driver links. gfortran and flang link
either way.

`pcons explain` shows how each program links and why, and where each
runtime library came from:

```text
=== hello  (program)  [env #1]  pcons-build.py:19
  ...
  * build/hello  <-  build/obj.hello/src/{main.cpp.o,math_utils.f90.o}
      /usr/bin/clang++ -o hello ... -L/opt/homebrew/Cellar/gcc/16.2.0/lib/gcc/current -lgfortran
    link: clang++  (cxx leads fortran; set link_language to change)
      lib gfortran                                            <- gfortran (fortran)
      libdir /opt/homebrew/Cellar/gcc/16.2.0/lib/gcc/current  <- gfortran (fortran)
```

The calls themselves are ordinary Fortran 2003 C interoperability:
`BIND(C)` on the Fortran side and `extern "C"` on the C++ side. Examples
[27](https://github.com/DarkStarSystems/pcons/tree/main/examples/27_fortran_calls_cxx)
and
[28](https://github.com/DarkStarSystems/pcons/tree/main/examples/28_cxx_calls_fortran)
show both directions.

### OpenMP

`env.apply_preset("openmp")` gives each compiler its own OpenMP flag. The
catch is the runtime: a program should load one OpenMP runtime, and the C
compiler pcons finds beside your Fortran compiler may bring a different
one.

- **flang:** a program that uses OpenMP in both C or C++ and Fortran needs
  the C and C++ compilers from flang's own LLVM. Apple's clang or gcc
  brings a different runtime, and a program that loads two crashes (`OMP:
  Error #15`) or doesn't link. Set `CC` and `CXX` to that LLVM's compilers:
  `/opt/homebrew/opt/llvm/bin/clang` and `clang++` for Homebrew's flang,
  `clang-21` and `clang++-21` beside apt.llvm.org's `flang-21`, or the
  `clang-cl` beside conda-forge's flang on Windows.
- **gfortran** beside clang mostly works, since LLVM's runtime also
  implements GCC's interface. With the Fortran code in a static library,
  one runtime serves both languages. In a shared library, each side loads
  its own: the program runs, but `omp_set_num_threads()` in C doesn't reach
  the Fortran code, and the two thread pools can oversubscribe the cores.
  For one runtime, set `CC` and `CXX` to gfortran's own gcc and g++
  (`gcc-16` and `g++-16` from Homebrew's `gcc`, say).

## Variants and presets

The variants (`debug`, `release`, `relwithdebinfo`, `minsizerel`) and the
[feature presets](user-guide.md#semantic-presets) work with every Fortran
compiler, each with its own flags: ifx's `warnings` is `-warn all`, for
instance. Not every compiler has every preset:

| Compiler | Missing |
|---|---|
| gfortran | nothing: it has every preset GCC has |
| flang | `sanitize`, `profile`, `hardened`, `coverage`; no `-Os`, so `minsizerel` uses `-O2`; `warnings` adds nothing, since flang ignores `-Wall` |
| ifx | `coverage`; `sanitize` is AddressSanitizer only; no `lto` on Windows |

In an environment with several compilers, a preset applies to all of them
or raises: with clang and flang, `apply_preset("sanitize")` is an error
naming flang, since sanitizing only the C++ isn't a sanitized build.
`env.has_preset("sanitize")` asks first. To sanitize only the C++, compile
the Fortran in a clone of the environment made before the preset; see
[Semantic Presets](user-guide.md#semantic-presets) for the recipe.

`env.set_runtime_linkage("static")` links the Fortran runtime statically
too (`-static-libgfortran`, `-static-libflangrt`, `-static-intel`); see
[Static Runtimes](user-guide.md#static-runtimes) for each compiler's
flags and the platforms where it can't.

## Windows

On Windows, a Fortran compiler mixes only with C and C++ compilers that
share its ABI:

- **gfortran** (MSYS2's MinGW build) is GNU-ABI, so it mixes with MinGW's
  gcc and g++.
- **flang** and **ifx** are MSVC-ABI, so they mix with MSVC and clang-cl.
  They use the DLL C runtime (`/MD`, `/MDd` for debug), as pcons' MSVC
  builds do.

Pure Fortran works with any of them. `env.add_toolchain()` picks a
toolchain with a matching ABI: `"fortran"` beside MSVC or clang-cl finds
flang or ifx, not gfortran, and `"c"` beside gfortran finds MinGW gcc. A
toolchain that doesn't match, asked for by name or with `$FC`, `$CC` or
`$CXX`, is an error that lists the ones that would. Only an environment's
own toolchains have to match: a project can have a MinGW environment and
an MSVC one side by side.

**Getting flang.** The official LLVM installer for Windows doesn't include
flang. conda-forge's `flang_win-64` package does, with its runtime library
(its `flang` package is the compiler alone), but without the intrinsic
modules that `USE iso_c_binding` and the rest need. Copy
`lib\clang\23\finclude` from LLVM's Windows release archive of the same
version (`clang+llvm-23.1.3-x86_64-pc-windows-msvc.tar.zst`) into the
environment's `Library\lib\clang\23`. Visual Studio's bundled LLVM has a
`flang.exe` without its intrinsic modules, so pcons passes over it and
says why.

**ifx** takes MSVC-style options (`/module:`, `/warn:all`) and links with
`link.exe`. A program with ifx-compiled sources is linked as a console
program, since its Fortran `main` is in Intel's runtime. A C++ `WinMain`
program with Fortran sources sets `/SUBSYSTEM:WINDOWS` in
`env.link.flags`.

**DLLs** export only what they're told to. ifx honors `!DIR$ ATTRIBUTES
DLLEXPORT`, but flang ignores it, so a flang DLL exports nothing unless
its routines are listed: give them `BIND(C)` and list them in
`exported_symbols` (see [Exporting Only Some
Symbols](user-guide.md#exporting-only-some-symbols)). A DLL that exports
nothing has no import library, and a program that links it fails with
"no such file" for its `.lib`.

## Packages

A package built from Fortran should say so, so that a program linking it
gets the Fortran runtime:

```python
from pcons.packages.description import PackageDescription
from pcons.packages.imported import ImportedTarget

netcdf_fortran = ImportedTarget.from_package(
    PackageDescription(name="netcdf-fortran", libraries=["netcdff"], languages=["fortran"])
)
```

A package's include directories reach the Fortran compiler too, so its
module directories need nothing more. Its compile flags reach only the
compilers of the languages it declares: a C package's `-pthread` doesn't
reach gfortran.

## Configure checks

`ToolChecks` probes the Fortran compiler with `"fc"`. `check_flag()`
rejects an option the compiler would only warn about, so a flag gfortran
accepts and ifx only warns about counts as unsupported for ifx. `try_compile()`
compiles a Fortran test program:

```python
from pcons import Configure
from pcons.configure.checks import ToolChecks

config = Configure(build_dir=project.build_dir)
checks = ToolChecks(config, env, "fc")

# Each compiler has its own flag for this; keep whichever it accepts
for flag in ("-fbacktrace", "-traceback"):
    if checks.check_flag(flag).success:
        env.fc.flags.append(flag)

quad = checks.try_compile("""
    integer, parameter :: qp = selected_real_kind(33)
    real(qp) :: x
    end
""")
env.fc.defines.append(f"WITH_QP={int(quad.success)}")
config.save()
```

The header, type, function and macro checks write C programs, so they
need a C or C++ tool. See [Feature Detection](user-guide.md#feature-detection)
for the rest.

## Limitations

- ifx rewrites every `.mod` file it compiles, even when nothing in it
  changed, so editing a module's implementation recompiles everything that
  uses the module. gfortran and flang recompile those only when the
  module's interface changes.
- Pcons finds included files without running the preprocessor, so an
  include inside an `#if` counts even when the condition is false. It
  costs an occasional extra recompile, never a missed one.
- Only the ninja generator builds Fortran.

## Examples

| Example | Shows |
|---|---|
| [`25_fortran_hello`](https://github.com/DarkStarSystems/pcons/tree/main/examples/25_fortran_hello) | A one-file program with whichever compiler is installed |
| [`26_fortran_modules`](https://github.com/DarkStarSystems/pcons/tree/main/examples/26_fortran_modules) | Modules, compiled in dependency order |
| [`27_fortran_calls_cxx`](https://github.com/DarkStarSystems/pcons/tree/main/examples/27_fortran_calls_cxx) | A Fortran `main` calling C++, with `link_language` |
| [`28_cxx_calls_fortran`](https://github.com/DarkStarSystems/pcons/tree/main/examples/28_cxx_calls_fortran) | C++ calling Fortran, with the Fortran runtime added |
| [`94_fortran_submodules`](https://github.com/DarkStarSystems/pcons/tree/main/examples/94_fortran_submodules) | A module implemented in nested submodules |
| [`95_fortran_includes`](https://github.com/DarkStarSystems/pcons/tree/main/examples/95_fortran_includes) | `#include` and `INCLUDE`, and the recompiles they cause |
| [`96_fortran_configure_checks`](https://github.com/DarkStarSystems/pcons/tree/main/examples/96_fortran_configure_checks) | Probing the compiler for flags and features |

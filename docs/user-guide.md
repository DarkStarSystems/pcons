# Pcons User Guide <small>v{{ version }}</small>

Pcons is a Python-based build system that generates [Ninja](https://ninja-build.org/) build files for C/C++ projects. It combines some of the best ideas from SCons and CMake: Python as the configuration language, environments with tools, and a fast generator architecture with proper dependency tracking.

## Why Pcons?

### Key Features

- **Python is the language**: No custom DSL to learn. Your `pcons-build.py` is real Python with full IDE support, debugging, and all the power of the Python ecosystem.
- **Fast builds with Ninja**: Pcons generates Ninja files and lets Ninja handle the actual compilation. This means fast, parallel builds with minimal overhead.
- **Automatic dependency tracking**: Pcons tracks dependencies between source files, object files, and outputs, rebuilding only what's necessary.
- **Transitive requirements**: Like CMake's "usage requirements," include directories and link flags automatically propagate through your dependency tree.
- **Tool-agnostic core**: The core knows nothing about C++ or any language. All language support comes through Tools and Toolchains, making it extensible.
- **Works with `uv`**: Designed for modern Python workflows with `uv` as the recommended package manager.

### Comparison with Other Build Systems

| Feature | Pcons | Make | CMake | SCons |
|---------|-------|------|-------|-------|
| Configuration language | Python | Makefile | CMake DSL | Python |
| Build executor | Ninja | Make | Make/Ninja | SCons |
| Learning curve | Low (if you know Python) | Medium | High | Medium |
| IDE integration | Yes (`compile_commands.json`) | Limited | Yes | Yes |
| Dependency tracking | Automatic | Manual | Automatic | Automatic |
| Transitive dependencies | Yes | No | Yes | Limited |

---

## Quick Start

### Installing Pcons

#### Using `uv`

`uv` is a fast modern python package and project manager. Install it from [here](https://github.com/astral-sh/uv). Highly recommended, and it's a simple quick install.

You can run pcons directly from PyPI with `uvx` (no installation required):

```bash
uvx pcons ...
```

Or add it to your project:

```bash
uv add pcons
pcons ...
```

Or install globally:

```bash
uv tool install pcons
pcons ...
```

#### With `pipx` or python

pcons is on PyPI, so if you have pipx, just `pipx install pcons`. With plain python, you can install pcons globally using `python -mpip install pcons` or use a venv if desired.


### Your First Build: Hello World

Let's build a simple "Hello World" program.

**1. Create the source file** (`hello.cpp`):

```cpp
#include <iostream>

int main() {
    std::cout << "Hello from pcons!" << std::endl;
    return 0;
}
```

**2. Create the build script** (`pcons-build.py`):

```python
#!/usr/bin/env python3
from pcons import Project

# Create project with build directory
project = Project("hello", build_dir="build")

# Create an environment with the system default C/C++ toolchain
env = project.Environment(toolchain="c")

# Create a program target
hello = project.Program("hello", env)
hello.add_sources(["hello.cpp"])

# Set this as the default target
project.Default(hello)
```

**3. Generate and build**:

```bash
# Using uvx (recommended)
uvx pcons

# Or if pcons is installed
pcons
```

This runs your `pcons-build.py` to generate `build/build.ninja`, then invokes Ninja to compile your program. If you don't have ninja installed, pcons will try to invoke it via `uvx ninja`.

> **Tip:** You can swap in a ninja-compatible runner like [n2](https://github.com/evmar/n2) (a Rust rewrite of Ninja) with `pcons --ninja=n2` or by setting `NINJA=n2` in the environment to get more advanced rebuild checking. For content-hash rebuilds, use `env.use_compiler_cache()` (see the Compiler Caching section below).

**4. Run your program**:

```bash
./build/hello
# Output: Hello from pcons!
```

### Understanding the Commands

Pcons provides several commands:

```bash
pcons                    # Generate build files AND build (default)
pcons --watch            # ... and keep rebuilding as files change
pcons generate           # Only generate build.ninja
pcons build              # Only run ninja (assumes build.ninja exists)
pcons clean              # Clean build artifacts
pcons clean --all        # Remove entire build directory
pcons info               # Show pcons-build.py documentation
pcons init               # Create pcons-build.py (adopts existing C/C++ sources,
                         # or scaffolds a hello-world starter in an empty dir)
```

---

## Supported Languages and Toolchains

Pcons ships with built-in support for several languages and toolchains. The core is completely tool-agnostic — all language support comes from toolchain modules that register themselves at import time.

### Registered Toolchains

The following toolchains are auto-detected. Select one by name — `toolchain="c"` auto-detects a C/C++ toolchain, a specific name like `"gcc"` requires that toolchain, and a list is a preference order:

```python
env = project.Environment(toolchain="c")  # auto-detect C/C++
env = project.Environment(toolchain="msvc")  # require MSVC
env = project.Environment(toolchain=["gcc", "llvm"])  # first available wins
```

IDE autocompletion for these names comes from the generated `KnownToolchain` type; any registered name (including user-registered toolchains) also works. For programmatic control, the underlying finder functions like `find_c_toolchain(prefer=[...])` remain available and return `Toolchain` objects.

{{ toolchain_table }}

**Default C/C++ search order:**

- **Windows**: clang-cl → msvc → llvm → gcc
- **Linux / macOS**: llvm → gcc

### Selecting compilers with environment variables

pcons honors the conventional tool-selection environment variables, same as
make/autoconf/CMake/Meson — no build-script changes needed:

```console
$ CXX=g++-15 pcons          # build with a specific compiler
$ CC=clang-19 CXX=clang++-19 pcons
$ CXX=/opt/llvm/bin/clang++ pcons
```

| Variable | Selects | Toolchains |
|----------|---------|------------|
| `CC` | C compiler (and the link driver) | gcc, llvm, msvc, clang-cl |
| `CXX` | C++ compiler | gcc, llvm, msvc, clang-cl |
| `FC` | Fortran compiler | gfortran |
| `AR` | archiver | gcc, llvm, swift |
| `SWIFTC` | Swift compiler | swift |
| `CUDACXX` | CUDA compiler (nvcc) | cuda |
| `RC` | resource compiler | msvc, clang-cl |

Following the universal convention, these are **authoritative, not hints**:
a set variable selects that command; a value that can't be found is an
error, never a silent fall-through to detection. The rules:

- With `toolchain="c"` (auto-detect), `$CXX`/`$CC` steer detection to the
  named compiler's *family* — `CXX=g++-15` selects the gcc toolchain even
  where clang would normally win. Classification sniffs `--version`, so
  macOS's `g++`-that-is-really-Apple-clang is identified correctly.
- An explicitly requested toolchain that *contradicts* the variable
  (`CXX=g++-15` with `toolchain="msvc"`) is an error.
- Values a compiler-id can't classify (e.g. wrapper scripts) are used
  as-is on whichever toolchain is selected.
- Explicit script assignments (`env.cxx.cmd = ...`) and cross-preset
  `tool_cmds` still win over the environment; SDK-owned toolchains
  (emscripten, wasi) ignore these variables entirely, like CMake's
  Visual Studio generators.
- `env.explain()` attributes the result (`cxx.cmd <- $CXX`), so a
  forgotten `export CXX` in a shell profile is visible, not mysterious.

`CFLAGS`/`CXXFLAGS`/`LDFLAGS` are *not* read — flag policy belongs to the
build script (variants, presets), not the ambient environment.

**Swift** is available as `toolchain="swift"` (requires Xcode on macOS, or a
swift.org toolchain on Linux and Windows). Swift's compilation unit is the module, not the
file: each pcons target compiles as one Swift module in a single whole-module
`swiftc` invocation, so files in a target see each other without imports.
Importing another target's module is an ordinary dependency — the library's
`.swiftmodule` search path propagates as a usage requirement:

```python
env = project.Environment(toolchain="swift")

geometry = project.StaticLibrary("Geometry", env, sources=["lib/geometry.swift"])
app = project.Program("shapes", env, sources=["src/main.swift"])  # import Geometry
app.link_private(geometry)
```

The module name is the target name (sanitized to a Swift identifier); library
targets compile with `-parse-as-library`. Variants map to `-Onone -g` /
`-O`, and `werror` maps to `-warnings-as-errors`. `compile_commands.json`
entries are emitted per source file with the whole-module command, the
convention sourcekit-lsp expects. See `examples/46_swift_hello` and
`examples/47_swift_library`.

**Swift / C / C++ interop** works in both directions. Swift imports a C
library through a `module.modulemap` shipped in the library's include dir
(the propagated include path serves both the modulemap and headers). For
C++, enable interop mode and header emission:

```python
env = project.Environment(toolchain="swift")
env.add_toolchain("c")  # C/C++ compilers for mixed targets
env.swiftc.set_cxx_interop("c++17")  # Swift <-> C++ interop mode
env.swiftc.interop_header = True  # libraries emit <Module>-Swift.h

analyzer = project.StaticLibrary("Analyzer", env, sources=["analyzer.swift"])
app = project.Program(
    "demo", env, sources=["src/main.cpp"]
)  # #include "Analyzer-Swift.h"
app.link_private(analyzer)
```

The generated header lands next to the `.swiftmodule` in the propagated
include dir, and consumers' C++ compiles automatically wait for it. When a
C header is imported in C++-interop mode it is parsed as C++, so it needs
the usual `extern "C"` guards. Consuming the generated header from C++ is currently reliable on
macOS; on Linux it depends on the Swift version and C++ standard library in
use. Mixed links are handled automatically:
swiftc drives the link when Swift is involved (bringing the Swift runtime),
and a C/C++-driven link of Swift objects gets the runtime path injected via
`swiftc -print-target-info`. See `examples/48_swift_cxx_interop`.

A `module.modulemap` can be generated instead of hand-written —
`clang_module_map(project, "CStats", ["include/cstats.h"])` (from
`pcons.toolchains.swift`) writes one into the build tree and returns its
directory for `public.include_dirs`. For distributable libraries,
`env.swiftc.library_evolution = True` builds with
`-enable-library-evolution` and emits a `.swiftinterface` next to the
`.swiftmodule`. And Swift participates in cross presets: two lines target
iOS (see `examples/49_swift_ios`):

```python
env = project.Environment(toolchain="swift")
env.apply_cross_preset(ios(arch="arm64", min_version="15.0"))
```

**Fortran** (`gfortran`) is available as `toolchain="fortran"`. It supports all standard Fortran source extensions and uses Ninja dyndep to resolve `MODULE` / `USE` dependencies at build time (requires Ninja ≥ 1.10):

```python
env = project.Environment(toolchain="fortran")
project.Program("hello", env, sources=["src/main.f90", "src/greetings.f90"])
```

**Mixed C++/Fortran** builds use `env.add_toolchain()`. Runtime libraries are injected automatically in both directions:

```python
# Fortran primary: gfortran links, -lc++ / -lstdc++ injected for C++ objects
env = project.Environment(toolchain="fortran")
env.add_toolchain("c")

# C++ primary: g++/clang++ links, -lgfortran injected for Fortran objects
env = project.Environment(toolchain="c++")
env.add_toolchain("fortran")

project.Program("hello", env, sources=["src/main.f90", "src/helper.cpp"])
```

**CUDA** is designed to work alongside a C/C++ toolchain — CUDA handles `.cu` compilation while the host toolchain handles linking:

```python
env = project.Environment(toolchain="c++")
env.add_toolchain("cuda")
```

**Emscripten** requires the Emscripten SDK. Set the `EMSDK` environment variable, or install to `~/emsdk` or `/opt/emsdk`.

**WASI** requires the WASI SDK. Set `WASI_SDK_PATH`, or install to `/opt/wasi-sdk` or `~/.local/share/wasi-sdk` (also available via Homebrew).

**LaTeX** is available as a contrib toolchain using `latexmk`. It handles multi-pass compilation, BibTeX/Biber bibliography processing, makeindex, cross-references, and automatic dependency tracking (including `\input`'d files and `.bib` sources):

```python
from pcons.contrib.latex import find_latex_toolchain

env = project.Environment(toolchain=find_latex_toolchain())
env.latex.Pdf(build_dir / "paper.pdf", src_dir / "paper.tex")

# Optional: change engine or add flags
env.latex.engine = "xelatex"
env.latex.flags.append("-shell-escape")
```

**Rust** is supported as *interop*, not as a native toolchain: pcons does not compile `.rs` files itself and there is no Rust toolchain to detect or configure. Instead `project.CargoBuild()` drives `cargo build` as a black-box sub-build (cargo owns the Rust compile and its intra-Rust incremental logic) and wraps the resulting library so C/C++ consumers can `.link()` it like any other dependency. Cross-compilation, Rust dialect, and similar settings are configured on the cargo side, not through pcons's environment:

```python
rust_core = project.CargoBuild(
    "rust_core",
    env,
    manifest="rust/Cargo.toml",
    crate_type="staticlib",  # or "cdylib", "bin"
    profile="release",
)
app = project.Program("app", env, sources=["src/main.cpp"])
app.link(rust_core)  # -L/-l propagate automatically
```

Pass `generate_header="rust/cbindgen.toml"` to also run cbindgen and emit a C header from the Rust sources — pcons wires the header as an implicit dep of consumer compile steps, so the header exists before any `#include` is processed. See `examples/43_rust_cxx_hybrid/` (hand-written FFI header) and `examples/44_rust_cxx_cbindgen/` (cbindgen-generated header) for end-to-end examples. Other foreign build tools can be wired up the same way using `env.Command(restat=True)`.

### Builder Types

All builders are accessible as methods on `Project`:

{{ builder_table }}

### Custom Toolchains

You can register your own toolchain to support additional languages or compilers:

```python
from pcons.toolchains import toolchain_registry

toolchain_registry.register(
    MyToolchain,
    aliases=["my-toolchain"],
    check_command="my-compiler",
    tool_classes=[MyCompiler, MyLinker],
    category="c",
    platforms=["linux", "darwin", "win32"],
    description="My custom compiler",
)
```

---

## Core Concepts

Understanding these core concepts will help you write effective pcons build scripts.

### Build Script Lifecycle

Every pcons build script (`pcons-build.py`) follows three phases:

1. **Configure** - Set up toolchains, environments, and build options
2. **Describe** - Create targets and define their sources/dependencies
3. **Generate** - Resolve dependencies and write build files

Your script only describes the build — the resolve and generate steps run automatically when it finishes, whether invoked via the `pcons` CLI or run directly with Python. Ninja is the default generator; select another with `pcons -G make` (or the `PCONS_GENERATOR`/`GENERATOR` environment variables).

For finer control you can run either step explicitly:

```python
# ... define targets ...

# Resolve all dependencies now (generators do this automatically if needed)
project.resolve()

# Generate build files now, e.g. to run code after generation completes
project.generate()
```

### Project

A `Project` is the top-level container for your build. It holds all environments, targets, and nodes.

```python
from pcons import Project

# Create a project
project = Project("myproject", build_dir="build")

# Optionally specify the root directory
project = Project("myproject", root_dir=Path(__file__).parent, build_dir="build")
```

The project provides factory methods for creating targets:

- `project.Program()` - Create an executable
- `project.StaticLibrary()` - Create a static library (.a/.lib)
- `project.SharedLibrary()` - Create a shared library (.so/.dylib/.dll)
- `project.HeaderOnlyLibrary()` - Create a header-only library

### Environment

An `Environment` holds configuration for building: compiler settings, flags, include directories, and more. You can have multiple environments (e.g., for different platforms or variants).

```python
# Create environment with an auto-detected C/C++ toolchain
env = project.Environment(toolchain="c")

# Configure compiler flags
env.cc.flags.extend(["-Wall", "-Wextra"])
env.cxx.flags.extend(["-std=c++17"])

# Add include directories
env.cxx.includes.append("include")

# Add preprocessor defines
env.cxx.defines.append("VERSION=1")
```

Each environment has namespaced tool configurations:
- `env.cc` - C compiler settings
- `env.cxx` - C++ compiler settings
- `env.link` - Linker settings

### Path Conventions

Pcons uses consistent path conventions throughout:

- **Source paths** (inputs): Relative to the project root directory
- **Target paths** (outputs): Relative to the build directory
- **Install destinations**: Relative to the install prefix (`PCONS_INSTALL_PREFIX`, default `<project-root>/dist`) — see [Installing Files](#installing-files)
- **Absolute paths**: Pass through unchanged

This means you don't need to prefix output paths with `build_dir`:

```python
# Good: output paths are relative to build_dir
project.Tarfile(env, output="packages/release.tar.gz", ...)

# Install destinations are relative to the install prefix (default: dist/)
project.Install("lib", [mylib])              # -> <root>/dist/lib/
project.InstallDir(".", src_dir / "assets")  # -> <root>/dist/assets/

# Not needed: build_dir prefix is implicit
# project.Tarfile(env, output=build_dir / "packages/release.tar.gz", ...)  # Unnecessary
```

If you accidentally include the build directory name in a relative path (e.g., `"build/dist"`), pcons will warn you but keep the path as-is, in case you intentionally want a `build/` subdirectory inside the build directory.

### Toolchain

A `Toolchain` is a coordinated set of tools (compiler, linker, archiver) that work together. Pcons automatically detects available C/C++ toolchains.

```python
# Auto-detect the best available C/C++ toolchain by name.
# Uses platform-appropriate defaults:
#   Windows: clang-cl, msvc, llvm, gcc
#   Unix/Mac: llvm, gcc
env = project.Environment(toolchain="c")

# Or give a preference order, or require a specific toolchain
env = project.Environment(toolchain=["gcc", "llvm"])
env = project.Environment(toolchain="msvc")

# For programmatic selection, finder functions return Toolchain objects
from pcons import find_c_toolchain

toolchain = find_c_toolchain(prefer=["gcc", "llvm"])
```

Available toolchains:
- **LLVM** (Clang) - Default on macOS and Linux; uses GCC-style flags
- **Clang-CL** - Clang with MSVC-compatible flags for Windows
- **GCC** - Common on Linux
- **MSVC** - Visual Studio on Windows

### Targets

A `Target` represents something to build: a program, library, or other output. Targets have:

- **Sources**: Input files to compile
- **Dependencies**: Other targets this links against or requires
- **Usage Requirements**: Include dirs, defines, and flags

```python
# Create a program target
app = project.Program("myapp", env)
app.add_sources(["main.cpp", "util.cpp"])

# Create a library target
# Adding "include" as a public include_dir will cause
# the app's build to get the proper include flags to
# find this lib's headers.
lib = project.StaticLibrary("mylib", env)
lib.add_sources(["lib.cpp"])
lib.public.include_dirs.append(Path("include"))

# Link the program against the library
app.link_private(lib)
```

#### Target Types

| Method | Output | Use Case |
|--------|--------|----------|
| `Program()` | Executable | Applications, tools |
| `StaticLibrary()` | .a / .lib | Code reuse, no runtime dependency |
| `SharedLibrary()` | .so / .dylib / .dll | Plugins, shared code |
| `HeaderOnlyLibrary()` | None | Template libraries |

### Nodes

Nodes represent files in the dependency graph. Use `project.node()` to get or create a node:

```python
# Create or get a node for a file
src_node = project.node("src/main.cpp")

# Nodes track:
# - Path to the file
# - Builder that creates it (if any)
# - Dependencies
```

**When to use `project.node()` vs raw paths:**

Most pcons APIs accept raw paths (strings or `Path` objects) and convert them to nodes internally. You only need `project.node()` when:

```python
# Usually NOT needed - these are equivalent:
project.Install("dist", ["file.txt"])  # Path string - works fine
project.Install("dist", [Path("file.txt")])  # Path object - works fine
project.Install("dist", [project.node("file.txt")])  # Explicit node - also works

# Needed when you want to add explicit dependencies to a source file:
header = project.node("generated.h")
header.depends([generator_target])  # Now generated.h depends on generator
app.add_sources(["main.cpp"])  # main.cpp will rebuild when generated.h changes
```

### Builders

Builders define how to create output files from inputs. They're provided by tools within a toolchain. You typically don't create builders directly; instead, use the high-level target API.

Behind the scenes, when you call `project.Program()`, pcons uses:
- The `Object` builder to compile `.cpp` files to `.o` files
- The `Program` builder to link `.o` files into an executable

### Dependency Graph

Pcons builds a dependency graph of all files and their relationships:
```
hello.cpp  →  hello.o  →  hello (program)
             ↑
math.cpp  →  math.o  ─┘
```

When you run `pcons build`, Ninja uses this graph to:
1. Check timestamps on all files
2. Rebuild only files whose dependencies changed
3. Execute builds in parallel where possible

### Default and Alias Targets

**Default targets** are built when you run `ninja` with no arguments:

```python
# Set default targets - these build when you run just "ninja"
project.Default(app)
project.Default(lib, app)  # Can specify multiple
```

If you don't call `project.Default()`, all programs and libraries (static and shared) in the project are built by default. This is usually what you want for simple projects. Use `Default()` when you want to build only a subset by default — for example, to exclude test programs or optional tools from the default build.

`ninja all` (or `make all`) builds every target in the project, including custom commands, installers, and archives.

**Aliases** create named phony targets for convenient building:

```python
# Create an alias - builds with "ninja install"
project.Alias("install", installed_lib, installed_headers)

# Create an alias for tests
project.Alias("test", test_runner)

# Now you can run:
#   ninja install    # Build and install
#   ninja test       # Build and run tests
```

Aliases are Ninja phony targets - they don't produce files but depend on other targets. Target names (like `"myapp"` in `project.Program("myapp", env)`) are also usable with Ninja:

```bash
ninja myapp      # Build just the myapp target
ninja libfoo     # Build just libfoo
ninja install    # Build the install alias
```

---

## Building Projects Step by Step

Let's walk through a few progressively more complex examples.

### Hello World - Single File Program

The simplest possible project: one source file, one output.

**File structure:**
```
project/
├── pcons-build.py
└── hello.c
```

**hello.c:**
```c
#include <stdio.h>

int main(void) {
    printf("Hello from pcons!\n");
    return 0;
}
```

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pcons import Project

# Setup
project = Project("hello", build_dir="build")
env = project.Environment(toolchain="c")

# Create program
hello = project.Program("hello", env)
hello.add_sources(["hello.c"])
hello.private.compile_flags.extend(["-Wall", "-Wextra"])

project.Default(hello)
```

**Build and run:**
```bash
uvx pcons
./build/hello
# Output: Hello from pcons!
```

### Multiple Source Files

A program with multiple source files and a header.

**File structure:**
```
project/
├── pcons-build.py
├── include/
│   └── math_ops.h
└── src/
    ├── main.c
    └── math_ops.c
```

**include/math_ops.h:**
```c
#ifndef MATH_OPS_H
#define MATH_OPS_H

int add(int a, int b);
int multiply(int a, int b);

#endif
```

**src/math_ops.c:**
```c
#include "math_ops.h"

int add(int a, int b) {
    return a + b;
}

int multiply(int a, int b) {
    return a * b;
}
```

**src/main.c:**
```c
#include <stdio.h>
#include "math_ops.h"

int main(void) {
    int a = 5, b = 3;
    printf("add(%d, %d) = %d\n", a, b, add(a, b));
    printf("multiply(%d, %d) = %d\n", a, b, multiply(a, b));
    return 0;
}
```

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project

# Directories
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Setup
project = Project("calculator", build_dir="build")
env = project.Environment(toolchain="c")

# Create program with multiple sources
calculator = project.Program("calculator", env)
calculator.add_sources(
    [
        src_dir / "main.c",
        src_dir / "math_ops.c",
    ]
)

# Add include directory (private - only for building this target)
calculator.private.include_dirs.append(include_dir)
calculator.private.compile_flags.extend(["-Wall", "-Wextra"])

project.Default(calculator)
```

### Static Library

Create a reusable static library and link it to a program.

**File structure:**
```
project/
├── pcons-build.py
├── include/
│   └── math_utils.h
└── src/
    ├── main.c
    └── math_utils.c
```

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project

src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

project = Project("myproject", build_dir="build")
env = project.Environment(toolchain="c")

# Create static library
libmath = project.StaticLibrary("math", env)
libmath.add_sources([src_dir / "math_utils.c"])

# Public includes propagate to consumers
libmath.public.include_dirs.append(include_dir)

# Public link libs (e.g., math library on Linux).
# link("m") adds a raw -l library (placed after objects on the link line);
# use link_flags for other linker flags (placed before objects).
libmath.link("m")

# Create program that uses the library
app = project.Program("myapp", env)
app.add_sources([src_dir / "main.c"])
app.link_private(libmath)  # Gets libmath's public includes automatically!

project.Default(app)
```

Key points:
- `public.include_dirs` propagates to targets that link against this library
- `app.link_private(libmath)` adds libmath as a dependency and applies its public requirements. Use `link_private()` to keep the dependency local (as here, since `app` is the final program) or `link()` to re-export it to consumers of this target.

!!! note "`link()` / `link_private()` vs. the `link_libs` lists"
    `target.link(...)` and `target.link_private(...)` are the recommended high-level forms. They are exactly equivalent to appending to `target.public.link_libs` and `target.private.link_libs` respectively — those lists remain fully supported as the low-level form, and accept the same `Target` objects and library-name strings.

#### System Include Directories

Vendored third-party headers are a special case: you want them found, but you don't want their warnings, and you certainly don't want `-Werror` failing your build on code you can't change. Every compiler has a second kind of include path for this — `-isystem` on GCC/Clang, `/external:I` on MSVC (pcons adds `/external:W0` alongside it), `-imsvc` on clang-cl. In pcons it's `system_includes`, on the tool or as a usage requirement:

```python
# On the environment
env.cxx.system_includes.append(root / "vendor/ae-sdk")

# Or as a usage requirement, so consumers inherit the headers
# without inheriting the warnings
sdk = project.HeaderOnlyLibrary("ae_sdk")
sdk.public.system_include_dirs.append(root / "vendor/ae-sdk")
app.link(sdk)
```

Everything that works for `includes` works here: transitive propagation, deduplication, and path relativization in the generated build files. See `examples/58_system_includes`.

!!! note "Packages"
    Imported packages (`find_package()`, pkg-config, Conan) still contribute ordinary `-I` include dirs. Marking them system by default is a deliberate behavior change and is not in place yet.

### Shared/Dynamic Library

Create a shared library (`.so` on Linux, `.dylib` on macOS, `.dll` on Windows).

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project

src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

project = Project("myproject", build_dir="build")
env = project.Environment(toolchain="c")

# Create shared library
libplugin = project.SharedLibrary("plugin", env)
libplugin.add_sources([src_dir / "plugin.c"])
libplugin.public.include_dirs.append(include_dir)

# Optional: customize output name (overrides platform defaults)
libplugin.output_name = "myplugin.so"  # Override default libplugin.so

# Output naming defaults (can be overridden with output_name):
#   SharedLibrary "foo":
#     Linux:   libfoo.so
#     macOS:   libfoo.dylib
#     Windows: foo.dll
#   StaticLibrary "foo":
#     Linux/macOS: libfoo.a
#     Windows:     foo.lib
#   Program "foo":
#     Linux/macOS: foo
#     Windows:     foo.exe

# Create program that uses the library
app = project.Program("host", env)
app.add_sources([src_dir / "main.c"])
app.link_private(libplugin)

project.Default(app, libplugin)
```

### Project with Subdirectories

Organize a larger project with separate directories.

**File structure:**
```
project/
├── pcons-build.py
├── include/
│   ├── math_utils.h
│   └── physics.h
└── src/
    ├── main.c
    ├── math_utils.c
    └── physics.c
```

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project

project_dir = Path(__file__).parent
src_dir = project_dir / "src"
include_dir = project_dir / "include"
build_dir = project_dir / "build"

project = Project("simulator", root_dir=project_dir, build_dir=build_dir)
env = project.Environment(toolchain="c")

# Library: libmath - low-level math utilities
libmath = project.StaticLibrary("math", env)
libmath.add_sources([src_dir / "math_utils.c"])
libmath.public.include_dirs.append(include_dir)
libmath.link("m")  # Link math library

# Library: libphysics - depends on libmath
libphysics = project.StaticLibrary("physics", env)
libphysics.add_sources([src_dir / "physics.c"])
libphysics.link(libmath)  # Re-exports libmath's includes to consumers

# Program: simulator - main application
simulator = project.Program("simulator", env)
simulator.add_sources([src_dir / "main.c"])
simulator.link_private(libphysics)  # Gets BOTH physics and math includes!

# Set defaults
project.Default(simulator)
```

### Debug and Release Variants

Use `set_variant()` to switch between debug and release builds.

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project, get_variant

# Get variant from command line: pcons --variant=debug
# Defaults to "release"
variant = get_variant("release")
build_dir = Path("build") / variant

project = Project("myapp", build_dir=build_dir)
env = project.Environment(toolchain="c")

# Apply variant settings
# debug: -O0 -g
# release: -O2 -DNDEBUG
env.set_variant(variant)

# Add extra flags
env.cc.flags.append("-Wall")

app = project.Program("myapp", env)
app.add_sources(["main.c"])

project.Default(app)

print(f"Variant: {variant}")
print(f"Build dir: {build_dir}")
```

**Usage:**
```bash
# Release build (default)
uvx pcons
./build/release/myapp

# Debug build
uvx pcons --variant=debug
./build/debug/myapp
```

### Semantic Presets

In addition to build variants (debug/release), pcons provides **presets** for common development workflows. Presets are orthogonal to variants — you can combine them freely.

```python
# Apply warning flags (all warnings; add "werror" to make them errors)
env.apply_preset("warnings")

# Promote warnings to errors (compose with "warnings")
env.apply_preset("werror")

# Apply address/undefined behavior sanitizers
env.apply_preset("sanitize")

# Enable profiling
env.apply_preset("profile")

# Enable link-time optimization
env.apply_preset("lto")

# Enable security hardening flags
env.apply_preset("hardened")
```

Presets are toolchain-specific — each toolchain produces the appropriate flags:

| Preset | Unix (GCC/LLVM) | MSVC |
|--------|----------------|------|
| `warnings` | `-Wall -Wextra -Wpedantic` | `/W4` |
| `werror` | `-Werror` | `/WX` |
| `sanitize` | `-fsanitize=address,undefined -fno-omit-frame-pointer` | `/fsanitize=address` |
| `profile` | `-pg -g` (compile+link) | `/PROFILE` (linker) |
| `lto` | `-flto` (compile+link) | `/GL` (compile) + `/LTCG` (link) |
| `hardened` | `-fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE` + `-pie -Wl,-z,relro,-z,now` | `/GS /guard:cf` + `/DYNAMICBASE /NXCOMPAT /guard:cf` |

Combine presets with variants for a complete configuration:

```python
env.set_variant("release")
env.apply_preset("warnings")
env.apply_preset("lto")
```

Variants act like a knob: calling `set_variant()` again *replaces* the
previous variant's flags rather than piling on top of them, so
`env.set_variant("release")` followed by `env.set_variant("debug")` switches
cleanly. To build both variants side by side, clone the environment (see
[Environment Cloning](#environment-cloning)).

### Where Did This Flag Come From? (`env.explain()`)

Once variants, presets, and manual edits combine, it can be unclear which
setting produced a given flag. `env.explain()` attributes every flag, define,
and command override on the environment to the preset that contributed it;
anything you set directly (or a toolchain default) is labelled `(manual)`.

```python
env = project.Environment(toolchain="c")
env.set_variant("release")
env.apply_preset("warnings")
env.cc.flags.append("-fno-strict-aliasing")

print(env.explain("cc"))  # one tool; env.explain() covers all tools
```

Output:

```text
cc.flags:
  -O2                   <- release (variant)
  -Wall                 <- warnings (feature)
  -Wextra               <- warnings (feature)
  -Wpedantic            <- warnings (feature)
  -fno-strict-aliasing  <- (manual)
cc.defines:
  NDEBUG                <- release (variant)
```

`env.cc.explain()` is shorthand for `env.explain("cc")`. Cross presets and
SDK wiring show up the same way (e.g. `cc.cmd <- wasi-sdk`), so `explain()`
is the first tool to reach for when a build uses a flag — or a compiler —
you didn't expect.

---

## Working with External Dependencies

### Finding Packages with `project.find_package()`

The simplest way to use an external package is `project.find_package()`. It searches for the package using available finders (pkg-config, system paths) and returns an `ImportedTarget` that you can link against or apply to an environment.

```python
from pcons import Project

project = Project("myapp", build_dir="build")
env = project.Environment(toolchain="c")

# Find packages (raises PackageNotFoundError if not found)
zlib = project.find_package("zlib")
openssl = project.find_package("openssl", version=">=3.0")

# Find with components
boost = project.find_package("boost", components=["filesystem", "system"])

# Optional dependency — returns None if not found
optional = project.find_package("optional-dep", required=False)

# Use as a dependency (public requirements auto-propagate)
app = project.Program("myapp", env, sources=["main.cpp"])
app.link_private(zlib)

# Or apply directly to an environment
env.use(openssl)
```

By default, `find_package()` tries PkgConfigFinder first, then SystemFinder. You can prepend custom finders:

```python
from pcons.packages.finders import ConanFinder

# Add a Conan finder — it will be tried first
project.add_package_finder(ConanFinder(config, conanfile="conanfile.txt"))

# Now find_package() tries: Conan → PkgConfig → System
fmt = project.find_package("fmt")
```

**The finder-chain contract**: precedence is insertion order — each
`add_package_finder()` call prepends, so the most recently added finder is
consulted first, then the defaults (PkgConfig, then System). The first finder
to return a result wins; a finder that comes up empty (wrong version, tool
missing the package) falls through to the next, and the winning source is
recorded on the package as `found_by` (e.g. `"pkg-config"`, `"rez+pkg-config"`,
`"system"`). A finder whose tool isn't installed is skipped with a warning at
registration rather than silently never matching. Run with debug logging to
see which finder answered (or passed on) each package.

Results are cached per `(name, version, components)` — including *negative*
results, so repeated `find_package(..., required=False)` probes don't re-run
the finder chain and its subprocesses; a later `required=True` call for the
same key raises from the cache.

### Header-Only and Manual Packages

Some libraries (especially header-only ones) don't have `.pc` files and can't be found by `find_package()`. Create an `ImportedTarget` manually using `PackageDescription`:

```python
from pcons import ImportedTarget, PackageDescription

# Header-only library with no .pc file
httplib = ImportedTarget.from_package(
    PackageDescription(
        name="cpp-httplib",
        include_dirs=["/opt/homebrew/include"],
        defines=["CPPHTTPLIB_OPENSSL_SUPPORT"],
    )
)
```

If the manual package depends on another package, `link()` it to wire up transitive dependencies — don't copy public requirements manually:

```python
openssl = project.find_package("openssl")

httplib = # ... see above
httplib.link(openssl)  # openssl requirements propagate to anything linking httplib

# Now any target that links httplib automatically gets openssl too
app = project.Program("myapp", env, sources=["main.cpp"])
app.link_private(httplib)  # gets httplib AND openssl includes, libs, flags
```

### Using pkg-config

The `PkgConfigFinder` uses the system's pkg-config to find packages.

```python
from pcons.packages.finders import PkgConfigFinder

# Create finder
finder = PkgConfigFinder()

if finder.is_available():
    # Find a package
    zlib = finder.find("zlib", version=">=1.2")

    if zlib:
        print(f"Found zlib {zlib.version}")
        print(f"Includes: {zlib.include_dirs}")
        print(f"Libraries: {zlib.libraries}")

        # Apply to environment
        env.use(zlib)
```

### Using Conan Packages

The `ConanFinder` integrates with Conan 2.x for package management.

**conanfile.txt:**
```ini
[requires]
fmt/10.1.1

[generators]
PkgConfigDeps
```

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project, get_variant
from pcons.configure.config import Configure
from pcons.packages.finders import ConanFinder

project_dir = Path(__file__).parent
build_dir = project_dir / "build"
variant = get_variant("release")

# Configure and find toolchain
config = Configure(build_dir=build_dir)
toolchain = find_c_toolchain()

# Set up Conan
conan = ConanFinder(
    config,
    conanfile=project_dir / "conanfile.txt",
    output_folder=build_dir / "conan",
)

# Create project and environment
project = Project("conan_example", root_dir=project_dir, build_dir=build_dir)
env = project.Environment(toolchain=toolchain)
env.set_variant(variant)
env.cxx.flags.append("-std=c++17")

# Sync Conan profile with toolchain settings.
# cppstd can be set explicitly, or inferred from env.cxx.flags.
conan.sync_profile(toolchain, env=env, build_type=variant.capitalize())

# Install packages (cached, only runs when needed)
packages = conan.install()

# Get the fmt package
fmt_pkg = packages.get("fmt")
if not fmt_pkg:
    raise RuntimeError("fmt package not found")

# Apply package settings with env.use()
env.use(fmt_pkg)

# Build program
hello = project.Program("hello_fmt", env)
hello.add_sources([project_dir / "src" / "main.cpp"])

project.Default(hello)
```

#### sync_profile() Reference

`conan.sync_profile()` generates a Conan profile from pcons settings:

```python
conan.sync_profile(
    toolchain,  # Detects compiler, version, OS, arch
    env=env,  # Infers cppstd from env.cxx.flags (optional)
    build_type="Release",  # Release, Debug, RelWithDebInfo, MinSizeRel
    cppstd="23",  # Explicit C++ standard (overrides env inference)
)
```

The `cppstd` parameter sets `compiler.cppstd` in the Conan profile, which many packages require. If omitted, it's inferred from `env.cxx.flags` (e.g., `-std=c++23` becomes `compiler.cppstd=23`). You can also use the lower-level `conan.set_profile_setting("compiler.cppstd", "23")` before calling `sync_profile()`.

### The env.use() Helper

The `env.use()` method is the simplest way to apply package settings:

```python
# Apply all settings from a package
env.use(pkg)

# This automatically:
# - Adds include_dirs to cxx.includes
# - Adds defines to cxx.defines
# - Adds library_dirs to link.libdirs
# - Adds libraries to link.libs
# - Adds link_flags to link.flags
```

> If your project uses [rez](https://rez.readthedocs.io), see
> [Integrations → Rez](#rez-vfx-animation-package-manager) for native
> rez-resolve support — `RezFinder` plugs into `find_package()` and
> `rez_environment(env)` injects every resolved package's flags.

---

## Qt Applications

Pcons has first-class Qt 6 support — a Qt Widgets application is a
five-line build script:

```python
from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("myapp")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Widgets"])

app = project.QtProgram(
    "myapp",
    env,
    sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
    link=[qt.Widgets],
)
```

`find_qt()` locates Qt (pkg-config or qtpaths introspection — Linux
distro packages, Homebrew, the official installer, Windows) and handles
the platform quirks: macOS frameworks, MSVC's required flags, Windows
debug library suffixes. `QtProgram` takes `.ui` and `.qrc` files
directly in `sources` and finds `Q_OBJECT` classes automatically; the
scan happens when pcons generates, never during the build, and common
mistakes fail loudly with actionable messages.

Also available: `QtQmlModule` (QML modules with `QML_ELEMENT` C++
types), `QtResources` (embed files from a Python list — no `.qrc`
XML), `QtTranslations` (+ a `ninja lupdate` utility target), `QtDeploy`
(`ninja deploy` via macdeployqt/windeployqt), and the low-level
`env.qt.Moc/Uic/Rcc` builders.

**See the [Qt guide](qt.md)** for the full story: how automoc works,
the staleness guard, generated-file layout, platform notes, and current
limitations. Examples `52_qt_widgets` through `56_qt_deploy` are
working starting points, and the
[CMake porting guide](porting-from-cmake.md) maps each `qt_*` CMake
command to its pcons equivalent.

---

## Integrations

Pcons ships with first-class integrations for tools that aren't build
systems themselves but commonly drive — or are driven by — one. Each
integration lives under `pcons.integrations.<name>`.

### Rez (VFX/animation package manager)

[Rez](https://rez.readthedocs.io) is the dominant package manager in
VFX/animation pipelines. It resolves combinations of tool and library
versions and exposes them to a build via environment variables —
notably `REZ_USED_RESOLVE` (the resolved package list) and
`REZ_<PKG>_ROOT` (each package's install root). Rez is explicit that
it is **not** a build system; it expects the package author to plug in
their own tool. Pcons fits that gap.

Pcons is a *build-time* dependency for rez. Once a pcons-built package
lives in a rez repo, consumers (`rez-env mypackage -- ...`) treat it
like any other rez package — they don't need pcons installed. So
ignore this section if all you do is consume packages.

For the people who *do* care, the docs below are split by role:

| If you are… | …jump to |
| --- | --- |
| **Building an app or library** with pcons that depends on rez packages (Maya, OpenFX, Boost, in-house libs, etc.) | [Consuming rez packages from a pcons project](#consuming-rez-packages-from-a-pcons-project) |
| **Maintaining a rez package** and want `rez-build` to drive pcons as the build engine — same way it drives cmake or make today | [Shipping a rez package built with pcons](#shipping-a-rez-package-built-with-pcons) |
| **Running the rez install at your facility** (pipeline TD, build admin) and need to enable `build_system = "pcons"` for your maintainers | [Installing the pcons plugin into rez](#installing-the-pcons-plugin-into-rez) |

A common case is the first two combined: a studio plugin's
`package.py` is a rez package (it ships through the studio's pipeline)
**and** its source code links against `openfx`, `boost`, etc.
(themselves rez packages). The two halves are independent though, so
we cover them separately.

#### Consuming rez packages from a pcons project

> **Audience:** you have a `pcons-build.py` and your dependencies live
> in a rez repository. You want `-I` and `-L` flags for those deps to
> appear automatically. Your build is launched from inside `rez-env`.

The minimum needed in your `pcons-build.py`:

```python
from pcons import Project
from pcons.integrations.rez import is_in_rez_resolve, rez_environment

project = Project("my_app")
env = project.Environment(toolchain="c")

if is_in_rez_resolve():
    rez_environment(env)  # auto-applies every resolved rez package

app = project.Program("my_app", env, sources=["src/main.cpp"])
project.Default(app)
```

Then run your build inside a rez-env shell that has the deps you need:

```bash
rez-env openfx-1.4 boost-1.82 -- uvx pcons
./build/my_app
```

Inside that shell, `rez_environment(env)` walks every resolved package
and applies a convention-based scan of its install root:

- `<root>/include` → added to `include_dirs`
- `<root>/lib` → added to `library_dirs`
- `lib<name>.{a,dylib,so}` (or `<name>.lib` on Windows) → added to `libraries`
- `<root>/lib/pkgconfig/*.pc` (if present) → defers to `PkgConfigFinder`
  for richer metadata (most well-packaged C/C++ libs ship a `.pc` file)

The `is_in_rez_resolve()` guard means the same `pcons-build.py` works
both inside and outside rez — it just degrades to a vanilla pcons
build if no rez resolve is active.

The resolve is read from rez's Python API when it's importable in the
build interpreter (the resolved context is authoritative); otherwise
pcons parses the documented `REZ_*` environment variables. Either way
no rez install is required for the common standalone case.

##### Picking individual packages

If you only want to apply a subset of the resolve (e.g. you have
host-only build tools you don't want pulled into your link line), pass
a `packages=[...]` whitelist:

```python
rez_environment(env, packages=["openfx", "boost"])
```

##### Packages with a non-standard layout

The convention scan assumes `<root>/include` and `<root>/lib`. A package
that ships its own `.pc` file is handled automatically (it wins over the
scan). For one that does neither — multi-arch lib dirs, nested header
trees, or several libraries — describe it explicitly with a `RezLayout`:

```python
from pcons.integrations.rez import RezLayout, rez_environment

rez_environment(
    env,
    layouts={
        "mylib": RezLayout(
            include_dirs=("include", "include/detail"),
            library_dirs=("lib64",),
            libraries=("mylib_core", "mylib_extra"),
        ),
    },
)
```

A supplied layout is trusted verbatim and wins over both pkg-config and
the convention scan; paths are relative to the package's install root.
Leave `libraries` unset to keep `lib<name>` auto-detection. `RezFinder`
takes the same `layouts` map: `RezFinder({"mylib": RezLayout(...)})`.

##### Per-package access through `find_package()`

For more control — for example, linking `boost` to one target but not
another — register `RezFinder` with pcons's standard finder chain:

```python
from pcons.integrations.rez import RezFinder

project.add_package_finder(RezFinder())
boost = project.find_package("boost")
app.link(boost)  # boost flags propagate as a usage requirement
```

This works exactly like `find_package` does for pkg-config or Conan;
the only difference is the lookup source. Rez has no concept of
"components" — passing `components=[...]` to `find()` emits a warning
and is otherwise ignored.

#### Shipping a rez package built with pcons

> **Audience:** you maintain a rez `package.py` and want `rez-build`
> to invoke pcons. End users (or your CI) will run `rez-build -i` (or
> `rez-release`) and expect pcons to handle configure → build →
> install transparently.

You have **two ways** to wire pcons into a rez package:

##### Option A — quickest: `build_command` in `package.py`

Works out of the box, no plugin install needed. Rez's generic `custom`
build system runs whatever shell command you specify:

```python
# package.py
name = "myplugin"
version = "1.0.0"
requires = ["openfx-1.4", "boost-1.82"]
build_command = "uvx pcons --build-dir {build}"
```

`rez-build` resolves the build environment, sets `REZ_OPENFX_ROOT`
etc., and invokes your command. Your `pcons-build.py` then uses
[`rez_environment(env)`](#consuming-rez-packages-from-a-pcons-project)
to pick up the deps. Good for one-off packages or when you can't
modify the rez install.

##### Option B — native: `build_system = "pcons"`

Once pcons is installed in the same Python environment as rez (your
build admin's responsibility — see [Installing the pcons plugin into
rez](#installing-the-pcons-plugin-into-rez)), it registers a rez
`build_system` plugin via Python entry points. Rez then auto-detects
pcons the same way it auto-detects cmake from a `CMakeLists.txt`.
Declare it explicitly with `build_system = "pcons"`, or rely on
auto-detection from the presence of `pcons-build.py`:

```python
# package.py
name = "myplugin"
version = "1.0.0"
build_system = "pcons"  # explicit; rez also auto-detects
requires = ["openfx-1.4", "boost-1.82"]


def commands():
    env.PATH.append("{root}/bin")
```

Then:

```bash
cd path/to/myplugin
rez-build -i               # configure → ninja → ninja install
rez-env myplugin -- myplugin
```

The pcons plugin runs three phases inside the rez-resolved build env:

1. **Configure** — `pcons generate` (executes your `pcons-build.py`
   and writes `build.ninja`), with `PCONS_BUILD_DIR`,
   `PCONS_INSTALL_DIR`, and `PCONS_GENERATOR` set as env vars.
2. **Build** — `ninja -C <build_path>` (or `make`).
3. **Install** — only when `rez-build -i` (or `rez-release`) is used:
   `ninja -C <build_path> install`. For this to do anything, your
   `pcons-build.py` must declare an `install` alias — see below.

###### Install targets

Rez expects `ninja install` to copy build outputs to
`$PCONS_INSTALL_DIR`. Pcons doesn't auto-create an `install` target;
you wire one up in your `pcons-build.py`:

```python
import os

# ... build app ...
project.Default(app)

install_dir = os.environ.get("PCONS_INSTALL_DIR")
if install_dir:
    install_target = project.Install(f"{install_dir}/bin", [app])
    project.Alias("install", install_target)  # rez-build invokes "install"
```

###### Build options exposed to `rez-build`

The pcons plugin adds two flags to `rez-build`:

```bash
rez-build -- --pcons-generator=ninja --pcons-jobs=8
```

Verify the plugin is registered with rez:

```bash
rez-build --help    # should list "pcons" under -b {make,pcons,...}
```

##### Choosing between Option A and Option B

| Concern | Option A (`build_command`) | Option B (`build_system = "pcons"`) |
| --- | --- | --- |
| Setup | Nothing extra | one-time facility install of pcons into rez's venv ([how](#installing-the-pcons-plugin-into-rez)) |
| Discoverability | Per-package | Site-wide (any pcons-built package "just works") |
| Install support | Hand-rolled | Standard `rez-build -i` |
| CI/CD friction | Low | Low once the plugin is installed once on the build host |
| Right when… | You're trying it out, or the rez install isn't yours to modify | The studio standardizes on it |

A complete worked example — a `hello_lib` package built with rez's
built-in cmake plugin and a `hello_app` package that uses pcons via
`build_system = "pcons"` *and* depends on `hello_lib` through
`rez_environment` — lives in
[`examples/45_rez_integration/`](https://github.com/DarkStarSystems/pcons/tree/main/examples/45_rez_integration).
That example exercises both halves of the integration in one place.

#### Installing the pcons plugin into rez

> **Audience:** you're the pipeline TD or build admin running the rez
> install at your facility. Maintainers want `build_system = "pcons"`
> in their `package.py` files; you make that work.

Pcons registers a rez `build_system` plugin via Python entry points,
so rez discovers it the same way it discovers cmake, make, and any
other plugin: by reading `importlib.metadata` over its bundled Python
environment. The one-time setup is to install pcons into that env.

Assuming rez was installed via its [official
installer](https://rez.readthedocs.io/en/stable/installation.html)
into `/opt/rez`, install pcons with rez's wrapped Python interpreter:

```bash
/opt/rez/bin/rez/rez-python -m pip install pcons
```

`rez-python` is rez's bundled interpreter — installing into it puts
pcons on the same `sys.path` rez uses for plugin discovery. Verify
the plugin is registered:

```bash
rez-build --help    # should list "pcons" under -b {make,pcons,...}
```

After this, every package on every machine using this rez install can
declare `build_system = "pcons"` and have it work without further
setup. To upgrade pcons later, repeat the `pip install` (add `-U`).

##### Troubleshooting

If a maintainer runs `rez-build` on a package whose `package.py`
declares `build_system = "pcons"` and pcons *isn't* installed in
rez's bundled Python env, rez raises `RezPluginError` during argparse
setup — *before* its own error formatter sees it — so they get a
Python traceback ending in:
```
rez.exceptions.RezPluginError: Unrecognised build system plugin: 'pcons'
```

Fix: re-run the `rez-python -m pip install pcons` step above. The
same traceback shape occurs for any unregistered or misspelled
`build_system` value, including built-in ones like `cmake` — it's a
rez quirk, not pcons-specific.

---

## Build Commands

### pcons generate

Generate Ninja build files without building:

```bash
pcons generate                     # Generate build.ninja
pcons generate --variant=debug     # Generate for debug build
pcons generate CC=clang CXX=clang++  # Pass variables
```

### pcons build

Build targets using Ninja:

```bash
pcons build              # Build all default targets
pcons build myapp        # Build specific target
pcons build -j8          # Use 8 parallel jobs
pcons build --verbose    # Show commands being run
```

### Watching for changes

`--watch` builds once and then rebuilds whenever anything in the source tree
changes. It works with the default command and with `pcons build`, and takes
the same targets and options as a normal build:

```bash
pcons --watch            # Build, then rebuild on every change
pcons --watch myapp      # Watch, building only 'myapp'
pcons build --watch -j8
```

Editing the build script counts as a change: ninja re-runs pcons to bring
`build.ninja` up to date before building, so adding a source file or changing a
flag takes effect without restarting the watch. A build that fails does not
stop the watch — the next edit is usually the fix. Press Ctrl-C to stop.

The build directory is never watched (reacting to the build's own output would
loop forever), nor are VCS directories, virtualenvs, tool caches, or editor
scratch files. Anything ninja knows how to build is also left out, wherever it
lands — so a command that generates a file next to its sources, or an in-source
build (`-B .`), does not retrigger the build that wrote it.

Two things a watch reports that an ordinary build does not:

- **A build that did not converge.** If a command never creates the output it
  declares, ninja reruns it on every build and says nothing. After each
  successful build pcons asks ninja whether work remains, and passes on its
  answer:

  ```
  WARNING: the build did not converge: ninja still has work to do right after a
  successful build ... Ninja explains:
  WARNING:     output declared.txt doesn't exist
  ```

- **A rebuild loop.** If several builds in a row are triggered the instant the
  previous one finished, all by the same file, the watch stops and names it —
  that file is written by the build itself, so each build is asking for the
  next. Declare it as an output of the command that writes it, or send it to the
  build directory.

Watching uses the platform's native filesystem notification (inotify, FSEvents,
ReadDirectoryChangesW) through the
[watchfiles](https://pypi.org/project/watchfiles/) package. It installs with
pcons on Linux, macOS and Windows, so `--watch` works out of the box — including
with `uvx pcons --watch`. On any other platform pcons installs without it and
`--watch` says so; ask for it explicitly with `pip install 'pcons[watch]'`,
which builds from source and needs a Rust toolchain.

### pcons (default)

Running `pcons` without a subcommand does both generate and build:

```bash
pcons                    # Generate + Build
pcons --variant=debug    # Generate + Build with variant
pcons FOO=bar            # Pass variables
```

### pcons clean

Clean build artifacts:

```bash
pcons clean        # Run ninja -t clean
pcons clean --all  # Remove entire build directory
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `--variant=NAME` or `-v NAME` | Set build variant (debug, release) |
| `-B DIR` or `--build-dir=DIR` | Set build directory (default: build) |
| `-C` or `--reconfigure` | Force re-run configuration |
| `--fresh` | Discard the persisted cache and start clean |
| `-j N` or `--jobs=N` | Number of parallel build jobs |
| `--watch` | Rebuild whenever a watched file changes (needs `pcons[watch]`) |
| `--verbose` | Show verbose output |
| `--debug` | Show debug output |
| `KEY=value` | Pass build variables |

### Build Variables

Pass variables to your build script:

```bash
pcons PORT=ofx USE_CUDA=1 PREFIX=/usr/local
```

Access them in `pcons-build.py`:

```python
from pathlib import Path

from pcons import get_var

port = get_var("PORT", "ofx")
use_cuda = get_var("USE_CUDA", False)
prefix = get_var("PREFIX", Path("/usr/local"))
```

#### Typed Variables

The default's type selects the conversion, so a variable never has to be parsed
by hand:

```python
use_cuda = get_var("USE_CUDA", False)  # bool
opt_level = get_var("OPT_LEVEL", 2)  # int
scale = get_var("SCALE", 1.0)  # float
port = get_var("PORT", "ofx")  # str
prefix = get_var("PREFIX", Path("/usr/local"))  # Path
```

Pass `type=` when there is no default. The result is `None` when the variable is
unset, which is falsy, so it still reads well in a condition:

```python
if get_var("BUILD_TESTS", type=bool):
    ...
```

A default and a `type=` together raise: the default already picks the
conversion, so the pair is either redundant or a contradiction.

Booleans accept `1`, `on`, `yes`, `true`, `y` and `0`, `off`, `no`, `false`, `n`,
case-insensitive. Any other value raises `ConfigureError` instead of silently
reading as false, so `USE_CUDA=enabled` is reported rather than ignored. `int`
and `float` raise the same way on a value they cannot parse.

A `Path` is taken verbatim, never resolved, so `PREFIX=dist` stays relative and
you decide what it is relative to. An empty value is an error rather than
`Path(".")`.

The default itself is never parsed, it is returned as-is when the variable is
unset. With no default and no `type=`, `get_var` returns the raw string or
`None`.

### Persistent Configuration Cache

Settings you choose on the command line persist per build directory, like
CMake's `CMakeCache.txt`. Configure once, then run bare:

```bash
pcons generate PORT=ofx --variant=debug -G ninja   # choose settings
pcons                                               # reuses PORT, variant, generator
```

What persists: build variables, the variant, and the generator. They are stored
in `<build_dir>/pcons_cache.json` and written only after a successful run.

Precedence, highest to lowest:

1. This run's command line (`PORT=x`, `--variant`, `-G`)
2. Environment: `PORT=x pcons`, `VARIANT`, `GENERATOR`, and the `PCONS_VARS` /
   `PCONS_VARIANT` / `PCONS_GENERATOR` forms
3. Persisted cache from a prior run
4. The `default` passed to `get_var` / `get_variant`

An environment value overrides the cache but is not written to it, so exporting
one steers a run without changing what a later bare run reuses.

The cache is tied to `$PCONS_BUILD_DIR`, which `pcons` always sets (and `-B`
overrides). Running the script directly with `python pcons-build.py` uses no
cache, so the same environment produces the same build either way.

Inspect and reset:

```bash
pcons cache list      # show persisted vars, variant, generator
pcons cache show      # same, plus the cache file path and source dir
pcons cache path      # print the cache file path
pcons cache clear     # empty the cache
pcons generate --fresh PORT=y   # ignore the old cache, start clean
```

Change settings through these commands, not by editing `pcons_cache.json`. The
file is not a regeneration input, so a hand-edit is not picked up automatically,
and the self-regeneration command pins the values it was generated with, so a
manual change would be overwritten on the next run anyway.

Two guards catch stale caches:

- A variable that was persisted but the build script never reads is reported
  (`pcons FEATRUE=on` typo, or a setting you dropped).
- A cache whose recorded source directory no longer matches (a copied or moved
  build dir) is ignored with a warning and rebuilt for the current tree.

There is no API to read or write the cache from a build script; it holds only
the settings above. If you need structured configuration, write a Python config
file and import it from `pcons-build.py`.

---

## Testing

Pcons keeps the build-system / test-runner split clean: build scripts
**declare** tests via `project.Test(...)`, the configure step writes a
JSON manifest (`<build_dir>/tests.json`), and a separate runner —
`pcons test` (or `ninja test`) — executes them. This is the same model
CMake uses with CTest, and it keeps pcons itself out of the business
of running things at build time.

### Declaring Tests

```python
test_prog = project.Program("math_test", env, sources=["src/math.c", "src/test_math.c"])

# Most basic: run the program; pass = exit 0.
project.Test("math.add", test_prog, args=["add"], labels=["unit", "fast"])
project.Test("math.mul", test_prog, args=["mul"], labels=["unit", "fast"])

# should_fail=True inverts the exit code (XFAIL-style assertions).
project.Test(
    "math.expected_failure",
    test_prog,
    args=["bad-input"],
    should_fail=True,
    labels=["xfail"],
)

# disabled=True keeps the test in the manifest but always skips it.
project.Test(
    "math.slow", test_prog, args=["heavy"], labels=["slow"], disabled=True, timeout=60
)
```

The full set of fields (all keyword-only):

| Field | Type | Purpose |
|---|---|---|
| `args` | `Sequence[str]` | Arguments passed after the program. |
| `cwd` | `Path \| str \| None` | Working directory; defaults to the build dir. |
| `env` | `dict[str, str] \| None` | Extra environment variables for the test process. |
| `labels` | `Sequence[str]` | Tags for filtering (`unit`, `integration`, `slow`, `fuzz`, ...). |
| `timeout` | `float \| None` | Seconds before the runner kills the test. |
| `should_fail` | `bool` | If True, a non-zero exit code is a pass. |
| `serial` | `bool` | Don't run in parallel with other tests. |
| `disabled` | `bool` | Record the test but always skip. |
| `data` | `Sequence[Path \| str]` | Runtime data files (informational in v1). |
| `depends_on` | `Sequence[str]` | Names of tests that must pass before this one runs. |
| `discover` | `"gtest" \| "doctest" \| "catch2" \| None` | Expand into one entry per test case in the binary. |

`program` can be a `Target` (the typical case — depending on it ensures
`ninja test-build` compiles it first), or a `Path` / `str` to an
existing script or external binary.

### Fixtures and Test Ordering: `depends_on`

`depends_on` lets one test gate another. If a dependency fails (or
times out, errors, or skips because of its own failed dep), all
dependent tests are reported as skipped — no point running them.

```python
# Fixture-style: start a server, run tests against it, stop it.
start = project.Test("server.start", start_script, labels=["fixture"])
project.Test(
    "api.list_users",
    api_test,
    args=["list-users"],
    depends_on=["server.start"],
    labels=["api"],
)
project.Test(
    "api.create_user",
    api_test,
    args=["create-user"],
    depends_on=["server.start"],
    labels=["api"],
)
project.Test(
    "server.stop", stop_script, depends_on=["server.start"], labels=["fixture"]
)
```

When you filter with `-L` / `-R`, deps of selected tests are
auto-included so fixtures keep working:

```bash
pcons test -L api    # still runs server.start (and server.stop)
```

### Test-Case Discovery: `discover`

For binaries built against GoogleTest, doctest, or Catch2, listing
every test case in the Python build script is tedious. Set
`discover=` and the runner queries the binary at run time, expanding
your single `project.Test()` into one entry per test case:

```python
unit_tests = project.Program("unit_tests", env, sources=[...])
project.Test("unit", unit_tests, discover="gtest", labels=["unit"])
```

Run-time output:
```
Test project: myproject (84 tests)
      Start  1: unit.MathSuite.Add
 1/84 Test #1: unit.MathSuite.Add ..................... Passed  0.01s
      Start  2: unit.MathSuite.Subtract
 2/84 Test #2: unit.MathSuite.Subtract ................ Passed  0.01s
 ...
```

Each case becomes a separate test that:

- Inherits the parent's `labels`, `timeout`, `env`, `cwd`, etc.
- Runs the binary with the framework's single-case filter
  (`--gtest_filter=...`, `--test-case=...`, or a positional name).
- Appears in `--list`, JUnit output, and label/regex filters.

Protocols supported:

| `discover=` | Listing flag | Invocation |
|---|---|---|
| `"gtest"` | `--gtest_list_tests` | `<bin> --gtest_filter=Suite.Case` |
| `"doctest"` | `--list-test-cases --no-version` | `<bin> --test-case="<name>"` |
| `"catch2"` | `--list-test-names-only` | `<bin> "<name>"` |

If discovery fails (binary missing, framework not cooperative), the
runner falls back to running the parent as a single test and prints a
warning. References to the discovered parent in another test's
`depends_on` are rewritten to wait on every child.

### Tweaking Tests After Creation: `set_test_property`

Sometimes a property depends on context that isn't known at the
`project.Test()` call site — a slow test that needs a bigger timeout
under a particular toolchain, a label applied to every test in a
generated batch. `set_test_property()` / `set_test_properties()`
update an unresolved test, mirroring CMake's `set_tests_properties()`:

```python
from pcons import set_test_property, set_test_properties

t = project.Test("slow_one", prog)
set_test_property(t, "timeout", 600)

# Bulk: one call, many tests, many properties.
slow_tests = [t1, t2, t3]
set_test_properties(*slow_tests, timeout=600, labels=["slow"])
```

Valid property names are the same kwargs accepted by `project.Test()`:
`args`, `cwd`, `env`, `labels`, `timeout`, `should_fail`, `serial`,
`disabled`, `data`, `depends_on`, `discover`, `program`.

### Running Tests

```bash
ninja test                    # build, then run, all tests
pcons test                    # same effect, runs the test runner directly
pcons test -L unit            # only run "unit"-labeled tests
pcons test -L unit -LE slow   # unit, excluding slow
pcons test -R '^math\.'       # only run tests whose name matches the regex
pcons test --list             # show what would run, without running
pcons test -j 1               # serial mode (default: CPU count)
pcons test --junit out.xml    # emit JUnit XML for CI
pcons test --stop-on-fail     # stop after the first failure
pcons test -V                 # verbose: show stderr from failed tests
```

The runner picks up the manifest from the current build directory (or
walks upward to find one). Exit code is 0 if every selected test
passed, non-zero otherwise — which is why `ninja test` "just works"
for CI failure detection.

### The Test Manifest

`<build_dir>/tests.json` is plain JSON, versioned, and stable:

```json
{
  "version": 1,
  "project": "myproject",
  "build_dir": "build",
  "tests": [
    {
      "name": "math.add",
      "command": ["test_math", "add"],
      "cwd": null,
      "env": {},
      "labels": ["unit", "fast"],
      "timeout": null,
      "should_fail": false,
      "serial": false,
      "disabled": false,
      "data": [],
      "defined_at": "pcons-build.py:34"
    }
  ]
}
```

You can read it, grep it, and feed it to any other runner you like —
nothing in the format depends on the pcons runtime.

### Fuzzing

Pcons doesn't have a dedicated fuzz-test builder — a fuzz target is just
a fuzzer-instrumented `Program` plus one or two `Test`s. The same shape
works for libFuzzer, AFL++, and Honggfuzz; only the build flags and the
campaign invocation change. See `examples/41_fuzzing/` for a complete
libFuzzer harness; the recipes below show the engine-specific bits.

The recommended pattern is two tests per harness:

- a **regression** test that replays a committed seed corpus (fast,
  deterministic, runs on every commit), and
- a **campaign** test that actually fuzzes for a bounded wall-clock time
  (labelled `["fuzz"]` so `pcons test -L fuzz` runs only campaigns and
  `pcons test -LE fuzz` excludes them).

#### libFuzzer (clang built-in)

Same `LLVMFuzzerTestOneInput` entrypoint; libFuzzer's `main()` is linked
in by `-fsanitize=fuzzer`.

```python
fuzz_flags = ["-fsanitize=fuzzer,address", "-g", "-O1"]
env.cc.flags.extend(fuzz_flags)
env.link.flags.extend(fuzz_flags)

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test("parser.regression", harness, args=["-runs=0", corpus_dir], timeout=30)
project.Test(
    "parser.campaign",
    harness,
    args=[
        "-create_missing_dirs=1",
        "campaign-corpus",
        corpus_dir,
        "-max_total_time=60",
    ],
    labels=["fuzz"],
    timeout=90,
)
```

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    parse_keyvalue(data, size);
    return 0;
}
```

#### AFL++

The same `LLVMFuzzerTestOneInput` entrypoint works in persistent mode.
The compiler driver becomes `afl-clang-fast`; the campaign is run by
`afl-fuzz` rather than the harness binary itself.

```python
# Easiest setup: run pcons with CC=afl-clang-fast (and CXX=afl-clang-fast++).
# Or point a custom toolchain at the AFL++ driver explicitly.

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test(
    "parser.regression",
    "afl-showmap",
    args=[
        "-o",
        "/dev/null",
        "-i",
        corpus_dir,
        "--",
        str(harness.output_nodes[0].path),
        "@@",
    ],
)

project.Test(
    "parser.campaign",
    "afl-fuzz",
    args=[
        "-V",
        "60",
        "-i",
        corpus_dir,
        "-o",
        findings_dir,
        "--",
        str(harness.output_nodes[0].path),
    ],
    labels=["fuzz"],
    timeout=120,
)
```

#### Honggfuzz

Same harness; build with `hfuzz-clang` and link against `libhfuzz.a`. The
campaign is run by the `honggfuzz` binary.

```python
# Easiest setup: CC=hfuzz-clang (and CXX=hfuzz-clang++).

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test(
    "parser.campaign",
    "honggfuzz",
    args=[
        "-i",
        corpus_dir,
        "-o",
        findings_dir,
        "--run_time",
        "60",
        "--",
        str(harness.output_nodes[0].path),
    ],
    labels=["fuzz"],
    timeout=120,
)
```

#### Conventions

- Keep the seed corpus in the source tree (committed) and pass it as an
  absolute path so it resolves correctly from the build directory.
- For libFuzzer's regression mode, pass `-runs=0` before the corpus dir.
  Without it, libFuzzer treats the directory as a live corpus and keeps
  fuzzing instead of exiting.
- For the campaign, give libFuzzer two corpus paths — a build-relative
  output dir first (writable; new findings go there) and the seed corpus
  second (treated read-only). Without this split, the campaign writes
  every new-coverage input back into the seed corpus and pollutes the
  source tree.
- Label fuzz tests `["fuzz"]` so users can filter them on or off.
- Pair a fast regression test with a slower campaign — CI on every
  commit runs the regression; nightly CI runs the campaign with a real
  budget (`-max_total_time=600`, `-V 600`, `--run_time 600`).

#### Platform notes

- **Linux**: works out of the box with a modern clang.
- **macOS**: Apple's Xcode clang does not ship libFuzzer; install
  Homebrew LLVM (`brew install llvm`) and use that clang. The
  `examples/41_fuzzing/pcons-build.py` script also adds an explicit
  `-L<llvm>/lib/c++` to the link line, because Homebrew's libFuzzer
  archive references libc++ symbols that resolve only against
  Homebrew's libc++ — not Apple's SDK libc++.
- **Windows**: clang-cl supports `-fsanitize=fuzzer,address`, but the
  CRT and ASan DLL setup is more involved than what fits in a small
  example.

---

## Advanced Topics

### Supported Source File Types

Pcons toolchains support various source file types beyond standard C/C++:

| Extension | Description | Toolchains |
|-----------|-------------|------------|
| `.c` | C source | All |
| `.cpp`, `.cxx`, `.cc` | C++ source | All |
| `.cppm`, `.ixx`, `.cxxm`, `.c++m` | C++20 module interface unit | LLVM, MSVC |
| `.m` | Objective-C | LLVM |
| `.mm` | Objective-C++ | LLVM |
| `.s` | Assembly (preprocessed) | GCC, LLVM |
| `.S` | Assembly (needs C preprocessor) | GCC, LLVM |
| `.asm` | MASM assembly | MSVC, Clang-CL |
| `.rc` | Windows resource | MSVC, Clang-CL |
| `.metal` | Metal shaders (macOS) | LLVM |

#### Metal shaders (macOS)

`project.MetalLibrary` is the whole pipeline — each `.metal` source compiles to an `.air`, and the `.air` files link into the single `.metallib` an application loads at runtime:

```python
shaders = project.MetalLibrary(
    "effects", env, sources=["src/blur.metal", "src/warp.metal"]
)
project.Default(shaders)
```

It returns a `Target`, so the library can be a default target, an alias member, or something to `Install`, exactly like a program or a shared library. The output is named verbatim (`effects.metallib`) — no `lib` prefix, since shaders are looked up by name at runtime.

`env.metal.Object` and `env.metal.Library` drive the two steps separately and return nodes, like every tool-namespace builder. Use those only when an intermediate `.air` is wanted for its own sake. See `examples/62_metal_library`.

#### C++20 modules

When a target has at least one source whose extension is in
`{.cppm, .ixx, .cxxm, .c++m}`, pcons runs the C++ module scanner
(`cl /scanDependencies` for MSVC, `clang-scan-deps` for LLVM/Clang) on
every C++ TU in that target at configure time, and uses the P1689R5
output to inject the right compile flags (`/interface` vs
`/internalPartition` on MSVC, `-fmodule-output` and `-x c++-module` on
clang) and to produce the Ninja `dyndep` file that orders compilations.
Partition units that live in `.cpp` files (interface partitions like
`export module M:P;` or internal partitions like `module M:P;`) are
detected from the scan output and handled correctly.

If your project has *no* sources with one of those extensions but still
uses C++ modules — e.g. fmtlib's `src/fmt.cc` (primary interface in
`.cc`), or a target whose only module use is `import std;` — opt in
explicitly:

```python
env = project.Environment(toolchain="msvc")
env.cxx.modules = True
env.cxx.flags.extend(["/std:c++latest", "/EHsc"])
project.Program("hello", env, sources=["main.cpp"])  # main.cpp does `import std;`
```

`import std;` and `import std.compat;` work out of the box on MSVC:
pcons synthesizes a build node for `%VCToolsInstallDir%/modules/std.ixx`
(or `std.compat.ixx`), wires its `.ifc` into the dyndep file, and adds
the resulting `.obj` to every importing target's link inputs.

Compiled module interfaces (BMIs — `.gcm` / `.pcm` / `.ifc`) are only
consumable by translation units built with matching BMI-sensitive flags
(C++ dialect, ABI options, stdlib feature macros). pcons keys each BMI by
a hash of those flags and stores it under
`<build_dir>/cxx_modules/<hash>/`, so targets that compile a module
interface with compatible flags share one BMI, while targets using an
incompatible dialect (say `-std=c++23` vs `-std=c++26`) transparently get
their own. See `examples/39_bmi_compat`.

These are handled automatically when you add sources to a target:

```python
# C/C++ sources
app.add_sources(["main.cpp", "util.c"])

# Windows resources (icons, dialogs, version info)
app.add_sources(["app.rc"])

# Assembly
lib.add_sources(["fast_math.S"])  # Uses C preprocessor
lib.add_sources(["startup.s"])  # Raw assembly
```

### Custom Builders

Create custom tools for specialized build steps:

```python
from pcons.core.builder import CommandBuilder
from pcons.tools.tool import BaseTool


class ProtobufTool(BaseTool):
    def __init__(self) -> None:
        super().__init__("protoc")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "protoc",
            "protocmd": "$protoc.cmd --cpp_out=$$outdir $$in",
        }

    def builders(self) -> dict[str, object]:
        return {
            "Compile": CommandBuilder(
                "Compile",
                "protoc",
                "protocmd",
                src_suffixes=[".proto"],
                target_suffixes=[".pb.cc", ".pb.h"],
                single_source=True,
            ),
        }


# Use the tool
protoc_tool = ProtobufTool()
protoc_tool.setup(env)
env.protoc.Compile("build/message.pb.cc", "proto/message.proto")
```

### Subdirectories and Composable Libraries

`add_subdirectory()` runs another directory's `pcons-build.py` as part of the
current build, and every name assigned at module scope in that script comes back
as an attribute:

```python
libfoo = add_subdirectory("libfoo")
app.link(libfoo.libfoo)
```

The point of this is that a library builds either way — on its own during
development, and pulled into a larger tree when something depends on it. Write
the script the natural way and it works in both:

```python
project = Project("libfoo")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.default_environment  # the enclosing build's toolchain

config = configure_file("config.h.in", project.build_dir / "config.h", vars)
lib = project.StaticLibrary("foo", env, sources=["src/foo.c"])
lib.public.include_dirs.append(project.build_dir)
```

`project.root_dir` and `project.build_dir` always mean *this* project's source
directory and *this* project's build output, wherever it sits. Built directly,
`build_dir` is `build/`; embedded one level down, it is `build/libfoo/`. Nothing
in the script has to know which. The same holds several levels deep, and sibling
subdirectories stay in separate build directories.

Two things are worth knowing:

- The subdirectory must live under the top-level project. Pointing
  `add_subdirectory()` at a sibling checkout elsewhere on disk is an error.
- Only the environment needs the `is_top_level` branch, because a standalone
  build has no parent to take a toolchain from. `default_environment` searches
  enclosing projects, so a library nested several levels down still finds it.

To keep a library working both ways, list it in its example's `test.toml` and
the test suite will build it standalone as well as embedded:

```toml
[test]
standalone_subdirs = ["libfoo", "libfoo/libbar"]
```

See `examples/13_subdirs` for a worked example, including a library nested two
levels down.

### Multi-Platform Builds

Handle platform differences in your build script:

```python
import sys

env = project.Environment(toolchain="c")

# Add platform-specific flags
if sys.platform == "darwin":
    env.link.flags.append("-framework CoreFoundation")
elif sys.platform == "linux":
    env.link.libs.extend(["pthread", "dl"])
elif sys.platform == "win32":
    env.cxx.defines.append("WIN32")

# Add toolchain-specific warning flags
# clang-cl and msvc use MSVC-style flags (/W4)
# gcc and llvm use GCC-style flags (-Wall)
if toolchain.name in ("msvc", "clang-cl"):
    env.cxx.flags.append("/W4")
else:
    env.cxx.flags.extend(["-Wall", "-Wextra"])
```

### Windows: MSVC Without Visual Studio (msvcup)

On Windows, `find_c_toolchain()` normally discovers the MSVC compiler from an installed Visual Studio. If you don't want to install all of Visual Studio with C++ workloads and Windows SDKs — or if you need a reproducible, locked compiler version — you can use **msvcup** to download just the MSVC compiler and Windows SDK directly from Microsoft's CDN.

The `pcons.contrib.windows.msvcup` module wraps the [msvcup](https://github.com/marler8997/msvcup) tool. Call `ensure_msvc()` at the top of your build script, before `find_c_toolchain()`:

```python
import sys
from pcons import Project

if sys.platform == "win32":
    from pcons.contrib.windows.msvcup import ensure_msvc

    ensure_msvc("14.44.17.14", "10.0.22621.7")

project = Project("hello", build_dir="build")
env = project.Environment(toolchain="c")
project.Program("hello", env, sources=["hello.c"])
```

On the first run, `ensure_msvc()`:

1. Downloads `msvcup.exe` from GitHub releases (auto-detects x64 vs arm64)
2. Runs `msvcup install` to download the specified MSVC and SDK versions
3. Runs `msvcup autoenv` to create wrapper executables (`cl.exe`, `link.exe`, etc.)
4. Prepends the autoenv directory to `PATH`

Subsequent runs are fast — msvcup detects the toolchain is already installed and skips the download. Everything installs to `C:\msvcup`.

On non-Windows platforms, `ensure_msvc()` is a no-op (returns immediately).

#### Version Pinning

The MSVC version (e.g., `"14.44.17.14"`) and SDK version (e.g., `"10.0.22621.7"`) are explicit — every developer and CI machine gets the exact same compiler. To find available versions, run:
```
msvcup list
```

#### Lock Files

By default, `ensure_msvc()` writes a lock file to `C:\msvcup\msvcup.lock` for reproducible installs. You can specify a project-local lock file:

```python
ensure_msvc("14.44.17.14", "10.0.22621.7", lock_file="msvcup.lock")
```

#### Cross-Compilation

The target CPU is auto-detected from the host architecture (x64 on x86_64 machines, arm64 on ARM64). For cross-compilation, specify it explicitly:

```python
ensure_msvc("14.44.17.14", "10.0.22621.7", target_cpu="arm64")
```

#### CI Usage

msvcup is particularly useful in CI environments where you want reproducible builds without depending on whatever Visual Studio version happens to be pre-installed on the runner. See `examples/21_msvcup_hello/` for a complete working example.

### IDE Integration

Build generators (Ninja, Makefile, Xcode) automatically generate `compile_commands.json` alongside build files. A symlink is also created at the project root so tools find it automatically. No extra code is needed.

To disable generation entirely, or to keep everything inside the build
directory (no project-root symlink), generate explicitly:

```python
from pcons import Generator

Generator().generate(project, compile_commands=False)  # no compile_commands.json
Generator().generate(project, root_symlink=False)  # no root symlink
```

With multiple build configurations in one project root, the last generation
to run owns the root symlink.

This enables features in:
- **VS Code** with clangd extension
- **CLion** and other JetBrains IDEs
- **Vim/Neovim** with coc-clangd
- **Emacs** with eglot or lsp-mode

### Alternative Generators

While Ninja is the default and recommended build executor, pcons also supports generating Makefiles for environments where Ninja isn't available.

#### Makefile generator

Generate a traditional Makefile instead of Ninja build files — no script changes needed:

```bash
pcons -G make          # generates build/Makefile (and builds with make)
```

Or pin it in the build script, e.g. for a project that always uses make:

```python
from pcons import Generator

Generator("make").generate(project)  # creates build/Makefile
```

Then build with:

```bash
make -C build
```

The Makefile generator supports the same project structure as the Ninja generator, so you can switch between them without changing your build script.

### Dependency Visualization

Generate dependency graphs:

```python
from pcons.generators.mermaid import MermaidGenerator

# Generate Mermaid diagram
MermaidGenerator().generate(project)
# Creates build/deps.mmd
```

Or from the command line:

```bash
pcons generate --mermaid=deps.mmd    # To file
pcons generate --mermaid             # To stdout
pcons generate --graph=deps.dot      # DOT format
```

### Installing Files

Copy files to destination directories. Relative destinations are placed under
the **install prefix**, which defaults to `<project-root>/dist` and can be
overridden with the `PCONS_INSTALL_PREFIX` variable:

```bash
pcons PCONS_INSTALL_PREFIX=/usr/local
```

Absolute (rooted) destinations are used as-is. Pass `no_prefix=True` to keep a
relative destination inside the build directory instead (useful for staging).

```python
# Install library and headers (Install takes a list of sources)
project.Install("lib", [mylib])  # -> <prefix>/lib/
project.Install("include", header_nodes)  # -> <prefix>/include/

# Install with rename (InstallAs takes a single source, not a list)
project.InstallAs("bundle/plugin.ofx", plugin_lib)

# Install an entire directory tree (recursive copy)
# Copies src_dir/assets/* to <prefix>/assets/*
project.InstallDir(".", src_dir / "assets")
```

The `install_dir()` helper returns the conventional install subdirectory for a
target type, following the conventions of the platform the environment's
toolchain targets (`bin` for programs, `lib` for libraries — except DLLs, which
go in `bin` next to the executables that load them):

```python
from pcons import install_dir

exe = project.Program("hello", env, sources=["src/hello.c"])
project.Install(install_dir(env, "program"), [exe])  # -> <prefix>/bin/
```

**Note:** `Install()` accepts a list of sources and copies each to the destination directory. `InstallAs()` takes exactly one source and copies it to the specified path (with optional rename). If you need to install multiple files with renaming, use multiple `InstallAs()` calls.

`InstallDir` uses ninja's depfile mechanism for incremental rebuilds - if any file in the source directory changes, the copy is re-run.

### Generating pkg-config Files

To make a pcons-built library consumable by downstream CMake or pkg-config projects, generate a `.pc` file:

```python
lib = project.StaticLibrary("mylib", env, sources=["src/mylib.c"])
lib.public.include_dirs.append("include")

pc = project.generate_pc_file(lib, version="1.0.0", description="My library")
project.Install("lib/pkgconfig", [pc])
```

The `.pc` file is derived from the target's public usage requirements (include_dirs, defines, link_libs, link_flags). Dependencies that were found via pkg-config automatically become `Requires:` entries rather than inlined flags.

### Environment Cloning

Create variant environments by cloning:

```python
# Base environment
env = project.Environment(toolchain="c")

# Clone for profiling - gets a COPY of all settings
profile_env = env.clone()
profile_env.cxx.flags.extend(["-pg", "-fno-omit-frame-pointer"])

# Build both variants
app_release = project.Program("app", env)
app_profile = project.Program("app_profile", profile_env)
```

**Key points about environments:**

- Each `project.Environment()` call creates a fresh environment with toolchain defaults
- `env.clone()` creates a deep copy - changes to the clone don't affect the original
- Environments don't share state - there's no "base" environment that accumulates
- You can clone at any point and re-tune the clone: `set_variant()` (and other
  exclusive presets) *replace* the previous setting, so
  `debug_env = release_env.clone(); debug_env.set_variant("debug")` works
- If you see duplicate flags, check if you're accidentally adding flags multiple times in your script

### Temporary Environment Overrides

`env.override()` yields a temporary clone of the environment; the original is untouched. **Modify the clone** — it's an ordinary `Environment`, so a flag list is an ordinary Python list and every operation is just Python:

```python
with env.override() as tuned:
    tuned.cxx.flags.append("-O1")  # add
    tuned.cxx.flags.remove("-Werror")  # remove one
    tuned.cxx.flags = [
        f
        for f in tuned.cxx.flags  # remove by pattern
        if not f.startswith("-W")
    ]
    tuned.cxx.flags.insert(0, "-fno-strict-aliasing")  # order matters
    tuned.cxx.flags = ["-O1"]  # replace outright

    project.Library("mylib", tuned, sources=["lib.cpp"])
```

Keyword arguments are a shorthand that **assigns**, so they are for scalars:

```python
with env.override(variant="debug", cc__cmd="clang") as temp_env:
    project.Program("app_debug", temp_env, sources=["main.cpp"])
```

Tool settings use `tool__attr` notation because Python keywords can't contain a dot.

`override()` is `clone()` plus a scope, and the block isn't required — it just saves the assignment and shows where the modified environment applies. When the modified environment outlives one stretch of the script, keep a clone instead:

```python
careful = env.clone()
careful.cc.flags.remove("-O2")
careful.cc.flags.append("-O1")

lib.add_sources(["cuda-support.cxx"], env=careful)
lib.add_sources(["other-touchy.cxx"], env=careful)
```

!!! warning "Keyword arguments don't take lists"
    `env.override(cxx__flags=["-O1"])` raises. It can only mean "assign", but at a call site it *reads* as "add `-O1`" — and assigning would silently discard every flag the environment already carried (`-std=c++17`, the warning set, `-isystem` paths). Since which of add / remove / reorder / replace you meant can't be inferred, say it in the block:

    ```python
    with env.override() as tuned:
        tuned.cxx.flags.append("-O1")  # or .remove(...), or = [...]
    ```

    The error message names the flags the call would have dropped and shows each form.

#### Per-File Flags

To compile *one file* in a target differently, pass the environment along with the source:

```python
lib = project.StaticLibrary("core", env, sources=common_sources)

with env.override() as careful:
    careful.cxx.flags.append("-O1")  # this file miscompiles at -O2
    lib.add_sources(["cuda-support.cxx"], env=careful)
```

The file stays part of the target, so it keeps the target's include dirs, defines, and everything inherited from its dependencies — only the environment layer changes. That's the difference from declaring a second one-file target, which starts from nothing and has to re-state all of it.

`env.cc.Object()` (see `examples/17_object_sources`) remains the tool for a different job: compiling a standalone object that several targets can link without recompiling. It sits outside any target, so no target's usage requirements apply to it.

### Custom Commands with env.Command()

Use `env.Command()` to run arbitrary shell commands as build steps. This is useful for code generators, asset processing, or any tool that doesn't fit the standard compile/link model.

```python
# Generate a header from a template
env.Command(
    "config.h",  # Target file(s)
    ["config.h.in", "version.txt"],  # Source file(s)
    "python generate_config.py $SOURCES > $TARGET",
)

# Run a code generator with multiple outputs
env.Command(
    ["parser.c", "parser.h"],  # Multiple targets
    "grammar.y",  # Single source
    "bison -d -o ${TARGETS[0]} $SOURCE",
)

# Command with no source dependencies
env.Command(
    "timestamp.txt",
    None,  # No sources
    "date > $TARGET",
)
```

**Variable substitution:**

| Variable | Description |
|----------|-------------|
| `$SOURCE`, `$SOURCES` | All source files (space-separated) — the two spellings mean the same thing; use `${SOURCES[0]}` for the first one |
| `$TARGET`, `$TARGETS` | All target files (space-separated) |
| `${SOURCES[n]}` | Indexed source access (0-based) |
| `${TARGETS[n]}` | Indexed target access (0-based) |
| `${SOURCES[n:m]}` | A range of sources — either end may be omitted |
| `${TARGETS[n:m]}` | A range of targets |
| `$SRCDIR` | Project source tree root directory |
| `$$` | Literal `$` (escaped) |

Anything else inside `${...}` is an error. An unrecognized form would otherwise reach `build.ninja` as a shell-escaped literal and run as nonsense.

**Sources keep the order you wrote them in.** `${SOURCES[0]}` is the first source declared, whether or not it's another target's output:

```python
# ${SOURCES[0]} is the tool; ${SOURCES[1:]} is however many .def files there are
env.Command(
    target=gen_dir / "entries.c",
    source=[collate_tool, *def_files],
    command="./${SOURCES[0]} $TARGET ${SOURCES[1:]}",
)
```

**A substitution can be part of an argument** rather than all of it — the text around it comes along:

```python
command = "./${SOURCES[0]} --out=$TARGET -i${SOURCES[1:]}"
```

The `./` above is not decoration: `${SOURCES[0]}` expands to a plain build-directory name like `collate`, and a POSIX shell reads a bare name as something to look up on `$PATH`, where it will not find it. (`cmd.exe` searches the current directory instead, and does not take `./`, so a build script that runs a built tool should pick the prefix per platform — see `examples/61_command_substitution`.)

Text attached to a form that expands to *several* paths repeats on each of them, which is what such a flag always means: `-i${SOURCES[1:]}` becomes `-ione.def -itwo.def`, not one `-i` welded to the first path.

A slice is the right tool when the input count is a property of the project rather than of the rule — adding a `.def` file above changes nothing in the build script. See `examples/59_codegen_sources`, which also shows why a glob needs `project.add_configure_dependency()` on the directory it read.

Use `$SRCDIR` to reference files in the source tree that aren't listed as sources. Since the build runs from the build directory, relative paths to source-tree files won't resolve correctly without this:

```python
# Run a source-tree script that isn't a build dependency
env.Command(
    target="generated.h",
    source="schema.json",
    command="python $SRCDIR/tools/codegen.py $SOURCE -o $TARGET",
)
```

This is the one place in pcons where paths are not relative to the project root — `sources=` and `target=` are, a command's are not — so it is worth stating plainly: a *relative* path inside a command is looked for under the build directory. `"tools/gen.pl"` will not be found. Write `$SRCDIR/tools/gen.pl`, or pass an absolute path (pcons rewrites those to `$topdir/...` so the build file stays relocatable), or move the whole command with `cwd=` below.

pcons warns when a command token names a path under the build directory (`-Wl,build/libfoo.dylib`), since `project.build_dir` is relative to the project *root* and the command runs *in* the build directory — so that path resolves to `build/build/...`. Set `PCONS_WARN_BUILD_DIR_PATHS=0` on the occasion the path really is right as written.

**Don't quote tokens yourself.** pcons keeps a command as a list of tokens and quotes each one for the shell it is writing for, so `command=f'"{tool}" $SOURCE'` reaches the program with the quotes still attached and it reports that no such file exists. Write it bare; a token that must contain a space goes in the list form, which isn't split on whitespace. pcons raises on a token that *starts* with a quote — a trailing one is ordinary, since `-DNAME="value"` wants its quotes delivered. When the quotes really are meant, say so with `Verbatim`:

```python
from pcons import Verbatim

env.Command(
    target="counts.txt",
    source="log.txt",
    command=["awk", Verbatim("'{print $1}'"), "$SOURCE", ">", "$TARGET"],
)
```

**Running somewhere else: `cwd=`**

Build tools run from the build directory, and pcons writes every path in a command relative to it. Some tools can't live with that — they open an input by a path relative to the source root, or write beside their inputs. `cwd=` moves the command, and moves its paths with it: `$SOURCE`, `$TARGET` and `$SRCDIR` all come out relative to the directory you named, so nothing else in the rule changes. A relative `cwd` is taken from the project root.

```python
# The tool finds its input at "data/items.txt" -- relative to the source root
env.Command(
    target=gen_dir / "items.c",
    source=[make_items],
    depends=["data/items.txt"],
    command="$SOURCE $TARGET",
    cwd=project.root_dir,
)
```

Paths stay relative wherever they can, so `build.ninja` remains as relocatable as it was; only a directory no relative path can reach (another Windows drive) forces an absolute one. Makefiles already spell their source paths absolutely, so a moved command there is absolute throughout.

Don't write the `cd` into the command yourself. It looks equivalent and isn't: pcons wraps your command with steps of its own — `post_build()` commands, and the `write_if_different` stash below — that run in the build directory and name their files relative to it. A one-way `cd` strands them. `cwd=` changes back; a hand-written `cd` now fails the build rather than quietly costing you a rebuild. See `examples/60_command_cwd`.

**Extra dependencies with `depends=`:** Files listed in `depends=` trigger a rebuild when they change, but don't appear in `$SOURCE`/`$SOURCES`. Use this for scripts, config files, or other build-time inputs:

```python
# Rebuild when the codegen script or its config changes
env.Command(
    target="generated.h",
    source="schema.json",
    command="python $SRCDIR/tools/codegen.py $SOURCE -o $TARGET",
    depends=["tools/codegen.py", "tools/codegen.cfg"],
)
```

You can also add dependencies to any target after creation using `target.depends()`:

```python
app = project.Program("app", ["main.c"])
app.depends("version.txt")  # Rebuild when version.txt changes
```

Use `$$` for a literal dollar sign. pcons delivers it to the command *verbatim*: it is quoted and escaped so that neither ninja, nor make, nor the shell gets to interpret it. That is what tools that have their own use for a dollar need — the ELF dynamic linker, `awk`, `sed`:

```python
# Set rpath to $ORIGIN for portable shared libraries
env.link.flags.append("-Wl,-rpath,$$ORIGIN")

# The program, not the shell, sees the dollar
env.Command(
    target="rev.txt",
    source="in.txt",
    command="stamper --keyword=$$Revision$$ --out=$TARGET $SOURCE",
)
```

So `$$HOME` is *not* a shell variable reference — it is the five characters `$HOME`. Build scripts are Python, so read environment variables there, at configure time, where the value is visible to pcons and recorded in the build files:

```python
import os

env.Command(
    target="output.txt",
    source="input.txt",
    command=f"pack --home={os.environ['HOME']} $SOURCE $TARGET",
)
```

The command runs during the build phase, and Ninja tracks dependencies so the command only re-runs when sources change.

**Multiple commands:** Chain commands with shell operators:

```python
# Run multiple steps with && (stops on first failure)
env.Command(
    target="output.txt",
    source="input.txt",
    command="step1 $SOURCE -o temp.txt && step2 temp.txt -o $TARGET",
)
```

**Generators that rewrite everything: `write_if_different=True`**

Ninja's `restat` skips downstream work when a command's output didn't actually change — but only if the generator leaves unchanged files alone, and most generators rewrite every output on every run. `write_if_different=True` fixes that without the generator's cooperation: pcons stashes the outputs, runs the command, and restores any output that came back byte-identical, timestamp included. It implies `restat=True`.

```python
env.Command(
    target=[gen_dir / f"S_{name}.c" for name in names],
    source=[manifest],
    command=f"{python} $SRCDIR/tools/gen.py $SOURCE",
    write_if_different=True,  # one changed input != recompile everything
)
```

Without it, adding one entry to a 280-plugin manifest recompiles all 280. With it, only the new one. See `examples/57_staged_generation`.

The two halves of that stash have to run in the same directory, so a command that changes directory and doesn't change back fails the build with an explanation, rather than restoring nothing and exiting 0. Use `cwd=` (above) instead of a bare `cd`.

### Post-Build Commands

Add commands that run after a target is built using `target.post_build()`:

```python
plugin = project.SharedLibrary("myplugin", env, sources=["plugin.cpp"])

# Add rpath for macOS plugin loading
plugin.post_build("install_name_tool -add_rpath @loader_path $out")

# Code sign the output
plugin.post_build("codesign --sign - $out")
```

**Variable substitution in post_build:**

| Variable | Description |
|----------|-------------|
| `$out` | The primary output file path |
| `$in` | The input files (space-separated) |

Commands run in the order they are added. The fluent API allows chaining:

```python
plugin.post_build("cmd1 $out").post_build("cmd2 $out")
```

`target.pre_build()` is the mirror image, for commands that must run before the target's own command, with the same `$out`/`$in` substitutions.

### Archive Builders (Tarfile and Zipfile)

Pcons provides built-in builders for creating tar and zip archives. These are useful for packaging releases, bundling documentation, or creating distributable artifacts.

#### Creating Tar Archives

Use `project.Tarfile()` to create tar archives with optional compression:

```python
# Create a gzipped tarball (compression inferred from extension)
docs_archive = project.Tarfile(
    env,
    output="dist/docs.tar.gz",
    sources=["docs/", "README.md", "LICENSE"],
)

# Create a bz2-compressed tarball
backup = project.Tarfile(
    env,
    output="dist/backup.tar.bz2",
    sources=["data/"],
)

# Create an xz-compressed tarball
release = project.Tarfile(
    env,
    output="dist/release.tar.xz",
    sources=["bin/", "lib/"],
)

# Create an uncompressed tarball
raw = project.Tarfile(
    env,
    output="dist/raw.tar",
    sources=["files/"],
)
```

**Compression options:**
| Extension | Compression |
|-----------|-------------|
| `.tar.gz`, `.tgz` | gzip |
| `.tar.bz2` | bz2 |
| `.tar.xz` | xz |
| `.tar` | None (uncompressed) |

You can also specify compression explicitly:

```python
# Override inferred compression
archive = project.Tarfile(
    env,
    output="dist/archive.tar.gz",
    sources=["files/"],
    compression="bz2",  # Use bz2 despite .tar.gz extension
)
```

#### Creating Zip Archives

Use `project.Zipfile()` to create zip archives:

```python
# Create a zip archive
release_zip = project.Zipfile(
    env,
    output="dist/release.zip",
    sources=["bin/myapp", "lib/libcore.so", "README.md"],
)
```

#### Common Options

Both archive builders support:

- **`output`**: Path to the output archive file
- **`sources`**: List of files, directories, or Targets to include
- **`base_dir`**: Base directory for computing archive paths (default: ".")
- **`name`**: Optional target name for `ninja <name>` (default: derived from output path)

```python
# Custom base_dir to strip source paths
# Files in "build/release/bin/" become just "bin/" in the archive
archive = project.Tarfile(
    env,
    output="dist/package.tar.gz",
    sources=["build/release/bin/", "build/release/lib/"],
    base_dir="build/release",
)

# Custom target name
archive = project.Tarfile(
    env,
    output="dist/docs.tar.gz",
    sources=["docs/"],
    name="package_docs",  # Run with: ninja package_docs
)
```

#### Using Archives with Install

Since archive builders return `Target` objects, you can pass them to `Install()`:

```python
# Create archives
docs_tar = project.Tarfile(env, output="build/docs.tar.gz", sources=["docs/"])
release_zip = project.Zipfile(env, output="build/release.zip", sources=["bin/", "lib/"])

# Install archives to a packages directory
project.Install("packages/", [docs_tar, release_zip])

# Set archives as default build targets
project.Default(docs_tar, release_zip)
```

For a complete example, see `examples/06_archive_install/pcons-build.py` which creates source and binary tarballs with an `install` alias:

```bash
cd examples/06_archive_install
python pcons-build.py
ninja -f build/build.ninja          # Build the program
ninja -f build/build.ninja install  # Create and install tarballs to ./Installers
```

### Platform Installers

Pcons includes helpers for creating native installers on macOS and Windows. These live in `pcons.contrib.installers` and integrate into the build graph just like any other target — Ninja handles incremental rebuilds automatically.

#### macOS: `.pkg` Installers

Create standard macOS installer packages using `pkgbuild` and `productbuild` (requires Xcode Command Line Tools).

**Simple component package** (wraps `pkgbuild`):

```python
from pcons.contrib.installers import macos

pkg = macos.create_component_pkg(
    project,
    env,
    identifier="com.example.myapp",
    version="1.0.0",
    sources=[app],
    install_location="/usr/local/bin",
)
```

**Full-featured installer** with welcome screen, license, and branding (wraps `productbuild`):

```python
pkg = macos.create_pkg(
    project,
    env,
    name="MyApp",
    version="1.0.0",
    identifier="com.example.myapp",
    sources=[app],
    install_location="/usr/local/bin",
    min_os_version="10.13",
    welcome=Path("installer/welcome.rtf"),
    license=Path("LICENSE.rtf"),
    readme=Path("installer/readme.html"),
)
```

**Key `create_pkg()` parameters:**

| Parameter | Description |
|-----------|-------------|
| `name` | Application/package name |
| `version` | Package version string |
| `identifier` | Bundle identifier (e.g., `"com.example.myapp"`) |
| `sources` | List of Targets, FileNodes, or paths to package |
| `install_location` | Where files are installed (default: `"/Applications"`) |
| `min_os_version` | Minimum macOS version (e.g., `"10.13"`) |
| `welcome`, `readme`, `license`, `conclusion` | Installer UI pages (`.rtf` or `.html`) |
| `background` | Background image for the installer |
| `scripts_dir` | Directory with `preinstall`/`postinstall` scripts |
| `sign_identity` | Code signing identity |

#### macOS: `.dmg` Disk Images

Create compressed disk images with `hdiutil`:

```python
dmg = macos.create_dmg(
    project,
    env,
    name="MyApp",
    sources=[app],
    applications_symlink=True,  # Add /Applications symlink for drag-install
)
```

| Parameter | Description |
|-----------|-------------|
| `name` | Application name (used as volume name) |
| `sources` | Files to include in the disk image |
| `volume_name` | Custom volume name (defaults to `name`) |
| `format` | `"UDZO"` (zlib, default), `"UDBZ"` (bzip2), `"ULFO"` (lzfse), `"UDRO"` (uncompressed) |
| `applications_symlink` | Add `/Applications` symlink for drag-and-drop install (default: `True`) |

#### macOS: Signing and Notarization

Helper functions return commands you can use with `env.Command()` or run externally:

```python
# Sign with Developer ID
sign_cmd = macos.sign_pkg(
    Path("build/MyApp-1.0.0.pkg"),
    identity="Developer ID Installer: My Company",
)

# Notarize for distribution
notarize_cmd = macos.notarize_cmd(
    Path("build/MyApp-1.0.0.pkg"),
    apple_id="dev@example.com",
    team_id="TEAM123",
    password_keychain_item="notarize-profile",
)
```

#### Windows: `.msix` Packages

Create modern Windows MSIX packages using `MakeAppx.exe` (requires Windows SDK):

```python
from pcons.contrib.installers import windows

msix = windows.create_msix(
    project,
    env,
    name="MyApp",
    version="1.0.0.0",
    publisher="CN=Example Corp",
    sources=[app],
    display_name="My Application",
    description="A great application",
    executable="myapp.exe",
)
```

| Parameter | Description |
|-----------|-------------|
| `name` | Package name (alphanumeric, no spaces) |
| `version` | Version in `X.Y.Z.W` format |
| `publisher` | Publisher identity (e.g., `"CN=Example Corp"`) |
| `sources` | Files to package |
| `executable` | Main executable name (defaults to first source) |
| `display_name` | User-visible name |
| `description` | Package description |
| `processor_architecture` | `"x64"`, `"x86"`, or `"arm64"` (default: `"x64"`) |
| `sign_cert` | Path to `.pfx` certificate for signing |
| `sign_password_env` | Name of an environment variable holding the certificate password (not the password itself, so it's never baked into `build.ninja`) |

#### Complete Platform-Conditional Example

```python
from pcons.contrib import platform

installer_targets = []

if platform.is_macos():
    from pcons.contrib.installers import macos

    pkg = macos.create_pkg(
        project,
        env,
        name="MyApp",
        version="1.0.0",
        identifier="com.example.myapp",
        sources=[app],
        install_location="/usr/local/bin",
    )
    dmg = macos.create_dmg(project, env, name="MyApp", sources=[app])
    installer_targets.extend([pkg, dmg])

elif platform.is_windows():
    from pcons.contrib.installers import windows

    msix = windows.create_msix(
        project,
        env,
        name="MyApp",
        version="1.0.0.0",
        publisher="CN=Example Corp",
        sources=[app],
    )
    installer_targets.append(msix)

if installer_targets:
    project.Alias("installers", *installer_targets)
```

Build with:

```bash
pcons                # Build the application
ninja -C build installers  # Build installer packages
```

For a complete working example, see `examples/19_installers/`.

### Building Python Packages (PEP 517 Backend)

!!! warning "Experimental"
    The `pcons.pyproject` backend is new and marked experimental: the
    `[tool.pcons]` keys and the `PCONS_BUILD_WHEEL` convention described below
    may still change based on feedback.

Pcons includes a [PEP 517](https://peps.python.org/pep-0517/) build backend,
so a Python package with native extensions can use pcons as its build system
directly from `pyproject.toml` — `pip install`, `uv sync`, `uv build`, and
editable installs all work with no extra tooling:

```toml
[build-system]
requires = ["pcons"]
build-backend = "pcons.pyproject"

[project]
name = "mypkg"
version = "1.0.0"
requires-python = ">=3.11"

[tool.pcons]
variant = "release"          # optional: pcons variant to build
install-target = "install"   # alias to build for wheels (default: "wheel")
# variables = { SOME_VAR = "value" }  # optional: extra pcons variables
```

#### How wheels are built

When a frontend (pip, uv, ...) asks for a wheel, the backend:

1. Runs your `pcons-build.py` with `PCONS_INSTALL_PREFIX` pointing at a clean
   staging directory, and `PCONS_BUILD_WHEEL=1` (see below).
2. Runs ninja on the `install-target` alias, so your `Install()` targets copy
   their outputs into the staging directory.
3. Packages **everything in the staging directory, preserving its directory
   structure**, into the wheel.

The staging directory is the **site-packages image**: the tree your install
target creates there is exactly the tree users get in `site-packages`.

#### The `PCONS_BUILD_WHEEL` variable

This is where the build script comes in. A normal `ninja install` should
follow the usual `bin`/`lib` conventions, but a wheel build needs a
package-shaped layout (`mypkg/__init__.py`, `mypkg/_ext.so`, ...) at the
staging root. The backend sets the variable `PCONS_BUILD_WHEEL=1` during wheel
builds so one build script can serve both:

```python
from pcons import get_var, install_dir

if get_var("PCONS_BUILD_WHEEL", False):
    # Wheel build: the install prefix is the site-packages image.
    # Lay files out exactly as they should appear after installation.
    dest = "."
else:
    # Normal install: usual bin/lib conventions.
    dest = install_dir(env, "shared_library")

project.Install(dest, [my_extension], name="install")
```

If your build script ignores `PCONS_BUILD_WHEEL` and installs to `lib/`, the
wheel will build and install, but won't have the correct dir layout. Always check the variable in
any script that feeds the backend.

#### Editable installs

`pip install -e .` / `uv sync` (PEP 660) skips the staging step entirely: the
backend builds the project and writes a wheel containing only a `.pth` file
that puts the **build directory** on `sys.path`. Imports resolve directly to
the compiled extensions in `build/`, so after editing C++ sources, re-running
`ninja` is enough — no reinstall needed. (`PCONS_BUILD_WHEEL` is *not* set for
editable builds.)

#### Metadata and sdists

The backend honors the PEP 621 `[project]` fields `name`, `version`,
`requires-python`, and `dependencies` (emitted as `Requires-Dist`). Any other
non-empty `[project]` field raises an error rather than being silently
dropped from the wheel's metadata — remove the field or file an issue.
`name` and `version` are required.

`build_sdist` ships the whole source tree (recursively, excluding build
output, VCS data, and tool caches) plus the spec-required `PKG-INFO`.

Ninja is requested automatically as a build requirement in isolated builds
when it isn't already on PATH (a `NINJA` environment variable override is
respected).

For a complete working example — a [nanobind](https://nanobind.readthedocs.io/)
C++ extension using Conan, exercising editable installs, wheel builds, and
sdists via `uv` — see `examples/50_pyproject/`.

### macOS Framework Linking

On macOS, link against system frameworks using `env.Framework()`:

```python
import sys

if sys.platform == "darwin":
    # Link a single framework
    env.Framework("CoreFoundation")

    # Link multiple frameworks
    env.Framework("Foundation", "Metal", "QuartzCore")

    # Add framework search paths for non-system frameworks
    env.link.frameworkdirs.append("/Library/Frameworks")
    env.Framework("SomeThirdParty")
```

This adds the appropriate `-framework` and `-F` flags to the linker command. Framework linking is only available on macOS with GCC or LLVM toolchains.

For more complex scenarios where you need framework flags in compile commands (e.g., for headers), you can also access the raw flags:

```python
# Manual approach (usually not needed)
env.link.flags.extend(["-framework", "Metal"])
env.link.flags.extend(["-F", "/path/to/frameworks"])
```

### Paths in Linker Flags (PathToken)

Sometimes you need to embed a file path inside a linker flag, such as `-Wl,-force_load,<path>` (macOS whole-archive linking) or `-Wl,--version-script=<path>`. Plain strings don't work here because the path needs to be relativized correctly for the generator (Ninja runs from the build directory, so paths must be relative to it).

Use `PathToken` to embed paths in flags:

```python
from pcons import PathToken, Project

project = Project("myapp")
env = project.Environment(toolchain="c")

lib = project.StaticLibrary("mylib", env)
lib.add_sources(["src/mylib.c"])

prog = project.Program("myapp", env)
prog.add_sources(["src/main.c"])
prog.link_private(lib)

# Force-load all symbols from the static library (macOS)
prog.private.link_flags.append(
    PathToken(prefix="-Wl,-force_load,", path="libmylib.a", path_type="build")
)
```

`PathToken` takes three key arguments:
- **`prefix`**: The flag text before the path (e.g., `"-Wl,-force_load,"`, `"-Wl,--version-script="`)
- **`path`**: The file path
- **`path_type`**: How the path should be interpreted:
  - `"build"` — relative to the build directory (for build outputs like libraries)
  - `"project"` — relative to the project root (for source tree files)
  - `"absolute"` — used as-is

See `examples/33_path_in_flags` for a complete working example.

### Multi-Architecture Builds

Pcons supports building for multiple CPU architectures, which is useful for:
- **macOS**: Creating universal binaries that run on both Intel and Apple Silicon
- **Windows**: Building for x64, x86, or ARM64

#### Target Architecture API

Use `env.set_target_arch()` to configure an environment for a specific architecture:

```python
from pcons import Project

project = Project("mylib")

# Create environment for arm64
env_arm64 = project.Environment(toolchain="c")
env_arm64.set_target_arch("arm64")
env_arm64.build_dir = Path("build/arm64")

# Create environment for x86_64
env_x86_64 = project.Environment(toolchain="c")
env_x86_64.set_target_arch("x86_64")
env_x86_64.build_dir = Path("build/x86_64")
```

The architecture setting is orthogonal to build variants, so you can combine them:

```python
env.set_variant("release")
env.set_target_arch("arm64")
```

#### Platform-Specific Behavior

**macOS (GCC/LLVM):**
- Adds `-arch <arch>` flags to compiler and linker
- Supported architectures: `arm64`, `x86_64`

**Windows (MSVC):**
- Adds `/MACHINE:<ARCH>` to linker and librarian
- For a non-native arch, selects the matching cross toolset: the
  `bin/Host<host>/<arch>` compiler binaries plus the VC and Windows SDK
  `<arch>` library directories (the dev shell's `LIB` covers only the host
  arch). Raises with install guidance if the cross toolset component isn't
  installed in Visual Studio.
- Supported architectures: `x64`, `x86`, `arm64`, `arm64ec`
- Aliases: `amd64`→`x64`, `x86_64`→`x64`, `aarch64`→`arm64`

**Windows (Clang-CL):**
- Adds `--target=<triple>` to compilers (e.g., `--target=aarch64-pc-windows-msvc`)
- Adds `/MACHINE:<ARCH>` to linker
- For a non-native arch, also adds the VC and Windows SDK `<arch>` library
  directories (same requirement as MSVC: the cross build-tools component
  must be installed)

**Linux (GCC/LLVM):**
- A bare arch name can't retarget the compiler on Linux, so
  `set_target_arch()` **raises**. Use a cross preset instead — e.g.
  `linux_cross(triple="aarch64-linux-gnu")` — or a dedicated cross
  toolchain (see [Cross-Compilation Presets](#cross-compilation-presets)).

For example, on a Windows x64 machine this builds an ARM64 binary — no
vcvars cross shell needed, just the ARM64 build-tools component:

```python
env = project.Environment(toolchain="c")  # MSVC or clang-cl
env.set_target_arch("arm64")
app = project.Program("myapp", env, sources=["main.c"])
```

#### macOS Universal Binaries

To create a universal binary that runs on both Intel and Apple Silicon Macs, build for each architecture separately and combine with `lipo`:

```python
from pathlib import Path
from pcons import Project
from pcons.util.macos import create_universal_binary

project = Project("mylib")

# Build for arm64
env_arm64 = project.Environment(toolchain="c")
env_arm64.set_target_arch("arm64")
env_arm64.set_variant("release")
lib_arm64 = project.StaticLibrary("mylib", env_arm64, sources=["lib.c"])
# Note: output goes to build/libmylib.a by default

# Build for x86_64 (use different build dir to avoid conflicts)
env_x86_64 = project.Environment(toolchain="c")
env_x86_64.set_target_arch("x86_64")
env_x86_64.set_variant("release")
env_x86_64.build_dir = Path("build/x86_64")
lib_x86_64 = project.StaticLibrary("mylib_x86", env_x86_64, sources=["lib.c"])

# Combine into universal binary
lib_universal = create_universal_binary(
    project,
    "mylib_universal",
    inputs=[lib_arm64, lib_x86_64],
    output="build/universal/libmylib.a",
)

project.Default(lib_universal)
```

The `create_universal_binary()` function:
- Takes a list of architecture-specific binaries (as Targets, FileNodes, or paths)
- Uses `lipo -create` to combine them
- Returns a Target object representing the universal binary

This works for static libraries, dynamic libraries, and executables.

### Cross-Compilation Presets

For cross-compiling to other platforms, pcons provides ready-made presets that configure sysroot, target triple, architecture flags, and SDK paths.

```python
from pcons.toolchains.presets import android, ios, linux_cross, pyodide

# Android NDK
env.apply_cross_preset(android(ndk="~/android-ndk", arch="arm64-v8a"))

# iOS — works with both the Swift and LLVM (C/C++/Objective-C++) toolchains;
# the iPhoneOS SDK is resolved via xcrun unless sdk= is given
env.apply_cross_preset(ios(arch="arm64", min_version="15.0"))

# iOS Simulator
env.apply_cross_preset(ios(arch="x86_64"))

# WebAssembly presets apply to the *dedicated* wasm toolchains, which own
# output suffixes (.js/.wasm), shared-library rules, and the link driver —
# applying a wasm preset to a native toolchain raises. The presets add
# target-specific flags, e.g. pyodide() side-module flags:
env = project.Environment(toolchain="emscripten")
env.apply_cross_preset(pyodide("2026_0"))

# Generic Linux cross-compilation
env.apply_cross_preset(
    linux_cross(
        triple="aarch64-linux-gnu",
        sysroot="/opt/aarch64-sysroot",
    )
)
```

For a fully self-contained WASI build, prefer the dedicated WASI toolchain:

```python
env = project.Environment(toolchain="wasi")
project.Program("hello", env, sources=["src/hello.c"])
```

#### Available Factory Functions

| Factory | Key Arguments | Description |
|---------|--------------|-------------|
| `android(ndk, arch, api)` | `arch`: arm64-v8a, armeabi-v7a, x86_64, x86; `api`: minimum API level (default 21) | Android NDK cross-compilation |
| `ios(arch, min_version, sdk)` | `arch`: arm64 or x86_64 (simulator); `min_version`: deployment target | iOS cross-compilation |
| `emscripten(emsdk)` | `emsdk`: path to Emscripten SDK (optional if emcc in PATH) | WebAssembly via Emscripten (requires `toolchain="emscripten"`) |
| `wasi_sdk(sdk_path)` | `sdk_path`: path to wasi-sdk (optional, auto-detected) | WebAssembly via wasi-sdk (requires `toolchain="wasi"`) |
| `pyodide(abi, emsdk)` | `abi`: Pyodide ABI version (default "2026_0") | Pyodide extension modules (requires `toolchain="emscripten"`) |
| `linux_cross(triple, sysroot)` | `triple`: GCC/Clang target triple; `sysroot`: target sysroot path | Generic Linux cross-compilation |

The WebAssembly presets apply only to their dedicated toolchains; applying
one to a native toolchain raises.

#### Custom Cross-Compilation Presets

For targets not covered by the built-in factories, create a `CrossPreset` directly:

```python
from pcons.toolchains.presets import CrossPreset

# Custom embedded target
preset = CrossPreset(
    name="riscv-bare",
    arch="riscv64",
    triple="riscv64-unknown-elf",
    sysroot="/opt/riscv/sysroot",
    extra_compile_flags=("-march=rv64gc", "-mabi=lp64d"),
    extra_link_flags=("-nostdlib",),
    tool_cmds={
        "cc": "/opt/riscv/bin/riscv64-unknown-elf-gcc",
        "cxx": "/opt/riscv/bin/riscv64-unknown-elf-g++",
        "link": "/opt/riscv/bin/riscv64-unknown-elf-g++",
        "ar": "/opt/riscv/bin/riscv64-unknown-elf-ar",
    },
)
env.apply_cross_preset(preset)
```

GCC selects targets by binary, not by flag, so a GCC cross preset must name
the cross binaries in `tool_cmds` — including `link` and `ar`, or those
steps silently run the host tools. Clang-family toolchains retarget via
`triple` and can usually omit `tool_cmds`.

The `CrossPreset` fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable name |
| `arch` | `str` | Target CPU name in the target ecosystem's vocabulary (metadata; the triple encodes the CPU) |
| `triple` | `str \| None` | Compiler target triple (used with `--target` on Clang) |
| `sysroot` | `str \| None` | Root of the target's headers/libraries (`--sysroot`, or `-isysroot`/SDK on Apple) |
| `extra_compile_flags` | `tuple[str, ...]` | Additional compile flags |
| `extra_link_flags` | `tuple[str, ...]` | Additional link flags |
| `tool_cmds` | `dict[str, str]` | Per-tool command overrides keyed by pcons tool name (`cc`, `cxx`, `link`, `ar`, ...) |
| `env_vars` | `dict[str, str]` | Deprecated alias for `tool_cmds` using CC/CXX/LD/AR vocabulary; `tool_cmds` wins on conflict |

### Compiler Cache

Speed up rebuilds by wrapping compile commands with [ccache](https://ccache.dev/) or [sccache](https://github.com/mozilla/sccache):

```python
# Auto-detect: tries sccache first, then ccache
env.use_compiler_cache()

# Explicit choice
env.use_compiler_cache("ccache")
env.use_compiler_cache("sccache")
```

This sets the cache as the *launcher* on the `cc` and `cxx` tools (see below). Only compile commands are affected — the linker and archiver have nothing to cache. If the requested tool isn't in PATH, a warning is logged and no changes are made.

Notes:
- On MSVC (`cl.exe`), only sccache works. If you request ccache with an MSVC toolchain, pcons warns and does nothing.
- Calling `use_compiler_cache()` twice is a no-op, and it leaves any launcher you set yourself in place.

### Command Launchers

A launcher is a program that runs *in front of* the command an edge would otherwise run: `ccache` ahead of the compiler, `time` or `valgrind` ahead of anything you want to measure or check. It belongs to a tool namespace, so it follows the tool rather than any one target:

```python
env.cc.launcher = ["ccache"]
env.cc.launcher = ["ccache", "time"]  # stacked, outermost first
```

Like every command in pcons, a launcher is a list of tokens rather than a string, so a program whose path contains a space stays one argument.

A launcher can also belong to a single command rather than to a tool, which is what a wrapper for one expensive step wants:

```python
env.Command(
    target="model.stl",
    source="model.py",
    command="python $SOURCE --out $TARGET",
    launcher=["valgrind", "-q"],
)
```

Both compose, outermost first: a launcher on the tool runs outside the one on the command.

Two things worth knowing:

- **Launcher tokens are passed through as written.** They are a program and its arguments, not paths in the dependency graph, so pcons does not rewrite them for the directory the build runs in. Use absolute paths (`project.root_dir / "tools" / "wrap.py"`).
- **`compile_commands.json` reports the compiler itself**, without launchers, so clangd and other tools see the real compile.

See `examples/63_command_launcher` for two stacked launchers wrapping every C compile, and a third belonging to one command.

### Multiple Toolchains

Pcons supports combining multiple toolchains in a single environment. This is useful for projects that mix languages, such as C++ with CUDA, or C++ with Cython.

#### Adding Additional Toolchains

Use `env.add_toolchain()` to add extra toolchains to an environment:

```python
from pcons import Project

project = Project("gpu_app", build_dir="build")

# Create environment with C/C++ toolchain
env = project.Environment(toolchain="c++")

# Add CUDA toolchain for .cu files
env.add_toolchain("cuda")

# Now this target can have both .cpp and .cu sources
app = project.Program("gpu_app", env)
app.add_sources(
    [
        "main.cpp",  # Compiled with C++ compiler
        "kernel.cu",  # Compiled with CUDA nvcc
    ]
)
```

#### How Source Routing Works

When a target has sources with different file extensions, pcons routes each source to the appropriate compiler:

- `.c` files → C compiler from primary toolchain
- `.cpp`, `.cxx`, `.cc` files → C++ compiler from primary toolchain
- `.cu` files → CUDA compiler from CUDA toolchain (if added)

The primary toolchain (passed to `project.Environment()`) has precedence. If multiple toolchains claim to handle the same file type, the primary toolchain wins.

#### Variant Support with Multiple Toolchains

When you call `env.set_variant()`, the variant is applied to all toolchains:

```python
env = project.Environment(toolchain="c++")
env.add_toolchain("cuda")

# This applies "debug" settings to both C++ AND CUDA compilers
env.set_variant("debug")
# C++ gets: -O0 -g
# CUDA gets: -G -g (device debugging)
```

#### Available Toolchain Finders

Toolchain name strings resolve through finder functions, which are also available directly for programmatic use:

| Function | Description |
|----------|-------------|
| `find_c_toolchain()` | Find C/C++ toolchain (LLVM, GCC, MSVC, etc.) |
| `find_cuda_toolchain()` | Find CUDA toolchain (returns `None` if nvcc not found) |

---

## Feature Detection

Pcons provides a two-part configuration system for detecting compiler capabilities and generating config headers. The two parts have distinct roles:

- **`ToolChecks`** — does the real work: compiles test programs to probe for flags, headers, types, functions, and macros. Stores results through `Configure`.
- **`Configure`** — manages caching (persists results to `build/pcons_config.json` so subsequent runs are fast), accumulates `#define` entries, and generates `config.h`.

### ToolChecks: Probing the Compiler

`ToolChecks` compiles small test programs with your actual compiler to detect what's available. It needs both a `Configure` (for caching) and an `Environment` (to know which compiler to run).

```python
from pathlib import Path
from pcons.configure.config import Configure
from pcons.configure.checks import ToolChecks

config = Configure(build_dir=Path("build"))
env = project.Environment(toolchain="c")

# Create a checker for the C compiler
checks = ToolChecks(config, env, "cc")

# Check if a compiler flag is supported
if checks.check_flag("-Wall").success:
    env.cc.flags.append("-Wall")

if checks.check_flag("-std=c++20").success:
    env.cxx.flags.append("-std=c++20")

# Check if a header exists
if checks.check_header("sys/mman.h").success:
    env.cc.defines.append("HAVE_MMAN_H")

# Check if a type exists (optionally specifying which headers to include)
if checks.check_type("size_t", headers=["stddef.h"]).success:
    pass

# Get the size of a type (uses compile-time assertion, no need to run)
int_size = checks.check_type_size("int")  # Returns 4 on most systems
ptr_size = checks.check_type_size("void*")  # 8 on 64-bit, 4 on 32-bit

# Check if a function is available (compiles + links)
if checks.check_function(
    "pthread_create", headers=["pthread.h"], libs=["pthread"]
).success:
    env.link.libs.append("pthread")

# Read a predefined compiler macro
gcc_ver = checks.check_define("__GNUC__")  # e.g. "14"

# Read constants out of the project's own headers -- one preprocessor run
# for as many macros as you like
version = checks.check_defines(
    ["VERSION_NAME", "VERSION_MAJOR", "USE_DONGLES"],
    headers=["core/version.h"],
    include_dirs=[src_dir],
)

# Custom compile check with arbitrary source code
has_neon = checks.try_compile(
    "#include <arm_neon.h>\nint main() { float a[] = {1,1}; vld1q_f32_x2(a); return 0; }"
).success
```

All results are automatically cached through `Configure`. On the first run, each check compiles a test program; on subsequent runs, cached results are returned instantly:

```python
result1 = checks.check_flag("-Wall")
assert result1.cached is False  # First run: compiled a test

result2 = checks.check_flag("-Wall")
assert result2.cached is True  # Second run: from cache
```

The cache key includes a signature of the compiler command *and its current flags*, so switching compilers — or retargeting the same compiler with a cross preset (`--target=`, `-isysroot`) — invalidates the relevant entries automatically. Checks probe the same compilation the build will do.

Checks compile the way the build does: the tool's flags, defines, and include directories all apply. That matters most for the case that doesn't fail — a header that compiles either way and takes a different `#ifdef` branch will hand back a plausible wrong value if the probe doesn't carry the same defines the build will. Per-call `defines=` adds to the environment's rather than replacing them.

#### Reading Macros From Headers

`check_define()` and `check_defines()` accept `headers=`, `include_dirs=`, and `defines=`, so they read constants out of the project's own headers — version strings, feature flags, install paths — not just compiler builtins. Four outcomes are distinguishable:

| in the header | returned |
|---|---|
| (not defined) | `None` |
| `#define FOO` | `""` |
| `#define FOO 42` | `"42"` |
| `#define FOO "MyLib 2024"` | `'"MyLib 2024"'` |

Quotes are kept, so a string literal is distinguishable from a number and from a defined-but-empty macro, and the value can go straight into a generated config header. Use the batch form when reading several from one header: configure time is dominated by process startup, and `check_defines()` answers them all in a single preprocessor run.

For a macro you know is a string, `as_string=True` gives you the string it denotes rather than its expansion text — adjacent literals concatenated, quotes removed, simple C escapes decoded:

```python
# support/config.h:  #define DEFAULT_DIR "/Applications/" "MyApp 2024" "/config"
checks.check_define("DEFAULT_DIR", headers=["support/config.h"], as_string=True)
# -> "/Applications/MyApp 2024/config"
```

It raises if the macro isn't a string literal, since asking for a string back from `#define N 42` is a mistake in the call rather than a value to guess at.

A probe that fails to preprocess at all — a missing header, a bad include path — is not cached. It's an error condition rather than an answer about the macro, and with staged generation the header may simply not exist yet on the first pass.

### Configure: Caching, Defines, and Config Headers

`Configure` serves as the shared state between checks and the config header generator. You can also use it directly to define values, find programs, or record features you know about without needing a compiler check:

```python
config = Configure(build_dir=Path("build"))

# Find a program in PATH (result is cached, keyed by a PATH signature —
# a changed PATH re-searches instead of returning stale results)
ninja = config.find_program("ninja")
if ninja:
    print(f"Found ninja {ninja.version} at {ninja.path}")

# Manually define values for the config header
config.define("VERSION_MAJOR", 1)
config.define("VERSION_MINOR", 2)
config.define("VERSION_STRING", "1.2.0")
config.define("HAVE_FEATURE_A")

# Mark a feature as absent
config.undefine("MISSING_FEATURE")

# Save cache for next run
config.save()
```

### Generating Config Headers

After running checks and defining values, generate a `config.h` with `write_config_header()`. This collects all the `#define` entries accumulated by both `ToolChecks` (via `config.set()`) and direct `config.define()` calls:

```python
# Run checks — results are recorded in config
checks = ToolChecks(config, env, "cc")
if checks.check_header("sys/mman.h").success:
    config.define("HAVE_SYS_MMAN_H")

config.define("VERSION_MAJOR", 1)
config.define("VERSION_STRING", "1.2.0")
config.check_sizeof("int", env=env)  # Defines SIZEOF_INT
config.check_sizeof("void*", env=env)  # Defines SIZEOF_VOIDP
config.undefine("MISSING_FEATURE")

# Generate the header
config.write_config_header(
    Path("build/config.h"),
    guard="MY_CONFIG_H",
    include_platform=True,  # Add PCONS_OS_* and PCONS_ARCH_* defines
)
```

This generates:

```c
#ifndef MY_CONFIG_H
#define MY_CONFIG_H

/* Platform detection */
#define PCONS_OS_MACOS 1
#define PCONS_ARCH_ARM64 1

/* Feature and header checks */
#define HAVE_SYS_MMAN_H 1

/* Type sizes */
#define SIZEOF_INT 4
#define SIZEOF_VOIDP 8

/* Custom definitions */
#define VERSION_MAJOR 1
#define VERSION_STRING "1.2.0"
/* #undef MISSING_FEATURE */

#endif /* MY_CONFIG_H */
```

Note: all configure checks — including `check_sizeof()` — are **compile-time only**: they ask the configured compiler (with the environment's flags, so cross presets apply) and never execute anything, which makes them correct under cross-compilation. Values like type sizes are computed with compile-time probes (`int check[sizeof(T) == N ? 1 : -1]`), the same technique autoconf, CMake, and Meson use. If a genuinely run-time answer is ever needed, provide it explicitly with `config.define()`.

### Template-Based Config Files with `configure_file()`

For projects that use template-based configuration (like CMake's `configure_file()`), pcons provides a `configure_file()` function that substitutes variables in a template and writes the result:

```python
from pcons import configure_file

configure_file(
    "src/config.h.in",
    "build/config.h",
    {"VERSION": "1.2.3", "HAVE_ZLIB": "1"},
)
```

Two substitution styles are supported:

**CMake style** (default) — processes `#cmakedefine` directives and `@VAR@` substitutions:

```c
/* config.h.in */
#define VERSION "@VERSION@"
#cmakedefine01 HAVE_THREADS
#cmakedefine HAVE_ZLIB
```

With `{"VERSION": "1.2.3", "HAVE_THREADS": "1"}` this produces:

```c
#define VERSION "1.2.3"
#define HAVE_THREADS 1
/* #undef HAVE_ZLIB */
```

**At style** (`style="at"`) — simple `@VAR@` replacement only:

```python
configure_file("version.txt.in", "build/version.txt", {"VERSION": "1.2.3"}, style="at")
```

Options:

- `strict=True` (default): raises `KeyError` if a `@VAR@` has no matching key
- `strict=False`: missing variables are replaced with empty string
- Write-if-changed: the output file is only written if its content would change

This is especially useful when porting CMake projects to pcons, since the template files can often be used as-is.

### Re-running pcons Automatically

Generated build files carry a self-regeneration rule: Ninja's `generator = 1` edge, and the equivalent makefile-remake rule for Make. Editing the build script — or anything it read while describing the build — re-runs pcons before anything is built, in the same `ninja` invocation. No wrapper script, no stale graph.

Registered automatically:

- the build script itself;
- every Python module imported from inside the project tree, so a description split across `build-scripts/*.py` is fully covered;
- `configure_file()` templates.

Anything else — a data file your script reads directly — you declare:

```python
project.add_configure_dependency(project.root_dir / "plugins.def")
```

The regen rule is omitted when the invocation can't be reconstructed (for example a build script executed in an unusual way). `project.generate()` still writes the build files; they just won't re-run pcons on their own.

### Staged Generation: Targets Discovered Mid-Build

Some projects can't know their target list until something has run: a definition language, an IDL, a schema, a plugin manifest — and often the program that reads it is built by the same build.

pcons describes the graph and hands it to Ninja; it never creates targets while the build runs. Instead it stages, and the build system drives the staging:

```python
manifest_path = project.build_dir / "gen/plugins-list.txt"

# Pass 1: only the part of the graph that produces the manifest.
lister = project.Program("list-plugins", env, sources=["src/list-plugins.c"])
manifest = env.Command(
    target=manifest_path,
    source=[lister],
    depends=["plugins.def"],
    command="./$SOURCE $SRCDIR/plugins.def $TARGET",  # ./ so /bin/sh finds it
    write_if_different=True,
)


# Pass 2: runs only once the manifest exists.
@project.when_generated(manifest_path)
def _plugins(path):
    for name in path.read_text().split():
        make_plugin(name)
```

From a clean tree, one `ninja` compiles and runs `list-plugins`, notices that `build.ninja` depends on the manifest it just produced, re-runs pcons, reloads, and builds the discovered targets.

`when_generated` is sugar over the primitive:

```python
path = project.generated_input(manifest_path)  # -> Path | None
```

Either form registers the path as a configure dependency, so the build system re-runs pcons as soon as it appears or changes. A staged input that no rule produces is an error — it could never appear, and the build would silently stay incomplete.

This is the one sanctioned filesystem check in a build script. It asks whether a declared build *input* has been produced yet and records the answer as a dependency; deciding whether something is a *target* by looking at the filesystem is still wrong.

Pair it with `write_if_different=True` (see [Custom Commands](#custom-commands-with-envcommand)) or a re-run of the generator will invalidate everything downstream of every output it touched. A complete worked example is `examples/57_staged_generation`.

Ninja handles this natively; GNU make 4.x does too. GNU make 3.81 — still `/usr/bin/make` on macOS — compares makefile prerequisite timestamps at whole-second granularity and can miss a manifest written in the same second, so use ninja or a modern GNU make for staged builds.

---

## Troubleshooting

### No toolchain found

**Error:** `RuntimeError: No C/C++ toolchain found`

**Solution:** Install a compiler:
- macOS: `xcode-select --install`
- Ubuntu/Debian: `sudo apt install build-essential`
- Fedora: `sudo dnf install gcc gcc-c++`
- Windows: Install Visual Studio with C++ workload, or use [msvcup](#windows-msvc-without-visual-studio-msvcup) for a lightweight install

### Ninja not found

**Error:** `ninja not found in PATH`

**Solution:** Install Ninja:
- macOS: `brew install ninja`
- Ubuntu/Debian: `sudo apt install ninja-build`
- pip: `pip install ninja`

### Missing sources

**Error:** `MissingSourceError: File not found: src/missing.cpp`

**Solution:** Check that all source files exist and paths are correct.

### Dependency cycles

**Error:** `DependencyCycleError: Cycle detected: A -> B -> A`

**Solution:** Refactor to break the cycle. Two libraries shouldn't depend on each other.

---

## Reference

### Project Methods

| Method | Description |
|--------|-------------|
| `Project(name, build_dir)` | Create a project |
| `project.Environment(toolchain)` | Create an environment |
| `project.Program(name, env)` | Create a program target |
| `project.StaticLibrary(name, env)` | Create a static library |
| `project.SharedLibrary(name, env)` | Create a shared library |
| `project.HeaderOnlyLibrary(name)` | Create a header-only library |
| `project.Install(dir, sources)` | Install files to a directory |
| `project.InstallAs(dest, source)` | Install with rename |
| `project.Tarfile(env, output, sources)` | Create tar archive (.tar, .tar.gz, etc.) |
| `project.Zipfile(env, output, sources)` | Create zip archive |
| `project.Default(*targets)` | Set default build targets |
| `project.Alias(name, *targets)` | Create a named alias |
| `project.resolve()` | Resolve all dependencies |
| `project.node(path)` | Get/create a file node |
| `project.find_package(name, ...)` | Find external package (returns ImportedTarget) |
| `project.add_package_finder(finder)` | Prepend a custom package finder |

### Target Methods

| Method | Description |
|--------|-------------|
| `target.add_source(path)` | Add a source file |
| `target.add_sources(paths)` | Add multiple source files |
| `target.add_sources(paths, env=e)` | Compile those sources with a different environment; on a source the target already has, sets its environment in place |
| `target.set_option(key, value)` | Set a builder/toolchain option (e.g. `install_name`) |
| `target.link(t, "m")` | Link a dependency (or raw lib name) and re-export it to consumers |
| `target.link_private(t, "m")` | Link a dependency (or raw lib name), keeping it local |
| `target.add_dependency(t)` | Add a non-link build dependency |
| `target.public.include_dirs` | Include dirs for consumers |
| `target.public.system_include_dirs` | Like `include_dirs`, but as system headers (warnings suppressed) |
| `target.public.link_libs.append(t)` | Low-level form of `link()` (append a `Target` or `-l` name) |
| `target.private.link_libs.append(t)` | Low-level form of `link_private()` |
| `target.public.link_libs` | Libraries to link (`-l`; placed after objects) |
| `target.public.link_flags` | Linker flags (placed before objects; use `link_libs` for `-l` libraries). Use `PathToken` for flags containing paths. |
| `target.public.defines` | Defines for consumers |
| `target.public.link_dirs` | Library search directories (`-L`) |
| `target.public.frameworks` / `framework_dirs` | macOS frameworks (`-framework` / `-F`) |
| `target.private.compile_flags` | Flags for this target only |

These are the names pcons reads. Any other name raises — the lists are consumed by name, so a typo like `lib_dirs` would otherwise be stored and never looked at, and the build would fail somewhere else entirely (`ld: library 'Foo' not found`, naming the library rather than the mistake). A toolchain or extension that consumes a name of its own declares it with `pcons.core.target.register_usage_requirement()`.

The same rule applies to the other named surfaces: `set_option()` takes only options a builder or toolchain declared with `register_target_option()`, `env.<tool>.<var> = ...` only assigns variables the tool declared (use `env.<tool>.set(name, value)` to introduce one), and adding a source a target already has raises unless `env=` is given. In each case the alternative is a value nothing reads.

### Environment Methods

| Method | Description |
|--------|-------------|
| `env.set_variant(name)` | Set debug/release variant |
| `env.set_target_arch(arch)` | Set target CPU architecture |
| `env.apply_preset(name)` | Apply flag preset (warnings, werror, sanitize, profile, lto, hardened) |
| `env.apply_cross_preset(preset)` | Apply cross-compilation preset |
| `env.explain(tool=None)` | Attribute each flag/define/command to the preset that set it |
| `env.use_compiler_cache(tool=None)` | Wrap compilers with ccache/sccache |
| `env.use(package)` | Apply package settings |
| `env.clone()` | Create a copy |
| `env.override(**kwargs)` | Context manager for temporary overrides |
| `env.add_toolchain(toolchain)` | Add additional toolchain (e.g., CUDA) |
| `env.Command(target, source, cmd)` | Run arbitrary shell command |
| `env.Framework(*names)` | Link macOS frameworks (macOS only) |
| `env.Glob(pattern)` | Find files matching a glob pattern |
| `env.cc` | C compiler settings |
| `env.cxx` | C++ compiler settings |
| `env.link` | Linker settings |

### Helper Functions

| Function | Description |
|----------|-------------|
| `find_c_toolchain()` | Find an available C/C++ toolchain (platform-aware defaults) |
| `find_c_toolchain(prefer=[...])` | Find toolchain with explicit preference order |
| `find_cuda_toolchain()` | Find CUDA toolchain (returns `None` if nvcc not found) |
| `configure_file(template, output, vars)` | Substitute variables in a template file (CMake or @VAR@ style) |
| `get_var(name, default, type=None)` | Get a build variable, converted to the default's type (or `type=`): bool, int, float, str, Path |
| `get_variant(default)` | Get the build variant |
| `ensure_msvc(msvc_ver, sdk_ver)` | Install MSVC toolchain via msvcup (Windows only; import from `pcons.contrib.windows.msvcup`) |

### Generators

| Class | Description |
|-------|-------------|
| `Generator` | Generate build files using default generator (specified by cmdline, env, or default: Ninja) |
| `NinjaGenerator` | Generate Ninja build files |
| `MakefileGenerator` | Generate traditional Makefiles |
| `CompileCommandsGenerator` | Generate compile_commands.json for IDEs |
| `MermaidGenerator` | Generate Mermaid dependency diagrams |

### Configuration and Feature Detection

| Class/Method | Description |
|--------------|-------------|
| `Configure(build_dir)` | Create configuration context |
| `config.define(name, value=1)` | Define a preprocessor symbol |
| `config.undefine(name)` | Mark a symbol as undefined |
| `config.check_sizeof(type, env=env)` | Get the size of a type via the target compiler and define `SIZEOF_*` |
| `config.write_config_header(path)` | Generate a config.h file |
| `ToolChecks(config, env, tool)` | Create feature checker for a tool |
| `checks.check_flag(flag)` | Check if compiler accepts a flag |
| `checks.check_header(name)` | Check if a header exists |
| `checks.check_type(name, headers=[])` | Check if a type exists |
| `checks.check_type_size(name)` | Get the size of a type |
| `checks.check_function(name)` | Check if a function is available |
| `checks.check_define(name, headers=[])` | Read a macro's value, from the compiler or a header |
| `checks.check_defines(names, headers=[])` | Read several macros in one preprocessor run |
| `checks.try_compile(source)` | Try to compile arbitrary source code |

### macOS Utilities

| Function | Description |
|----------|-------------|
| `create_universal_binary(project, name, inputs, output)` | Combine arch-specific binaries into universal binary (returns Target) |
| `get_dylib_install_name(path)` | Get a dylib's install name |
| `fix_dylib_references(target, dylibs, lib_dir)` | Fix dylib references for bundle creation |

Import from `pcons.util.macos`.

---

## Add-on Modules

Pcons provides an add-on/plugin system for creating reusable modules that handle domain-specific tasks like plugin bundle creation, SDK configuration, or custom package discovery.

### Module Search Paths

Pcons automatically discovers and loads modules from these locations (in priority order):

1. **`PCONS_MODULES_PATH`** - Environment variable (colon/semicolon-separated paths)
2. **`~/.pcons/modules/`** - User's global modules
3. **`./pcons_modules/`** - Project-local modules

You can also specify additional paths via the CLI:

```bash
pcons --modules-path=/path/to/modules
```

### Using Modules

Loaded modules are accessible via the `pcons.modules` namespace:

```python
from pcons.modules import mymodule

# Or access all loaded modules
import pcons.modules

print(dir(pcons.modules))  # ['mymodule', ...]
```

### Creating a Module

Create a Python file in one of the search paths. Modules follow a simple convention:

```python
# ~/.pcons/modules/ofx.py
"""OFX plugin support for pcons."""

__pcons_module__ = {
    "name": "ofx",
    "version": "1.0.0",
    "description": "OFX plugin bundle creation",
}


def setup_env(env, platform=None):
    """Configure environment for OFX plugin building."""
    env.cxx.includes.extend(
        [
            "openfx/include",
            "openfx/Examples/include",
        ]
    )
    if platform and not platform.is_windows:
        env.cxx.flags.append("-fvisibility=hidden")


def create_bundle(project, env, plugin_name, sources, *, build_dir, version="1.0.0"):
    """Create OFX plugin bundle with proper structure."""
    from pcons.contrib import bundle

    bundle_name = f"{plugin_name}.ofx.bundle"
    bundle_dir = build_dir / bundle_name

    plugin = project.SharedLibrary(plugin_name, env)
    plugin.output_name = f"{plugin_name}.ofx"
    plugin.add_sources(sources)

    # Install to bundle
    arch_dir = bundle_dir / "Contents" / bundle.get_arch_subdir("darwin", "arm64")
    project.Install(arch_dir, [plugin])

    return plugin


def register():
    """Optional: Register custom builders at load time."""
    # This is called automatically when the module loads
    pass
```

Then use it in your build script:

```python
# pcons-build.py
from pcons import Project
from pcons.modules import ofx  # Auto-loaded!

project = Project("myplugin")
env = project.Environment(toolchain="c")

ofx.setup_env(env)
plugin = ofx.create_bundle(
    project,
    env,
    "myplugin",
    sources=["src/plugin.cpp"],
    build_dir=project.build_dir,
)
```

### Contrib Modules

Pcons includes built-in helper modules in `pcons.contrib`:

```python
from pcons.contrib import bundle, platform

# Bundle creation helpers
plist = bundle.generate_info_plist("MyPlugin", "1.0.0", bundle_type="BNDL")
bundle.create_macos_bundle(project, env, plugin, bundle_dir="build/MyPlugin.bundle")
bundle.create_flat_bundle(project, env, plugin, bundle_dir="build/MyPlugin")
arch_dir = bundle.get_arch_subdir("darwin", "arm64")  # "MacOS-arm-64"

# Platform utilities
if platform.is_macos():
    ext = platform.get_shared_lib_extension()  # ".dylib"
    name = platform.format_shared_lib_name("foo")  # "libfoo.dylib"
```

### Module API Reference

| Function/Attribute | Description |
|-------------------|-------------|
| `__pcons_module__` | Optional dict with module metadata (name, version, description) |
| `register()` | Optional function called at load time to register builders |
| `setup_env(env, ...)` | Convention: Configure an environment for the module's domain |

| `pcons.modules` Function | Description |
|-------------------------|-------------|
| `load_modules(extra_paths)` | Load modules from search paths |
| `get_module(name)` | Get a loaded module by name |
| `list_modules()` | List names of all loaded modules |
| `get_search_paths()` | Get the module search paths |
| `clear_modules()` | Clear all loaded modules (for testing) |

| `pcons.contrib.bundle` Function | Description |
|--------------------------------|-------------|
| `generate_info_plist(name, version, ...)` | Generate macOS Info.plist content |
| `create_macos_bundle(...)` | Create macOS .bundle structure |
| `create_flat_bundle(...)` | Create flat directory bundle |
| `get_arch_subdir(platform, arch)` | Get architecture subdirectory name |

| `pcons.contrib.latex` Function | Description |
|-------------------------------|-------------|
| `find_latex_toolchain()` | Find and configure a LaTeX toolchain (requires `latexmk` in PATH) |

| `pcons.contrib.platform` Function | Description |
|----------------------------------|-------------|
| `is_macos()`, `is_linux()`, `is_windows()` | Platform checks |
| `get_platform_name()` | Get platform name ("darwin", "linux", "win32") |
| `get_arch()` | Get current architecture ("x86_64", "arm64", etc.) |
| `get_shared_lib_extension()` | Get shared lib extension (".dylib", ".so", ".dll") |
| `format_shared_lib_name(name)` | Format as shared lib filename |

---

## Further Reading

- [Qt Guide](qt.md) - Building Qt applications: automoc, QML, translations, deployment
- [Architecture Document](architecture.md) - Design details and implementation status
- [Example Projects](https://github.com/DarkStarSystems/pcons/tree/main/examples) - Working examples to learn from
- [Contributing Guide](contributing.md) - How to contribute to pcons

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
from pcons import Project, find_c_toolchain, Generator

# Create project with build directory
project = Project("hello", build_dir="build")

# Create an environment with the system default C/C++ toolchain
env = project.Environment(toolchain=find_c_toolchain())

# Create a program target
hello = project.Program("hello", env)
hello.add_sources(["hello.cpp"])

# Set this as the default target
project.Default(hello)

# Resolve dependencies and generate build files
Generator().generate(project, "build")
```

**3. Generate and build**:

```bash
# Using uvx (recommended)
uvx pcons

# Or if pcons is installed
pcons
```

This runs your `pcons-build.py` to generate `build/build.ninja`, then invokes Ninja to compile your program. If you don't have ninja installed, pcons will try to invoke it via `uvx ninja`.

**4. Run your program**:

```bash
./build/hello
# Output: Hello from pcons!
```

### Understanding the Commands

Pcons provides several commands:

```bash
pcons                    # Generate build files AND build (default)
pcons generate           # Only generate build.ninja
pcons build              # Only run ninja (assumes build.ninja exists)
pcons clean              # Clean build artifacts
pcons clean --all        # Remove entire build directory
pcons info               # Show pcons-build.py documentation
pcons init               # Create a template pcons-build.py
```

---

## Core Concepts

Understanding these core concepts will help you write effective pcons build scripts.

### Build Script Lifecycle

Every pcons build script (`pcons-build.py`) follows three phases:

1. **Configure** - Set up toolchains, environments, and build options
2. **Describe** - Create targets and define their sources/dependencies
3. **Generate** - Resolve dependencies and write build files

Your script must call a generator at the end:

```python
# ... define targets ...

# OPTIONAL: Resolve all dependencies (computes effective requirements)
# Generators will resolve the project if it's not already resolved.
project.resolve()

# REQUIRED: Generate build files (Ninja is the default generator, but Makefile and Xcode generators are also included)
Generator().generate(project, build_dir)
```

The `pcons` CLI executes your script but does NOT automatically call resolve/generate - your script controls when and how this happens. This gives you flexibility for conditional generation or multiple generators.

### Project

A `Project` is the top-level container for your build. It holds all environments, targets, and nodes.

```python
from pcons import Project

# Create a project
project = Project("myproject", build_dir="build")

# Optionally specify the root directory
project = Project(
    "myproject",
    root_dir=Path(__file__).parent,
    build_dir="build"
)
```

The project provides factory methods for creating targets:

- `project.Program()` - Create an executable
- `project.StaticLibrary()` - Create a static library (.a/.lib)
- `project.SharedLibrary()` - Create a shared library (.so/.dylib/.dll)
- `project.HeaderOnlyLibrary()` - Create a header-only library

### Environment

An `Environment` holds configuration for building: compiler settings, flags, include directories, and more. You can have multiple environments (e.g., for different platforms or variants).

```python
# Create environment with toolchain
env = project.Environment(toolchain=toolchain)

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
- **Absolute paths**: Pass through unchanged

This means you don't need to prefix output paths with `build_dir`:

```python
# Good: paths are relative to build_dir
project.Install("dist/lib", [mylib])
project.Tarfile(env, output="packages/release.tar.gz", ...)
project.InstallDir("dist", src_dir / "assets")

# Not needed: build_dir prefix is implicit
# project.Install(build_dir / "dist/lib", [mylib])  # Unnecessary
```

If you accidentally include the build directory name in a relative path (e.g., `"build/dist"`), pcons will warn you but keep the path as-is, in case you intentionally want a `build/` subdirectory inside the build directory.

### Toolchain

A `Toolchain` is a coordinated set of tools (compiler, linker, archiver) that work together. Pcons automatically detects available C/C++ toolchains.

```python
from pcons import find_c_toolchain

# Auto-detect the best available toolchain
# Uses platform-appropriate defaults:
#   Windows: clang-cl, msvc, llvm, gcc
#   Unix/Mac: llvm, gcc
toolchain = find_c_toolchain()

# Or specify a preference order
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
app.link(lib)
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
project.Install("dist", ["file.txt"])           # Path string - works fine
project.Install("dist", [Path("file.txt")])     # Path object - works fine
project.Install("dist", [project.node("file.txt")])  # Explicit node - also works

# Needed when you want to add explicit dependencies to a source file:
header = project.node("generated.h")
header.depends([generator_target])  # Now generated.h depends on generator
app.add_sources(["main.cpp"])       # main.cpp will rebuild when generated.h changes
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
project.Alias("install", [installed_lib, installed_headers])

# Create an alias for tests
project.Alias("test", [test_runner])

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
from pcons import Project, find_c_toolchain, Generator

# Setup
toolchain = find_c_toolchain()
project = Project("hello", build_dir="build")
env = project.Environment(toolchain=toolchain)

# Create program
hello = project.Program("hello", env)
hello.add_sources(["hello.c"])
hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Generate
project.Default(hello)
Generator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, Generator

# Directories
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Setup
toolchain = find_c_toolchain()
project = Project("calculator", build_dir="build")
env = project.Environment(toolchain=toolchain)

# Create program with multiple sources
calculator = project.Program("calculator", env)
calculator.add_sources([
    src_dir / "main.c",
    src_dir / "math_ops.c",
])

# Add include directory (private - only for building this target)
calculator.private.include_dirs.append(include_dir)
calculator.private.compile_flags.extend(["-Wall", "-Wextra"])

# Generate
project.Default(calculator)
Generator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, Generator

src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

toolchain = find_c_toolchain()
project = Project("myproject", build_dir="build")
env = project.Environment(toolchain=toolchain)

# Create static library
libmath = project.StaticLibrary("math", env)
libmath.add_sources([src_dir / "math_utils.c"])

# Public includes propagate to consumers
libmath.public.include_dirs.append(include_dir)

# Public link libs (e.g., math library on Linux)
libmath.public.link_libs.append("m")

# Create program that uses the library
app = project.Program("myapp", env)
app.add_sources([src_dir / "main.c"])
app.link(libmath)  # Gets libmath's public includes automatically!

project.Default(app)
Generator().generate(project, "build")
```

Key points:
- `public.include_dirs` propagates to targets that link against this library
- `app.link(libmath)` adds libmath as a dependency and applies its public requirements

### Shared/Dynamic Library

Create a shared library (`.so` on Linux, `.dylib` on macOS, `.dll` on Windows).

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project, find_c_toolchain, Generator

src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

toolchain = find_c_toolchain()
project = Project("myproject", build_dir="build")
env = project.Environment(toolchain=toolchain)

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
app.link(libplugin)

project.Default(app, libplugin)
Generator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, Generator
from pcons.generators.compile_commands import CompileCommandsGenerator

project_dir = Path(__file__).parent
src_dir = project_dir / "src"
include_dir = project_dir / "include"
build_dir = project_dir / "build"

toolchain = find_c_toolchain()
project = Project("simulator", root_dir=project_dir, build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Library: libmath - low-level math utilities
libmath = project.StaticLibrary("math", env)
libmath.add_sources([src_dir / "math_utils.c"])
libmath.public.include_dirs.append(include_dir)
libmath.public.link_libs.append("m")  # Link math library

# Library: libphysics - depends on libmath
libphysics = project.StaticLibrary("physics", env)
libphysics.add_sources([src_dir / "physics.c"])
libphysics.link(libmath)  # Gets libmath's includes transitively

# Program: simulator - main application
simulator = project.Program("simulator", env)
simulator.add_sources([src_dir / "main.c"])
simulator.link(libphysics)  # Gets BOTH physics and math includes!

# Set defaults and generate
project.Default(simulator)

# Generate build files
Generator().generate(project, build_dir)

# Generate compile_commands.json for IDE integration
CompileCommandsGenerator().generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
print(f"Generated {build_dir / 'compile_commands.json'}")
```

### Debug and Release Variants

Use `set_variant()` to switch between debug and release builds.

**pcons-build.py:**
```python
#!/usr/bin/env python3
from pathlib import Path
from pcons import Project, find_c_toolchain, Generator, get_variant

# Get variant from command line: pcons --variant=debug
# Defaults to "release"
variant = get_variant("release")
build_dir = Path("build") / variant

toolchain = find_c_toolchain()
project = Project("myapp", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Apply variant settings
# debug: -O0 -g
# release: -O2 -DNDEBUG
env.set_variant(variant)

# Add extra flags
env.cc.flags.append("-Wall")

app = project.Program("myapp", env)
app.add_sources(["main.c"])

project.Default(app)
Generator().generate(project, build_dir)

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
# Apply warning flags (all warnings + warnings-as-errors)
env.apply_preset("warnings")

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
| `warnings` | `-Wall -Wextra -Wpedantic -Werror` | `/W4 /WX` |
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

---

## Working with External Dependencies

### Finding Packages with `project.find_package()`

The simplest way to use an external package is `project.find_package()`. It searches for the package using available finders (pkg-config, system paths) and returns an `ImportedTarget` that you can link against or apply to an environment.

```python
from pcons import Project, find_c_toolchain, Generator

toolchain = find_c_toolchain()
project = Project("myapp", build_dir="build")
env = project.Environment(toolchain=toolchain)

# Find packages (raises PackageNotFoundError if not found)
zlib = project.find_package("zlib")
openssl = project.find_package("openssl", version=">=3.0")

# Find with components
boost = project.find_package("boost", components=["filesystem", "system"])

# Optional dependency — returns None if not found
optional = project.find_package("optional-dep", required=False)

# Use as a dependency (public requirements auto-propagate)
app = project.Program("myapp", env, sources=["main.cpp"])
app.link(zlib)

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

Results are cached: calling `find_package("zlib")` twice returns the same target.

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
from pcons import Project, find_c_toolchain, Generator, get_variant
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

# Sync profile with toolchain settings
conan.sync_profile(toolchain, build_type=variant.capitalize())

# Install packages (cached, only runs when needed)
packages = conan.install()

print(f"Found packages: {list(packages.keys())}")

# Get the fmt package
fmt_pkg = packages.get("fmt")
if not fmt_pkg:
    raise RuntimeError("fmt package not found")

# Create project and environment
project = Project("conan_example", root_dir=project_dir, build_dir=build_dir)
env = project.Environment(toolchain=toolchain)
env.set_variant(variant)
env.cxx.flags.append("-std=c++17")

# Apply package settings with env.use()
env.use(fmt_pkg)

# Build program
hello = project.Program("hello_fmt", env)
hello.add_sources([project_dir / "src" / "main.cpp"])

project.Default(hello)
Generator().generate(project, build_dir)
```

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
| `-j N` or `--jobs=N` | Number of parallel build jobs |
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
from pcons import get_var

port = get_var('PORT', default='ofx')
use_cuda = get_var('USE_CUDA', default='0') == '1'
prefix = get_var('PREFIX', default='/usr/local')
```

---

## Advanced Topics

### Supported Source File Types

Pcons toolchains support various source file types beyond standard C/C++:

| Extension | Description | Toolchains |
|-----------|-------------|------------|
| `.c` | C source | All |
| `.cpp`, `.cxx`, `.cc` | C++ source | All |
| `.m` | Objective-C | LLVM |
| `.mm` | Objective-C++ | LLVM |
| `.s` | Assembly (preprocessed) | GCC, LLVM |
| `.S` | Assembly (needs C preprocessor) | GCC, LLVM |
| `.asm` | MASM assembly | MSVC, Clang-CL |
| `.rc` | Windows resource | MSVC, Clang-CL |
| `.metal` | Metal shaders (macOS) | LLVM |

These are handled automatically when you add sources to a target:

```python
# C/C++ sources
app.add_sources(["main.cpp", "util.c"])

# Windows resources (icons, dialogs, version info)
app.add_sources(["app.rc"])

# Assembly
lib.add_sources(["fast_math.S"])  # Uses C preprocessor
lib.add_sources(["startup.s"])    # Raw assembly
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

### Multi-Platform Builds

Handle platform differences in your build script:

```python
import sys
from pcons import find_c_toolchain

toolchain = find_c_toolchain()

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

### IDE Integration

Pcons generates `compile_commands.json` for IDE integration:

```python
from pcons.generators.compile_commands import CompileCommandsGenerator

# Generate compile_commands.json
CompileCommandsGenerator().generate(project, build_dir)
```

This enables features in:
- **VS Code** with clangd extension
- **CLion** and other JetBrains IDEs
- **Vim/Neovim** with coc-clangd
- **Emacs** with eglot or lsp-mode

### Alternative Generators

While Ninja is the default and recommended build executor, pcons also supports generating Makefiles for environments where Ninja isn't available.

#### MakefileGenerator

Generate a traditional Makefile instead of Ninja build files:

```python
from pcons.generators.makefile import MakefileGenerator

# Generate Makefile
MakefileGenerator().generate(project, build_dir)
# Creates build/Makefile
```

Then build with:

```bash
make -C build
```

The MakefileGenerator supports the same project structure as NinjaGenerator, so you can switch between them without changing your build script.

### Dependency Visualization

Generate dependency graphs:

```python
from pcons.generators.mermaid import MermaidGenerator

# Generate Mermaid diagram
MermaidGenerator().generate(project, build_dir)
# Creates build/deps.mmd
```

Or from the command line:

```bash
pcons generate --mermaid=deps.mmd    # To file
pcons generate --mermaid             # To stdout
pcons generate --graph=deps.dot      # DOT format
```

### Installing Files

Copy files to destination directories (paths are relative to `build_dir`):

```python
# Install library and headers (Install takes a list of sources)
project.Install("dist/lib", [mylib])
project.Install("dist/include", header_nodes)

# Install with rename (InstallAs takes a single source, not a list)
project.InstallAs("bundle/plugin.ofx", plugin_lib)

# Install an entire directory tree (recursive copy)
# Copies src_dir/assets/* to build/dist/assets/*
project.InstallDir("dist", src_dir / "assets")
```

**Note:** `Install()` accepts a list of sources and copies each to the destination directory. `InstallAs()` takes exactly one source and copies it to the specified path (with optional rename). If you need to install multiple files with renaming, use multiple `InstallAs()` calls.

`InstallDir` uses ninja's depfile mechanism for incremental rebuilds - if any file in the source directory changes, the copy is re-run.

### Environment Cloning

Create variant environments by cloning:

```python
# Base environment
env = project.Environment(toolchain=toolchain)

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
- If you see duplicate flags, check if you're accidentally adding flags multiple times in your script

### Temporary Environment Overrides

Use `env.override()` as a context manager to temporarily modify settings for specific files or targets. This creates a cloned environment with the specified changes, leaving the original unchanged.

```python
# Override cross-tool variables
with env.override(variant="profile") as profile_env:
    project.Program("app_profile", profile_env, sources=["main.cpp"])

# Override tool settings using double-underscore notation
# (because Python kwargs can't contain dots)
with env.override(cxx__flags=["-fno-exceptions"]) as no_except_env:
    project.Library("mylib", no_except_env, sources=["lib.cpp"])

# The yielded env is a full clone - you can modify it further
with env.override(variant="debug") as debug_env:
    debug_env.cxx.defines.append("EXTRA_DEBUG")
    debug_env.cxx.flags.extend(["-g3", "-fno-omit-frame-pointer"])
    project.Library("mylib_debug", debug_env, sources=["lib.cpp"])

# Combine multiple overrides
with env.override(variant="debug", cc__cmd="clang") as temp_env:
    # temp_env has both changes applied
    pass
```

This is particularly useful when you need to compile a few files with different settings without creating a permanent cloned environment.

### Custom Commands with env.Command()

Use `env.Command()` to run arbitrary shell commands as build steps. This is useful for code generators, asset processing, or any tool that doesn't fit the standard compile/link model.

```python
# Generate a header from a template
env.Command(
    "config.h",                              # Target file(s)
    ["config.h.in", "version.txt"],          # Source file(s)
    "python generate_config.py $SOURCES > $TARGET"
)

# Run a code generator with multiple outputs
env.Command(
    ["parser.c", "parser.h"],                # Multiple targets
    "grammar.y",                             # Single source
    "bison -d -o ${TARGETS[0]} $SOURCE"
)

# Command with no source dependencies
env.Command(
    "timestamp.txt",
    None,                                    # No sources
    "date > $TARGET"
)
```

**Variable substitution:**

| Variable | Description |
|----------|-------------|
| `$SOURCE` | First source file |
| `$SOURCES` | All source files (space-separated) |
| `$TARGET` | First target file |
| `$TARGETS` | All target files (space-separated) |
| `${SOURCES[n]}` | Indexed source access (0-based) |
| `${TARGETS[n]}` | Indexed target access (0-based) |
| `$$` | Literal `$` (escaped) |

Use `$$` to include a literal dollar sign in commands. This is useful for shell variables that should be expanded at build time rather than generation time:

```python
# Set rpath to $ORIGIN for portable shared libraries
env.link.flags.append("-Wl,-rpath,'$$ORIGIN'")

# Use shell environment variables
env.Command("output.txt", "input.txt", "echo $$HOME > $TARGET")
```

The command runs during the build phase, and Ninja tracks dependencies so the command only re-runs when sources change.

**Multiple commands:** Chain commands with shell operators:

```python
# Run multiple steps with && (stops on first failure)
env.Command(
    target="output.txt",
    source="input.txt",
    command="step1 $SOURCE -o temp.txt && step2 temp.txt -o $TARGET"
)
```

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
    project, env,
    identifier="com.example.myapp",
    version="1.0.0",
    sources=[app],
    install_location="/usr/local/bin",
)
```

**Full-featured installer** with welcome screen, license, and branding (wraps `productbuild`):

```python
pkg = macos.create_pkg(
    project, env,
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
    project, env,
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
    project, env,
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
| `sign_password` | Certificate password |

#### Complete Platform-Conditional Example

```python
from pcons.contrib import platform

installer_targets = []

if platform.is_macos():
    from pcons.contrib.installers import macos

    pkg = macos.create_pkg(
        project, env,
        name="MyApp", version="1.0.0",
        identifier="com.example.myapp",
        sources=[app],
        install_location="/usr/local/bin",
    )
    dmg = macos.create_dmg(project, env, name="MyApp", sources=[app])
    installer_targets.extend([pkg, dmg])

elif platform.is_windows():
    from pcons.contrib.installers import windows

    msix = windows.create_msix(
        project, env,
        name="MyApp", version="1.0.0.0",
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

### Multi-Architecture Builds

Pcons supports building for multiple CPU architectures, which is useful for:
- **macOS**: Creating universal binaries that run on both Intel and Apple Silicon
- **Windows**: Building for x64, x86, or ARM64

#### Target Architecture API

Use `env.set_target_arch()` to configure an environment for a specific architecture:

```python
from pcons import Project, find_c_toolchain, Generator

project = Project("mylib")
toolchain = find_c_toolchain()

# Create environment for arm64
env_arm64 = project.Environment(toolchain=toolchain)
env_arm64.set_target_arch("arm64")
env_arm64.build_dir = Path("build/arm64")

# Create environment for x86_64
env_x86_64 = project.Environment(toolchain=toolchain)
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
- Supported architectures: `x64`, `x86`, `arm64`, `arm64ec`
- Aliases: `amd64`→`x64`, `x86_64`→`x64`, `aarch64`→`arm64`

**Windows (Clang-CL):**
- Adds `--target=<triple>` to compilers (e.g., `--target=aarch64-pc-windows-msvc`)
- Adds `/MACHINE:<ARCH>` to linker

#### macOS Universal Binaries

To create a universal binary that runs on both Intel and Apple Silicon Macs, build for each architecture separately and combine with `lipo`:

```python
from pathlib import Path
from pcons import Project, find_c_toolchain, Generator
from pcons.util.macos import create_universal_binary

project = Project("mylib")
toolchain = find_c_toolchain()

# Build for arm64
env_arm64 = project.Environment(toolchain=toolchain)
env_arm64.set_target_arch("arm64")
env_arm64.set_variant("release")
lib_arm64 = project.StaticLibrary("mylib", env_arm64, sources=["lib.c"])
# Note: output goes to build/libmylib.a by default

# Build for x86_64 (use different build dir to avoid conflicts)
env_x86_64 = project.Environment(toolchain=toolchain)
env_x86_64.set_target_arch("x86_64")
env_x86_64.set_variant("release")
env_x86_64.build_dir = Path("build/x86_64")
lib_x86_64 = project.StaticLibrary("mylib_x86", env_x86_64, sources=["lib.c"])

# Combine into universal binary
lib_universal = create_universal_binary(
    project,
    "mylib_universal",
    inputs=[lib_arm64, lib_x86_64],
    output="build/universal/libmylib.a"
)

project.Default(lib_universal)
Generator().generate(project, "build")
```

The `create_universal_binary()` function:
- Takes a list of architecture-specific binaries (as Targets, FileNodes, or paths)
- Uses `lipo -create` to combine them
- Returns a Target object representing the universal binary

This works for static libraries, dynamic libraries, and executables.

### Cross-Compilation Presets

For cross-compiling to other platforms, pcons provides ready-made presets that configure sysroot, target triple, architecture flags, and SDK paths.

```python
from pcons.toolchains.presets import android, ios, wasm, linux_cross

# Android NDK
env.apply_cross_preset(android(ndk="~/android-ndk", arch="arm64-v8a"))

# iOS
env.apply_cross_preset(ios(arch="arm64", min_version="15.0"))

# iOS Simulator
env.apply_cross_preset(ios(arch="x86_64"))

# WebAssembly via Emscripten
env.apply_cross_preset(wasm(emsdk="~/emsdk"))
# Or if emcc is already in PATH:
env.apply_cross_preset(wasm())

# Generic Linux cross-compilation
env.apply_cross_preset(linux_cross(
    triple="aarch64-linux-gnu",
    sysroot="/opt/aarch64-sysroot",
))
```

#### Available Factory Functions

| Factory | Key Arguments | Description |
|---------|--------------|-------------|
| `android(ndk, arch, api)` | `arch`: arm64-v8a, armeabi-v7a, x86_64, x86; `api`: minimum API level (default 21) | Android NDK cross-compilation |
| `ios(arch, min_version, sdk)` | `arch`: arm64 or x86_64 (simulator); `min_version`: deployment target | iOS cross-compilation |
| `wasm(emsdk)` | `emsdk`: path to Emscripten SDK (optional if emcc in PATH) | WebAssembly via Emscripten |
| `linux_cross(triple, sysroot)` | `triple`: GCC/Clang target triple; `sysroot`: target sysroot path | Generic Linux cross-compilation |

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
    env_vars={
        "CC": "/opt/riscv/bin/riscv64-unknown-elf-gcc",
        "CXX": "/opt/riscv/bin/riscv64-unknown-elf-g++",
    },
)
env.apply_cross_preset(preset)
```

The `CrossPreset` fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable name |
| `arch` | `str` | Target architecture |
| `triple` | `str \| None` | Compiler target triple (used with `--target` on Clang) |
| `sysroot` | `str \| None` | Path to target sysroot (`--sysroot`) |
| `sdk_path` | `str \| None` | Path to SDK root |
| `extra_compile_flags` | `tuple[str, ...]` | Additional compile flags |
| `extra_link_flags` | `tuple[str, ...]` | Additional link flags |
| `env_vars` | `dict[str, str]` | CC/CXX command overrides |

### Compiler Cache

Speed up rebuilds by wrapping compile commands with [ccache](https://ccache.dev/) or [sccache](https://github.com/mozilla/sccache):

```python
# Auto-detect: tries sccache first, then ccache
env.use_compiler_cache()

# Explicit choice
env.use_compiler_cache("ccache")
env.use_compiler_cache("sccache")
```

This prepends the cache tool to the `cc` and `cxx` commands. Only compile commands are wrapped — the linker and archiver are left unchanged. If the requested tool isn't in PATH, a warning is logged and no changes are made.

Notes:
- On MSVC (`cl.exe`), only sccache works. If you request ccache with an MSVC toolchain, pcons warns and does nothing.
- Commands are never double-wrapped: calling `use_compiler_cache()` when commands are already wrapped is a no-op.

### Multiple Toolchains

Pcons supports combining multiple toolchains in a single environment. This is useful for projects that mix languages, such as C++ with CUDA, or C++ with Cython.

#### Adding Additional Toolchains

Use `env.add_toolchain()` to add extra toolchains to an environment:

```python
from pcons import Project, find_c_toolchain
from pcons.toolchains import find_cuda_toolchain

project = Project("gpu_app", build_dir="build")
toolchain = find_c_toolchain()

# Create environment with C/C++ toolchain
env = project.Environment(toolchain=toolchain)

# Add CUDA toolchain for .cu files
cuda_toolchain = find_cuda_toolchain()
if cuda_toolchain:
    env.add_toolchain(cuda_toolchain)

# Now this target can have both .cpp and .cu sources
app = project.Program("gpu_app", env)
app.add_sources([
    "main.cpp",       # Compiled with C++ compiler
    "kernel.cu",      # Compiled with CUDA nvcc
])

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
env = project.Environment(toolchain=c_toolchain)
env.add_toolchain(cuda_toolchain)

# This applies "debug" settings to both C++ AND CUDA compilers
env.set_variant("debug")
# C++ gets: -O0 -g
# CUDA gets: -G -g (device debugging)
```

#### Available Toolchain Finders

| Function | Description |
|----------|-------------|
| `find_c_toolchain()` | Find C/C++ toolchain (LLVM, GCC, MSVC, etc.) |
| `find_cuda_toolchain()` | Find CUDA toolchain (returns `None` if nvcc not found) |

```python
from pcons.toolchains import find_c_toolchain, find_cuda_toolchain

# Both return None if not available
c_toolchain = find_c_toolchain()
cuda_toolchain = find_cuda_toolchain()
```

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
env = project.Environment(toolchain=toolchain)

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
int_size = checks.check_type_size("int")    # Returns 4 on most systems
ptr_size = checks.check_type_size("void*")  # 8 on 64-bit, 4 on 32-bit

# Check if a function is available (compiles + links)
if checks.check_function("pthread_create", headers=["pthread.h"], libs=["pthread"]).success:
    env.link.libs.append("pthread")

# Read a predefined compiler macro
gcc_ver = checks.check_define("__GNUC__")  # e.g. "14"
```

All results are automatically cached through `Configure`. On the first run, each check compiles a test program; on subsequent runs, cached results are returned instantly:

```python
result1 = checks.check_flag("-Wall")
assert result1.cached is False    # First run: compiled a test

result2 = checks.check_flag("-Wall")
assert result2.cached is True     # Second run: from cache
```

The cache key includes the compiler path, so switching compilers invalidates the relevant entries automatically.

### Configure: Caching, Defines, and Config Headers

`Configure` serves as the shared state between checks and the config header generator. You can also use it directly to define values, find programs, or record features you know about without needing a compiler check:

```python
config = Configure(build_dir=Path("build"))

# Find a program in PATH (result is cached)
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
config.check_sizeof("int")     # Defines SIZEOF_INT
config.check_sizeof("void*")   # Defines SIZEOF_VOIDP
config.undefine("MISSING_FEATURE")

# Generate the header
config.write_config_header(
    Path("build/config.h"),
    guard="MY_CONFIG_H",
    include_platform=True,      # Add PCONS_OS_* and PCONS_ARCH_* defines
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

Note: `config.check_sizeof()` uses Python's `ctypes` to determine sizes on the host machine. For cross-compilation where host and target sizes differ, use `ToolChecks.check_type_size()` instead — it compiles a test program with the target compiler.

---

## Troubleshooting

### No toolchain found

**Error:** `RuntimeError: No C/C++ toolchain found`

**Solution:** Install a compiler:
- macOS: `xcode-select --install`
- Ubuntu/Debian: `sudo apt install build-essential`
- Fedora: `sudo dnf install gcc gcc-c++`
- Windows: Install Visual Studio with C++ workload

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
| `target.link(*targets)` | Add library dependencies |
| `target.public.include_dirs` | Include dirs for consumers |
| `target.public.link_libs` | Link libs for consumers |
| `target.public.defines` | Defines for consumers |
| `target.private.compile_flags` | Flags for this target only |

### Environment Methods

| Method | Description |
|--------|-------------|
| `env.set_variant(name)` | Set debug/release variant |
| `env.set_target_arch(arch)` | Set target CPU architecture |
| `env.apply_preset(name)` | Apply flag preset (warnings, sanitize, profile, lto, hardened) |
| `env.apply_cross_preset(preset)` | Apply cross-compilation preset |
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
| `get_var(name, default)` | Get a build variable |
| `get_variant(default)` | Get the build variant |

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
| `config.check_sizeof(type)` | Get the size of a type and define `SIZEOF_*` |
| `config.check_header(name)` | Check if a header exists |
| `config.write_config_header(path)` | Generate a config.h file |
| `ToolChecks(config, env, tool)` | Create feature checker for a tool |
| `checks.check_flag(flag)` | Check if compiler accepts a flag |
| `checks.check_header(name)` | Check if a header exists |
| `checks.check_type(name, headers=[])` | Check if a type exists |
| `checks.check_type_size(name)` | Get the size of a type |

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
    env.cxx.includes.extend([
        "openfx/include",
        "openfx/Examples/include",
    ])
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
from pcons import Project, find_c_toolchain, Generator
from pcons.modules import ofx  # Auto-loaded!

project = Project("myplugin")
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)

ofx.setup_env(env)
plugin = ofx.create_bundle(
    project, env, "myplugin",
    sources=["src/plugin.cpp"],
    build_dir=project.build_dir,
)

Generator().generate(project)
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

| `pcons.contrib.platform` Function | Description |
|----------------------------------|-------------|
| `is_macos()`, `is_linux()`, `is_windows()` | Platform checks |
| `get_platform_name()` | Get platform name ("darwin", "linux", "win32") |
| `get_arch()` | Get current architecture ("x86_64", "arm64", etc.) |
| `get_shared_lib_extension()` | Get shared lib extension (".dylib", ".so", ".dll") |
| `format_shared_lib_name(name)` | Format as shared lib filename |

---

## Further Reading

- [Architecture Document](architecture.md) - Design details and implementation status
- [Example Projects](https://github.com/DarkStarSystems/pcons/tree/main/examples) - Working examples to learn from
- [Contributing Guide](contributing.md) - How to contribute to pcons

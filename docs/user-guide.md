# Pcons User Guide

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

### Prerequisites

Before using pcons, ensure you have:

1. **uv** - The fast Python package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
2. **A C/C++ compiler** for C/C++ projects like the one in this user guide.
  - One of:
    - Clang/LLVM (macOS, Linux)
    - Clang-CL (Windows - MSVC-compatible Clang)
    - GCC (Linux)
    - MSVC (Windows)
  For other types of projects like building docs, swig, or game assets, pcons can use your own tools.
3. **Ninja** - The build executor ([install guide](https://ninja-build.org/))
You don't really need to install ninja because pcons will use `uvx ninja` when needed.

### Installing Pcons

You can run pcons directly from PyPI with `uvx` (no installation required):

```bash
uvx pcons
```

Or add it to your project:

```bash
uv add pcons
```

Or install globally:

```bash
uv tool install pcons
```

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
from pathlib import Path
from pcons import Project, find_c_toolchain, NinjaGenerator

# Find an available C/C++ toolchain (clang, gcc, or msvc)
toolchain = find_c_toolchain()

# Create project with build directory
project = Project("hello", build_dir="build")

# Create an environment with the toolchain
env = project.Environment(toolchain=toolchain)

# Create a program target
hello = project.Program("hello", env)
hello.add_sources(["hello.cpp"])

# Set this as the default target
project.Default(hello)

# Resolve dependencies and generate build files
project.resolve()
NinjaGenerator().generate(project, "build")

print(f"Generated build/build.ninja")
```

**3. Generate and build**:

```bash
# Using uvx (recommended)
uvx pcons

# Or if pcons is installed
pcons
```

This runs your `pcons-build.py` to generate `build/build.ninja`, then invokes Ninja to compile your program.

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

Your script must explicitly call `project.resolve()` and a generator at the end:

```python
# ... define targets ...

# REQUIRED: Resolve all dependencies (computes effective requirements)
project.resolve()

# REQUIRED: Generate build files
NinjaGenerator().generate(project, build_dir)
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
#   Unix:    llvm, gcc
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

# Running "ninja" builds defaults; "ninja all" also builds defaults
```

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
from pathlib import Path
from pcons import Project, find_c_toolchain, NinjaGenerator

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
project.resolve()
NinjaGenerator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, NinjaGenerator

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
project.resolve()
NinjaGenerator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, NinjaGenerator

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
project.resolve()
NinjaGenerator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, NinjaGenerator

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
project.resolve()
NinjaGenerator().generate(project, "build")
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
from pcons import Project, find_c_toolchain, NinjaGenerator
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
project.resolve()

# Generate build files
NinjaGenerator().generate(project, build_dir)

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
from pcons import Project, find_c_toolchain, NinjaGenerator, get_variant

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
project.resolve()
NinjaGenerator().generate(project, build_dir)

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

---

## Working with External Dependencies

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
from pcons import Project, find_c_toolchain, NinjaGenerator, get_variant
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
project.resolve()
NinjaGenerator().generate(project, build_dir)
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

project.resolve()

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

project.resolve()

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

project.resolve()

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
from pcons import Project, find_c_toolchain, NinjaGenerator

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
from pcons import Project, find_c_toolchain, NinjaGenerator
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
project.resolve()
NinjaGenerator().generate(project, "build")
```

The `create_universal_binary()` function:
- Takes a list of architecture-specific binaries (as Targets, FileNodes, or paths)
- Uses `lipo -create` to combine them
- Returns a Target object representing the universal binary

This works for static libraries, dynamic libraries, and executables.

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

project.resolve()
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

Pcons provides a configuration system for detecting compiler capabilities, headers, and types. This is useful for generating config headers or conditionally enabling features.

### Configure and ToolChecks

```python
from pcons.configure.config import Configure
from pcons.configure.checks import ToolChecks

# Create configuration context
config = Configure(build_dir=Path("build"))

# Create environment with toolchain
env = project.Environment(toolchain=toolchain)

# Create checker for the C compiler
checks = ToolChecks(config, env, "cc")

# Check if a compiler flag is supported
if checks.check_flag("-Wall").success:
    env.cc.flags.append("-Wall")

# Check if a header exists
if checks.check_header("sys/mman.h").success:
    env.cc.defines.append("HAVE_MMAN_H")

# Check if a type exists (optionally with headers)
if checks.check_type("size_t", headers=["stddef.h"]).success:
    pass

# Get the size of a type
int_size = checks.check_type_size("int")  # Returns 4 on most systems
ptr_size = checks.check_type_size("void*")  # 8 on 64-bit, 4 on 32-bit
```

### Caching

Check results are automatically cached in the build directory. Subsequent runs with the same compiler and flags will use cached results, making configuration fast:

```python
# First run - compiles test programs
result1 = checks.check_flag("-Wall")
assert result1.cached is False

# Second run - uses cache
result2 = checks.check_flag("-Wall")
assert result2.cached is True
```

### Generating Config Headers

Use `Configure` to detect features and generate a `config.h` file:

```python
from pathlib import Path
from pcons.configure.config import Configure

config = Configure(build_dir=Path("build"))

# Define features
config.define("HAVE_FEATURE_A")
config.define("VERSION_MAJOR", 1)
config.define("VERSION_MINOR", 2)
config.define("VERSION_STRING", "1.2.0")

# Conditionally define based on checks
if config.check_header("sys/mman.h"):
    pass  # HAVE_SYS_MMAN_H is automatically defined

# Check and record type sizes
config.check_sizeof("int")    # Defines SIZEOF_INT
config.check_sizeof("void*")  # Defines SIZEOF_VOIDP

# Mark missing features
config.undefine("MISSING_FEATURE")

# Generate the header file
config.write_config_header(
    Path("build/config.h"),
    guard="MY_CONFIG_H",           # Custom include guard
    include_platform=True,         # Add PCONS_OS_* and PCONS_ARCH_* defines
)
```

This generates a header like:

```c
#ifndef MY_CONFIG_H
#define MY_CONFIG_H

/* Platform detection */
#define PCONS_OS_MACOS 1
#define PCONS_ARCH_ARM64 1

/* Feature detection */
#define HAVE_FEATURE_A 1
#define VERSION_MAJOR 1
#define VERSION_MINOR 2
#define VERSION_STRING "1.2.0"
#define SIZEOF_INT 4
#define SIZEOF_VOIDP 8
/* #undef MISSING_FEATURE */

#endif /* MY_CONFIG_H */
```

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
| `NinjaGenerator` | Generate Ninja build files (default) |
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

## Further Reading

- [Architecture Document](architecture.md) - Design details and implementation status
- [Example Projects](https://github.com/garyo/pcons/tree/main/examples) - Working examples to learn from
- [Contributing Guide](contributing.md) - How to contribute to pcons

# Pcons User Guide

Pcons is a Python-based build system that generates [Ninja](https://ninja-build.org/) build files for C/C++ projects. It combines some of the best ideas from SCons and CMake: Python as the configuration language, environments with tools, and a fast generator architecture with proper dependency tracking.

## Why Pcons?

### Key Features

- **Python is the language**: No custom DSL to learn. Your `build.py` is real Python with full IDE support, debugging, and all the power of the Python ecosystem.
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

**2. Create the build script** (`build.py`):

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

This runs your `build.py` to generate `build/build.ninja`, then invokes Ninja to compile your program.

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
pcons info               # Show build.py documentation
pcons init               # Create a template build.py
```

---

## Core Concepts

Understanding these core concepts will help you write effective pcons build scripts.

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

Nodes represent files in the dependency graph. You rarely create them directly; instead, use `project.node()` for deduplication:

```python
# Create or get a node for a file
src_node = project.node("src/main.cpp")

# Nodes track:
# - Path to the file
# - Builder that creates it (if any)
# - Dependencies
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

---

## Building Projects Step by Step

Let's walk through a few progressively more complex examples.

### Hello World - Single File Program

The simplest possible project: one source file, one output.

**File structure:**
```
project/
├── build.py
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

**build.py:**
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
├── build.py
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

**build.py:**
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
├── build.py
├── include/
│   └── math_utils.h
└── src/
    ├── main.c
    └── math_utils.c
```

**build.py:**
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

**build.py:**
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

# Optional: customize output name
libplugin.output_name = "myplugin.so"  # Override default libplugin.so

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
├── build.py
├── include/
│   ├── math_utils.h
│   └── physics.h
└── src/
    ├── main.c
    ├── math_utils.c
    └── physics.c
```

**build.py:**
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

**build.py:**
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

**build.py:**
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

Access them in `build.py`:

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

Copy files to destination directories:

```python
# Install library and headers
project.Install("dist/lib", [mylib])
project.Install("dist/include", header_nodes)

# Install with rename
project.InstallAs("bundle/plugin.ofx", plugin_lib)
```

### Environment Cloning

Create variant environments:

```python
# Base environment
env = project.Environment(toolchain=toolchain)

# Clone for profiling
profile_env = env.clone()
profile_env.cxx.flags.extend(["-pg", "-fno-omit-frame-pointer"])

# Build both variants
app_release = project.Program("app", env)
app_profile = project.Program("app_profile", profile_env)
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
| `env.use(package)` | Apply package settings |
| `env.clone()` | Create a copy |
| `env.cc` | C compiler settings |
| `env.cxx` | C++ compiler settings |
| `env.link` | Linker settings |

### Helper Functions

| Function | Description |
|----------|-------------|
| `find_c_toolchain()` | Find an available C/C++ toolchain (platform-aware defaults) |
| `find_c_toolchain(prefer=[...])` | Find toolchain with explicit preference order |
| `get_var(name, default)` | Get a build variable |
| `get_variant(default)` | Get the build variant |

---

## Further Reading

- [Architecture Document](../ARCHITECTURE.md) - Design details and implementation status
- [Example Projects](../tests/examples/) - Working examples to learn from
- [Contributing Guide](../CONTRIBUTING.md) - How to contribute to pcons

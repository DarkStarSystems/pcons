# pcons User Manual

pcons is a Python-based build system that generates [Ninja](https://ninja-build.org/) build files. It provides a clean, Pythonic API for defining build rules without the complexity of traditional build systems.

## Why pcons?

- **No framework magic** - Your `build.py` is just a Python script. Import pcons and use it however you want.
- **Tool-agnostic core** - The core knows nothing about C++ or any language. All language support comes through Tools and Toolchains.
- **Ninja backend** - Fast, parallel builds with proper dependency tracking.
- **Extensible** - Create custom tools for any build step.

## Quick Start

### Installation

```bash
pip install pcons
```

### Your First Build Script

Create a `build.py`:

```python
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# Find an available C compiler (clang, gcc, or msvc)
toolchain = find_c_toolchain()

# Create a project
project = Project("hello", build_dir="build")
env = project.Environment(toolchain=toolchain)

# Compile and link
obj = env.cc.Object("build/hello.o", "src/hello.c")
env.link.Program("build/hello", obj)

# Generate build.ninja
generator = NinjaGenerator()
generator.generate(project, "build")
```

Run it:

```bash
python build.py
ninja -C build
```

## Command Line Interface

pcons provides a command-line tool for common build operations. The CLI handles configuration, generation, and building in a streamlined workflow.

### Basic Usage

```bash
pcons              # Configure (if needed) → Generate → Build
pcons build        # Same as above
pcons generate     # Configure (if needed) → Generate only
pcons clean        # Clean build outputs (runs ninja -t clean)
pcons info         # Show build.py documentation and available variables
```

### Configuration

The **configure** phase detects available tools, runs feature checks, and caches results. This happens automatically on first run.

- **Auto-configure**: Configuration runs automatically if no cache exists
- **Caching**: Results are saved to `pcons_config.json` in the build directory
- **Re-configure**: Use `--reconfigure` or `-C` to force re-running configuration checks

```bash
pcons --reconfigure    # Force re-run configuration checks
pcons -C               # Short form
```

In your `build.py`, configuration typically looks like:

```python
from pcons.configure.config import Configure

config = Configure(build_dir=build_dir)
if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    toolchain = find_c_toolchain()
    toolchain.configure(config)
    # Run any other checks here
    config.set("configured", True)
    config.save()

project = Project("myproject")
```

### Build Variants

Use the `--variant` (or `-v`) flag to select debug/release builds:

```bash
pcons --variant=debug     # Debug build (-O0 -g)
pcons --variant=release   # Release build (-O2 -DNDEBUG)
pcons -v debug            # Short form
```

The variant is passed to your build script and affects toolchain flags. In `build.py`:

```python
env = project.Environment(toolchain=toolchain)
env.set_variant("debug")  # Or get from command line args
```

### Build Variables

Pass variables to your build script using `NAME=value` syntax:

```bash
pcons PORT=ofx VARIANT=release
pcons CC=clang CXX=clang++ USE_CUDA=1
```

Access variables in `build.py` using `get_var()`:

```python
from pcons import get_var

# Get variable with default
port = get_var('PORT', default='ofx')
use_cuda = get_var('USE_CUDA', default='0') == '1'

# Configure based on variables
if port == 'ofx':
    sources.append('src/plugin-ofx.cpp')
elif port == 'ae':
    sources.append('src/plugin-ae.cpp')
```

**Variable precedence** (highest to lowest):
1. Command line: `pcons VAR=value`
2. Environment variable: `VAR=value pcons` (only if not set on command line)

Variables are stored internally by pcons and do not pollute the shell environment for subprocesses. This keeps build commands clean and reproducible.

**Common variables**:
- `PORT` - Select build target/port (e.g., `ofx`, `ae`)
- `CC`, `CXX` - Override C/C++ compiler
- `PREFIX` - Installation prefix
- Custom project-specific variables

**Documenting variables**: Add a docstring to your `build.py` to document available variables. Users can view it with `pcons info`:

```python
"""Build script for MyProject.

Variables:
    PORT      - Build target: ofx, ae (default: ofx)
    USE_CUDA  - Enable CUDA: 0, 1 (default: 0)
"""
```

```bash
$ pcons info
Build script: build.py

Build script for MyProject.

Variables:
    PORT      - Build target: ofx, ae (default: ofx)
    USE_CUDA  - Enable CUDA: 0, 1 (default: 0)
```

### How It Works

1. **First run**: Configuration checks run (find compilers, check features), results cached
2. **Subsequent runs**: Load cached config, skip checks, generate and build
3. **When to reconfigure**: After installing new compilers, changing system setup, or if builds fail unexpectedly

## Core Concepts

### Project

A `Project` is the top-level container for your build. It holds all environments and targets.

```python
project = Project("myproject", build_dir="build")
```

### Environment

An `Environment` holds tool configurations and creates build targets. You can have multiple environments (e.g., for different platforms or variants).

```python
env = project.Environment(toolchain=toolchain)
env.cc.flags = ["-Wall", "-Wextra"]
env.cc.defines = ["-DVERSION=1"]
```

### Tools and Toolchains

A **Tool** knows how to perform a specific build operation (compile, link, etc.). A **Toolchain** is a coordinated set of tools that work together.

```python
from pcons.toolchains import find_c_toolchain

# Auto-detect available toolchain
toolchain = find_c_toolchain()  # tries llvm, gcc, msvc

# Or specify preference
toolchain = find_c_toolchain(prefer=["gcc", "llvm"])
```

### Builders

Each tool provides **Builders** - methods that create build targets:

```python
# cc tool provides Object builder
obj = env.cc.Object("build/main.o", "src/main.c")

# link tool provides Program and SharedLibrary builders
env.link.Program("build/myapp", obj)
```

### Variants

Set build variants like "debug" or "release". The toolchain interprets what these mean:

```python
env.set_variant("debug")    # -O0 -g for gcc/clang
env.set_variant("release")  # -O2 -DNDEBUG for gcc/clang
```

### Installing Files

Use `Install()` and `InstallAs()` to copy files to destination directories. This is useful for creating application bundles, packaging, or deployment.

```python
# Install files to a directory (keeps original filenames)
project.Install("dist/lib", [mylib])           # Install library
project.Install("dist/bin", [myapp])           # Install program
project.Install("dist/include", header_files)  # Install headers

# Install with rename
project.InstallAs("bundle/plugin.ofx", plugin_lib)  # Rename during install
project.InstallAs("dist/icon.png", "src/app_icon.png")
```

Install targets integrate with the dependency graph - they're built automatically when needed. You can declare Install targets anywhere in your build script; the actual file copying happens after all dependencies are resolved.

**Custom output names**: Use `output_name` to override default library/program naming:

```python
plugin = project.SharedLibrary("myplugin", env, sources=sources)
plugin.output_name = "myplugin.ofx"  # Instead of libmyplugin.dylib
```

**Bundle example** (macOS OFX plugin):

```python
bundle_dir = build_dir / "MyPlugin.ofx.bundle" / "Contents" / "MacOS"
resources_dir = build_dir / "MyPlugin.ofx.bundle" / "Contents" / "Resources"

# Build plugin with custom suffix
plugin = project.SharedLibrary("myplugin", env, sources=plugin_sources)
plugin.output_name = "myplugin.ofx"

# Install to bundle
project.Install(bundle_dir, [plugin])
project.InstallAs(resources_dir / "icon.png", "src/icon.png")
```

## Creating Custom Tools

You can create tools for any build step. Here's a tool that concatenates files:

```python
from pcons.core.builder import CommandBuilder
from pcons.tools.tool import BaseTool

class ConcatTool(BaseTool):
    def __init__(self) -> None:
        super().__init__("concat")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "cat",
            "bundlecmd": "$concat.cmd $$in > $$out",
        }

    def builders(self) -> dict[str, object]:
        return {
            "Bundle": CommandBuilder(
                "Bundle",
                "concat",
                "bundlecmd",
                src_suffixes=[".txt"],
                target_suffixes=[".txt"],
                single_source=False,
            ),
        }

# Use it
concat_tool = ConcatTool()
concat_tool.setup(env)
env.concat.Bundle("build/combined.txt", [
    "src/header.txt",
    "src/body.txt",
])
```

## Command Templates

Command templates use variable substitution:

- `$tool.var` - Substitutes the tool variable (e.g., `$cc.cmd` → `gcc`)
- `$$in` - The input file(s)
- `$$out` - The output file

Example:
```python
"objcmd": "$cc.cmd $cc.flags -c -o $$out $$in"
# Becomes: gcc -Wall -O2 -c -o build/main.o src/main.c
```

## Examples

See the `tests/examples/` directory for complete working examples:

- `01_concat` - Custom tool demonstration
- `02_hello_c` - Simple C program
- `03_multi_file` - Multi-file C project with includes
- `04_variants` - Debug/release build variants

## API Reference

### pcons.get_var

```python
get_var(name: str, default: str | None = None) -> str | None
```

Get a build variable set on the command line or from environment.

### pcons.core.project.Project

```python
Project(name: str, build_dir: str | Path)
project.Install(dest_dir, sources, name=None) -> Target
project.InstallAs(dest, source, name=None) -> Target
project.Default(*targets) -> None
project.Alias(name, *targets) -> AliasNode
project.resolve() -> None
```

### pcons.core.environment.Environment

```python
project.Environment(toolchain: Toolchain | None = None)
env.set_variant(name: str, **kwargs)
env.clone() -> Environment
```

### pcons.toolchains

```python
find_c_toolchain(prefer: list[str] | None = None) -> Toolchain
toolchain_registry.register(...)  # For custom toolchains
```

### pcons.tools.tool.BaseTool

```python
class MyTool(BaseTool):
    def __init__(self) -> None:
        super().__init__("toolname")

    def default_vars(self) -> dict[str, object]: ...
    def builders(self) -> dict[str, Builder]: ...
```

### pcons.generators.ninja.NinjaGenerator

```python
generator = NinjaGenerator()
generator.generate(project: Project, build_dir: str | Path)
```

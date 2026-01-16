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

### pcons.core.project.Project

```python
Project(name: str, build_dir: str | Path)
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

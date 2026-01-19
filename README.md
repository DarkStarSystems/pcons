# Pcons

A modern Python-based build system that generates Ninja (or Makefile) build files.

[![CI](https://github.com/garyo/pcons/actions/workflows/main.yml/badge.svg)](https://github.com/garyo/pcons/actions/workflows/main.yml)

## Overview

Pcons is inspired by [SCons](https://scons.org) and [CMake](https://cmake.org), taking the best ideas from each:

- **From SCons**: Environments, Tools, dependency tracking, Python as the configuration language
- **From CMake**: Generator architecture (configure once, build fast), usage requirements that propagate through dependencies

**Key design principles:**

- **Configuration, not execution**: Pcons generates Ninja files; Ninja executes the build
- **Python is the language**: No custom DSL—build scripts are real Python with full IDE support
- **Language-agnostic**: Build C++, Rust, LaTeX, protobuf, or anything else
- **Explicit over implicit**: Dependencies are discoverable and traceable

## Status

🚧 **Under active development** - ready for experimentation and feedback.

Core functionality is working: C/C++ compilation, static and shared libraries, programs, and install targets. See [ARCHITECTURE.md](ARCHITECTURE.md) for design details.

## Quick Example

```python
# pcons-build.py
from pcons.core.project import Project
from pcons.toolchains import find_c_toolchain

project = Project("myapp", build_dir="build")

# Find and configure a C/C++ toolchain
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)
env.cc.flags.extend(["-Wall"])

# Build a static library
lib = project.StaticLibrary("core", env)
lib.sources.append(project.node("src/core.c"))
lib.public.include_dirs.append(Path("include"))

# Build a program
app = project.Program("myapp", env)
app.sources.append(project.node("src/main.c"))
app.link(lib)

project.Default(app)
project.resolve()
project.generate()
```

```bash
uv run pcons-build.py
ninja -C build
```

## Installation

```bash
# Using uv (recommended)
uv add pcons

# Or pip
pip install pcons
```

For development:

```bash
git clone https://github.com/garyo/pcons.git
cd pcons
uv sync
```

## Development

```bash
# Run tests
uv run pytest

# Run linter
make lint

# Format code
make fmt

# Or use uv directly
uv run ruff check pcons/
uv run mypy pcons/
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Design document and implementation status
- [CONTRIBUTING.md](CONTRIBUTING.md) - How to contribute

## License

MIT License - see [LICENSE](LICENSE)

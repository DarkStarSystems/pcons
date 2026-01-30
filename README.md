# Pcons

A modern Python-based build system. Builds anything that requires a repeatable workflow using a dependency graph. Works with Ninja (or Makefile) to do the builds.

[![CI](https://github.com/DarkStarSystems/pcons/actions/workflows/main.yml/badge.svg)](https://github.com/DarkStarSystems/pcons/actions/workflows/main.yml)
[![PyPI](https://img.shields.io/pypi/v/pcons)](https://pypi.org/project/pcons/)
[![Python](https://img.shields.io/pypi/pyversions/pcons)](https://pypi.org/project/pcons/)

## Overview

Pcons is inspired by [SCons](https://scons.org) and [CMake](https://cmake.org), taking a few of the best ideas from each:

- **From SCons**: Environments, Tools, dependency tracking, Python as the configuration language
- **From CMake**: Generator architecture (configure once, build fast), usage requirements that propagate through dependencies

**Key design principles:**

- **Configuration, not execution**: Pcons generates Ninja files; Ninja executes the build
- **Python is the language**: No custom DSL—build scripts are real Python with full IDE support
- **Language-agnostic**: Build C++, Rust, LaTeX, protobuf, or anything else
- **Explicit over implicit**: Dependencies are discoverable and traceable
- **Extensible**: Add-on modules for domain-specific tasks (plugin bundles, SDK configuration, etc.)

## Why another software build tool?

I was one of the original developers of SCons, and helped maintain it for many years. I love that python is the config language; that makes build descriptions incredibly flexible and powerful. Recently I've been using CMake for more projects, and despite the deeply painful configuration language, I've come to appreciate its power: conan integration, the separation between *describing* the build and running it, and dependency propagation, among other things. I feel that SCons hasn't kept up with modern python; like any very widely used mature project, it has a lot of accumulated wisdom but also a bit ossified ways of doing things.

I've been thinking for years now about rearchitecting SCons onto a modern python stack with Path and decorators and all the other wonderful stuff python has been doing, and fixing some of the pain points at the same time (substitution/quoting, extensibility, tracing, separation between description and building, and more), but I've never had the time to dig into it. But recently as I've been using a lot more of Claude Code as a programming assistant, and it has gotten significantly better, it seemed like the right time to try this as a collaborative project. So, meet pcons!

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
env = project.Environment(toolchain=find_c_toolchain())
env.cc.flags.extend(["-Wall"])

# Build a static library
lib = project.StaticLibrary("core", env)
lib.sources.append(project.node("src/core.c"))
lib.public.include_dirs.append(Path("include"))

# Build a program using it
app = project.Program("myapp", env)
app.sources.append(project.node("src/main.c"))
app.link(lib)

# Generate the ninja.build script
project.generate()
```

```bash
uvx pcons # generate ninja.build and run it, producing build/myapp (or build/myapp.exe)
```

## Installation

No installation needed, if you have `uv`; just use `uvx pcons` to configure and build. `uvx pcons --help` for more info.
If you want to install it, though:

```bash
# Using uv
uv add pcons

# Or pip
pip install pcons
```

## Documentation

- User Guide is at [ReadTheDocs](https://pcons.readthedocs.io)
- [ARCHITECTURE.md](ARCHITECTURE.md) - Design document and implementation status
- [CONTRIBUTING.md](CONTRIBUTING.md) - How to contribute

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

## License

MIT License - see [LICENSE](LICENSE)

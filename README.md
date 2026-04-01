# Pcons

A modern open-source cross-platform zero-install Python-based build system. Builds anything that requires a repeatable workflow, using a dependency graph. Easy to use, reliable and quick. Uses Ninja (or Makefile, XCode, or MSVS) to do the builds. Optimized for C/C++, Fortran, CUDA, wasm etc. but should work for anything that needs building.

[![CI](https://github.com/DarkStarSystems/pcons/actions/workflows/main.yml/badge.svg)](https://github.com/DarkStarSystems/pcons/actions/workflows/main.yml)
[![codecov](https://codecov.io/gh/DarkStarSystems/pcons/graph/badge.svg)](https://codecov.io/gh/DarkStarSystems/pcons)
[![PyPI](https://img.shields.io/pypi/v/pcons)](https://pypi.org/project/pcons/)
[![PyPI Downloads](https://static.pepy.tech/badge/pcons)](https://pepy.tech/projects/pcons)
[![Python](https://img.shields.io/pypi/pyversions/pcons)](https://pypi.org/project/pcons/)
[![Doc Status](https://readthedocs.org/projects/pcons/badge/?version=latest)](https://pcons.readthedocs.io/en/latest/?badge=latest)

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

I was one of the original developers of SCons, and helped maintain it for many years. I love that python is the config language; that makes build descriptions incredibly flexible and powerful. Recently I've been using CMake for more projects, and despite the deeply painful configuration language, I've come to appreciate its power: conan integration, the separation between *describing* the build andrunning it, and dependency propagation, among other things. I feel that SCons hasn't kept up with modern python; like any very widely used mature project, it has a lot of accumulated wisdom but also a bit ossified ways of doing things.

I've been thinking for years now about rearchitecting SCons onto a modern python stack with Path and decorators and all the other wonderful stuff python has been doing, and fixing some of the pain points at the same time (substitution/quoting, extensibility, tracing, separation between description and building, and more), but I've never had the time to dig into it. But recently as I've been using a lot more of Claude Code as a programming assistant, and it has gotten significantly better, it seemed like the right time to try this as a collaborative project. So, meet pcons!

## Status

🚧 **Under active development** - ready for experimentation and feedback. It's working in several medium-sized projects.

Core functionality is working and well tested: C/C++/Fortran compilation, static and shared libraries, programs, install targets, installers (Win/Mac), and mixed-language builds. See [ARCHITECTURE.md](ARCHITECTURE.md) for design details.

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
# Install as a CLI tool (recommended)
uv tool install pcons
pcons ...

# Or add to a project's dependencies
uv add pcons

# Or with pip
pip install pcons
```

## Documentation

- User Guide is at [ReadTheDocs](https://pcons.readthedocs.io)
- [ARCHITECTURE.md](ARCHITECTURE.md) - Design document and implementation status
- [CONTRIBUTING.md](CONTRIBUTING.md) - How to contribute

## Fetching Dependencies

`pcons` also ships with `pcons-fetch`, a helper for downloading and building
third-party dependencies from source using a simple `deps.toml` file.

Example:

```toml
[packages.zlib]
url = "https://zlib.net/zlib-1.3.1.tar.gz"
version = "1.3.1"
build = "cmake"
sha256 = "9a93b2b7df..."
```

```bash
pcons-fetch fetch deps.toml
```

`sha256` is optional but recommended for archive downloads, especially in CI.
When present, `pcons-fetch` verifies the downloaded archive before extraction
and aborts on mismatch. This check applies to archive URLs, not Git clones.

Archive extraction is also path-safe: `pcons-fetch` rejects archive members
that try to escape the destination directory via absolute paths, `..`
components, or tar/zip link tricks.

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

## This Project is AI-Assisted

PCons is my long-term vision for a modern build tool. I've used Claude Code extensively to assist in creating this project, mostly Claude Opus 4.6. It has been a huge help in realizing the vision I've had for a long time. If you reflexively or morally reject all AI-generated or AI-assisted code, pcons is not for you. That said, I've reviewed every decision and nearly every line, and this code reflects my vision, my architecture, my goals and my priorities. I take full responsibility for it, and as a professional software engineer with 40+ years of C/C++/python experience I stand behind it. I also intend to support it long-term.

One of my sub-goals has been to make sure the documentation and source organization is clear; not just for humans but for browsing by AI agents. I want to make it easy for a human or an AI agent to create a best-practices `pcons-build.py` for *your* project quickly and easily. Using AI to auto-generate doc and making sure APIs are clean and consistent helps with that goal.

## License

MIT License - see [LICENSE](LICENSE)


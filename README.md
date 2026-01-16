# Pcons

A modern Python-based build system that generates Ninja (or Makefile) build files.

[![CI](https://github.com/garyo/pcons/actions/workflows/main.yml/badge.svg)](https://github.com/garyo/pcons/actions/workflows/main.yml)

## Overview

Pcons is inspired by [SCons](https://scons.org) and [CMake](https://cmake.org), taking the best ideas from each:

- **From SCons**: Environments, Tools, powerful dependency tracking, Python as the configuration language
- **From CMake**: Generator architecture (configure once, build fast), usage requirements that propagate through dependencies

**Key design principles:**

- **Configuration, not execution**: Pcons generates Ninja files; Ninja executes the build
- **Python is the language**: No custom DSL—build scripts are real Python with full IDE support
- **Language-agnostic**: Build C++, Rust, LaTeX, protobuf, or anything else
- **Explicit over implicit**: Dependencies are discoverable and traceable

## Status

🚧 **Under active development** - not yet usable for real projects.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the roadmap.

## Quick Example (Target API)

```python
# configure.py
from pcons import Configure

config = Configure()
config.find_toolchain('cxx')
config.save()
```

```python
# build.py
from pcons import Project, load_config

config = load_config()
project = Project('myapp', config)

env = project.Environment(toolchain=config.cxx_toolchain)
env.cxx.flags = ['-std=c++20', '-Wall']

lib = env.StaticLibrary('core', sources=env.Glob('src/*.cpp'),
    public_include_dirs=['include'])

app = env.Program('myapp', sources=['main.cpp'], link_libs=[lib])

project.Default(app)
project.generate()
```

```bash
pcons configure
pcons generate
ninja
```

## Installation

```bash
# Using uv (recommended)
uv add pcons  # Not yet published

# Or via uvx for one-off usage
uvx pcons configure
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

- [ARCHITECTURE.md](ARCHITECTURE.md) - Design document
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) - Development roadmap

## License

MIT License - see [LICENSE](LICENSE)

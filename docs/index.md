# pcons <small>v{{ version }}</small>

A Python-based build system that generates [Ninja](https://ninja-build.org/) build files.

- **Zero install** - Run directly with `uvx pcons` (requires only [uv](https://docs.astral.sh/uv/))
- **Pure Python** - Your build script is just Python. No DSL to learn.
- **Fast builds** - Generates Ninja files for parallel builds with proper dependency tracking.
- **Extensible** - Create custom tools for any build step.

## Quick Start

**1. Create `hello.c`:**

```c
#include <stdio.h>
int main() { printf("Hello, world!\n"); return 0; }
```

**2. Create `pcons-build.py`:**

```python
from pcons import Project, Generator, find_c_toolchain

project = Project("hello", build_dir="build")
env = project.Environment(toolchain=find_c_toolchain())

hello = project.Program("hello", env, sources=["hello.c"])
Generator().generate(project)
```

**3. Build and run:**

```bash
uvx pcons
./build/hello
```

That's it. No installation, no configuration files, no build directory setup.

Instead of uv, you can use `pipx` or just `python -mpip install pcons` to install.

## Next Steps

See the [User Guide](user-guide.md) for:

- CLI options (variants, variables, reconfiguration)
- Multi-file projects with include paths
- Shared libraries and installation
- Creating custom tools
- API reference

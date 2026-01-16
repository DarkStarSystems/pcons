# Pcons Implementation Plan

This document outlines the phased implementation of Pcons, a Python-based build system that generates Ninja files.

## Guiding Principles

1. **Test-driven**: Each component has tests before or alongside implementation
2. **Incremental**: Each phase produces working, testable code
3. **Dependencies respected**: Build components in order of dependency
4. **Dogfooding**: Use pcons to build itself as soon as practical (Phase 7+)
5. **uv-first**: Use uv for all Python tooling, support PEP 723 scripts

## Python Tooling (uv-first)

Pcons uses [uv](https://docs.astral.sh/uv/) as the primary Python package manager.

### Project Setup

```
pyproject.toml      # Project metadata, dependencies, build config
uv.lock             # Locked dependencies for reproducibility
```

### Development Workflow

```bash
# Create/sync virtual environment
uv sync

# Run tests
uv run pytest

# Run pcons CLI
uv run pcons --help

# Add a dependency
uv add <package>

# Add a dev dependency
uv add --dev <package>
```

### PEP 723 Script Support

Build scripts (`configure.py`, `build.py`) should support PEP 723 inline metadata, allowing them to be run standalone with `uv run`:

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///

from pcons import Configure

config = Configure()
# ...
```

This allows:
```bash
uv run configure.py   # Installs pcons automatically if needed
uv run build.py
```

### uvx Support

pcons should be runnable via uvx for one-off usage:

```bash
uvx pcons configure
uvx pcons generate
```

### CI Configuration

```yaml
# .github/workflows/main.yml
- uses: astral-sh/setup-uv@v4
- run: uv sync
- run: uv run pytest
```

## Phase Overview

```
Phase 1: Core Foundation          ████████░░░░░░░░░░░░░░░░░░░░░░
Phase 2: Environment & Tools      ░░░░░░░░████████░░░░░░░░░░░░░░
Phase 3: Project & Targets        ░░░░░░░░░░░░░░░░████░░░░░░░░░░
Phase 4: Ninja Generator          ░░░░░░░░░░░░░░░░░░░░████░░░░░░
Phase 5: Configure System         ░░░░░░░░░░░░░░░░░░░░░░░░████░░
Phase 6: Built-in Toolchains      ░░░░░░░░░░░░░░░░░░░░░░░░░░████
Phase 7: CLI                      ░░░░░░░░░░░░░░░░░░░░░░░░░░░░██
Phase 8: Package Management       ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase 9: Polish & Extensions      ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
```

---

## Phase 1: Core Foundation

**Goal**: Establish the fundamental building blocks that everything else depends on.

### 1.1 Project Structure

```
pyproject.toml               # Project metadata, dependencies (uv-managed)
uv.lock                      # Locked dependencies
pcons/
├── __init__.py              # Public API exports
├── __main__.py              # Entry point
├── cli.py                   # CLI implementation
├── core/
│   ├── __init__.py
│   ├── node.py              # Node hierarchy
│   ├── subst.py             # Variable substitution
│   └── errors.py            # Custom exceptions
└── util/
    ├── __init__.py
    └── source_location.py   # Track where things are defined
tests/
├── conftest.py
├── core/
│   ├── test_node.py
│   └── test_subst.py
```

### 1.2 Node Hierarchy

```python
# core/node.py
class Node:
    explicit_deps: list[Node]
    implicit_deps: list[Node]
    builder: Builder | None
    defined_at: SourceLocation

class FileNode(Node):
    path: Path

class DirNode(Node):
    path: Path
    members: list[Node]      # For dir-as-target semantics

class ValueNode(Node):
    name: str
    value: Any

class AliasNode(Node):
    name: str
    targets: list[Node]
```

**Tests**:
- Create nodes of each type
- Add dependencies
- Track source locations
- Dir member management

### 1.3 Variable Substitution Engine

```python
# core/subst.py
class Substitution:
    def expand(self, template: str, namespace: dict) -> str:
        """Recursively expand $var and ${var} references."""

    def expand_to_list(self, template: str, namespace: dict) -> list[str]:
        """Expand, handling list values appropriately."""
```

**Features**:
- `$var` and `${var}` syntax
- `$tool.var` namespaced access
- `$$` for literal `$`
- Recursive expansion (expand until no `$` remain)
- Circular reference detection
- Unknown variable → error
- List value handling (space-join in strings, preserve in lists)

**Tests**:
- Simple substitution
- Nested/recursive substitution
- Namespaced variables
- List expansion
- Circular reference detection
- Unknown variable errors
- Escape sequences

### 1.4 Error Infrastructure

```python
# core/errors.py
class PconsError(Exception):
    """Base for all pcons errors."""
    location: SourceLocation | None

class ConfigureError(PconsError): ...
class SubstitutionError(PconsError): ...
class DependencyCycleError(PconsError): ...
class MissingVariableError(SubstitutionError): ...
```

### Deliverables
- [ ] Node classes with full functionality
- [ ] Substitution engine with recursive expansion
- [ ] Error classes
- [ ] 100% test coverage for this phase

---

## Phase 2: Environment & Tools

**Goal**: Implement the namespaced Environment and Tool/Builder abstractions.

### 2.1 Tool Namespace

```python
# core/toolconfig.py
class ToolConfig:
    """Namespace for a single tool's configuration."""
    _vars: dict[str, Any]

    def __getattr__(self, name: str) -> Any: ...
    def __setattr__(self, name: str, value: Any): ...
    def clone(self) -> ToolConfig: ...
    def as_namespace(self) -> dict: ...  # For substitution
```

### 2.2 Environment

```python
# core/environment.py
class Environment:
    _tools: dict[str, ToolConfig]
    _vars: dict[str, Any]           # Cross-tool variables
    project: Project

    def __getattr__(self, name: str) -> ToolConfig:
        """Access tool namespace: env.cc, env.cxx, etc."""

    def clone(self) -> Environment: ...

    def subst(self, template: str, **extra) -> str:
        """Expand variables in template."""

    def add_tool(self, tool: Tool) -> None: ...
```

### 2.3 Tool Protocol

```python
# tools/tool.py
class Tool(Protocol):
    name: str

    def setup(self, env: Environment) -> None:
        """Initialize tool namespace with default variables."""

    def builders(self) -> dict[str, Builder]:
        """Return builders this tool provides."""
```

### 2.4 Builder Protocol

```python
# core/builder.py
class Builder(Protocol):
    name: str
    tool: Tool
    src_suffixes: list[str]
    target_suffixes: list[str]

    def __call__(
        self,
        env: Environment,
        target: str | Path,
        sources: list[str | Path | Node],
        **kwargs
    ) -> list[Node]:
        """Create target nodes from sources."""

    @property
    def language(self) -> str | None:
        """Language this builder compiles (for linker selection)."""
```

### 2.5 Toolchain Protocol

```python
# tools/toolchain.py
class Toolchain(Protocol):
    name: str
    tools: dict[str, Tool]
    language_priority: dict[str, int]

    def setup(self, env: Environment) -> None:
        """Add all tools to environment."""
```

### Deliverables
- [ ] ToolConfig with attribute access and cloning
- [ ] Environment with tool namespaces
- [ ] Tool protocol and base implementation
- [ ] Builder protocol and base implementation
- [ ] Toolchain protocol
- [ ] Tests for all components

---

## Phase 3: Project & Targets

**Goal**: Implement the Project container and Target with usage requirements.

### 3.1 Target

```python
# core/target.py
class Target:
    name: str
    nodes: list[Node]
    builder: Builder
    required_languages: set[str]

    # Usage requirements
    public_include_dirs: list[Path]
    public_link_libs: list[Target]
    public_defines: list[str]
    public_compile_flags: list[str]
    public_link_flags: list[str]

    # Private requirements
    private_include_dirs: list[Path]
    private_link_libs: list[Target]
    private_defines: list[str]

    def collect_usage_requirements(self) -> UsageRequirements:
        """Collect transitive public requirements from all dependencies."""
```

### 3.2 Project

```python
# core/project.py
class Project:
    name: str
    root_dir: Path
    build_dir: Path
    config: Config | None

    _environments: list[Environment]
    _targets: list[Target]
    _nodes: dict[Path, Node]        # Deduplication
    _default_targets: list[Target]

    def Environment(self, toolchain: Toolchain = None, **kwargs) -> Environment:
        """Create and register a new environment."""

    def node(self, path: Path | str) -> Node:
        """Get or create a node for a path (deduplication)."""

    def Default(self, *targets: Target) -> None: ...

    def validate(self) -> list[PconsError]:
        """Check for cycles, missing sources, etc."""
```

### 3.3 Dependency Graph Utilities

```python
# core/graph.py
def topological_sort(targets: list[Target]) -> list[Target]: ...
def detect_cycles(targets: list[Target]) -> list[list[Target]]: ...
def collect_all_nodes(targets: list[Target]) -> set[Node]: ...
```

### Deliverables
- [ ] Target with usage requirements
- [ ] Transitive requirement collection
- [ ] Project container with node deduplication
- [ ] Graph validation (cycle detection)
- [ ] Tests for transitive requirements propagation

---

## Phase 4: Ninja Generator

**Goal**: Generate working Ninja build files.

### 4.1 Generator Protocol

```python
# generators/generator.py
class Generator(Protocol):
    name: str

    def generate(self, project: Project, output_dir: Path) -> None: ...
```

### 4.2 Ninja Generator

```python
# generators/ninja.py
class NinjaGenerator(Generator):
    name = 'ninja'

    def generate(self, project: Project, output_dir: Path) -> None:
        """Write build.ninja file."""

    def _write_rules(self, f: IO, project: Project) -> None: ...
    def _write_builds(self, f: IO, project: Project) -> None: ...
    def _write_defaults(self, f: IO, project: Project) -> None: ...
    def _escape_path(self, path: Path) -> str: ...
    def _format_rule(self, builder: Builder, env: Environment) -> str: ...
```

**Ninja features to support**:
- Rules with command, description, depfile, deps
- Build statements with explicit, implicit (`|`), order-only (`||`) deps
- Variables at file and build scope
- Phony rules for aliases
- Default targets
- Response files (for Windows)

### 4.3 compile_commands.json Generator

```python
# generators/compile_commands.py
class CompileCommandsGenerator(Generator):
    name = 'compile_commands'

    def generate(self, project: Project, output_dir: Path) -> None:
        """Write compile_commands.json for IDE integration."""
```

### 4.4 Integration Test

Create a minimal but real build:
```python
# tests/integration/test_ninja_gen.py
def test_simple_c_program(tmp_path):
    """Generate ninja for a simple C program and verify it builds."""
    # Write test source
    # Create project with mock C tool
    # Generate ninja
    # Run ninja
    # Verify executable exists
```

### Deliverables
- [ ] Generator protocol
- [ ] NinjaGenerator with all features
- [ ] CompileCommandsGenerator
- [ ] Integration test with real ninja invocation
- [ ] Handle Windows response files

---

## Phase 5: Configure System

**Goal**: Implement tool detection and configuration caching.

### 5.1 Configure Context

```python
# configure/config.py
class Configure:
    """Context for the configure phase."""
    platform: Platform
    _cache: dict[str, Any]
    _toolchains: dict[str, Toolchain]
    packages: dict[str, PackageDescription]

    def find_program(self, name: str, hints: list[Path] = None) -> Path | None: ...
    def find_toolchain(self, kind: str, candidates: list[str] = None) -> Toolchain: ...
    def set(self, key: str, value: Any) -> None: ...
    def get(self, key: str, default: Any = None) -> Any: ...
    def save(self, path: Path = None) -> None: ...

def load_config(path: Path = None) -> Config:
    """Load cached configuration."""
```

### 5.2 Platform Detection

```python
# configure/platform.py
@dataclass
class Platform:
    os: str              # 'linux', 'darwin', 'windows'
    arch: str            # 'x86_64', 'arm64', etc.
    is_64bit: bool
    exe_suffix: str      # '', '.exe'
    shared_lib_suffix: str  # '.so', '.dylib', '.dll'
    static_lib_suffix: str  # '.a', '.lib'

def detect_platform() -> Platform: ...
```

### 5.3 Feature Checks

```python
# configure/checks.py
class ToolChecks:
    """Feature checking for a configured tool."""
    tool: Tool
    env: Environment

    def check_flag(self, flag: str) -> bool:
        """Test if compiler accepts this flag."""

    def check_header(self, header: str) -> bool:
        """Test if header is available."""

    def check_define(self, define: str) -> str | None:
        """Get value of predefined macro, or None."""

    def check_type_size(self, type_name: str) -> int | None:
        """Get sizeof(type), or None."""
```

### 5.4 Config Caching

- Format: JSON (readable, diffable)
- Location: `build/pcons_config.json` by default
- Invalidation: Hash of configure.py + tool versions

### Deliverables
- [ ] Configure context
- [ ] Platform detection
- [ ] Feature checks (compile tests)
- [ ] Config save/load
- [ ] Cache invalidation logic

---

## Phase 6: Built-in Toolchains

**Goal**: Implement GCC, LLVM, and MSVC toolchains with real tool detection.

### 6.1 GCC Toolchain

```python
# toolchains/gcc.py
class GccToolchain(Toolchain):
    name = 'gcc'

    # Tools
    cc: GccCCompiler
    cxx: GccCxxCompiler
    ar: GccArchiver
    link: GccLinker
```

**GCC C/C++ Compiler Tool**:
- Detection: `gcc --version`, `g++ --version`
- Variables: `cmd`, `flags`, `includes`, `defines`, `depflags`
- Builders: `Object`
- Default command template: `$cc.cmd $cc.flags $cc.includes $cc.defines $cc.depflags -c -o $out $in`

**GCC Archiver Tool**:
- Detection: `ar --version`
- Builders: `StaticLibrary`

**GCC Linker Tool**:
- Builders: `Program`, `SharedLibrary`
- Language-aware: selects gcc vs g++ based on inputs

### 6.2 LLVM Toolchain

```python
# toolchains/llvm.py
class LlvmToolchain(Toolchain):
    name = 'llvm'
    # Similar structure, clang/clang++/llvm-ar/lld
```

### 6.3 MSVC Toolchain

```python
# toolchains/msvc.py
class MsvcToolchain(Toolchain):
    name = 'msvc'
    # cl.exe, lib.exe, link.exe
    # Handle vcvars environment
```

### 6.4 End-to-End Test

```python
def test_build_real_program(tmp_path):
    """Full test: configure, generate, build a real C++ program."""
```

### Deliverables
- [ ] GCC toolchain (full)
- [ ] LLVM toolchain (full)
- [ ] MSVC toolchain (basic, Windows-only)
- [ ] End-to-end test on CI

---

## Phase 7: CLI

**Goal**: Provide the command-line interface.

### 7.1 CLI Structure

```bash
pcons configure [options]     # Run configure.py
pcons generate [options]      # Run build.py, generate ninja
pcons build [targets...]      # Wrapper around ninja
pcons clean                   # Clean build artifacts
pcons --help
```

### 7.2 Implementation

```python
# cli.py
import argparse

def main():
    parser = argparse.ArgumentParser(prog='pcons')
    subparsers = parser.add_subparsers()

    # pcons configure
    cfg_parser = subparsers.add_parser('configure')
    cfg_parser.add_argument('--build-dir', default='build')
    cfg_parser.set_defaults(func=cmd_configure)

    # pcons generate
    gen_parser = subparsers.add_parser('generate')
    gen_parser.set_defaults(func=cmd_generate)

    # pcons build
    build_parser = subparsers.add_parser('build')
    build_parser.add_argument('targets', nargs='*')
    build_parser.set_defaults(func=cmd_build)

    args = parser.parse_args()
    args.func(args)
```

### 7.3 Script Discovery

- `configure.py` in current dir (or `--configure-script`)
- `build.py` in current dir (or `--build-script`)

### Deliverables
- [ ] CLI with configure/generate/build/clean
- [ ] Script discovery
- [ ] Helpful error messages
- [ ] `--verbose` and `--debug` flags

---

## Phase 8: Package Management

**Goal**: Implement package finding and pcons-fetch.

### 8.1 Package Description

```python
# packages/description.py
@dataclass
class PackageDescription:
    name: str
    version: str
    include_dirs: list[Path]
    library_dirs: list[Path]
    libraries: list[str]
    defines: list[str]
    compile_flags: list[str]
    link_flags: list[str]
    dependencies: list[str]
    components: dict[str, ComponentDescription]

    @classmethod
    def from_toml(cls, path: Path) -> PackageDescription: ...
    def to_toml(self, path: Path) -> None: ...
```

### 8.2 ImportedTarget

```python
# packages/imported.py
class ImportedTarget(Target):
    """Target for external dependencies."""
    is_imported: bool = True
    package: PackageDescription
```

### 8.3 Finders

```python
# packages/finders/
class PkgConfigFinder: ...
class SystemFinder: ...
class ConanFinder: ...    # Phase 9
class VcpkgFinder: ...    # Phase 9
```

### 8.4 pcons-fetch

```bash
pcons-fetch deps.toml [options]
```

- Parse deps.toml
- Download sources
- Build with CMake/autotools/meson/custom
- Generate .pcons-pkg.toml files

### Deliverables
- [ ] PackageDescription with TOML serialization
- [ ] ImportedTarget
- [ ] PkgConfigFinder
- [ ] SystemFinder
- [ ] pcons-fetch for CMake and autotools projects

---

## Phase 9: Polish & Extensions

**Goal**: Production readiness.

### 9.1 Additional Generators
- [ ] MakefileGenerator
- [ ] VSCodeGenerator (tasks.json, c_cpp_properties.json)
- [ ] XcodeGenerator (basic)

### 9.2 Scanner System
- [ ] Scanner protocol
- [ ] C/C++ header scanner (configure-time fallback)
- [ ] Depfile support in generators (already partly done)

### 9.3 Additional Finders
- [ ] ConanFinder (read Conan-generated files)
- [ ] VcpkgFinder
- [ ] CMakePackageFinder (read CMake config files)

### 9.4 Documentation
- [ ] User guide
- [ ] API reference
- [ ] Examples repository
- [ ] Migration guide from SCons/CMake

### 9.5 Performance
- [ ] Profile large projects
- [ ] Optimize node deduplication
- [ ] Lazy loading where appropriate

### 9.6 Additional Tools
- [ ] Fortran toolchain
- [ ] CUDA toolchain
- [ ] Custom tool examples

---

## Testing Strategy

### Unit Tests
Every module has corresponding tests in `tests/`.

### Integration Tests
- `tests/integration/`: End-to-end tests that run real builds
- Use temporary directories
- Test on multiple platforms via CI

### CI Matrix
```yaml
os: [ubuntu-latest, macos-latest, windows-latest]
python: ['3.11', '3.12']
```

---

## Milestones

### Milestone 1: "It Generates Ninja" (Phases 1-4)
- Can define a project in Python
- Generates valid build.ninja
- No real tool detection yet (mock tools)

### Milestone 2: "It Builds C++" (Phases 5-6)
- Real GCC/Clang detection
- Can configure and build a real C++ project
- End-to-end test passes

### Milestone 3: "Usable CLI" (Phase 7)
- Full CLI workflow
- Can replace a simple Makefile project

### Milestone 4: "External Dependencies" (Phase 8)
- Can use system libraries
- pcons-fetch works for common cases

### Milestone 5: "Production Ready" (Phase 9)
- Documentation complete
- Multiple generators
- Tested on real-world projects

---

## Getting Started

After cleaning the repo:

```bash
# Initialize uv environment
uv sync

# Verify setup
uv run pcons --help
uv run pytest

# Start with Phase 1
# 1. Implement core/node.py with tests
# 2. Implement core/subst.py with tests
# 3. Implement core/errors.py
```

Begin with Phase 1.1 - the Node hierarchy - as it's the foundation everything builds on.

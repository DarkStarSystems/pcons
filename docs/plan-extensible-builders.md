# Extensible Builder and Toolchain Architecture

## Overview

This plan redesigns pcons to treat all builders and toolchains as first-class add-ons, with no special treatment for built-ins. The key principles are:

1. **All builders are add-ons**: Built-in builders (Program, Install, Tarfile) register the same way as user-defined builders
2. **All toolchains are add-ons**: Built-in toolchains (GCC, LLVM, MSVC) register the same way as user-defined toolchains
3. **Native-looking API**: User-defined builders are callable as `project.InstallSymlink(...)` just like built-ins
4. **Unified command handling**: All commands go through the same substitution path using `$includes`, `$defines`, etc.
5. **Expansion packs**: Users can create packages that add multiple builders/toolchains

## Architecture Components

### 1. Builder Registry (`pcons/core/builder_registry.py`)

```python
@dataclass
class BuilderRegistration:
    name: str                    # e.g., "Program", "Install"
    builder_class: type          # The builder class
    target_type: TargetType      # e.g., TargetType.PROGRAM
    factory_class: type | None   # NodeFactory class for resolution

class BuilderRegistry:
    _builders: dict[str, BuilderRegistration] = {}

    @classmethod
    def register(cls, name, *, builder_class, target_type, factory_class=None): ...

    @classmethod
    def get(cls, name) -> BuilderRegistration | None: ...

# Decorator for registration
def builder(name: str, **options):
    def decorator(cls):
        BuilderRegistry.register(name, builder_class=cls, **options)
        return cls
    return decorator
```

### 2. Dynamic Project Methods

Project uses `__getattr__` to expose registered builders:

```python
class Project:
    def __getattr__(self, name):
        reg = BuilderRegistry.get(name)
        if reg:
            return self._make_builder_method(name)
        raise AttributeError(...)

    def __dir__(self):  # For IDE auto-completion
        return list(super().__dir__()) + BuilderRegistry.names()
```

### 3. Generic Target Structure

Replace hardcoded `_install_*` slots with generic storage:

```python
class Target:
    __slots__ = (..., "_builder_data", "_builder_name")

    # _builder_data is a dict for builder-specific data
    # _builder_name identifies which builder created this target
```

### 4. Resolver Dispatch Table

Replace if/elif chains with factory dispatch:

```python
class Resolver:
    def __init__(self, project):
        self._factories = {}
        for name, reg in BuilderRegistry.all().items():
            if reg.factory_class:
                self._factories[name] = reg.factory_class(project)

    def _resolve_target(self, target):
        factory = self._factories.get(target._builder_name)
        if factory:
            factory.resolve(target, target._env)
```

### 5. NodeFactory Protocol

All builder factories implement this protocol:

```python
class NodeFactory(Protocol):
    def resolve(self, target: Target, env: Environment | None) -> None:
        """Resolve the target, creating output nodes."""
        ...

    def resolve_pending(self, target: Target, resolver: Resolver) -> None:
        """Resolve pending sources (phase 2)."""
        ...
```

### 6. Unified Command Templates

All commands use the same variable substitution:

- `$includes` - Include directories with prefix
- `$defines` - Preprocessor defines with prefix
- `$extra_flags` - Additional compile flags
- `$ldflags` - Linker flags
- `$libdirs` - Library directories with prefix
- `$libs` - Libraries with prefix
- `$$in` / `$$out` - Ninja input/output (escaped, become `$in`/`$out`)

### 7. Expansion Packs

```python
# pcons_gamedev/__init__.py
def register(project=None):
    BuilderRegistry.register("CompileShaders", builder_class=ShaderBuilder, ...)
    toolchain_registry.register(HlslToolchain, ...)
```

With optional entry point auto-discovery via `pyproject.toml`.

## File Structure

### New Files

| File | Purpose |
|------|---------|
| `pcons/core/builder_registry.py` | Builder registration system |
| `pcons/builders/__init__.py` | Built-in builder package |
| `pcons/builders/install.py` | Install, InstallAs, InstallDir builders |
| `pcons/builders/archive.py` | Tarfile, Zipfile builders |
| `pcons/builders/compile.py` | Program, StaticLibrary, SharedLibrary builders |

### Modified Files

| File | Changes |
|------|---------|
| `pcons/core/project.py` | Remove hardcoded methods, add `__getattr__` dispatch |
| `pcons/core/target.py` | Replace `_install_*` slots with `_builder_data`, `_builder_name` |
| `pcons/core/resolver.py` | Replace if/elif with dispatch table |
| `pcons/generators/ninja.py` | Read builder metadata generically |
| `pcons/__init__.py` | Add plugin auto-discovery |

## Implementation Phases

### Phase A: Foundation
1. Create `builder_registry.py` with `BuilderRegistry` class
2. Create `NodeFactory` protocol
3. Add builder registration decorator

### Phase B: Builder Migration
1. Create `pcons/builders/` package
2. Move Install, InstallAs, InstallDir to `install.py`
3. Move Tarfile, Zipfile to `archive.py`
4. Move Program, Library to `compile.py`
5. Register all built-in builders

### Phase C: Target Generalization
1. Add `_builder_data` and `_builder_name` to Target
2. Migrate existing `_install_*` usage
3. Update Resolver dispatch

### Phase D: Project Integration
1. Add `__getattr__` to Project
2. Add `__dir__` for auto-completion
3. Remove hardcoded methods

### Phase E: Expansion Pack Support
1. Add entry point discovery
2. Document expansion pack API

## Example Usage After Implementation

```python
# Basic usage (unchanged API)
project = Project("myapp")
env = project.Environment(toolchain=gcc)
app = project.Program("myapp", env, sources=["main.cpp"])
project.Install("dist/bin", [app])

# Loading an expansion pack
import pcons_gamedev
pcons_gamedev.register(project)
shaders = project.CompileShaders("shaders/", output="build/shaders")

# Creating a custom builder
@builder("InstallSymlink", target_type=TargetType.INTERFACE)
class InstallSymlinkBuilder:
    @staticmethod
    def create_target(project, dest, source, **kwargs):
        target = Target(...)
        target._builder_name = "InstallSymlink"
        target._builder_data = {"dest": dest, "source": source}
        return target

# Immediately available
project.InstallSymlink("dist/latest", app)
```

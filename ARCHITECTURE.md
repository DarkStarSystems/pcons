# Pcons Architecture

A modern Python-based build system that generates Ninja (or other) build files.

---

## Implementation Status Summary

| Component | Status | Notes |
|-----------|--------|-------|
| **Core System** | | |
| Node hierarchy (FileNode, DirNode, etc.) | Implemented | Full support |
| Environment with namespaced tools | Implemented | Full support |
| Variable substitution | Implemented | Recursive, with functions |
| Target with usage requirements | Implemented | Public/private requirements |
| Project container | Implemented | Full support |
| Resolver (lazy node creation) | Implemented | Full support |
| Builder Registry | Implemented | Extensible builder system |
| **Builders** | | |
| Program, StaticLibrary, SharedLibrary | Implemented | Compile/link builders |
| Install, InstallAs, InstallDir | Implemented | File installation builders |
| Tarfile, Zipfile | Implemented | Archive builders |
| HeaderOnlyLibrary, ObjectLibrary | Implemented | Interface and object-only |
| Command | Implemented | Custom shell commands |
| **Configure Phase** | | |
| Configure class | Partial | Basic program/toolchain finding |
| Feature checks (compile tests) | Partial | Methods exist, need toolchain integration |
| Configuration caching | Implemented | JSON-based |
| Config header generation | Implemented | write_config_header() |
| load_config() function | Implemented | Loads saved config |
| **Toolchains** | | |
| Toolchain base class | Implemented | Plugin registry, ToolchainContext |
| GCC toolchain | Implemented | Auto-detection, C/C++ |
| LLVM/Clang toolchain | Implemented | Auto-detection, C/C++ |
| MSVC toolchain | Implemented | Auto-detection, C/C++ |
| Clang-cl toolchain | Implemented | Clang with MSVC compatibility |
| **Generators** | | |
| Ninja generator | Implemented | Primary, full support |
| compile_commands.json | Implemented | For IDE integration |
| Mermaid diagram generator | Implemented | For visualization |
| Makefile generator | Implemented | For environments without Ninja |
| Xcode generator | Implemented | macOS, limited custom command support |
| VSCode generator | Planned | Not yet implemented |
| **Package Management** | | |
| PackageDescription | Implemented | TOML format |
| ImportedTarget | Implemented | Wraps external deps |
| pkg-config finder | Implemented | Reads .pc files |
| System finder | Implemented | Manual search |
| Conan finder | Implemented | Conan 2.x with PkgConfigDeps |
| vcpkg finder | Planned | Not yet implemented |
| pcons-fetch tool | Implemented | CMake/autotools support |
| **Module System** | | |
| Module discovery | Implemented | Auto-load from search paths |
| pcons.modules namespace | Implemented | Access loaded modules |
| pcons.contrib package | Implemented | Bundle/platform helpers |
| **Scanners** | | |
| Scanner interface | Implemented | Protocol defined |
| C/C++ header scanner | Planned | Relies on depfiles |
| Build-time depfiles | Implemented | Via Ninja |

**Legend:**
- **Implemented** - Feature is complete and working
- **Partial** - Feature exists but has limitations or missing integration
- **Planned** - Documented but not yet implemented

---

## Design Philosophy

**Configuration, not execution.** Unlike SCons which both configures and executes builds, Pcons is purely a build file generator. Python scripts describe what to build; Ninja (or Make) executes it. This separation provides:

- Fast incremental builds (Ninja handles this well)
- Clear mental model (configure once, build many times)
- Simpler codebase (no need for parallel execution, job scheduling, etc.)

**Python is the language.** No custom DSL. Build scripts are Python programs with access to the full language. This means real debugging, real testing, real IDE support.

**Language-agnostic.** The core system knows nothing about C++ or any specific language. All language support comes through Tools and Toolchains. Building LaTeX documents, protobuf files, or custom asset pipelines should be as natural as building C++.

**Explicit over implicit.** Dependencies should be discoverable and traceable. When something rebuilds unexpectedly (or fails to rebuild), users should be able to understand why.

**uv-first Python.** The project uses [uv](https://docs.astral.sh/uv/) for Python package management. All scripts support PEP 723 inline metadata, and the project uses `pyproject.toml` with `uv.lock` for reproducible development environments.

---

## Execution Model: Three Distinct Phases

### Phase 1: Configure
> **Status: Partial** - Configure class exists with program finding, caching, and config header generation. Feature checks (compile tests) require toolchain integration.

**Separate from build description.** Tool detection is complex and must complete before builds are defined.

```bash
pcons configure [options]
```

1. Platform detection (OS, architecture)
2. Toolchain discovery (find compilers, linkers, etc.)
3. Tool feature detection (run test compiles, check #defines, probe capabilities)
4. Cache results for subsequent runs

**Output:** Configuration cache (e.g., `pcons_config.json` or Python pickle)

**Why separate?** Tool detection often requires:
- Running executables (`gcc --version`, `cl /?`)
- Test compilations (`check if -std=c++20 works`)
- Feature probes (`does this compiler support __attribute__((visibility))`)

This is slow and shouldn't run on every build description parse.

```python
# pcons-configure.py - runs during configure phase
from pcons import Configure

config = Configure()

# Find a C++ toolchain
cxx = config.find_toolchain('cxx', candidates=['gcc', 'clang', 'msvc'])

# Probe features
cxx.check_flag('-std=c++20')
cxx.check_header('optional')
cxx.check_define('__cpp_concepts')

# Save configuration
config.save()
```

### Phase 2: Build Description
> **Status: Implemented** - Project, Environment, Target, and Resolver all fully functional.

**Uses cached configuration.** Fast, runs every time build files might need updating.

```bash
pcons generate
```

1. Load configuration cache
2. Execute build scripts (Python)
3. Build dependency graph (Nodes, Targets)
4. Validate graph (cycles, missing sources)
5. Run configure-time scanners if needed

**Output:** In-memory Project with complete dependency graph

```python
# pcons-build.py - runs during generate phase
from pcons import Project, load_config

config = load_config()  # Fast: loads cached results
project = Project('myapp', config)

env = project.Environment(toolchain=config.cxx)
# ... define builds ...
```

### Phase 3: Resolve

The resolver takes the build description, and:
1. Propagates build flags (public includes, link flags etc.) from dependencies forward to their targets
2. Resolves the build into actual Nodes per each source/target file
3. Substitutes variables in each Target command(s), producing the final commands to execute for that target

The resolve phase is significant; before that, the node graph is sparse, with build info for targets but many nodes not yet defined. After `resolve()`, the node graph is complete and we can generate the build file. Some targets, like Install, defer much of their actual work until `resolve()`. For instance, when Install is passed a Target or a directory, it doesn't know at that point what exact nodes or files will be contained in it, so it can't yet know how exactly to install the given files or dirs. During resolve, it lazily creates that info so it's up to date when the generator asks for it.

### Phase 4: Generate
> **Status: Partial** - Ninja generator fully implemented. compile_commands.json and Mermaid diagram generators available. Makefile and IDE generators planned.

1. Generator traverses the dependency graph
2. Adjust paths as needed for the generator (e.g. Ninja target paths are specified relative to build dir)
2. Emits build rules and definitions into e.g. `build.ninja` or `Makefile`
3. Generates auxiliary files (`compile_commands.json`, IDE projects)

**Output:** Build files ready for execution

### Phase 4: Build

User runs `ninja` (or `make`). Pcons is not involved.

---

## Core Abstractions

### Node
> **Status: Implemented**

The fundamental unit in the dependency graph. A Node represents something that can be a dependency or a target.

```
Node (abstract)
├── FileNode        # A file (source or generated)
├── DirNode         # A directory (first-class, see semantics below)
├── ValueNode       # A computed value (e.g., config hash, version string)
└── AliasNode       # A named group of targets (phony)
```

**Key properties:**
- `explicit_deps`: Dependencies declared by the user
- `implicit_deps`: Dependencies discovered by scanners or from depfiles
- `builder`: The Builder that produces this node (if it's a target)
- `defined_at`: Source location where this node was created (for debugging)

### Directory Node Semantics
> **Status: Implemented**

Directories require special handling. Their semantics differ based on usage:

**Directory as Target:**
A directory target is up-to-date when **all specified files within it** are up-to-date. It acts as a collector.

```python
# install_dir depends on all files installed into it
install_dir = env.InstallDir('dist/lib', [lib1, lib2, lib3])
# install_dir is up-to-date iff lib1, lib2, lib3 are all installed
```

Implementation: DirNode as target holds references to its member file nodes. The generator emits the dir as a phony target depending on all members.

```ninja
build dist/lib: phony dist/lib/lib1.a dist/lib/lib2.a dist/lib/lib3.a
```

**Directory as Source:**
A directory source represents **the directory and all files within it** that are part of the build (sources or targets). Files present on disk but not declared in the build are ignored.

```python
# asset_dir as source - depends on all declared assets within
assets = env.Glob('assets/*.png')  # Explicitly declared files
packed = env.PackAssets('game.pak', asset_dir)
# Rebuilds if any declared asset changes, not if random files appear
```

This avoids the SCons problem where touching an unrelated file in a source directory triggers rebuilds.

**Directory Existence:**
For cases where you only need the directory to exist (e.g., output directories), use order-only dependencies:

```python
obj = env.cc.Object('build/obj/foo.o', 'foo.c')
# Generator emits: build build/obj/foo.o: cc foo.c || build/obj
```

### Environment with Namespaced Tools
> **Status: Implemented**

Environments provide **namespaced configuration** for each tool, avoiding the SCons problem of flat variable collisions.

```python
env = project.Environment(toolchain='gcc')

# Tool-specific namespaces
env.cc.cmd = 'gcc'
env.cc.flags = ['-Wall', '-O2']
env.cc.includes = ['/usr/include']
env.cc.defines = ['NDEBUG']

env.cxx.cmd = 'g++'
env.cxx.flags = ['-Wall', '-O2', '-std=c++20']
env.cxx.includes = ['/usr/include']

env.link.cmd = 'g++'
env.link.flags = ['-L/usr/lib']
env.link.libs = ['m', 'pthread']

env.ar.cmd = 'ar'
env.ar.flags = ['rcs']
```

**Why namespaces matter:**
- `CFLAGS` vs `CXXFLAGS` vs `FFLAGS` confusion is eliminated
- Each tool owns its configuration
- Cloning an environment clones all tool configs
- Tools can have tool-specific variables without collision

**Namespace structure:**
```python
env.{tool_name}.{variable}

# Examples:
env.cc.flags        # C compiler flags
env.cxx.flags       # C++ compiler flags
env.fortran.flags   # Fortran compiler flags
env.link.flags      # Linker flags
env.ar.flags        # Archiver flags
env.protoc.flags    # Protobuf compiler flags
env.tarfile.compression # Compression for building a tar file (e.g. "gzip")
```

**Cross-tool variables** live at the environment level:
```python
env.build_dir = 'build'
env.variant = 'release'
```

### Variable Substitution (Always Recursive)
> **Status: Implemented**

Variable expansion is **always recursive**. This is essential for building complex command lines.

```python
env.cc.cmd = 'gcc'
env.cc.flags = ['-Wall', '$cc.opt_flag']
env.cc.opt_flag = '-O2'
env.cc.include_flags = ['-I$inc' for inc in env.cc.includes]
env.cc.define_flags = ['-D$d' for d in env.cc.defines]

# Command line template - references other variables
env.cc.cmdline = ['$cc.cmd', '$cc.flags', '$cc.include_flags', '$cc.define_flags', '-c', '-o', '$out', '$in']

# Expansion happens recursively:
# 1. $cc.cmdline expands, revealing $cc.cmd, $cc.flags, etc.
# 2. $cc.flags expands, revealing $cc.opt_flag
# 3. $cc.opt_flag expands to '-O2'
# ... and so on until no $ references remain
```

**Substitution rules:**
1. `$var` or `${var}` - expand variable (recursive)
2. `$tool.var` or `${tool.var}` - expand tool-namespaced variable
3. `$$` - literal `$`
4. List values are space-joined when interpolated into strings
5. Unknown variables are **errors** (not silent empty strings)
6. Circular references are detected and reported as errors

**Special variables** (set by builders at expansion time):
- `$in` - input file(s)
- `$out` - output file(s)
- `$in[0]` - first input file
- `$out[0]` - first output file

### Tool
> **Status: Implemented** - Base Tool class and protocol defined. GCC toolchain implemented with C/C++ tools.

A Tool knows how to perform a specific type of transformation. Tools are **namespaced within environments** and provide Builders.

```python
class Tool(Protocol):
    name: str           # e.g., 'cc', 'cxx', 'fortran', 'ar', 'link'

    def configure(self, config: Configure) -> ToolConfig:
        """Detect and configure this tool. Called during configure phase."""
        ...

    def setup(self, env: Environment) -> None:
        """Initialize tool namespace in environment. Called when tool is added."""
        ...

    def builders(self) -> dict[str, Builder]:
        """Return builders this tool provides."""
        ...
```

**Key insight: Builders are tool-specific, not suffix-specific.**

The "Object builder" problem in SCons: multiple tools produce `.o` files (C, C++, Fortran, CUDA, etc.). SCons's single `Object()` builder is ambiguous.

**Solution:** Each tool provides its own object builder:

```python
# Explicit tool selection
c_obj = env.cc.Object('foo.o', 'foo.c')        # C compiler
cxx_obj = env.cxx.Object('bar.o', 'bar.cpp')   # C++ compiler
f_obj = env.fortran.Object('baz.o', 'baz.f90') # Fortran compiler
cuda_obj = env.cuda.Object('qux.o', 'qux.cu')  # CUDA compiler
```

**Convenience with explicit defaults:**

```python
# env.Object() can exist as a dispatcher based on suffix
# but the mapping is explicit and user-configurable
env.object_builders = {
    '.c': env.cc,
    '.cpp': env.cxx,
    '.cxx': env.cxx,
    '.f90': env.fortran,
    '.cu': env.cuda,
}

obj = env.Object('foo.o', 'foo.cpp')  # Dispatches to env.cxx.Object
```

### Toolchain
> **Status: Implemented** - Base Toolchain class with ToolchainContext support. GCC, LLVM, MSVC, and Clang-cl toolchains all working.

A Toolchain is a coordinated set of Tools that work together.

```python
class Toolchain:
    name: str
    tools: dict[str, Tool]  # name -> tool

    def configure(self, config: Configure) -> bool:
        """Configure all tools in this toolchain."""
        ...

    def setup(self, env: Environment) -> None:
        """Add all tools to environment."""
        ...
```

**Why Toolchains matter:**
- GCC toolchain: gcc (cc), g++ (cxx), ar, ld
- LLVM toolchain: clang (cc), clang++ (cxx), llvm-ar, lld
- MSVC toolchain: cl (cc, cxx), lib (ar), link
- Cross-compilation: arm-none-eabi-gcc toolchain

**Toolchain guarantees:**
- All tools in a toolchain are compatible
- Switching toolchains switches all related tools atomically
- No mixing GCC compiler with MSVC linker

```python
# configure.py
gcc = config.find_toolchain('gcc')
llvm = config.find_toolchain('llvm')

# pcons-build.py
env_gcc = project.Environment(toolchain=gcc)
env_llvm = project.Environment(toolchain=llvm)
```

### Builder Registry and Extensible Builders
> **Status: Implemented**

All builders in pcons register through a unified `BuilderRegistry`. This ensures user-defined builders are on equal footing with built-ins - there's no special treatment for built-in builders like `Program` or `Install`.

**Key components:**

1. **BuilderRegistration** - Metadata for a registered builder:
```python
@dataclass
class BuilderRegistration:
    name: str                      # e.g., "Program", "Install"
    create_target: Callable        # Function to create a Target
    target_type: TargetType        # e.g., TargetType.PROGRAM
    factory_class: type | None     # Optional NodeFactory for resolution
    requires_env: bool             # Whether builder needs an Environment
    description: str               # Human-readable description
```

2. **BuilderRegistry** - Global registry:
```python
class BuilderRegistry:
    @classmethod
    def register(cls, name, *, create_target, target_type, factory_class=None, ...): ...

    @classmethod
    def get(cls, name) -> BuilderRegistration | None: ...

    @classmethod
    def names(cls) -> list[str]: ...
```

3. **@builder decorator** - Easy registration:
```python
@builder("InstallSymlink", target_type=TargetType.INTERFACE)
class InstallSymlinkBuilder:
    @staticmethod
    def create_target(project, dest, source, **kwargs):
        target = Target(...)
        target._builder_name = "InstallSymlink"
        target._builder_data = {"dest": dest, "source": source}
        project.add_target(target)
        return target

# Immediately available on any Project:
project.InstallSymlink("dist/latest", app)
```

**How it works:**

1. **Registration**: Builders register with `BuilderRegistry` at module load time (via `@builder` decorator)
2. **Dynamic dispatch**: `Project.__getattr__` checks `BuilderRegistry` and returns a bound method
3. **IDE support**: `Project.__dir__` includes registered builder names for auto-completion
4. **Resolution**: Builders can provide a `factory_class` that handles target resolution

**Built-in builders** (in `pcons/builders/`):
- `compile.py`: Program, StaticLibrary, SharedLibrary, ObjectLibrary, HeaderOnlyLibrary, Command
- `install.py`: Install, InstallAs, InstallDir
- `archive.py`: Tarfile, Zipfile

**Creating custom builders:**

```python
from pcons.core.builder_registry import builder
from pcons.core.target import Target, TargetType

@builder("CompileShaders", target_type=TargetType.COMMAND, requires_env=True)
class ShaderBuilder:
    @staticmethod
    def create_target(project, env, *, output, sources, **kwargs):
        target = Target(output, target_type=TargetType.COMMAND)
        target._env = env
        target._project = project
        target._builder_name = "CompileShaders"
        target._builder_data = {"output": output, "sources": sources}
        # ... set up build info ...
        project.add_target(target)
        return target

# Now available:
project.CompileShaders(env, output="shaders.pak", sources=["*.glsl"])
```

**Expansion packs** - packages that add multiple builders:
```python
# pcons_gamedev/__init__.py
def register(project=None):
    # Import triggers @builder registration
    from pcons_gamedev import shaders, assets
```

### NodeFactory Protocol

Builders that need custom resolution logic can provide a `factory_class`:

```python
class NodeFactory(Protocol):
    def __init__(self, project: Project) -> None:
        """Initialize with project reference."""
        ...

    def resolve(self, target: Target, env: Environment | None) -> None:
        """Resolve target in phase 1 (compilation)."""
        ...

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources in phase 2 (after outputs are populated)."""
        ...
```

The Resolver builds a dispatch table from registered factories and uses it during resolution.

### Transitive Tool Requirements (Language Propagation)
> **Status: Implemented**

When linking, the linker must match the "strongest" language used in the objects.

**Problem:** If you link C objects with one C++ object, you need the C++ linker (for libstdc++, C++ runtime init, etc.).

**Solution:** Objects carry their source language, which propagates to link decisions.

```python
c_obj = env.cc.Object('a.o', 'a.c')       # c_obj.language = 'c'
cxx_obj = env.cxx.Object('b.o', 'b.cpp')  # cxx_obj.language = 'cxx'

# Program builder examines all objects' languages
# Finds 'cxx', so uses C++ linker
exe = env.Program('myapp', [c_obj, cxx_obj])
# Automatically: uses g++ to link, adds -lstdc++ if needed
```

**Language strength ordering** (configurable per toolchain):
```python
# Higher = stronger, wins link-time tool selection
language_strength = {
    'c': 1,
    'cxx': 2,
    'fortran': 3,    # Fortran runtime often required
    'cuda': 4,       # CUDA requires nvcc link step
}
```

**Implementation:** Target tracks `required_languages: set[str]`. Linker builder inspects this to choose the right link command.

### Target (Build Specification with Usage Requirements)
> **Status: Implemented**

A Target represents a high-level build artifact with usage requirements that propagate to dependents.

```python
class Target:
    name: str
    nodes: list[Node]              # The actual files produced
    required_languages: set[str]   # Languages used (for linker selection)

    # Usage requirements (propagate to dependents transitively)
    public_include_dirs: list[DirNode]
    public_link_libs: list[Target]
    public_defines: list[str]
    public_link_flags: list[str]

    # Build requirements (for building this target only)
    private_include_dirs: list[DirNode]
    private_link_libs: list[Target]
    private_defines: list[str]
```

**Usage requirements propagate transitively:**

```python
# libbase has public includes
libbase = env.StaticLibrary('base', base_sources,
    public_include_dirs=['include/base'])

# libfoo uses libbase, and exposes its own includes
libfoo = env.StaticLibrary('foo', foo_sources,
    public_include_dirs=['include/foo'],
    private_link_libs=[libbase])  # libbase is private impl detail

# libbar uses libfoo publicly
libbar = env.StaticLibrary('bar', bar_sources,
    public_link_libs=[libfoo])

# app links libbar, transitively gets:
# - libbar's public includes
# - libfoo's public includes (via libbar)
# - libbase is NOT exposed (was private to libfoo)
app = env.Program('app', ['main.cpp'],
    link_libs=[libbar])
```

### Target Resolution and Lazy Node Creation
> **Status: Implemented**

**Targets represent builds without containing output nodes initially.**

When you call `project.SharedLibrary("mylib", env)`, it returns a Target object that *describes* what to build, but doesn't yet contain the actual output nodes. The Target is a configuration object:

```python
lib = project.SharedLibrary("mylib", env, sources=["lib.cpp"])
lib.output_name = "mylib.ofx"  # Customize output filename

# At this point:
# - lib.sources contains the source FileNodes
# - lib.output_nodes is EMPTY []
# - lib.object_nodes is EMPTY []
```

**Resolution populates the nodes.** The Resolver, called via `project.resolve()`, processes all targets in dependency order and:

1. Computes effective requirements (flags from transitive dependencies)
2. Creates object nodes for each source file
3. Creates output nodes (library/program files) with proper naming
4. Sets up build_info with commands and flags

```python
project.resolve()

# Now:
# - lib.object_nodes contains [FileNode("build/obj.mylib/lib.o")]
# - lib.output_nodes contains [FileNode("build/mylib.ofx")]
```

**Why this design?** The output filename and build flags depend on:
- The `output_name` attribute (may be set after target creation)
- Toolchain defaults (platform-specific naming like `.dylib` vs `.so`)
- Effective requirements from dependencies (must be computed in dependency order)

**Pending sources for lazy resolution.** Some operations, like `Install()`, need to reference a target's outputs. Rather than requiring users to carefully order their build script, targets can have `_pending_sources` - references that are resolved after the main resolution phase:

```python
# These can appear in any order:
lib = project.SharedLibrary("mylib", env, sources=["lib.cpp"])
install = project.Install("dist/lib", [lib])  # lib.output_nodes is empty here!

# resolve() handles it:
# 1. Phase 1: Resolve build targets (populates lib.output_nodes)
# 2. Phase 2: Resolve pending sources (install now sees lib.output_nodes)
project.resolve()
```

This makes build scripts declarative - the order of declarations doesn't matter.

### ToolchainContext: Extensible Build Variables
> **Status: Implemented**

**Problem:** The core shouldn't know about C/C++ concepts like `-I` include flags or `-D` defines, but generators need to write these flags to build files. How do we keep the core tool-agnostic while supporting toolchain-specific flag formatting?

**Solution:** The `ToolchainContext` protocol provides a clean abstraction layer between the resolver and generators.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Resolution Phase                              │
│                                                                          │
│  Target + Environment                                                    │
│         │                                                                │
│         ▼                                                                │
│  compute_effective_requirements()  ──► EffectiveRequirements            │
│         │                              (includes, defines, flags, etc.)  │
│         ▼                                                                │
│  toolchain.create_build_context()  ──► ToolchainContext                 │
│         │                              (CompileLinkContext for C/C++)   │
│         ▼                                                                │
│  node._build_info["context"] = context                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Generation Phase                               │
│                                                                          │
│  Generator reads node._build_info["context"]                            │
│         │                                                                │
│         ▼                                                                │
│  context.get_env_overrides() ──► {"includes": ["/path1", "/path2"],     │
│                                   "defines": ["FOO", "BAR=1"],          │
│                                   "extra_flags": ["-Wall", "-O2"],      │
│                                   "ldflags": ["-pthread"],              │
│                                   "libs": ["foo", "bar"],               │
│                                   "libdirs": ["/path1", "/path2"]}      │
│         │                                                                │
│         ▼                                                                │
│  Resolver sets overrides on env.<tool>.* namespace                      │
│         │                                                                │
│         ▼                                                                │
│  subst() expands command template with effective values                 │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key design points:**

1. **ToolchainContext Protocol** - Defines a single method:
   ```python
   class ToolchainContext(Protocol):
       def get_env_overrides(self) -> dict[str, object]:
           """Return values to set on env.<tool>.* before command expansion.

           These values are set on the environment's tool namespace so that
           template expressions like ${prefix(cc.iprefix, cc.includes)} are
           expanded during subst() with the effective requirements.
           """
           ...
   ```

2. **CompileLinkContext** - Standard implementation for C/C++ toolchains:
   ```python
   @dataclass
   class CompileLinkContext:
       includes: list[str]      # Include directories (no prefix)
       defines: list[str]       # Preprocessor definitions (no prefix)
       flags: list[str]         # Additional compiler flags
       link_flags: list[str]    # Linker flags
       libs: list[str]          # Libraries to link (no prefix)
       libdirs: list[str]       # Library search directories (no prefix)

       # Prefixes (customizable per toolchain)
       include_prefix: str = "-I"
       define_prefix: str = "-D"
       libdir_prefix: str = "-L"
       lib_prefix: str = "-l"

       def get_env_overrides(self) -> dict[str, object]:
           # Returns unprefixed values - subst() applies prefixes via ${prefix(...)}
           # Values: {"includes": ["/path1", "/path2"], "defines": ["FOO", "BAR"]}
           ...
   ```

3. **MsvcCompileLinkContext** - MSVC-specific formatting:
   ```python
   @dataclass
   class MsvcCompileLinkContext(CompileLinkContext):
       include_prefix: str = "/I"
       define_prefix: str = "/D"
       libdir_prefix: str = "/LIBPATH:"
       lib_prefix: str = ""  # MSVC uses full names: kernel32.lib
   ```

**Variable substitution flow:**

1. **Command templates** in toolchains include placeholders and prefix functions:
   ```python
   # In LlvmCcTool
   "objcmd": ["$cc.cmd", "$cc.flags", "${prefix($cc.iprefix, $cc.includes)}",
              "${prefix($cc.dprefix, $cc.defines)}", "$cc.extra_flags",
              "-c", "-o", "$$out", "$$in"]
   ```

2. **Resolver applies context overrides** before template expansion:
   ```python
   # context.get_env_overrides() returns unprefixed values
   overrides = {"includes": ["/path1", "/path2"], "defines": ["FOO", "BAR=1"]}
   # Resolver sets these on env.cc.includes, env.cc.defines, etc.
   ```

3. **subst() expands command** with effective values (prefix function applies toolchain prefixes):
   ```ninja
   rule cc_objcmd
     command = clang -I/path1 -I/path2 -DFOO -DBAR=1 -Wall -c -o $out $in
   ```

   Note: The `${prefix(...)}` function applies the toolchain's include/define prefixes
   (e.g., `-I`, `-D` for GCC/LLVM or `/I`, `/D` for MSVC). Paths with spaces are
   quoted appropriately for the target shell format.

**Shell quoting and command formatting:**

Commands flow through two stages before becoming the final string written to build files:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Command Template                                │
│   ["$cc.cmd", "$cc.flags", "-c", "-o", "$$out", "$$in"]                 │
│   (stored as list of tokens in toolchain)                               │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        subst() - Variable Expansion                      │
│                                                                          │
│   Input:  ["$cc.cmd", "$cc.flags", "-c", "-o", "$$out", "$$in"]        │
│   Output: ["clang", "-Wall", "-O2", "-c", "-o", "$out", "$in"]         │
│                                                                          │
│   Returns list[str] - tokens stay separate, no quoting yet             │
│   $$out becomes $out (ninja variables preserved)                        │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   to_shell_command() - Final Formatting                  │
│                                                                          │
│   shell="ninja": No quoting - Ninja handles its own escaping            │
│                  $in, $out preserved as-is                              │
│                  Output: "clang -Wall -O2 -c -o $out $in"               │
│                                                                          │
│   shell="bash":  POSIX quoting for special chars (spaces, $, etc.)     │
│                  Output: "clang -Wall -O2 -c -o '$out' '$in'"           │
│                                                                          │
│   shell="cmd":   Windows CMD quoting rules                              │
│   shell="powershell": PowerShell quoting rules                          │
└─────────────────────────────────────────────────────────────────────────┘
```

Key implementation details (in `pcons/core/subst.py`):

- **`subst()`** expands variables recursively but returns a **list of tokens**, preserving structure
- **`to_shell_command()`** joins tokens with shell-appropriate quoting via `_quote_for_shell()`
- **`shell="ninja"`** is special: no quoting is applied because Ninja handles its own variable expansion and escaping. Ninja variables like `$in`, `$out`, `$out.d` pass through unmodified.
- **Lists stay as lists** until the final `to_shell_command()` call - this ensures proper quoting of paths with spaces, special characters, etc.

Generators call `env.subst(template, shell=...)` which internally calls both functions:
```python
# In ninja.py - Ninja handles quoting, preserve $in/$out
command = env.subst(cmd_template, shell="ninja")

# In makefile.py - Need POSIX quoting
command = env.subst(command_template, shell="posix")
```

**Shell quoting during command expansion:**

When `subst()` expands command templates, it applies shell-appropriate quoting
based on the target format (Ninja or POSIX shell):

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    context.get_env_overrides()                           │
│                                                                          │
│   Returns: {"includes": ["/path", "/My Headers"],                       │
│             "defines": ["FOO", 'MSG="Hello World"']}                    │
│   (unprefixed values - prefix function applies toolchain prefixes)      │
└─────────────────────────────────────────────────────────────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
┌───────────────────────────┐           ┌───────────────────────────┐
│   env.subst(shell="ninja")│           │  env.subst(shell="posix") │
│                           │           │                           │
│ Paths with spaces get     │           │ Paths with spaces get     │
│ double-quoted for Ninja   │           │ single-quoted for shell   │
│                           │           │                           │
│ Output:                   │           │ Output:                   │
│ -I/path "-I/My Headers"   │           │ -I/path '-I/My Headers'   │
└───────────────────────────┘           └───────────────────────────┘
```

This design ensures paths with spaces (e.g., `/Users/Alice/My Projects/include`)
and defines with special characters (e.g., `MSG="Hello World"`) work correctly
across all output formats.

**Why this design?**

- **Core stays generic**: The core only sees `ToolchainContext.get_env_overrides() -> dict[str, object]`
- **Toolchains control formatting**: GCC uses `-I`, MSVC uses `/I` - prefix functions in command templates use toolchain-specific prefixes
- **subst() handles quoting**: The `subst()` function knows the target shell format and applies appropriate quoting
- **Paths with spaces work**: By keeping tokens as lists and quoting during expansion, paths like `/My Projects/include` are properly handled
- **Extensible**: A hypothetical LaTeX toolchain could define `DocumentContext` with completely different variables

**Custom toolchains** can provide their own context classes:

```python
@dataclass
class DocumentContext:
    """Context for document generation (hypothetical)."""
    input_format: str = "markdown"
    output_format: str = "pdf"
    template: str | None = None

    def get_env_overrides(self) -> dict[str, object]:
        result = {"format": [f"--from={self.input_format}", f"--to={self.output_format}"]}
        if self.template:
            result["template"] = [f"--template={self.template}"]
        return result
```

### All Build Outputs Are Targets
> **Status: Implemented** - All builder methods return Target objects for consistency.

**Design principle:** Every builder that creates output files should return a `Target` object, not raw `FileNode`s or `list[FileNode]`.

**Why this matters:**
- **Consistency**: Users can pass any build output to `Install()`, other builders, etc.
- **Dependency tracking**: Targets participate in the dependency resolution system
- **Future extensibility**: Targets can have usage requirements if needed later

**Correct pattern:**
```python
# Project methods return Target
lib = project.StaticLibrary("mylib", env, sources=["lib.c"])
archive = project.Tarfile(env, output="dist/docs.tar.gz", sources=["docs/"])
generated = env.Command(target="out.h", source="in.txt", command="...")

# All can be used uniformly
project.Install("dist/", [lib, archive, generated])
```

**Builder methods that return Target:**
- `project.Program()` - Executable programs
- `project.StaticLibrary()` - Static libraries
- `project.SharedLibrary()` - Shared/dynamic libraries
- `project.HeaderOnlyLibrary()` - Header-only interface libraries
- `project.ObjectLibrary()` - Object files without linking
- `project.Tarfile()` - Tar archives (.tar, .tar.gz, .tar.bz2, .tar.xz)
- `project.Zipfile()` - Zip archives
- `project.Install()` - Install/copy operations
- `project.InstallAs()` - Install with rename
- `env.Command()` - Custom shell commands

**Historical note:** Early versions of `env.Command()` returned `list[FileNode]` for simplicity. This was changed in v0.2.0 to return `Target` for consistency. The new signature uses keyword-only arguments for clarity.

**Implementation guideline:** When adding new builders:
1. Create a `Target` object with appropriate `target_type`
2. Store build info in `target._build_info`
3. Register with `project.add_target(target)`
4. Return the `Target`, not the output nodes

### Scanner
> **Status: Partial** - Scanner protocol defined. Build-time depfiles work via Ninja. Configure-time scanning not yet implemented.

A Scanner discovers implicit dependencies (e.g., C/C++ header includes).

```python
class Scanner(Protocol):
    def scan(self, node: FileNode, env: Environment) -> list[Node]:
        """Return implicit dependencies of this node."""
        ...

    def depfile_rule(self) -> str | None:
        """Return depfile generation flags, or None for configure-time scanning."""
        # e.g., '-MD -MF $out.d' for GCC
        ...
```

**Current Status Note:**

Configure-time scanning (parsing source files during the generate phase to extract
dependencies) is **not yet implemented** and is deferred. For C/C++ projects, this is
not a problem because modern compilers support depfile generation, which is more
accurate and doesn't require pcons to understand the preprocessor.

**Why configure-time scanning is deferred:**
- Build-time depfiles are more accurate (compiler knows all includes, macros, etc.)
- Implementing a correct C/C++ scanner requires handling preprocessor conditionals
- Most tools that pcons targets already support depfile generation
- Adding a scanner for a language can be done later without breaking existing builds

**Scanning strategies:**

1. **Build-time depfiles** (preferred): Compiler generates deps during build
   ```ninja
   rule cc
     depfile = $out.d
     deps = gcc
     command = gcc -MD -MF $out.d -c -o $out $in
   ```
   This is what pcons uses for C/C++ via toolchain SourceHandler.depfile settings.

2. **Configure-time scanning** (not yet implemented): Parse sources during generate phase
   - Would be used when tool doesn't support depfiles
   - Results would be embedded in build graph
   - Example use case: custom template languages, document includes

### Generator
> **Status: Implemented** - Ninja and Makefile generators fully implemented. CompileCommandsGenerator and MermaidGenerator available. IDE generators planned.

A Generator transforms the dependency graph into build files.

```python
class Generator(Protocol):
    name: str

    def generate(self, project: Project, output_dir: Path) -> None:
        """Write build files for this project."""
        ...
```

**Generators:**
- `NinjaGenerator`: Primary output format - **Implemented**
- `CompileCommandsGenerator`: For IDE/tooling integration - **Implemented**
- `MermaidGenerator`: For dependency graph visualization - **Implemented**
- `MakefileGenerator`: For environments without Ninja - **Implemented**
- `XcodeGenerator`: Xcode project files - **Implemented** (with limitations, see below)
- `VSCodeGenerator`: VSCode project files - **Planned**

**Generator responsibilities:**
- Translate Nodes and Builders into build rules
- Handle platform-specific details (path separators, response files on Windows)
- Emit depfile rules for incremental builds
- Properly handle directory semantics (order-only vs real deps)

#### Xcode Generator Limitations

The Xcode generator creates `.xcodeproj` bundles that can be opened in Xcode or built
with `xcodebuild`. However, Xcode has a fundamentally different build model than
Ninja/Make, which imposes some limitations:

**Supported:**
- Program, StaticLibrary, SharedLibrary targets (native `PBXNativeTarget`)
- Install, InstallAs, InstallDir targets (via `PBXAggregateTarget` with shell scripts)
- Tarfile, Zipfile targets (via `PBXAggregateTarget` with shell scripts)
- Target dependencies (including implicit dependencies from Install/Archive sources)
- Compile flags, defines, include paths, link flags
- Debug/Release configurations

**Not Supported:**
- **Source generators / custom commands with dependency tracking**: Xcode's
  `PBXShellScriptBuildPhase` doesn't support ninja-style depfiles, so commands that
  generate source files won't trigger rebuilds when their dependencies change.
- **Fine-grained incremental builds for script phases**: Xcode's script phases use
  input/output file lists, not depfiles, so incremental rebuild detection is limited.
- **Aliases**: Xcode doesn't have a direct equivalent to ninja aliases. Use explicit
  target names or create aggregate targets manually.
- **ObjectLibrary**: Not directly representable in Xcode's target model.

**Path handling differences:**
- Xcode puts built products in `Release/` or `Debug/` subdirectories
- The generator handles path translation for shell scripts automatically
- Source files use paths relative to the project root (via `$topdir` variable)

**Testing note:** The Xcode generator works for building, but automated tests in
`test_examples.py` use ninja-specific commands (e.g., `ninja -C build install`) that
don't have direct xcode equivalents in the test harness.

### Project
> **Status: Implemented**

The top-level container for the entire build specification.

```python
class Project:
    name: str
    config: Config               # Loaded from configure phase
    root_dir: Path
    build_dir: Path
    environments: list[Environment]
    targets: list[Target]
    default_targets: list[Target]
    nodes: dict[Path, Node]      # All nodes, keyed by path

    def Environment(self, toolchain: Toolchain = None, **kwargs) -> Environment:
        """Create a new environment in this project."""
        ...

    def Default(self, *targets: Target) -> None:
        """Set default build targets."""
        ...

    def generate(self, generators: list[Generator] = None) -> None:
        """Generate build files."""
        ...
```

---

## Key Design Decisions

### Tool-Agnostic Core
> **Status: Implemented** - Core modules (`pcons/core/`) are language-agnostic. All tool-specific code is in `pcons/tools/` and `pcons/toolchains/`.

The core (`pcons/core/`) must remain completely tool-agnostic. It knows nothing about:
- Compiler flags (`-O2`, `/Od`, `-g`, etc.)
- Preprocessor defines (`-D`, `/D`)
- Language-specific concepts (C flags, C++ flags, linker flags)
- Specific tool names (gcc, clang, msvc)

**Why this matters:** Pcons should support any build tool - C/C++ compilers, Rust, Go, LaTeX, game engines, Python bundlers, protobuf compilers, and tools we haven't imagined yet. The core provides:
- Dependency graph management
- Variable substitution
- Environment and tool namespaces
- Node and target abstractions

**Toolchains own their semantics:** Each toolchain (GCC, LLVM, MSVC, etc.) implements its own `apply_variant()` method to handle build variants like "debug" or "release". The core only knows the variant *name* - toolchains define what it means.

```python
# Core only provides:
env.set_variant("debug")  # Just a name, delegates to toolchain

# GCC toolchain implements:
def apply_variant(self, env, variant, **kwargs):
    if variant == "debug":
        env.cc.flags.extend(["-O0", "-g"])
        env.cc.defines.extend(["-DDEBUG"])

# A hypothetical LaTeX toolchain might implement:
def apply_variant(self, env, variant, **kwargs):
    if variant == "draft":
        env.latex.options.append("draft")
```

**Guidelines for new code:**
- Never add compiler flags, tool names, or language-specific logic to `pcons/core/`
- Tool-specific code belongs in `pcons/toolchains/` or `pcons/tools/`
- If you need build configuration, implement it in the toolchain

### Rebuild Detection: Timestamps vs Signatures
> **Status: Implemented** - Pcons generates Ninja files which handle rebuild detection.

**Decision: Rely on Ninja's timestamp + command comparison.**

SCons uses content signatures (MD5/SHA) stored in a database. This is powerful but:
- Requires reading every source file on every build
- Database can become corrupted or out of sync
- Adds complexity

Ninja uses:
- File modification timestamps
- Command line comparison (rebuild if command changes)
- Depfiles for implicit dependencies

This is sufficient for most cases and much simpler. The tradeoff:
- Touching a file without changing it triggers rebuild (rare in practice)
- Ninja handles this well and is battle-tested

### Error Handling
> **Status: Implemented** - Custom error hierarchy with source location tracking.

**Fail fast, fail clearly.**

- Missing source file: Error at generate time
- Missing tool: Error at configure time (not silent skip)
- Dependency cycle: Error with cycle path shown
- Unknown variable: Error (not silent empty string)
- Circular variable reference: Error with chain shown

**Traceability:**
- Every Node knows where it was defined (file:line)
- Error messages include this information
- Debug mode shows full dependency chains

### Extensibility Points
> **Status: Implemented** - Builder registry fully implemented. Toolchain, scanner, and generator registries also available.

**Builders are plugins (fully implemented):**
```python
from pcons.core.builder_registry import builder
from pcons.core.target import TargetType

@builder("MyBuilder", target_type=TargetType.COMMAND)
class MyBuilder:
    @staticmethod
    def create_target(project, ...):
        ...

# Immediately available: project.MyBuilder(...)
```

**Toolchains are plugins:**
```python
@register_toolchain('my_toolchain')
class MyToolchain(Toolchain):
    ...
```

**Scanners are plugins:**
```python
@register_scanner('.xyz')
class XyzScanner(Scanner):
    ...
```

**Generators are plugins:**
```python
@register_generator('bazel')
class BazelGenerator(Generator):
    ...
```

**Expansion packs** - third-party packages can add multiple builders and toolchains:
```python
# my_expansion/__init__.py
def register():
    from my_expansion import builders, toolchains  # triggers @builder registration
```

### Module/Add-on System
> **Status: Implemented**

Pcons provides an add-on/plugin system for creating reusable modules that handle domain-specific tasks (plugin bundles, SDK configuration, custom package discovery).

**Module discovery:**
Modules are automatically discovered from these locations (in priority order):
1. `PCONS_MODULES_PATH` environment variable
2. `~/.pcons/modules/` - User's global modules
3. `./pcons_modules/` - Project-local modules

```python
# ~/.pcons/modules/ofx.py
"""OFX plugin support."""

__pcons_module__ = {
    "name": "ofx",
    "version": "1.0.0",
}

def setup_env(env):
    env.cxx.flags.append("-fvisibility=hidden")

def register():
    """Called automatically at load time."""
    pass
```

**Module access:**
```python
# In pcons-build.py
from pcons.modules import ofx
ofx.setup_env(env)
```

**Contrib modules:** Generic helpers ship with pcons in `pcons.contrib`:
- `pcons.contrib.bundle` - macOS bundle and flat bundle creation helpers
- `pcons.contrib.platform` - Platform detection utilities

```python
from pcons.contrib import bundle, platform

plist = bundle.generate_info_plist("MyPlugin", "1.0.0")
if platform.is_macos():
    bundle.create_macos_bundle(project, env, plugin, bundle_dir="...")
```

---

## Platform-Specific Features

### Windows Manifest Support
> **Status: Implemented** - Located in `pcons/contrib/windows/manifest.py`

Windows applications require SxS manifests for proper DPI awareness, visual styles,
UAC elevation, and assembly dependencies. Pcons provides helpers to generate these
manifests and embed them in executables.

```python
from pcons.contrib.windows import manifest

# Create application manifest with common settings
app_manifest = manifest.create_app_manifest(
    project, env,
    output="app.manifest",
    dpi_aware="PerMonitorV2",     # Windows 10+ DPI awareness
    visual_styles=True,           # Modern UI controls
    uac_level="asInvoker",        # Run without elevation
    supported_os=["win10", "win81", "win7"],
)

# Add to program sources - automatically embedded by MSVC linker
app = project.Program("myapp", env)
app.add_sources(["main.c", app_manifest])
```

For private DLL assemblies:
```python
# Create assembly manifest for DLL collection
assembly = manifest.create_assembly_manifest(
    project, env,
    name="MyApp.Libraries",
    version="1.0.0.0",
    dlls=[mylib, helper_lib],
)
```

### Installer Generation
> **Status: Implemented** - Located in `pcons/contrib/installers/`

Pcons provides platform-specific installer generation helpers:

**macOS** (`pcons/contrib/installers/macos.py`):
- `create_component_pkg()`: Simple .pkg with pkgbuild
- `create_pkg()`: Full product archive with productbuild (UI customization, license)
- `create_dmg()`: Disk image with optional /Applications symlink

```python
from pcons.contrib.installers import macos

# Create a .pkg installer
pkg = macos.create_pkg(
    project, env,
    name="MyApp",
    version="1.0.0",
    identifier="com.example.myapp",
    sources=[app],
    install_location="/Applications",
    welcome=Path("installer/welcome.rtf"),
)

# Create a drag-and-drop .dmg
dmg = macos.create_dmg(
    project, env,
    name="MyApp",
    sources=[app_bundle],
    applications_symlink=True,
)
```

**Windows** (`pcons/contrib/installers/windows.py`):
- `create_msix()`: Modern MSIX package for Windows 10+

Staging directories (`.pkg_staging/`, `.dmg_staging/`, `.msix_staging/`) are
validated to ensure they don't conflict with user build outputs.

---

## File Organization
> **Note:** This shows the file organization with implementation status.

```
pcons/
├── __init__.py
├── __main__.py              # CLI entry point .................... [Implemented]
├── cli.py                   # Command-line interface ............. [Implemented]
├── modules.py               # Module discovery and loading ....... [Implemented]
├── contrib/
│   ├── __init__.py          # Contrib package init ............... [Implemented]
│   ├── bundle.py            # Bundle creation helpers ............ [Implemented]
│   ├── platform.py          # Platform detection utilities ....... [Implemented]
│   ├── installers/          # Platform installer generation
│   │   ├── __init__.py
│   │   ├── macos.py         # .pkg and .dmg creation ............ [Implemented]
│   │   └── windows.py       # MSIX package creation .............. [Implemented]
│   └── windows/
│       └── manifest.py      # Windows SxS manifest generation .... [Implemented]
├── core/
│   ├── __init__.py
│   ├── node.py              # Node hierarchy ..................... [Implemented]
│   ├── environment.py       # Environment with namespaced tools .. [Implemented]
│   ├── builder.py           # Builder base class ................. [Implemented]
│   ├── builder_registry.py  # Extensible builder registration .... [Implemented]
│   ├── paths.py             # PathResolver for path handling ..... [Implemented]
│   ├── scanner.py           # Scanner interface .................. [Partial]
│   ├── target.py            # Target with usage requirements ..... [Implemented]
│   ├── project.py           # Project container .................. [Implemented]
│   ├── subst.py             # Variable substitution engine ....... [Implemented]
│   └── build_context.py     # ToolchainContext implementations ... [Implemented]
├── builders/
│   ├── __init__.py          # Builder registration ............... [Implemented]
│   ├── compile.py           # Program, Library builders .......... [Implemented]
│   ├── install.py           # Install, InstallAs, InstallDir ..... [Implemented]
│   └── archive.py           # Tarfile, Zipfile builders .......... [Implemented]
├── configure/
│   ├── __init__.py
│   ├── config.py            # Configure context and caching ...... [Implemented]
│   ├── checks.py            # Feature checks (compile tests) ..... [Partial - needs toolchain]
│   └── platform.py          # Platform detection ................. [Implemented]
├── tools/
│   ├── __init__.py          # Tool registry ...................... [Implemented]
│   ├── tool.py              # Tool base class .................... [Implemented]
│   ├── toolchain.py         # Toolchain base class ............... [Implemented]
│   ├── cc.py                # C compiler tool .................... [Implemented]
│   ├── cxx.py               # C++ compiler tool .................. [Implemented]
│   ├── fortran.py           # Fortran compiler tool .............. [Planned]
│   ├── link.py              # Linker tools ....................... [Implemented]
│   └── ...                  # Other tools
├── toolchains/
│   ├── __init__.py
│   ├── gcc.py               # GCC toolchain ...................... [Implemented]
│   ├── llvm.py              # LLVM/Clang toolchain ............... [Implemented]
│   ├── msvc.py              # MSVC toolchain ..................... [Implemented]
│   ├── clang_cl.py          # Clang-cl toolchain ................. [Implemented]
│   └── unix.py              # Base Unix toolchain ................ [Implemented]
├── generators/
│   ├── __init__.py          # Generator registry ................. [Implemented]
│   ├── generator.py         # Generator base class ............... [Implemented]
│   ├── ninja.py             # Ninja generator .................... [Implemented]
│   ├── mermaid.py           # Mermaid diagram generator .......... [Implemented]
│   ├── compile_commands.py  # compile_commands.json .............. [Implemented]
│   └── makefile.py          # Makefile generator ................. [Implemented]
├── scanners/
│   ├── __init__.py          # Scanner registry ................... [Planned]
│   ├── c.py                 # C/C++ header scanner ............... [Planned - uses depfiles]
│   └── ...
├── packages/
│   ├── __init__.py          # Package loading utilities .......... [Implemented]
│   ├── description.py       # PackageDescription class ........... [Implemented]
│   ├── imported.py          # ImportedTarget class ............... [Implemented]
│   ├── finders/
│   │   ├── __init__.py
│   │   ├── base.py          # Base finder class .................. [Implemented]
│   │   ├── pkgconfig.py     # pkg-config finder .................. [Implemented]
│   │   ├── system.py        # Manual system search ............... [Implemented]
│   │   ├── conan.py         # Conan finder ....................... [Implemented]
│   │   └── vcpkg.py         # vcpkg finder ....................... [Planned]
│   └── fetch/
│       ├── __init__.py
│       ├── cli.py           # pcons-fetch CLI .................... [Implemented]
│       └── ...              # (CMake/autotools builders inline)
└── util/
    ├── __init__.py
    ├── path.py              # Path utilities ..................... [Implemented]
    └── ...
```

---

## Example: Complete Build
> **Note:** This example shows the intended API. Some features (like `find_toolchain('cxx')` auto-detection and `config.packages` dict) are partially implemented or planned.

### pcons-configure.py
```python
from pcons import Configure
from pcons.packages import PkgConfigFinder, ConanFinder, SystemFinder

config = Configure()

# Find C++ toolchain (tries gcc, then clang, then msvc)
cxx_toolchain = config.find_toolchain('cxx')

# Check for C++20 support
if cxx_toolchain.cxx.check_flag('-std=c++20'):
    config.set('cxx_standard', 'c++20')
else:
    config.set('cxx_standard', 'c++17')

# Check for optional headers
config.set('have_optional', cxx_toolchain.cxx.check_header('optional'))

# Find dependencies
config.packages['zlib'] = config.find_package('zlib',
    finders=[PkgConfigFinder, SystemFinder(libraries=['z'])])

config.packages['openssl'] = config.find_package('openssl',
    finders=[PkgConfigFinder])

# Or load from pcons-fetch results
for pkg_file in Path('deps/install').glob('*.pcons-pkg.toml'):
    pkg = config.load_package(pkg_file)
    config.packages[pkg.name] = pkg

# Save
config.save()
```

### pcons-build.py
```python
from pcons import Project, load_config

config = load_config()
project = Project('myapp', config)

# Import external dependencies as targets
zlib = project.ImportedTarget(config.packages['zlib'])
openssl = project.ImportedTarget(config.packages['openssl'])

# Create environment with configured toolchain
env = project.Environment(toolchain=config.cxx_toolchain)
env.cxx.flags = [f'-std={config.cxx_standard}', '-Wall']

if config.have_optional:
    env.cxx.defines.append('HAVE_OPTIONAL')

# Debug variant
debug = env.clone()
debug.cxx.flags += ['-g', '-O0']
debug.cxx.defines += ['DEBUG']
debug.build_dir = 'build/debug'

# Release variant
release = env.clone()
release.cxx.flags += ['-O3', '-DNDEBUG']
release.build_dir = 'build/release'

# Build library (uses cxx tool explicitly for .cpp files)
libcore_sources = env.Glob('src/core/*.cpp')
libcore = release.StaticLibrary(
    'core',
    sources=libcore_sources,
    public_include_dirs=['include'],
    private_link_libs=[zlib],      # Uses zlib internally
)

# Build executable - links against libcore and openssl
# Automatically uses C++ linker because libcore contains C++ objects
# Gets zlib transitively through libcore (if it were public)
app = release.Program(
    'myapp',
    sources=['src/main.cpp'],
    link_libs=[libcore, openssl],
)

project.Default(app)
project.generate()
```

---

## Open Questions

1. **Configuration caching**: What format? JSON for readability, or pickle for speed? When to invalidate? (Probably: hash of configure.py + tool versions)

2. **Variant builds**: Handled via `env.set_variant("debug")` which delegates to the toolchain's `apply_variant()` method. Each toolchain defines what variants mean for its tools. Environment cloning allows multiple variant builds in the same project.

3. **Distributed builds**: distcc/icecream/sccache should "just work" by wrapping compiler commands. Do we need explicit support?

4. **Test integration**: Should test discovery be built-in? Leaning toward: provide hooks, let pytest/gtest handle discovery.

---

## Package Management Integration
> **Status: Partial** - PackageDescription, ImportedTarget, pkg-config finder, system finder, and pcons-fetch tool are implemented. Conan/vcpkg finders planned. Integration with Project.ImportedTarget() needs work.

**Core principle: Pcons handles consumption, not acquisition.**

Pcons is not a package manager. External tools (Conan, vcpkg, pcons-fetch, manual builds) handle fetching and building dependencies. Pcons imports the results through a standard description format.

```
┌─────────────────────────────────────────────────────────────┐
│                    Package Sources                          │
├──────────┬──────────┬──────────┬──────────┬────────────────┤
│  Conan   │  vcpkg   │ System   │ Source   │  Manual        │
│          │          │ (apt,    │ (pcons-  │  (prebuilt     │
│          │          │  brew)   │  fetch)  │   in tree)     │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴───────┬────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│              Package Description Files                       │
│                   (.pcons-pkg.toml)                         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                        Pcons                                 │
│         Imports as ImportedTarget with usage requirements   │
└─────────────────────────────────────────────────────────────┘
```

### Package Description Format
> **Status: Implemented** - PackageDescription class with TOML serialization.

A simple TOML format that any tool can generate:

```toml
# zlib.pcons-pkg.toml
[package]
name = "zlib"
version = "1.2.13"

[usage]
include_dirs = ["/usr/local/include"]
library_dirs = ["/usr/local/lib"]
libraries = ["z"]                    # becomes -lz
defines = []
compile_flags = []
link_flags = []

# Other packages this depends on (for transitive deps)
[dependencies]
# none for zlib
```

For component-based packages (Boost, Qt, etc.):

```toml
# boost.pcons-pkg.toml
[package]
name = "boost"
version = "1.84.0"

[usage]
# Base usage (header-only parts)
include_dirs = ["/opt/boost/include"]

# Named components
[components.filesystem]
library_dirs = ["/opt/boost/lib"]
libraries = ["boost_filesystem"]
dependencies = ["boost:system"]      # depends on another component

[components.system]
library_dirs = ["/opt/boost/lib"]
libraries = ["boost_system"]

[components.headers]
# Header-only, no libraries
```

### ImportedTarget
> **Status: Implemented** - Class exists with full API. Integration with Project needs completion.

An ImportedTarget represents an external dependency. It has usage requirements but no build rules.

```python
class ImportedTarget(Target):
    """A target representing an external/pre-built dependency."""

    # Inherited from Target:
    # - public_include_dirs
    # - public_link_libs
    # - public_defines
    # - public_link_flags

    # Additional:
    library_files: list[Path]    # Actual .a/.so/.lib files
    library_dirs: list[Path]     # -L paths
    is_imported: bool = True     # No build rules generated
```

### Package Finders
> **Status: Partial** - PkgConfigFinder, SystemFinder, and ConanFinder implemented. VcpkgFinder planned.

Finders locate packages and generate `.pcons-pkg.toml` files (or create ImportedTargets directly).

```python
# In configure.py
from pcons import Configure
from pcons.packages import (
    PkgConfigFinder,
    ConanFinder,
    VcpkgFinder,
    SystemFinder,
)

config = Configure()

# From pkg-config (reads .pc files)
zlib = PkgConfigFinder.find('zlib')

# From Conan (reads conan-generated files)
# Assumes you've run: conan install . --output-folder=build
openssl = ConanFinder.find('openssl', conan_folder='build')

# From vcpkg
fmt = VcpkgFinder.find('fmt', vcpkg_root='/opt/vcpkg')

# Manual system search
jpeg = SystemFinder.find('jpeg',
    headers=['jpeglib.h'],
    libraries=['jpeg'],
    include_hints=['/usr/include', '/opt/local/include'],
    library_hints=['/usr/lib', '/opt/local/lib'],
)

# From existing .pcons-pkg.toml file
custom = config.load_package('deps/custom.pcons-pkg.toml')

config.packages['zlib'] = zlib
config.packages['openssl'] = openssl
config.packages['fmt'] = fmt
config.packages['jpeg'] = jpeg
config.packages['custom'] = custom
config.save()
```

### Using Packages in Builds
> **Status: Planned** - This API pattern is the goal; current implementation requires manual flag handling.

```python
# In pcons-build.py
from pcons import Project, load_config

config = load_config()
project = Project('myapp', config)

env = project.Environment(toolchain=config.cxx_toolchain)

# Import packages as targets
zlib = project.ImportedTarget(config.packages['zlib'])
openssl = project.ImportedTarget(config.packages['openssl'])

# For component-based packages
boost_fs = project.ImportedTarget(
    config.packages['boost'],
    components=['filesystem']  # pulls in 'system' transitively
)

# Use them like any other target - usage requirements propagate
app = env.Program('myapp', ['main.cpp'],
    link_libs=[zlib, openssl, boost_fs])
# Automatically gets all include dirs, library dirs, libraries, flags
```

### pcons-fetch: Source Dependency Tool
> **Status: Implemented** - CLI tool with CMake and autotools support. Generates .pcons-pkg.toml files.

For building dependencies from source, pcons provides `pcons-fetch`, a companion tool that:
1. Downloads/clones source code
2. Builds using the dependency's native build system
3. Generates `.pcons-pkg.toml` describing the result

```bash
pcons-fetch deps.toml --prefix=deps/install --toolchain=gcc-release
```

#### deps.toml format

```toml
# deps.toml - source dependencies to fetch and build

[settings]
prefix = "deps/install"          # where to install
source_dir = "deps/src"          # where to download sources
build_dir = "deps/build"         # where to build

# Compiler/flags to use (passed via environment variables)
[settings.env]
CC = "gcc"
CXX = "g++"
CFLAGS = "-O2"
CXXFLAGS = "-O2 -std=c++17"

[dependencies.zlib]
url = "https://github.com/madler/zlib/archive/refs/tags/v1.3.1.tar.gz"
sha256 = "..."                    # optional integrity check
build_system = "cmake"           # cmake, autotools, meson, make, custom
cmake_args = ["-DBUILD_SHARED_LIBS=OFF"]

[dependencies.json]
url = "https://github.com/nlohmann/json"
type = "git"
tag = "v3.11.3"
build_system = "cmake"
cmake_args = ["-DJSON_BuildTests=OFF"]

[dependencies.sqlite]
url = "https://www.sqlite.org/2024/sqlite-autoconf-3450000.tar.gz"
build_system = "autotools"
configure_args = ["--disable-shared", "--enable-static"]

[dependencies.custom_lib]
url = "https://example.com/custom.tar.gz"
build_system = "custom"
build_commands = [
    "make CC=$CC CFLAGS=$CFLAGS",
    "make install PREFIX=$PREFIX",
]
```

#### How pcons-fetch works

1. **Download**: Fetch and extract sources (or git clone)
2. **Configure**: Run build system's configure step with appropriate flags
3. **Build**: Run the build
4. **Install**: Install to the specified prefix
5. **Generate**: Create `.pcons-pkg.toml` by examining installed files

**Flag propagation** uses environment variables (CC, CXX, CFLAGS, CXXFLAGS, LDFLAGS). This is imperfect but universal - almost every build system respects these.

```python
# pcons-fetch internally does something like:
env = os.environ.copy()
env['CC'] = settings.env.CC
env['CXX'] = settings.env.CXX
env['CFLAGS'] = settings.env.CFLAGS
env['CXXFLAGS'] = settings.env.CXXFLAGS

if build_system == 'cmake':
    subprocess.run([
        'cmake', source_dir,
        '-DCMAKE_INSTALL_PREFIX=' + prefix,
        '-DCMAKE_C_COMPILER=' + env['CC'],
        '-DCMAKE_CXX_COMPILER=' + env['CXX'],
        *cmake_args
    ], env=env)
    subprocess.run(['cmake', '--build', '.'], env=env)
    subprocess.run(['cmake', '--install', '.'], env=env)
```

#### Generated package description

After building, pcons-fetch examines the install prefix and generates:

```toml
# deps/install/zlib.pcons-pkg.toml (auto-generated)
[package]
name = "zlib"
version = "1.3.1"
built_by = "pcons-fetch"
source = "https://github.com/madler/zlib/archive/refs/tags/v1.3.1.tar.gz"

[usage]
include_dirs = ["deps/install/include"]
library_dirs = ["deps/install/lib"]
libraries = ["z"]

[build_info]
# For debugging/reproducibility
cc = "gcc"
cxx = "g++"
cflags = "-O2"
cxxflags = "-O2 -std=c++17"
```

### Integration with External Package Managers
> **Status: Planned** - Conan and vcpkg integration planned but not yet implemented.

#### Conan Integration

For users who prefer Conan's more sophisticated dependency resolution:

```ini
# conanfile.txt
[requires]
zlib/1.3.1
openssl/3.2.0
boost/1.84.0

[generators]
PconsDeps
```

We provide a Conan generator (`PconsDeps`) that outputs `.pcons-pkg.toml` files:

```bash
conan install . --output-folder=build --build=missing
# Creates build/zlib.pcons-pkg.toml, build/openssl.pcons-pkg.toml, etc.
```

Then in configure.py:
```python
# Load all Conan-generated package files
for pkg_file in Path('build').glob('*.pcons-pkg.toml'):
    pkg = config.load_package(pkg_file)
    config.packages[pkg.name] = pkg
```

#### vcpkg Integration

Similar approach - vcpkg generates CMake files, we provide a finder that reads them:

```python
# VcpkgFinder reads vcpkg's installed packages
fmt = VcpkgFinder.find('fmt', vcpkg_root=os.environ.get('VCPKG_ROOT'))
```

### Package Search Order
> **Status: Partial** - Individual finders work; chained search order pattern not yet implemented.

When finding a package, finders can search multiple sources:

```python
# Try to find zlib from multiple sources, in order
zlib = config.find_package('zlib',
    finders=[
        PkgConfigFinder,           # Try pkg-config first
        ConanFinder(folder='build'), # Then Conan
        SystemFinder(              # Finally, manual search
            headers=['zlib.h'],
            libraries=['z'],
        ),
    ]
)
```

### Limitations and Tradeoffs

**ABI Compatibility**: When building from source, pcons-fetch uses environment variables for compiler/flags. This works for most cases but:
- Not all flags should propagate (e.g., `-Werror` might break deps)
- C++ ABI compatibility requires matching compiler versions
- Some build systems ignore environment variables

**Recommendation**: For complex C++ dependencies with ABI concerns, use Conan with matching profiles. For simpler C libraries or when building everything from source with the same compiler, pcons-fetch works well.

**What pcons-fetch is NOT**:
- A full dependency resolver (no SAT solving, no version constraints)
- A binary cache (always builds from source)
- A replacement for Conan/vcpkg for complex projects

It's intentionally simple: fetch, build with your flags, generate description.

---

## Non-Goals

- **Being a package manager**: Use Conan, vcpkg, or system packages
- **Being an executor**: Ninja/Make handle this better
- **Supporting legacy SCons scripts**: Clean break, new API
- **Hiding complexity**: Power users need access to the full graph

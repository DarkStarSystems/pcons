# Pcons Architecture

A modern Python-based build system that generates Ninja (or other) build files.

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
# configure.py - runs during configure phase
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
# build.py - runs during generate phase
from pcons import Project, load_config

config = load_config()  # Fast: loads cached results
project = Project('myapp', config)

env = project.Environment(toolchain=config.cxx)
# ... define builds ...
```

### Phase 3: Generate

1. Generator traverses the dependency graph
2. Build rules are emitted (e.g., `build.ninja`)
3. Auxiliary files generated (`compile_commands.json`, IDE projects)

**Output:** Build files ready for execution

### Phase 4: Build

User runs `ninja` (or `make`). Pcons is not involved.

---

## Core Abstractions

### Node

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
```

**Cross-tool variables** live at the environment level:
```python
env.build_dir = 'build'
env.variant = 'release'
```

### Variable Substitution (Always Recursive)

Variable expansion is **always recursive**. This is essential for building complex command lines.

```python
env.cc.cmd = 'gcc'
env.cc.flags = ['-Wall', '$cc.opt_flag']
env.cc.opt_flag = '-O2'
env.cc.include_flags = ['-I$inc' for inc in env.cc.includes]
env.cc.define_flags = ['-D$d' for d in env.cc.defines]

# Command line template - references other variables
env.cc.cmdline = '$cc.cmd $cc.flags $cc.include_flags $cc.define_flags -c -o $out $in'

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
- `$first_in` - first input file
- `$first_out` - first output file

### Tool

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

# build.py
env_gcc = project.Environment(toolchain=gcc)
env_llvm = project.Environment(toolchain=llvm)
```

### Builder

A Builder creates target nodes from source nodes, using a specific Tool.

```python
class Builder:
    name: str
    tool: Tool
    src_suffixes: list[str]      # What this builder accepts
    target_suffixes: list[str]   # What this builder produces (can be multiple)
    scanner: Scanner | None      # For implicit dependency discovery

    def __call__(self, env: Environment, target, sources, **kwargs) -> list[Node]:
        """Create target nodes from source nodes."""
        ...

    @property
    def language(self) -> str:
        """Language this builder compiles (for link-time tool selection)."""
        ...
```

### Transitive Tool Requirements (Language Propagation)

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

### Scanner

A Scanner discovers implicit dependencies.

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

**Scanning strategies:**

1. **Build-time depfiles** (preferred): Compiler generates deps during build
   ```ninja
   rule cc
     depfile = $out.d
     deps = gcc
     command = gcc -MD -MF $out.d -c -o $out $in
   ```

2. **Configure-time scanning**: Parse sources during generate phase
   - Used when tool doesn't support depfiles
   - Results embedded in build graph

### Generator

A Generator transforms the dependency graph into build files.

```python
class Generator(Protocol):
    name: str

    def generate(self, project: Project, output_dir: Path) -> None:
        """Write build files for this project."""
        ...
```

**Generators:**
- `NinjaGenerator`: Primary output format
- `MakefileGenerator`: For environments without Ninja
- `CompileCommandsGenerator`: For IDE/tooling integration (can run alongside others)
- `VSCodeGenerator`, `XcodeGenerator`: IDE project files

**Generator responsibilities:**
- Translate Nodes and Builders into build rules
- Handle platform-specific details (path separators, response files on Windows)
- Emit depfile rules for incremental builds
- Properly handle directory semantics (order-only vs real deps)

### Project

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

**Tools are plugins:**
```python
@register_tool('my_tool')
class MyTool(Tool):
    name = 'my_tool'
    ...
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

---

## File Organization

```
pcons/
├── __init__.py
├── __main__.py              # CLI entry point
├── cli.py                   # Command-line interface
├── core/
│   ├── __init__.py
│   ├── node.py              # Node hierarchy
│   ├── environment.py       # Environment with namespaced tools
│   ├── builder.py           # Builder base class
│   ├── scanner.py           # Scanner interface
│   ├── target.py            # Target with usage requirements
│   ├── project.py           # Project container
│   └── subst.py             # Variable substitution engine
├── configure/
│   ├── __init__.py
│   ├── config.py            # Configure context and caching
│   ├── checks.py            # Feature checks (compile tests, etc.)
│   └── platform.py          # Platform detection
├── tools/
│   ├── __init__.py          # Tool registry
│   ├── tool.py              # Tool base class
│   ├── toolchain.py         # Toolchain base class
│   ├── cc.py                # C compiler tool
│   ├── cxx.py               # C++ compiler tool
│   ├── fortran.py           # Fortran compiler tool
│   ├── link.py              # Linker tools (static, shared, exe)
│   └── ...                  # Other tools
├── toolchains/
│   ├── __init__.py
│   ├── gcc.py               # GCC toolchain
│   ├── llvm.py              # LLVM/Clang toolchain
│   ├── msvc.py              # MSVC toolchain
│   └── ...
├── generators/
│   ├── __init__.py          # Generator registry
│   ├── generator.py         # Generator base class
│   ├── ninja.py             # Ninja generator
│   ├── makefile.py          # Makefile generator
│   └── compile_commands.py  # compile_commands.json
├── scanners/
│   ├── __init__.py          # Scanner registry
│   ├── c.py                 # C/C++ header scanner
│   └── ...
├── packages/
│   ├── __init__.py          # Package loading utilities
│   ├── description.py       # PackageDescription class
│   ├── imported.py          # ImportedTarget class
│   ├── finders/
│   │   ├── __init__.py
│   │   ├── pkgconfig.py     # pkg-config finder
│   │   ├── conan.py         # Conan finder
│   │   ├── vcpkg.py         # vcpkg finder
│   │   └── system.py        # Manual system search
│   └── fetch/
│       ├── __init__.py
│       ├── cli.py           # pcons-fetch CLI
│       ├── download.py      # Source downloading
│       └── builders/        # Build system adapters
│           ├── cmake.py
│           ├── autotools.py
│           ├── meson.py
│           └── custom.py
└── util/
    ├── __init__.py
    ├── path.py              # Path utilities
    └── ...
```

---

## Example: Complete Build

### configure.py
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

### build.py
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

```python
# In build.py
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

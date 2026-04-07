# Porting from CMake to Pcons

This guide maps common CMake patterns to their pcons equivalents. It's designed for both humans and AI agents porting existing CMake projects.

**Key philosophy differences:**

- **CMake** uses a custom DSL with generator expressions, macros, and functions. Build logic often mixes configuration with build rules.
- **Pcons** uses plain Python. Conditionals are `if/else`, loops are `for`, and the full Python ecosystem (pathlib, os, regex) is available.
- Both systems separate configuration from build execution. Both can generate Ninja and Make files (pcons defaults to Ninja).

---

## Quick Reference

| CMake | pcons |
|-------|-------|
| `project(name)` | `Project("name")` |
| `add_executable(name src...)` | `project.Program("name", env, sources=[...])` |
| `add_library(name STATIC src...)` | `project.StaticLibrary("name", env, sources=[...])` |
| `add_library(name SHARED src...)` | `project.SharedLibrary("name", env, sources=[...])` |
| `target_link_libraries(t lib)` | `t.link(lib)` |
| `target_link_libraries(t -lm)` | `t.public.link_libs.append("m")` |
| `target_include_directories(t PUBLIC dir)` | `t.public.include_dirs.append(dir)` |
| `target_compile_definitions(t PRIVATE DEF)` | `t.private.defines.append("DEF")` |
| `set_target_properties(t PROPERTIES OUTPUT_NAME n)` | `t.output_name = "n"` |
| `set_target_properties(t PROPERTIES PREFIX "")` | `t.output_prefix = ""` |
| `set_target_properties(t PROPERTIES SUFFIX ".ofx")` | `t.output_suffix = ".ofx"` |
| `find_package(Foo)` | `project.find_package("Foo")` |
| `configure_file(in out)` | `configure_file(in, out, vars)` |
| `check_include_file(h VAR)` | `checks.check_header("h")` |
| `check_function_exists(f VAR)` | `checks.check_function("f")` |
| `check_c_compiler_flag(f VAR)` | `checks.check_flag("f")` |
| `check_c_source_compiles(src VAR)` | `checks.try_compile(src)` |
| `add_compile_options(-Wall)` | `env.cc.flags.append("-Wall")` |
| `option(OPT "desc" ON)` | `pcons.get_var("OPT", "ON")` or `os.environ.get("OPT", "ON")` |
| `install(TARGETS t DESTINATION d)` | `project.Install(d, [t])` |
| `add_custom_command(...)` | `env.Command(target, source, cmd)` |

---

## Project Setup

### CMake

```cmake
cmake_minimum_required(VERSION 3.20)
project(mylib VERSION 1.2.3 LANGUAGES C CXX)
```

### Pcons

```python
from pcons import Project, find_c_toolchain, Generator

project = Project("mylib", build_dir="build")
env = project.Environment(toolchain=find_c_toolchain())
```

Version handling is plain Python — read it from a file, set it as a variable, or hardcode it:

```python
from pathlib import Path
version = Path("VERSION").read_text().strip()
```

### Build directory

CMake typically uses an out-of-source `cmake -B build` directory. Pcons uses `build_dir="build"` as the default in the `Project()` constructor. Run the build with `uvx pcons` (or `pcons`) and then `ninja -C build`.

---

## Targets and Sources

### Programs

```cmake
# CMake
add_executable(myapp main.c util.c)
```

```python
# pcons
app = project.Program("myapp", env, sources=["main.c", "util.c"])
```

### Static Libraries

```cmake
# CMake
add_library(mylib STATIC src/lib.c src/util.c)
```

```python
# pcons
mylib = project.StaticLibrary("mylib", env, sources=["src/lib.c", "src/util.c"])
```

### Shared Libraries

```cmake
# CMake
add_library(mylib SHARED src/lib.c)
```

```python
# pcons
mylib = project.SharedLibrary("mylib", env, sources=["src/lib.c"])
```

Pcons automatically applies platform naming conventions (just like CMake):

| Target type | Linux | macOS | Windows |
|------------|-------|-------|---------|
| `StaticLibrary("foo")` | `libfoo.a` | `libfoo.a` | `foo.lib` |
| `SharedLibrary("foo")` | `libfoo.so` | `libfoo.dylib` | `foo.dll` |
| `Program("foo")` | `foo` | `foo` | `foo.exe` |

### Adding sources after creation

```cmake
# CMake
target_sources(mylib PRIVATE extra.c)
```

```python
# pcons
mylib.add_sources(["extra.c"])
```

---

## Dependencies and Usage Requirements

CMake's `target_link_libraries` with `PUBLIC`/`PRIVATE`/`INTERFACE` maps directly to pcons's usage requirements system.

### Linking targets

```cmake
# CMake
target_link_libraries(app PRIVATE mylib)
```

```python
# pcons
app.link(mylib)
```

`link()` applies the dependency's public usage requirements (include dirs, defines, link flags) transitively — just like CMake's `PUBLIC`.

### PUBLIC vs PRIVATE

```cmake
# CMake
target_include_directories(mylib PUBLIC include)
target_include_directories(mylib PRIVATE src)
target_compile_definitions(mylib PUBLIC USE_FEATURE)
target_compile_definitions(mylib PRIVATE INTERNAL_FLAG)
```

```python
# pcons
mylib.public.include_dirs.append("include")
mylib.private.include_dirs.append("src")
mylib.public.defines.append("USE_FEATURE")
mylib.private.defines.append("INTERNAL_FLAG")
```

### System libraries (`-l` flags)

!!! warning "Use `link_libs`, not `link_flags`"
    On Linux, link order matters. Libraries specified with `link_libs` are placed **after** object files on the link line (correct for `-l` resolution). `link_flags` are placed **before** objects.

```cmake
# CMake
target_link_libraries(mylib PUBLIC m pthread)
```

```python
# pcons
mylib.public.link_libs.append("m")
mylib.public.link_libs.append("pthread")
```

### INTERFACE-only libraries

CMake's `add_library(foo INTERFACE)` maps to pcons's header-only library:

```cmake
# CMake
add_library(headers INTERFACE)
target_include_directories(headers INTERFACE include)
```

```python
# pcons
headers = project.HeaderOnlyLibrary("headers")
headers.public.include_dirs.append("include")
```

---

## Configure Checks and config.h

CMake's `check_*` macros map to pcons's `ToolChecks` class.

### Setup

```cmake
# CMake
include(CheckIncludeFile)
include(CheckFunctionExists)
include(CheckCCompilerFlag)
include(CheckCSourceCompiles)
```

```python
# pcons
from pcons.configure.config import Configure
from pcons.configure.checks import ToolChecks

config = Configure(build_dir=build_dir)
checks = ToolChecks(config, env, "cc")  # "cc" for C, "cxx" for C++
```

### Header checks

```cmake
# CMake
check_include_file(alloca.h HAVE_ALLOCA_H)
```

```python
# pcons
have_alloca_h = checks.check_header("alloca.h").success
```

### Function checks

```cmake
# CMake
check_function_exists(qsort_r HAVE_QSORT_R)
```

```python
# pcons
have_qsort_r = checks.check_function("qsort_r").success
# With headers:
have_mremap = checks.check_function("mremap", headers=["sys/mman.h"]).success
```

### Compiler flag checks

```cmake
# CMake
check_c_compiler_flag(-Wno-unused-function HAVE_WNO_UNUSED)
```

```python
# pcons
have_wno_unused = checks.check_flag("-Wno-unused-function").success
```

`check_flag` automatically adds `-Werror` (or `/WX` on MSVC) when testing, so flags like `-Wno-stringop-overflow` that Clang silently accepts are correctly rejected.

### Source compilation checks

```cmake
# CMake
check_c_source_compiles("
    #include <emmintrin.h>
    int main() { __m128i x = _mm_setzero_si128(); (void)x; return 0; }
" HAVE_SSE2)
```

```python
# pcons
have_sse2 = checks.try_compile(
    '#include <emmintrin.h>\nint main() { __m128i x = _mm_setzero_si128(); (void)x; return 0; }',
    extra_flags=["-msse2"],
).success
```

### configure_file()

Pcons's `configure_file()` supports CMake-style `#cmakedefine`, `#cmakedefine01`, and `@VAR@` substitutions — your existing `.h.in` templates usually work as-is.

```cmake
# CMake
configure_file(cmake/config.h.in ${CMAKE_BINARY_DIR}/config.h)
```

```python
# pcons
from pcons import configure_file

configure_file(
    "cmake/config.h.in",
    build_dir / "config.h",
    {"HAVE_ALLOCA_H": "1" if have_alloca_h else "", "VERSION": "1.2.3"},
    strict=False,  # ignore template variables you don't define
)
```

Given this template:

```c
#cmakedefine HAVE_ALLOCA_H
#cmakedefine01 HAVE_THREADS
#define VERSION "@VERSION@"
```

With `{"HAVE_ALLOCA_H": "1", "VERSION": "1.2.3"}`, pcons produces:

```c
#define HAVE_ALLOCA_H
#define HAVE_THREADS 0
#define VERSION "1.2.3"
```

Use `strict=False` when your template references variables you don't set — they'll be treated as undefined (`#cmakedefine` becomes `/* #undef */`, `@VAR@` becomes empty string).

---

## Compiler Flags

### Global flags

```cmake
# CMake
add_compile_options(-Wall -Wextra)
```

```python
# pcons
env.cc.flags.extend(["-Wall", "-Wextra"])
```

### Per-target flags

```cmake
# CMake
target_compile_options(mylib PRIVATE -fvisibility=hidden)
```

```python
# pcons
mylib.private.compile_flags.append("-fvisibility=hidden")
```

### Per-file flags

CMake's `set_source_files_properties` maps to pcons's `env.override()` + `Object()` pattern:

```cmake
# CMake
set_source_files_properties(simd.c PROPERTIES COMPILE_FLAGS "-mavx2")
```

```python
# pcons
with env.override() as simd_env:
    simd_env.cc.flags.append("-mavx2") # could remove, replace or modify here too!
    obj = simd_env.cc.Object(build_dir / "simd.o", "simd.c")[0]
mylib.add_sources([obj])
```

### Platform-specific flags

Replace CMake generator expressions with Python conditionals:

```cmake
# CMake
target_compile_definitions(mylib PRIVATE
    $<$<PLATFORM_ID:Linux>:_GNU_SOURCE>
    $<$<PLATFORM_ID:Windows>:WIN32_LEAN_AND_MEAN>
)
```

```python
# pcons
from pcons import get_platform
plat = get_platform()

if plat.is_posix:
    env.cc.defines.append("_GNU_SOURCE")
if plat.is_windows:
    env.cc.defines.append("WIN32_LEAN_AND_MEAN")
```

### Presets

Pcons has built-in presets for common flag sets:

```python
env.apply_preset("warnings")   # -Wall -Wextra etc.
env.apply_preset("sanitize")   # AddressSanitizer
env.apply_preset("lto")        # Link-time optimization
env.apply_preset("hardened")   # Security hardening flags
```

---

## Output Naming

Like CMake, pcons applies platform-appropriate prefix and suffix automatically. You can override any part.

```cmake
# CMake
set_target_properties(mylib PROPERTIES
    OUTPUT_NAME "fyaml"
    PREFIX ""
    SUFFIX ".plugin"
)
```

```python
# pcons
mylib.output_name = "fyaml"     # base name (platform prefix/suffix still applied)
mylib.output_prefix = ""        # override prefix (e.g., remove "lib" on Linux)
mylib.output_suffix = ".plugin" # override suffix
```

`output_name` is the **base name** — platform naming conventions are applied around it, just like CMake's `OUTPUT_NAME`. For a `SharedLibrary`:

- `output_name = "fyaml"` produces `libfyaml.so` (Linux), `libfyaml.dylib` (macOS), `fyaml.dll` (Windows)
- Adding `output_prefix = ""` produces `fyaml.so`, `fyaml.dylib`, `fyaml.dll`

---

## Installation

```cmake
# CMake
install(TARGETS mylib DESTINATION lib)
install(FILES include/mylib.h DESTINATION include)
install(DIRECTORY assets/ DESTINATION share/myapp)
```

```python
# pcons
project.Install("lib", [mylib])
project.Install("include", [project.node("include/mylib.h")])
project.InstallDir("share/myapp", "assets")
```

Install paths are relative to `build_dir`.

For installing with a rename, use `InstallAs`:

```python
project.InstallAs("lib/plugin.ofx", mylib)
```

### Generating pkg-config files

```python
pc = project.generate_pc_file(mylib, version="1.0.0", description="My library")
project.Install("lib/pkgconfig", [pc])
```

---

## External Dependencies

### find_package

```cmake
# CMake
find_package(ZLIB REQUIRED)
target_link_libraries(app PRIVATE ZLIB::ZLIB)
```

```python
# pcons
zlib = project.find_package("zlib")
app.link(zlib)
```

`find_package()` searches using pkg-config first, then system paths. For optional dependencies:

```python
optional_dep = project.find_package("libfoo", required=False)
if optional_dep:
    app.link(optional_dep)
```

### Conan integration

```python
from pcons.packages.finders import ConanFinder

project.add_package_finder(ConanFinder(config, conanfile="conanfile.txt"))
fmt = project.find_package("fmt")
```

### Manual / header-only packages

```cmake
# CMake
add_library(httplib INTERFACE)
target_include_directories(httplib INTERFACE /opt/include)
```

```python
# pcons
from pcons import ImportedTarget, PackageDescription

httplib = ImportedTarget.from_package(PackageDescription(
    name="cpp-httplib",
    include_dirs=["/opt/include"],
))
```

---

## Custom Commands

```cmake
# CMake
add_custom_command(
    OUTPUT generated.h
    COMMAND python ${CMAKE_SOURCE_DIR}/tools/codegen.py ${CMAKE_CURRENT_SOURCE_DIR}/schema.json -o generated.h
    DEPENDS schema.json tools/codegen.py
)
```

```python
# pcons
env.Command(
    target="generated.h",
    source="schema.json",
    command="python $SRCDIR/tools/codegen.py $SOURCE -o $TARGET",
    depends=["tools/codegen.py"],
)
```

Variable substitution in commands:

| Variable | Description |
|----------|-------------|
| `$SOURCE` | First source file |
| `$SOURCES` | All source files |
| `$TARGET` | First target file |
| `$TARGETS` | All target files |
| `$SRCDIR` | Project source tree root |
| `$$` | Literal `$` |

---

## Patterns That Don't Map Directly

### Generator expressions

CMake generator expressions like `$<BUILD_INTERFACE:...>` or `$<$<CONFIG:Debug>:...>` have no pcons equivalent. Use Python conditionals instead — they're evaluated at configure time, which is equivalent since pcons generates build files per-configuration anyway.

```cmake
# CMake
target_compile_definitions(app PRIVATE $<$<CONFIG:Debug>:DEBUG_MODE>)
```

```python
# pcons
from pcons import get_variant
if get_variant() == "debug":
    app.private.defines.append("DEBUG_MODE")
```

### OBJECT libraries

CMake's `add_library(objs OBJECT src.c)` compiles sources without archiving them. In pcons, compile individual objects with `env.cc.Object()`:

```python
obj = env.cc.Object(build_dir / "special.o", "special.c")[0]
mylib.add_sources([obj])
app.add_sources([obj])
```

### Same source, different flags (SIMD variants)

A common pattern in high-performance C libraries is compiling the same source file multiple times with different SIMD flags. Use `env.override()` to create scoped flag changes:

```python
for variant_name, flags in [("sse2", ["-msse2"]), ("avx2", ["-mavx2"])]:
    with env.override() as v:
        v.cc.flags.extend(flags)
        obj = v.cc.Object(
            build_dir / f"simd_{variant_name}.o",
            "simd_dispatch.c"
        )[0]
    mylib.add_sources([obj])
```

!!! warning "Shared libraries need separate objects"
    If you use pre-compiled objects in both a static and shared library, compile them separately. Shared libraries need `-fPIC` on Linux x86_64, and pcons only adds `-fPIC` automatically for sources compiled as part of a `SharedLibrary` target — not for pre-compiled `Object()` nodes.

    ```python
    # Compile once for static, once for shared (different object dirs)
    static_objs = compile_variants(env, "obj_static")
    shared_objs = compile_variants(env, "obj_shared")
    
    static_lib = project.StaticLibrary("mylib", env, sources=lib_sources)
    static_lib.add_sources(static_objs)
    
    shared_lib = project.SharedLibrary("mylib_shared", env, sources=lib_sources)
    shared_lib.add_sources(shared_objs)
    ```

### FetchContent / ExternalProject

CMake's `FetchContent` downloads and builds dependencies at configure time. Pcons doesn't have a built-in equivalent in the build script. Use `pcons-fetch` (a companion tool) or manage dependencies externally.

---

## Debugging

### Verbose build output

CMake's `cmake --build . --verbose` or `VERBOSE=1 make` is equivalent to:

```bash
pcons build --verbose    # Show full compiler/linker commands
ninja -C build -v        # Or pass -v directly to ninja
```

### Debug tracing

Pcons has per-subsystem debug tracing, enabled with `--debug=SUBSYSTEM`:

```bash
pcons --debug=configure   # Tool detection, feature checks, compiler probes
pcons --debug=resolve     # Target resolution, dependency propagation
pcons --debug=generate    # Build file writing, rule creation, path handling
pcons --debug=subst       # Variable substitution, token expansion
pcons --debug=env         # Environment creation, tool setup
pcons --debug=deps        # Dependency graph, effective requirements
pcons --debug=all         # Everything
pcons --debug=resolve,deps  # Multiple subsystems (comma-separated)
```

You can also set `PCONS_DEBUG=resolve,deps` as an environment variable.

### Configure check debugging

When configure checks produce unexpected results, `--debug=configure` shows the exact commands, compiler output, and cached results:

```bash
pcons -C --debug=configure   # Force reconfigure with debug output
```

Check results are cached in the build directory. Use `-C` (or `--reconfigure`) to force re-running all checks. The cache file is `build/configure_cache.json` — you can inspect it directly.

### Inspecting the build graph

```bash
pcons generate --mermaid=deps.mmd    # Mermaid dependency diagram
pcons generate --graph=deps.dot      # DOT format for Graphviz
```

### The build script is just Python

Unlike CMake, you can debug the build script itself with standard Python tools:

```bash
python -m pdb pcons-build.py         # Step through with debugger
python -c "import pcons; ..."        # Test API interactively
```

Print statements work during generation — they run at configure/generate time, not build time. Your IDE should also give good completion for pcons because all the API functions and classes are typed and documented.

---

## Gotchas and Tips

1. **`link_libs` vs `link_flags`**: Use `link_libs` for `-l` libraries (e.g., `"m"`, `"pthread"`). These are placed **after** objects on the link line, which matters on Linux where the linker resolves symbols left-to-right. `link_flags` are placed **before** objects and are for flags like `-Wl,-rpath`.

2. **Pre-compiled objects and `-fPIC`**: If you compile objects with `env.cc.Object()` and add them to both a static and shared library, compile them separately. Pcons auto-adds `-fPIC` for `SharedLibrary` sources, but not for pre-compiled objects.

3. **`configure_file` with `strict=False`**: CMake templates often have variables you won't define in pcons (e.g., `CMAKE_INSTALL_PREFIX`). Use `strict=False` to silently replace undefined `@VAR@` with empty string and `#cmakedefine` with `/* #undef */`.

4. **`check_flag` handles Clang correctly**: Clang silently accepts unknown `-Wno-*` flags. Pcons's `check_flag()` adds `-Werror` automatically to catch this.

5. **Platform detection**: Use `get_platform()` for host platform info (`is_linux`, `is_macos`, `is_windows`, `arch`, `is_64bit`). For cross-compilation, query the toolchain instead.

6. **`project.generate()` shorthand**: Instead of `Generator().generate(project)`, you can call `project.generate()` directly.

7. **Environment cloning vs override**: Use `env.clone()` for permanent forks (debug vs release). Use `env.override()` for temporary, scoped changes (per-file flags).

8. **Removing flags from a cloned environment**: CMake's per-target flags are additive. In pcons, if you clone an environment and need to *remove* a flag (e.g., `-fno-rtti` for a consumer library that needs RTTI), you modify the list directly on the cloned env using plain python. Plan for this when a project has libraries with different flag requirements.

9. **Transitive dependencies work automatically**: Like CMake, when A links B and B links C, A automatically gets C's public requirements. You don't need to repeat `link()` calls — just link your direct dependencies and public include dirs, defines, and link libs propagate transitively.

10. **Configure-time vs build-time code generation**: If you generate files with plain Python at configure time (e.g., embedding assets into headers), pcons won't track changes to the input files across builds. For inputs that may change, either use `env.Command()` (runs at build time with dependency tracking) or add `target.depends()` on the generated file's inputs so rebuilds are triggered correctly.

11. **Build variables vs CMake cache**: CMake saves `-D` options in `CMakeCache.txt`. Pcons doesn't cache command-line variables — `pcons FOO=bar` sets `FOO` for that run only. Use `os.environ` or `pcons.get_var()` and document which variables your build expects.

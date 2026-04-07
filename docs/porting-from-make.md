# Porting from Make to Pcons

This guide maps common Makefile patterns to their pcons equivalents. It's designed for both humans and AI agents porting existing Make-based projects.

**Key philosophy differences:**

- **Make** tracks file timestamps and runs shell commands via pattern rules. Build logic is expressed in a terse, whitespace-sensitive DSL with implicit rules, automatic variables (`$@`, `$<`, `$^`), and recursive expansion.
- **Pcons** uses plain Python. Conditionals are `if/else`, loops are `for`, and the full Python ecosystem (pathlib, os, regex) is available.
- **Make** executes builds directly. **Pcons** generates Ninja (or Make) files — it never runs compilers itself. This separation means faster incremental builds and better parallelism.

---

## Quick Reference

| Make | pcons |
|------|-------|
| `CC = gcc` | `env = project.Environment(toolchain=find_c_toolchain())` |
| `TARGET = myapp` | `app = project.Program("myapp", env, sources=[...])` |
| `$(TARGET): $(OBJS)` | (automatic — pcons compiles and links from sources) |
| `%.o: %.c` | (automatic — pcons generates compile rules) |
| `CFLAGS += -Wall` | `env.cc.flags.append("-Wall")` |
| `CXXFLAGS += -std=c++17` | `env.cxx.flags.append("-std=c++17")` |
| `LDFLAGS += -L/usr/local/lib` | `env.link.flags.append("-L/usr/local/lib")` |
| `LDLIBS += -lm -lpthread` | `env.link.libs.extend(["m", "pthread"])` |
| `CPPFLAGS += -DFOO` | `env.cc.defines.append("FOO")` |
| `CPPFLAGS += -Iinclude` | `env.cc.include_dirs.append("include")` |
| `install: ...` | `project.Install("bin", [app])` |
| `ar rcs libfoo.a $(OBJS)` | `project.StaticLibrary("foo", env, sources=[...])` |
| `$(CC) -shared -o libfoo.so $(OBJS)` | `project.SharedLibrary("foo", env, sources=[...])` |
| `pkg-config --cflags libfoo` | `foo = project.find_package("libfoo")` |
| `.PHONY: libs` / `libs: libfoo libbar` | `project.Alias("libs", libfoo, libbar)` |
| Custom rule | `env.Command(target, source, cmd)` |

---

## Project Setup

### Make

```makefile
CC = gcc
CXX = g++
CFLAGS = -Wall -O2
CXXFLAGS = -Wall -O2 -std=c++17
LDFLAGS =
LDLIBS = -lm

SRCS = main.c util.c
OBJS = $(SRCS:.c=.o)
TARGET = myapp

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)
```

### Pcons

```python
from pcons import Project, find_c_toolchain, Generator

project = Project("myapp", build_dir="build")
env = project.Environment(toolchain=find_c_toolchain())

env.cc.flags.extend(["-Wall", "-O2"])
env.link.libs.append("m")

app = project.Program("myapp", env, sources=["main.c", "util.c"])

Generator().generate(project)
```

Pcons auto-detects the compiler (respecting `CC`/`CXX` environment variables), generates compile rules for each source, and links automatically. No pattern rules, no `clean` target (Ninja handles that with `ninja -t clean`), no manual object file lists.

### Build directory

Make typically builds in-source (`*.o` next to `*.c`). Pcons defaults to an out-of-source `build/` directory. Run `uvx pcons` then `ninja -C build`.

---

## Targets and Sources

### Programs

```makefile
# Make
SRCS = main.c util.c
OBJS = $(SRCS:.c=.o)
myapp: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)
```

```python
# pcons
app = project.Program("myapp", env, sources=["main.c", "util.c"])
```

### Static Libraries

```makefile
# Make
OBJS = src/lib.o src/util.o
libmylib.a: $(OBJS)
	$(AR) rcs $@ $^
```

```python
# pcons
mylib = project.StaticLibrary("mylib", env, sources=["src/lib.c", "src/util.c"])
```

### Shared Libraries

```makefile
# Make
OBJS = src/lib.o src/util.o
libmylib.so: $(OBJS)
	$(CC) -shared -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -fPIC -c -o $@ $<
```

```python
# pcons
mylib = project.SharedLibrary("mylib", env, sources=["src/lib.c"])
```

Pcons automatically applies `-fPIC` on Linux and handles platform naming conventions:

| Target type | Linux | macOS | Windows |
|------------|-------|-------|---------|
| `StaticLibrary("foo")` | `libfoo.a` | `libfoo.a` | `foo.lib` |
| `SharedLibrary("foo")` | `libfoo.so` | `libfoo.dylib` | `foo.dll` |
| `Program("foo")` | `foo` | `foo` | `foo.exe` |

---

## Variables and Flags

Make uses a flat namespace of variables (`CFLAGS`, `LDFLAGS`, etc.). Pcons uses namespaced tool properties — no collisions, no confusion about what applies where.

### Compiler flags

```makefile
# Make
CFLAGS += -Wall -Wextra -O2
CXXFLAGS += -std=c++17
CPPFLAGS += -DNDEBUG -Iinclude
```

```python
# pcons
env.cc.flags.extend(["-Wall", "-Wextra", "-O2"])
env.cxx.flags.append("-std=c++17")
env.cc.defines.append("NDEBUG")
env.cc.include_dirs.append("include")
```

Note: pcons separates defines and include dirs from raw flags. This lets toolchains apply the correct prefix (`-I` vs `/I`, `-D` vs `/D`) cross-platform.

### Linker flags and libraries

!!! warning "Use `link_libs`, not `link_flags` for `-l` libraries"
    On Linux, link order matters. Libraries specified with `link_libs` are placed **after** object files on the link line (correct for `-l` resolution). `link_flags` are placed **before** objects.

```makefile
# Make
LDFLAGS += -L/usr/local/lib -Wl,-rpath,/usr/local/lib
LDLIBS += -lz -lm
```

```python
# pcons
env.link.flags.extend(["-L/usr/local/lib", "-Wl,-rpath,/usr/local/lib"])
env.link.libs.extend(["z", "m"])  # No -l prefix needed
```

### Per-file flags

Make handles this with target-specific variables:

```makefile
# Make
simd.o: CFLAGS += -mavx2
```

```python
# pcons
with env.override() as simd_env:
    simd_env.cc.flags.append("-mavx2")
    obj = simd_env.cc.Object(build_dir / "simd.o", "simd.c")[0]
mylib.add_sources([obj])
```

### Conditional flags

Replace Make conditionals with Python:

```makefile
# Make
UNAME := $(shell uname)
ifeq ($(UNAME), Linux)
    CFLAGS += -D_GNU_SOURCE
    LDLIBS += -lpthread
endif
ifeq ($(UNAME), Darwin)
    LDFLAGS += -framework CoreFoundation
endif
ifdef DEBUG
    CFLAGS += -g -O0 -DDEBUG
else
    CFLAGS += -O2 -DNDEBUG
endif
```

```python
# pcons
from pcons import get_platform, get_variant

plat = get_platform()
if plat.is_linux:
    env.cc.defines.append("_GNU_SOURCE")
    env.link.libs.append("pthread")
if plat.is_macos:
    env.link.flags.extend(["-framework", "CoreFoundation"])

if get_variant() == "debug":
    env.cc.flags.extend(["-g", "-O0"])
    env.cc.defines.append("DEBUG")
else:
    env.cc.flags.append("-O2")
    env.cc.defines.append("NDEBUG")
```

### Presets

Pcons has built-in presets for common flag sets, replacing boilerplate flag blocks:

```python
env.apply_preset("warnings")   # -Wall -Wextra etc.
env.apply_preset("sanitize")   # AddressSanitizer
env.apply_preset("lto")        # Link-time optimization
env.apply_preset("hardened")   # Security hardening flags
```

---

## Dependencies Between Targets

Make requires manually propagating flags between targets. Pcons uses usage requirements that propagate automatically.

### Linking a library to a program

```makefile
# Make
CFLAGS += -Iinclude
app: app.o libmylib.a
	$(CC) $(LDFLAGS) -o $@ app.o -L. -lmylib $(LDLIBS)
```

```python
# pcons
mylib = project.StaticLibrary("mylib", env, sources=["src/lib.c"])
mylib.public.include_dirs.append("include")

app = project.Program("app", env, sources=["app.c"])
app.link(mylib)  # Automatically gets include dirs, defines, link flags
```

`link()` applies the library's public usage requirements transitively. No need to manually add `-I`, `-L`, or `-l` flags.

### PUBLIC vs PRIVATE

Make has no concept of transitive vs local flags — you manage everything manually. Pcons distinguishes them:

```python
mylib.public.include_dirs.append("include")    # Consumers get this
mylib.private.include_dirs.append("src")        # Only mylib's sources get this
mylib.public.defines.append("USE_FEATURE")      # Consumers get this
mylib.private.defines.append("INTERNAL_FLAG")   # Only mylib gets this
```

When A links B and B links C, A automatically gets C's public requirements — no manual flag forwarding needed.

### Header-only libraries

```makefile
# Make (just flags, no build step)
CFLAGS += -Ivendor/header-lib/include
```

```python
# pcons
headers = project.HeaderOnlyLibrary("header-lib")
headers.public.include_dirs.append("vendor/header-lib/include")
app.link(headers)
```

---

## External Dependencies

### pkg-config

```makefile
# Make
CFLAGS += $(shell pkg-config --cflags libpng zlib)
LDLIBS += $(shell pkg-config --libs libpng zlib)
```

```python
# pcons
png = project.find_package("libpng")
zlib = project.find_package("zlib")
app.link(png)
app.link(zlib)
```

`find_package()` uses pkg-config automatically. For optional dependencies:

```python
optional_dep = project.find_package("libfoo", required=False)
if optional_dep:
    app.link(optional_dep)
```

### Manual library paths

```makefile
# Make
CFLAGS += -I/opt/mylib/include
LDFLAGS += -L/opt/mylib/lib
LDLIBS += -lmylib
```

```python
# pcons
from pcons import ImportedTarget, PackageDescription

mylib = ImportedTarget.from_package(PackageDescription(
    name="mylib",
    include_dirs=["/opt/mylib/include"],
    lib_dirs=["/opt/mylib/lib"],
    libs=["mylib"],
))
app.link(mylib)
```

---

## Custom Commands and Code Generation

### Simple code generation

```makefile
# Make
generated.h: schema.json tools/codegen.py
	python tools/codegen.py schema.json -o $@
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

### Multi-output commands

```makefile
# Make
parser.c parser.h: grammar.y
	bison -d -o parser.c grammar.y
```

```python
# pcons
env.Command(
    target=["parser.c", "parser.h"],
    source="grammar.y",
    command="bison -d -o ${TARGETS[0]} $SOURCE",
)
```

### Variable substitution in commands

| Variable | Description |
|----------|-------------|
| `$SOURCE` | First source file |
| `$SOURCES` | All source files |
| `$TARGET` | First target file |
| `$TARGETS` | All target files |
| `$SRCDIR` | Project source tree root |
| `$$` | Literal `$` |

Compare with Make's automatic variables:

| Make | pcons |
|------|-------|
| `$@` | `$TARGET` |
| `$<` | `$SOURCE` |
| `$^` | `$SOURCES` |
| `$(@D)` | (use Python `pathlib` for path manipulation) |

---

## Configure Checks

Make projects typically use a separate `configure` script (Autoconf) or hand-written shell snippets. Pcons has built-in configure checks.

### Setup

```python
from pcons.configure.config import Configure
from pcons.configure.checks import ToolChecks

config = Configure(build_dir=build_dir)
checks = ToolChecks(config, env, "cc")  # "cc" for C, "cxx" for C++
```

### Header checks

```makefile
# Make (hand-written or Autoconf-generated)
HAVE_ALLOCA_H := $(shell echo '#include <alloca.h>' | $(CC) -E - >/dev/null 2>&1 && echo 1)
```

```python
# pcons
have_alloca_h = checks.check_header("alloca.h").success
```

### Function checks

```python
have_qsort_r = checks.check_function("qsort_r").success
have_mremap = checks.check_function("mremap", headers=["sys/mman.h"]).success
```

### Compiler flag checks

```python
have_wno_unused = checks.check_flag("-Wno-unused-function").success
if have_wno_unused:
    env.cc.flags.append("-Wno-unused-function")
```

### Source compilation checks

```python
have_sse2 = checks.try_compile(
    '#include <emmintrin.h>\nint main() { __m128i x = _mm_setzero_si128(); (void)x; return 0; }',
    extra_flags=["-msse2"],
).success
```

### configure_file()

Generate config headers from templates:

```python
from pcons import configure_file

configure_file(
    "config.h.in",
    build_dir / "config.h",
    {"HAVE_ALLOCA_H": "1" if have_alloca_h else "", "VERSION": "1.2.3"},
)
```

Results are cached — re-running pcons skips checks whose inputs haven't changed. Use `pcons -C` to force reconfiguration.

---

## Output Naming

Pcons applies platform-appropriate prefix and suffix automatically. Override any part:

```python
mylib.output_name = "fyaml"     # base name
mylib.output_prefix = ""        # remove "lib" prefix on Linux
mylib.output_suffix = ".plugin" # custom suffix
```

Compare with Make where you hardcode output names:

```makefile
# Make — must handle platform differences manually
ifeq ($(UNAME), Linux)
    LIB = libfyaml.so
endif
ifeq ($(UNAME), Darwin)
    LIB = libfyaml.dylib
endif
```

---

## Installation

```makefile
# Make
PREFIX ?= /usr/local
install: myapp libmylib.a
	install -m 755 myapp $(DESTDIR)$(PREFIX)/bin/
	install -m 644 libmylib.a $(DESTDIR)$(PREFIX)/lib/
	install -m 644 include/mylib.h $(DESTDIR)$(PREFIX)/include/
```

```python
# pcons
project.Install("bin", [app])
project.Install("lib", [mylib])
project.Install("include", [project.node("include/mylib.h")])
```

Install paths are relative to `build_dir`. For installing with a rename:

```python
project.InstallAs("lib/plugin.ofx", mylib)
```

---

## Recursive Make → Pcons Subdirectories

Recursive Make is a common pattern for multi-directory projects, but it has well-known problems with dependency tracking across directories (see "Recursive Make Considered Harmful").

### Recursive Make

```makefile
# Top-level Makefile
SUBDIRS = lib app

all:
	for dir in $(SUBDIRS); do $(MAKE) -C $$dir; done
```

```makefile
# lib/Makefile
SRCS = lib.c util.c
OBJS = $(SRCS:.c=.o)
libfoo.a: $(OBJS)
	$(AR) rcs $@ $^
```

```makefile
# app/Makefile
CFLAGS += -I../lib
LDFLAGS += -L../lib
LDLIBS += -lfoo
app: main.o ../lib/libfoo.a
	$(CC) $(LDFLAGS) -o $@ main.o $(LDLIBS)
```

### Pcons (single build script, full dependency graph)

```python
# pcons-build.py
from pcons import Project, find_c_toolchain, Generator

project = Project("myproject", build_dir="build")
env = project.Environment(toolchain=find_c_toolchain())

# Library
lib = project.StaticLibrary("foo", env, sources=["lib/lib.c", "lib/util.c"])
lib.public.include_dirs.append("lib")

# Application
app = project.Program("app", env, sources=["app/main.c"])
app.link(lib)

Generator().generate(project)
```

Pcons builds the entire project as a single dependency graph — no recursive invocation, no cross-directory dependency problems, full parallel builds. You can split the pcons script into sub-scripts and invoke them from the top level without introducing any dependency issues.

---

## Patterns That Don't Map Directly

### Implicit rules

Make's implicit rules (`%.o: %.c`) are replaced by pcons's automatic compile rule generation. When you create a `Program` or `Library` with source files, pcons generates the correct compile commands for each source based on its extension and the environment's toolchain. You can create custom builders for any language's source/target mappings.

### .PHONY targets and Alias

Make's `.PHONY` targets serve two purposes: housekeeping commands (`clean`, `test`) and named groupings of real targets. Pcons handles these differently.

**Housekeeping** is handled by the build tool directly:

| Make | pcons equivalent |
|------|-----------------|
| `make clean` | `ninja -t clean` |
| `make all` | `ninja` (builds all targets by default) |
| `make test` | Run tests externally (e.g., `pytest`, `ctest`) |
| `make install` | `ninja install` (if Install targets are defined) |

**Named target groups** use `project.Alias()` — a named shortcut that builds one or more real targets:

```makefile
# Make
.PHONY: libs tests
libs: libfoo.a libbar.a
tests: test_foo test_bar
```

```python
# pcons
project.Alias("libs", libfoo, libbar)
project.Alias("tests", test_foo, test_bar)
```

Then build just that group with `ninja libs` or `ninja tests`.

Aliases can also be built up incrementally — calling `Alias()` with the same name adds to it:

```python
project.Alias("tests", test_foo)
# ... later ...
project.Alias("tests", test_bar)  # adds to existing "tests" alias
```

### $(shell ...) commands

Replace shell command substitution with Python:

```makefile
# Make
GIT_VERSION := $(shell git describe --tags)
CFLAGS += -DVERSION=\"$(GIT_VERSION)\"
```

```python
# pcons
import subprocess
git_version = subprocess.check_output(
    ["git", "describe", "--tags"], text=True
).strip()
env.cc.defines.append(f'VERSION="{git_version}"')
```

### Automatic dependency generation (-MMD)

Make projects often use GCC's `-MMD` flag to generate `.d` dependency files for header tracking:

```makefile
# Make
CFLAGS += -MMD -MP
-include $(OBJS:.o=.d)
```

Pcons handles this automatically. The Ninja generator uses `depfile` and `deps = gcc` — you never need to manage `.d` files yourself.

### VPATH / vpath

Make's `VPATH` searches for sources in multiple directories. In pcons, list full paths to sources:

```makefile
# Make
VPATH = src:lib:vendor
```

```python
# pcons — just list the full paths
sources = ["src/main.c", "lib/util.c", "vendor/helper.c"]
app = project.Program("app", env, sources=sources)
```

Or use Python to collect them:

```python
from pathlib import Path
sources = list(Path("src").glob("*.c")) + list(Path("lib").glob("*.c"))
```

### Computed variable names

Make's computed variable names (`$($(VAR)_FLAGS)`) and double-expansion (`$$`) are replaced by Python's native data structures:

```makefile
# Make
modules = audio video
audio_SRCS = audio.c mixer.c
video_SRCS = video.c render.c
$(foreach m,$(modules),$(eval $(m): $($(m)_SRCS:.c=.o)))
```

```python
# pcons
modules = {
    "audio": ["audio.c", "mixer.c"],
    "video": ["video.c", "render.c"],
}
libs = {}
for name, srcs in modules.items():
    libs[name] = project.StaticLibrary(name, env, sources=srcs)
```

---

## Debugging

### Verbose build output

```bash
# Make
make V=1                         # or VERBOSE=1, depends on the Makefile

# pcons
pcons build --verbose            # Show full compiler/linker commands
ninja -C build -v                # Or pass -v directly to ninja
```

### Debug tracing

```bash
pcons --debug=configure   # Tool detection, feature checks
pcons --debug=resolve     # Target resolution, dependency propagation
pcons --debug=generate    # Build file writing, path handling
pcons --debug=all         # Everything
```

### Inspecting the build graph

```bash
pcons generate --mermaid=deps.mmd    # Mermaid dependency diagram
pcons generate --graph=deps.dot      # DOT format for Graphviz
```

### The build script is just Python

Unlike Make, you can debug the build script with standard Python tools:

```bash
python -m pdb pcons-build.py         # Step through with debugger
python -c "import pcons; ..."        # Test API interactively
```

Print statements work during generation. Your IDE gives completion for pcons because the API is typed and documented.

---

## Gotchas and Tips

1. **`link_libs` vs `link_flags`**: Use `link_libs` for `-l` libraries (e.g., `"m"`, `"pthread"`). These are placed **after** objects on the link line, which matters on Linux. `link_flags` are for flags like `-Wl,-rpath`.

2. **No manual object lists**: Don't replicate Make's `OBJS = $(SRCS:.c=.o)` pattern. Pcons compiles sources automatically — just pass source files to `Program()` or `Library()`.

3. **Pre-compiled objects and `-fPIC`**: If you compile objects with `env.cc.Object()` and add them to both a static and shared library, compile them separately. Pcons auto-adds `-fPIC` for `SharedLibrary` sources, but not for pre-compiled objects.

4. **Platform detection**: Use `get_platform()` for host platform info (`is_linux`, `is_macos`, `is_windows`, `arch`, `is_64bit`). No more `$(shell uname)` parsing.

5. **Environment cloning vs override**: Use `env.clone()` for permanent forks (debug vs release). Use `env.override()` for temporary, scoped changes (per-file flags).

6. **Automatic header dependency tracking**: Pcons handles `-MMD` style dependency tracking automatically via Ninja's `depfile` mechanism. Don't add `-MMD` or `-MP` to your flags.

7. **Build variables**: `pcons FOO=bar` sets `FOO` for that run only (accessible via `pcons.get_var("FOO")`). Unlike Make's `?=` conditional assignment, there's no persistence — document which variables your build expects.

8. **Source globbing**: Make's `$(wildcard src/*.c)` maps to Python's `Path("src").glob("*.c")`. However, explicit source lists are generally preferred — they catch missing files immediately rather than silently building whatever happens to be on disk.

9. **Transitive dependencies work automatically**: When A links B and B links C, A automatically gets C's public requirements. No manual flag forwarding needed — a huge improvement over Make's flat variable model.

10. **Cross-platform by default**: Unlike Make, which requires platform-specific conditionals for output names, compiler flags, and tool paths, pcons handles platform differences in the toolchain layer. Your build script stays clean.

# Comparison with other software build tools

I started PCons because I love the philosophy of SCons: use python as the build language, with good tool support, variable substitution, and sensible control structures. But modern python has outstripped SCons's architecture years ago, and CMake has become the dominant build tool, because of things like transitive depencencies, conan integration, and wide adoption. But cmake uses a custom DSL (one which I don't like, and I've heard that from others). 
But what about the other popular modern build tools like Bazel and Meson?

# pcons vs Bazel vs Meson: Detailed Comparison

## Configuration Language

**pcons** uses plain Python 3.11+ as its build description language. There's no custom DSL — you write a `pcons-build.py` file that imports from `pcons` and calls builders directly. This means full IDE support, debuggability, and the entire Python ecosystem at your fingertips.

**Bazel** uses Starlark, a restricted, deterministic subset of Python. It's intentionally limited (no I/O, no side effects) to enable hermetic builds and remote caching. The learning curve is moderate; while it looks like Python, the semantics differ significantly (e.g., no `import`, no mutable globals).

**Meson** uses its own custom DSL, a statically-typed language that looks vaguely like Python but isn't. It's easy to learn for simple cases but can feel limiting when you need more expressive logic. Meson explicitly discourages arbitrary computation in build files. Pcons embraces it.

---

## Design Philosophy

**pcons** strictly separates *configuration* (what to build, described in Python) from *execution* (Ninja/Make does the actual building). The core is entirely tool-agnostic — it knows nothing about compilers, linkers, or languages. All tool-specific knowledge lives in pluggable toolchains. This means pcons could equally well drive document preparation, game asset pipelines, or scientific dataflows. It supports ccache and sccache for caching.

**Bazel** is built around *hermeticity* and *reproducibility*. Every action is sandboxed with explicit inputs/outputs. This enables remote build execution and aggressive caching across machines and CI. The trade-off is significant complexity and opinionated structure (required `WORKSPACE`, `BUILD` files, labels like `//path/to:target`).

**Meson** optimizes for *developer ergonomics* for C/C++ and other compiled languages. It's opinionated — it knows about compilers, flags, pkg-config, and wrap dependencies out of the box. The philosophy is "sensible defaults, don't make the user think about build system internals." Pcons has some of that, but doesn't go overboard, because it's usually easier and more transparent to just add some compiler flags directly.

---

## Build Execution

**pcons** generates Ninja (or Makefile/Xcode) files. The user (or pcons) then runs `ninja` directly. pcons is never involved in the actual build execution. This is a clean separation but means pcons has no visibility into build progress or failures at build time. It can set up builds for ccache or sccache.

**Bazel** has its own build execution engine. It doesn't generate Ninja files — it runs actions itself (or delegates to remote execution). This enables features like remote caching, remote execution (RBE), distributed builds, and fine-grained incremental builds with action-level caching.

**Meson** also generates Ninja files (or VS project files, Xcode, etc.) and defers execution to the backend. Similar to pcons in this respect.

---

## Language & Toolchain Support

**pcons** is C/C++-focused in practice, with GCC, Clang/LLVM, MSVC, and Clang-CL supported. Specialized toolchains for CUDA, Cython, WebAssembly (WASI + Emscripten), Metal shaders, and Windows RC files. The architecture is fully extensible via a toolchain registry, so adding new languages is a first-class operation. Users can also easily add tools, toolchains and builders. Polyglot projects *should* work well and are a design goal, but it is still early days.

**Bazel** has comprehensive, community-maintained rules for virtually every language: `rules_cc`, `rules_java`, `rules_go`, `rules_python`, `rules_rust`, `rules_proto`, `rules_nodejs`, etc. The monorepo model also means polyglot projects (mixing Java + Go + Python in one repo) work well.

**Meson** has strong built-in support for C, C++, Fortran, D, Rust, Java, C#, Vala, and others. Language support is first-class and well-tested, especially for C/C++. Less extensible than Bazel for new languages.

---

## Package / Dependency Management

**pcons** integrates with external package managers (pkg-config, Conan, vcpkg) but doesn't manage downloads itself at configure time. The `pcons-fetch` tool can build and install dependencies from source, wrapping CMake/autotools/Meson projects. Dependencies are represented as `ImportedTarget` objects with full usage-requirement propagation.

**Bazel** has `MODULE.bazel` (Bzlmod) for declaring external dependencies, with a registry model. It can download and build external deps hermetically. First-class support for Go modules, Maven, npm, etc. Remote repositories are a core Bazel concept.

**Meson** has the **WrapDB** system — a curated database of `.wrap` files that define how to download and build dependencies from source. Very easy for common packages. Also integrates with pkg-config and cmake-style config files. Subprojects allow vendoring deps directly.

---

## Incremental Build Quality

**pcons** relies entirely on Ninja for incrementality. Ninja's depfile mechanism handles header dependency tracking correctly. Build correctness is high for standard C/C++ patterns. No action-level caching beyond what the filesystem provides.

**Bazel** has the most sophisticated incremental build system of the three. Content-addressed action cache, remote cache sharing across CI and developer machines, and correct hermetic sandboxing mean stale builds are virtually impossible.

**Meson** relies on Ninja (primary) for incrementality. Header tracking via depfiles works well. Similar quality to pcons's Ninja output. Meson currently has better integration with Ninja's restat feature for minimizing rebuilds.

---

## Cross-Compilation

**pcons** supports cross-compilation via toolchain presets (Android NDK, iOS, WebAssembly, Linux cross) and `env.set_target_arch()`. It's relatively straightforward because the environment model cleanly separates host vs. target concerns.

**Bazel** has a powerful but complex platform/transition model (`--platforms`, `--cpu`, `--crosstool_top`). Getting cross-compilation right in Bazel requires deep knowledge of toolchain definitions and platform constraints. Very powerful once configured.

**Meson** uses machine files (e.g., `cross-file.ini`) that declare cross-compilation properties. Well-documented and practical for common cases like ARM Linux or Windows cross-compiles.

---

## IDE Integration

**pcons** auto-generates `compile_commands.json` alongside every build, enabling clangd, CLion, VS Code C++ extension, etc. Good IDE support for the generated artifacts.

**Bazel** has IDE integration via separate tools: `hedron_compile_commands` for `compile_commands.json`, Bazel plugins for VS Code and IntelliJ. Can be cumbersome to set up.

**Meson** generates `compile_commands.json` natively, with excellent IDE support. VS Code has a dedicated Meson extension.

---

## Scalability

**pcons** has not been tested on very large codebases. The lazy node resolution and Python-based configuration should scale reasonably for medium projects, but there's no known production usage at Google/Meta scale.

**Bazel** was designed at Google for monorepos with millions of lines of code and thousands of engineers. Incremental analysis, parallel action execution, and remote build execution make it viable at enormous scale. Significant overhead for small projects.

**Meson** scales well for large projects (used by GNOME, GStreamer, systemd, Mesa, Wine). Faster configure times than CMake at scale. Doesn't target Bazel-scale monorepos.

---

## Hermetic / Reproducible Builds

**pcons** makes no specific hermeticity guarantees. Build scripts can read files, call network APIs, or do arbitrary computation. Reproducibility depends on developer discipline; because it's all python, dependencies can be locked and queried. It does support 

**Bazel** enforces hermeticity via sandboxing. Every action sees only its declared inputs. This is the core feature that enables remote caching and reproducible builds across machines.

**Meson** doesn't enforce hermeticity but discourages side effects in build files by limiting the DSL's expressiveness.

---

## Installation & Setup

**pcons** can be run with `uvx pcons` — zero installation required beyond `uv` (Python's modern package runner). No daemon, no workspace lock files, no registry setup. It'll run ninja via uv as well.

**Bazel** requires installing the Bazel binary (or Bazelisk launcher). Projects need a `WORKSPACE` or `MODULE.bazel` file. Many projects require a Bazel version manager. First-time setup is non-trivial.

**Meson** installs easily via pip, brew, apt, etc. Requires Ninja separately. Setup is straightforward.

---

## Windows Support

**pcons** has first-class Windows support: MSVC toolchain, Clang-CL, Windows RC compiler, MSIX/AppX installer generation, SxS manifests. First-class support for `msvcup` as well (minimal VC and SDK install). CI tests run on Windows.

**Bazel** supports Windows but it's historically been the weakest platform. Improving rapidly but still has rough edges (path length limits, MSVC integration complexity).

**Meson** has excellent Windows support with MSVC, MinGW, and Clang-CL. One of its strong points vs. CMake.

---

## Configure-Time Feature Detection

C/C++ projects commonly need to probe the build environment at configure time — checking for headers, testing compiler capabilities, detecting platform features, and generating config headers. This is the `autoconf`/`cmake -DHAVE_FOO` territory.

### pcons

pcons has a `Configure` + `ToolChecks` API that covers the full range: `check_header()`, `check_flag()`, `check_function()`, `check_type()`, `try_compile()`, and `configure_file()` for template-based config header generation (CMake-style `#cmakedefine` and `@VAR@` substitution). Results are cached across runs.

Because it's plain Python, configure-time logic reads naturally — no special DSL syntax for conditionals, no magic variables. Here's a real excerpt from a pcons build of OpenEXR (a large, complex C++ library):

```python
config = Configure(build_dir=BUILD_DIR)
cc_checks = ToolChecks(config, env, "cc")
cxx_checks = ToolChecks(config, env, "cxx")

has_ucontext = cc_checks.check_header("ucontext.h").success

has_control_register = False
if has_ucontext:
    has_control_register = cc_checks.try_compile(
        "#include <ucontext.h>\nint main() { struct _libc_fpstate s; (void)s.mxcsr; return 0; }",
    ).success

has_gcc_inline_asm_avx = cxx_checks.try_compile("""
#if defined(__SSE2__)
int main() {
    int n = 0, eax = 0, edx = 0;
    __asm__("xgetbv; vzeroupper" : "=a"(eax), "=d"(edx) : "c"(n) : );
    return 0;
}
#else
#error No SSE support
#endif
""").success

# ARM64: check for vld1q_f32_x2 intrinsic (missing on some toolchains)
has_arm_vld1 = True
if IS_ARM64:
    has_arm_vld1 = cc_checks.try_compile(
        "#include <arm_neon.h>\nint main() { float a[] = {1.0, 1.0}; vld1q_f32_x2(a); return 0; }"
    ).success

# Optional Python bindings — just a try/except, no special syntax
try:
    python3 = project.find_package("python3-embed")
    pybind11 = project.find_package("pybind11")
    # ... build PyOpenEXR shared library
except Exception:
    pass  # silently skip if not available

# Generate config headers from CMake .in templates
configure_file("cmake/OpenEXRConfig.h.in", CONFIG_DIR / "OpenEXRConfig.h", config_vars)
```

Notice: version numbers are extracted from existing headers using Python regex. Platform detection is `sys.platform` and `os.uname()`. ARM64-specific source exclusions are a plain list comprehension. Optional components are `try/except`. All of this is just Python — no new concepts to learn.

### Meson

Meson has the best-in-class configure API of the traditional build tools. The `compiler` object provides `has_header()`, `compiles()`, `has_function()`, `has_type()`, `sizeof()`, and `find_library()`. `configure_file()` supports both substitution and `#mesondefine` (analogous to `#cmakedefine`).

```meson
cc = meson.get_compiler('c')
cxx = meson.get_compiler('cpp')

has_ucontext = cc.has_header('ucontext.h')

has_control_register = false
if has_ucontext
  has_control_register = cc.compiles('''
    #include <ucontext.h>
    int main() { struct _libc_fpstate s; (void)s.mxcsr; return 0; }
  ''', name: 'control register support')
endif

conf = configuration_data()
conf.set('HAVE_UCONTEXT_H', has_ucontext)
conf.set('IEX_HAVE_CONTROL_REGISTER_SUPPORT', has_control_register)
configure_file(input: 'IexConfig.h.in', output: 'IexConfig.h', configuration: conf)
```

This is clean and readable. Meson's configure API is mature and well-documented, and it handles caching automatically. The main limitations:
- **Optional components** require `dependency(..., required: false)` and conditional blocks — more verbose than `try/except`
- **Arbitrary logic** (e.g., extracting a version number from a header by regex) requires `run_command()` which is cumbersome and has limited error handling
- **Dynamic source lists** (e.g., filtering SIMD files based on architecture) need `foreach` loops and `files()` — workable but less natural than a Python list comprehension

### Bazel

Bazel has no built-in equivalent to `check_header` or `try_compile`. The philosophy is that the build graph must be fully deterministic and declared upfront — probing the environment at configure time is antithetical to hermeticity.

The idiomatic Bazel approach is:
- Use `platform` constraints and `select()` to choose between pre-declared variants
- Use `repository_rule` in Starlark to run arbitrary commands during the workspace loading phase (this is where configure-like probing happens)
- Use `cc_library` with `defines` driven by `select()` over platform/cpu conditions

```python
# In a repository_rule (Starlark, runs at workspace load time)
def _detect_features_impl(ctx):
    result = ctx.execute(["gcc", "-x", "c", "-", "-o", "/dev/null"],
                         input="#include <ucontext.h>\nint main(){}")
    ctx.file("features.bzl",
             "HAVE_UCONTEXT = %s" % (result.return_code == 0))

# In BUILD files — static selection, not dynamic probing
cc_library(
    name = "iex",
    defines = select({
        "//conditions:linux": ["HAVE_UCONTEXT_H"],
        "//conditions:default": [],
    }),
)
```

This works but is significantly more complex — `repository_rule`s are an advanced Starlark concept, and the results feed back into `select()` expressions scattered across BUILD files. There's no equivalent to a config header; you use `defines` instead. For projects that already have CMake-style `.h.in` templates (like OpenEXR), you'd need a custom genrule to process them.

### Summary

| Capability | pcons | Meson | Bazel |
|---|---|---|---|
| `check_header()` | Yes | Yes (`cc.has_header()`) | Via `repository_rule` |
| `try_compile()` | Yes | Yes (`cc.compiles()`) | Via `repository_rule` |
| `check_function()` | Yes | Yes (`cc.has_function()`) | Via `repository_rule` |
| `configure_file()` | Yes (`#cmakedefine`, `@VAR@`) | Yes (`#mesondefine`, `@VAR@`) | Custom `genrule` |
| Optional components | `try/except` | `required: false` + if block | `select()` + platform constraints |
| Arbitrary logic at configure time | Full Python | `run_command()` (limited) | `repository_rule` (complex) |
| Version extraction from files | Python regex, pathlib | `run_command()` + parsing | Custom Starlark |
| Result caching | Yes (JSON cache) | Yes (built-in) | Yes (action cache) |
| `--debug=configure` tracing | Yes (full trace + artifacts) | Verbose mode | Limited |

pcons and Meson are comparable in capability for standard configure checks. pcons's edge is that unusual cases — extracting versions from headers, filtering source lists by architecture, optional features — are plain Python with no special API needed. Meson handles the common cases very well but reaches its DSL limits on the less common ones. Bazel treats configure-time probing as a second-class citizen, which is a real friction point when porting projects that have significant autoconf/CMake configure logic.

---

## Build Description Expressiveness & Simplicity

One of pcons's practical advantages is how little code it takes to describe a non-trivial build. Consider a project with two static libraries (`libmath`, `libphysics`) and an executable that links both, with transitive include propagation and debug/release variants.

### pcons

```python
from pcons import Generator, Project, find_c_toolchain

project = Project("sim", build_dir="build")
base_env = project.Environment(toolchain=find_c_toolchain())

libmath = project.StaticLibrary("math", base_env, sources=["src/math.c"])
libmath.public.include_dirs.append("include")

libphysics = project.StaticLibrary("physics", base_env, sources=["src/physics.c"])
libphysics.link(libmath)  # gets libmath's public includes transitively

for variant in ["debug", "release"]:
    env = base_env.clone()
    env.set_variant(variant)
    prog = project.Program(f"sim_{variant}", env, sources=["src/main.c"])
    prog.link(libphysics)

Generator().generate(project)
```

This is **~15 lines of real Python**. Transitive include propagation is implicit via `.link()`. Variants are a plain `for` loop — no special syntax. The entire Python ecosystem (pathlib, os, subprocess, conditionals, comprehensions) is available without restriction.

### Bazel

```python
# BUILD file
cc_library(
    name = "math",
    srcs = ["src/math.c"],
    hdrs = glob(["include/**"]),
    includes = ["include"],
)

cc_library(
    name = "physics",
    srcs = ["src/physics.c"],
    deps = [":math"],
)

cc_binary(
    name = "sim",
    srcs = ["src/main.c"],
    deps = [":physics"],
)
```

Bazel's BUILD syntax is clean and readable for a single configuration. However:
- **No built-in variant loop** — debug/release variants require `select()` expressions or separate targets, and defining custom build configs involves `config_setting()` rules and command-line flags (`--compilation_mode=dbg`).
- **Starlark's restrictions** are invisible here but bite you the moment you need conditional logic, file globbing across directories, or computed target names.
- **Multi-directory projects** require a `BUILD` file in each directory or careful use of `glob()`.
- **Workspace setup** (`MODULE.bazel` or `WORKSPACE`, toolchain registration) adds boilerplate before you write a single target.

### Meson

```meson
project('sim', 'c')

libmath = static_library('math', 'src/math.c',
  include_directories: include_directories('include'))

libphysics = static_library('physics', 'src/physics.c',
  link_with: libmath)

foreach variant : ['debug', 'release']
  executable('sim_' + variant, 'src/main.c',
    link_with: libphysics,
    # variants require separate subdir() calls or build options,
    # not a simple loop with different flags per iteration
  )
endforeach
```

Meson is concise for the basic case and readable. However:
- **The `foreach` loop above is misleading** — Meson doesn't support applying different compiler flags per loop iteration in a natural way. Debug/release is handled via `buildtype` option (`meson setup --buildtype=release`), which is a global switch, not per-target.
- **No access to host environment** — you can't call `os.path`, read a file, or run an arbitrary subprocess during configuration without using Meson's limited `run_command()`.
- **Custom logic quickly hits DSL walls** — anything beyond standard patterns requires `meson.build` gymnastics or falls back to Python `meson.build` scripts (rare and poorly supported).

### Summary

| Aspect                             | pcons                    | Bazel                         | Meson                              |
|------------------------------------|--------------------------|-------------------------------|------------------------------------|
| Lines for libs + exe example       | ~15                      | ~20 (+ workspace boilerplate) | ~15                                |
| Variant builds                     | Plain `for` loop         | `select()` + config settings  | Global `--buildtype` only          |
| Conditional logic                  | Full Python              | Starlark (restricted)         | Limited DSL constructs             |
| File/path manipulation             | `pathlib`, `os`          | `glob()` only                 | `files()`, `include_directories()` |
| Escape hatch when DSL isn't enough | Not needed (it's Python) | Custom rules in Starlark      | `run_command()` (limited)          |
| Debuggability                      | Full Python debugger     | `bazel query`, limited        | Print statements only              |

pcons and Meson are comparable in line count for simple cases. pcons pulls ahead as complexity grows — variants, computed paths, platform-specific logic, and anything non-standard are just Python, with no DSL boundary to fight.

---

## Summary Table

| Feature                 | pcons                                             | Bazel                                  | Meson                              |
|-------------------------|---------------------------------------------------|----------------------------------------|------------------------------------|
| **Config language**     | Python 3.11+                                      | Starlark (subset of Python)            | Custom DSL                         |
| **Execution model**     | Generates Ninja/Make/Xcode                        | Executes directly (or RBE)             | Generates Ninja/VS/Xcode           |
| **Primary use case**    | C/C++, CUDA, Wasm (extensible)                    | Polyglot monorepos                     | C/C++ and many compiled languages  |
| **Hermeticity**         | None                                              | Strict (sandboxed)                     | None (DSL-limited)                 |
| **Remote caching**      | No                                                | Yes (built-in)                         | No                                 |
| **Incremental builds**  | Via Ninja                                         | Content-addressed cache                | Via Ninja                          |
| **Cross-compilation**   | Presets + env model                               | Platform/transition model              | Machine files                      |
| **Package mgmt**        | pkg-config, Conan, vcpkg, pcons-fetch             | Bzlmod, Maven, npm, etc.               | WrapDB, pkg-config, subprojects    |
| **IDE support**         | compile_commands.json                             | Via external tools                     | compile_commands.json native       |
| **Windows support**     | Excellent (MSVC, Clang-CL, MSIX)                  | Fair (improving)                       | Excellent                          |
| **Scalability**         | Small-medium projects                             | Massive monorepos                      | Large projects                     |
| **Setup overhead**      | Very low (`uvx pcons`)                            | High (Bazel/Bazelisk, workspace setup) | Low (pip/brew + ninja)             |
| **Extensibility**       | Plugin registry (toolchains, generators, modules) | Custom rules in Starlark               | Limited (constrained DSL)          |
| **Reproducibility**     | Developer discipline                              | Enforced by sandbox                    | Developer discipline               |
| **Graph output**        | Mermaid, DOT built-in                             | `bazel query`                          | None built-in                      |
| **Platform installers** | .pkg, .dmg, MSIX built-in                         | External rules needed                  | External tools needed              |
| **Learning curve**      | Low (plain Python)                                | High (Starlark + concepts)             | Low-Medium (custom DSL)            |
| **Production maturity** | Early (v0.8.x, active dev)                        | Very mature (Google-backed)            | Mature (used by GNOME, Mesa, etc.) |
| **Wasm support**        | WASI + Emscripten built-in                        | Via custom rules                       | Limited                            |
| **Compiler caching**    | ccache/sccache wrapper                            | Remote cache built-in                  | ccache integration                 |

---

## Bottom Line

Pcons occupies a niche between Meson's ergonomics and Bazel's power. It offers the expressiveness of real Python (vs. Starlark/custom DSL), an architecturally clean tool-agnostic core, and excellent platform coverage — while remaining lightweight and zero-install. The trade-offs are no hermeticity guarantees, no remote build execution, and early-stage maturity compared to either alternative.

# API Reference <small>v{{ version }}</small>

The pcons API at a glance. This page is for looking a call up; for what each one
is *for*, and how they fit together, see the [User Guide](user-guide.md).

## Project Methods

Every builder — `Program`, `StaticLibrary`, `Install`, `Command`, `Test`, the
installers, and the rest — is a method on `Project`. They are listed in the
User Guide's [builder table](user-guide.md#builder-types), which is generated
from the builder registry, so it is always complete. The rest of the `Project`
API:

| Method | Description |
|--------|-------------|
| `Project(name, build_dir)` | Create a project |
| `project.Environment(toolchain)` | Create an environment |
| `project.Default(*targets)` | Set default build targets |
| `project.Alias(name, *targets)` | Create a named alias |
| `project.resolve()` | Resolve all dependencies |
| `project.node(path)` | Get/create a file node |
| `project.find_package(name, ...)` | Find external package (returns ImportedTarget) |
| `project.find_package(name, system=True)` | Same, with the package's headers as system headers (`-isystem`) |
| `project.find_package(name, env=env)` | Same, searching and caching for that environment only |
| `project.add_package_finder(finder)` | Prepend a custom package finder |
| `project.add_package_finder(finder, env=env)` | Same, used in that environment only |
| `project.add_subdirectory(subdir, pick=None)` | Run a subdirectory's `pcons-build.py` as part of this project (also available as a bare `add_subdirectory()`) |
| `project.add_configure_dependency(path)` | Declare a file the build description read, so editing it re-runs pcons |
| `project.generated_input(path)` | A build-time-generated file to read: the path once it exists, else `None` |
| `project.when_generated(*paths)` | Decorator: run a block only once every named file has been generated |
| `project.cli_command(name=None)` | Declare a command reachable as `pcons run <name>` |
| `project.cli_group(name=None)` | Declare a group reachable as `pcons run <name> <verb>` |
| `project.generate_pc_file(target, version=, description=)` | Generate a pkg-config `.pc` file for a library target |

## Target Methods

| Method | Description |
|--------|-------------|
| `target.add_source(path)` | Add a source file |
| `target.add_sources(paths)` | Add multiple source files |
| `target.add_sources(paths, env=e)` | Compile those sources with a different environment; on a source the target already has, sets its environment in place |
| `target.set_option(key, value)` | Set a builder/toolchain option (e.g. `install_name`) |
| `target.link(t, "m")` | Link a dependency (or raw lib name) and re-export it to consumers |
| `target.link_private(t, "m")` | Link a dependency (or raw lib name), keeping it local |
| `target.depends(*items)` | Build targets or have files first, without linking them |
| `target.pre_build(command)` | Shell command to run before this target is built |
| `target.post_build(command)` | Shell command to run after this target is built |
| `target.get_option(key, default=None)` | Read an option set with `set_option()` |
| `target.public.include_dirs` | Include dirs for consumers |
| `target.public.system_include_dirs` | Like `include_dirs`, but as system headers (warnings suppressed) |
| `target.public.make_includes_system()` | Move every include dir to `system_include_dirs`, in place |
| `target.public.link_libs.append(t)` | Low-level form of `link()` (append a `Target` or `-l` name) |
| `target.private.link_libs.append(t)` | Low-level form of `link_private()` |
| `target.public.link_libs` | Libraries to link (`-l`; placed after objects) |
| `target.public.link_flags` | Linker flags (placed before objects; use `link_libs` for `-l` libraries). Use `PathToken` for flags containing paths. |
| `target.public.defines` | Defines for consumers |
| `target.public.link_dirs` | Library search directories (`-L`) |
| `target.public.frameworks` / `framework_dirs` | macOS frameworks (`-framework` / `-F`) |
| `target.private.compile_flags` | Flags for this target only |

These are the names pcons reads. Any other name raises — the lists are consumed by name, so a typo like `lib_dirs` would otherwise be stored and never looked at, and the build would fail somewhere else entirely (`ld: library 'Foo' not found`, naming the library rather than the mistake). A toolchain or extension that consumes a name of its own declares it with `pcons.core.target.register_usage_requirement()`.

The same rule applies to the other named surfaces: `set_option()` takes only options a builder or toolchain declared with `register_target_option()`, `env.<tool>.<var> = ...` only assigns variables the tool declared (use `env.<tool>.set(name, value)` to introduce one), and adding a source a target already has raises unless `env=` is given. In each case the alternative is a value nothing reads.

## Environment Methods

| Method | Description |
|--------|-------------|
| `env.set_variant(name)` | Set debug/release variant |
| `env.set_target_arch(arch)` | Set target CPU architecture |
| `env.apply_preset(name)` | Apply flag preset (warnings, werror, sanitize, profile, lto, hardened) |
| `env.apply_cross_preset(preset)` | Apply cross-compilation preset |
| `env.explain(tool=None)` | Attribute each flag/define/command to the preset that set it |
| `env.use_compiler_cache(tool=None)` | Wrap compilers with ccache/sccache |
| `env.use_clang_tidy(tool=None, args=())` | Run clang-tidy alongside every C/C++ compile |
| `env.use(package)` | Apply package settings |
| `env.clone()` | Create a copy |
| `env.override(**kwargs)` | Context manager for temporary overrides |
| `env.add_toolchain(toolchain)` | Add additional toolchain (e.g., CUDA) |
| `env.toolchain` | The primary toolchain this environment was created with |
| `env.Command(target, source, cmd)` | Run arbitrary shell command |
| `env.Framework(*names)` | Link macOS frameworks (macOS only) |
| `env.Glob(pattern)` | Find files matching a glob pattern |
| `env.cc` | C compiler settings |
| `env.cxx` | C++ compiler settings |
| `env.link` | Linker settings |

## Helper Functions

| Function | Description |
|----------|-------------|
| `find_c_toolchain()` | Find an available C/C++ toolchain (platform-aware defaults) |
| `find_c_toolchain(prefer=[...])` | Find toolchain with explicit preference order |
| `find_cuda_toolchain()` | Find CUDA toolchain (returns `None` if nvcc not found) |
| `configure_file(template, output, vars)` | Substitute variables in a template file (CMake or @VAR@ style) |
| `get_var(name, default, type=None)` | Get a build variable, converted to the default's type (or `type=`): bool, int, float, str, Path |
| `get_variant(default)` | Get the build variant |
| `ensure_msvc(msvc_ver, sdk_ver)` | Install MSVC toolchain via msvcup (Windows only; import from `pcons.contrib.windows.msvcup`) |

## Generators

| Class | Description |
|-------|-------------|
| `Generator` | Generate build files using default generator (specified by cmdline, env, or default: Ninja) |
| `NinjaGenerator` | Generate Ninja build files |
| `MakefileGenerator` | Generate traditional Makefiles |
| `CompileCommandsGenerator` | Generate compile_commands.json for IDEs |
| `MermaidGenerator` | Generate Mermaid dependency diagrams |

## Configuration and Feature Detection

| Class/Method | Description |
|--------------|-------------|
| `Configure(build_dir)` | Create configuration context |
| `config.define(name, value=1)` | Define a preprocessor symbol |
| `config.undefine(name)` | Mark a symbol as undefined |
| `config.check_sizeof(type, env=env)` | Get the size of a type via the target compiler and define `SIZEOF_*` |
| `config.write_config_header(path)` | Generate a config.h file |
| `ToolChecks(config, env, tool)` | Create feature checker for a tool |
| `checks.check_flag(flag)` | Check if compiler accepts a flag |
| `checks.check_header(name)` | Check if a header exists |
| `checks.check_type(name, headers=[])` | Check if a type exists |
| `checks.check_type_size(name)` | Get the size of a type |
| `checks.check_function(name)` | Check if a function is available |
| `checks.check_define(name, headers=[])` | Read a macro's value, from the compiler or a header |
| `checks.check_defines(names, headers=[])` | Read several macros in one preprocessor run |
| `checks.try_compile(source)` | Try to compile arbitrary source code |

## macOS Utilities

| Function | Description |
|----------|-------------|
| `create_universal_binary(project, name, inputs, output)` | Combine arch-specific binaries into universal binary (returns Target) |
| `get_dylib_install_name(path)` | Get a dylib's install name |
| `fix_dylib_references(target, dylibs, lib_dir)` | Fix dylib references for bundle creation |

Import from `pcons.util.macos`.

---

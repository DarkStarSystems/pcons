# Changelog

All notable changes to pcons will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.14.0] - 2026-04-14

### Added

- **`PathToken` exported from top-level `pcons` package**: Allows embedding paths inside arbitrary flags (e.g., `-Wl,-force_load,<path>`) with proper generator-relative path handling. See new example `33_path_in_flags`.

### Fixed

- **`PathToken.relativize()` now respects `path_type`**: Build-relative and absolute paths are no longer incorrectly transformed by the generator's relativizer (e.g., `path_type="build"` no longer gets a `$topdir/` prefix in Ninja output).

- **Multi-component `build_dir` paths (e.g., `build/release`) now work correctly**: The Ninja generator was only stripping the last component of the build directory prefix from output paths, causing double-nested paths like `build/release/build/release/libfoo.a`. Fixed in the Ninja generator, project node resolution, and path normalization warnings.

## [0.13.0] - 2026-04-07

### Added

- **`output_prefix` and `output_suffix` on targets**: Override platform-default prefix/suffix independently (e.g., `target.output_prefix = ""` to remove the `lib` prefix on Linux). Works alongside `output_name` which sets the base name.

- **`get_output_prefix()` / `get_output_suffix()` toolchain methods**: Toolchains can now override platform naming conventions per target type. Used by Emscripten (`.js` programs) and WASI (`.wasm` programs) toolchains.

- **`get_platform()` exported from top-level `pcons` package**: No longer need to import from `pcons.configure.platform`.

- **CMake-to-pcons porting guide**: New `docs/porting-from-cmake.md` with side-by-side command reference, detailed pattern mappings, debugging tips, and common gotchas.

### Changed

- **BREAKING: `output_name` is now a base name, not a raw filename**: `target.output_name = "foo"` produces `libfoo.so` (not `foo`), matching CMake's `OUTPUT_NAME` semantics. Platform prefix and suffix are always applied. Use `output_prefix`/`output_suffix` to override them.

### Improved

- **`check_flag()` now adds `-Werror` / `/WX` automatically**: Compiler flag checks no longer silently pass for unknown flags on Clang (which accepts unknown `-Wno-*` flags without error by default). Detects MSVC/clang-cl toolchains and uses `/WX` instead.

- **`link_libs` vs `link_flags` documentation**: User guide and architecture docs now clearly explain that `link_libs` is for `-l` libraries (placed after objects) and `link_flags` is for other linker flags (placed before objects).

## [0.12.1] - 2026-04-07

### Added

- **LaTeX contrib toolchain**: New `latexmk`-based toolchain for building LaTeX documents, with documentation and CI testing.

- **User error experience test suite**: 48 tests covering common user mistakes, ensuring clear error messages for misuse.

### Improved

- **Input validation for common errors**: Toolchain type mismatches, incorrect flag types, post-resolve mutation attempts, no-toolchain compilation, and unknown variant names now raise clear, actionable error messages instead of confusing failures.

- **UsageRequirements values validated**: Passing a string instead of a list (e.g., `target.public.defines = "-DFOO"`) now raises an immediate error with guidance.

- **Command() template variable validation**: Unknown `$variables` in `Command()` templates are now caught at build-description time, not at ninja-run time.

### Fixed

- **ReadTheDocs build**: Fixed pcons installation and `target_type` string rendering in documentation.

### CI

- Bumped `wasi-sdk` from v30 to v32.
- Bumped `mymindstorm/setup-emsdk` from 14 to 15.

## [0.12.0] - 2026-04-05

### Added

- **`project.generate_pc_file()` for pkg-config `.pc` generation**: Targets can now generate `.pc` files for downstream consumers. Handles prefix-relative paths, external include directories, and library flags automatically.

- **`target.nodes` computed property**: Returns all nodes (intermediate + output) for a target, convenient for dependency inspection.

- **`Alias()` accepts list arguments**: `project.Alias("name", [target1, target2])` now works in addition to `project.Alias("name", target1, target2)`.

- **Automatic `uvx ninja` fallback**: When `ninja` is not in PATH, pcons now falls back to running it via `uvx ninja`, so users with `uv` installed don't need a separate ninja installation.

- **MSVC detection via `vswhere`**: When `cl.exe` is not in PATH, pcons now finds Visual Studio installations via `vswhere.exe`, improving out-of-the-box Windows experience.

- **MSVC `link.exe`/`lib.exe` resolution**: When other tools shadow MSVC's `link.exe` or `lib.exe` in PATH (e.g., Cygwin, Git for Windows), pcons now resolves them from the Visual Studio installation directory.

### Changed

- **`object_nodes` renamed to `intermediate_nodes`**: Better reflects that these are not always object files (e.g., archive or custom tool intermediates).

- **`TargetType` enum replaced with plain strings**: `target.target_type` is now a simple string (`"program"`, `"static_library"`, etc.) instead of an enum value.

- **Core is now fully tool-agnostic**: All C/C++ compile-link logic has been extracted from `core/resolver.py` into `tools/compile_link.py` via a factory dispatch system. `UsageRequirements` is now generic and extensible with dict-based storage. The core knows nothing about compilers, linkers, or languages.

### Fixed

- **`.pc` file include paths**: External include directories (outside the install prefix) are now correctly emitted as absolute `-I` flags in the `Cflags` field.

## [0.11.0] - 2026-04-02

### Added

- **`project.generate()` convenience method**: Replaces the `Generator().generate(project)` pattern — build scripts no longer need to import `Generator`. Selects the right backend (ninja/make/xcode) from CLI flags or environment variables.

- **Smarter `Project()` defaults**: `build_dir` defaults to `PCONS_BUILD_DIR` env var (set by the CLI), and `root_dir` is inferred from the calling script's directory via stack inspection. Build scripts no longer need `Path(__file__).parent` or `os.environ.get("PCONS_BUILD_DIR", "build")` boilerplate.

- **Ninja `restat` support**: `env.Command(..., restat=True)` tells Ninja to re-check output timestamps after running a command. If the output didn't actually change, downstream rebuilds are skipped.

- **`target.depends()` for non-linked dependencies**: Targets can now declare implicit dependencies that propagate public usage requirements (includes, defines) without adding outputs to the linker command. Useful for generated headers.

- **`cppstd` parameter for `ConanFinder.sync_profile()`**: Sets `compiler.cppstd` in the Conan profile. Can be specified explicitly (`cppstd="23"`) or inferred automatically from `env.cxx.flags` (e.g., `-std=c++23`). Many Conan packages require this setting.

- **`PackageDescription` and `ImportedTarget` exported from top-level `pcons` package**: Users can now write `from pcons import ImportedTarget, PackageDescription` instead of importing from subpackages.

- **`pcons-fetch` header-only packages**: `build="none"` in deps.toml skips the build step for header-only libraries, using the source directory directly as the install prefix.

- **`pcons-fetch` commit SHA pinning**: Git refs that look like commit SHAs now trigger a full clone + checkout instead of `--depth=1 --branch`, which only works for branch/tag names.

### Fixed

- **Link flags no longer leak into `ar` commands**: When a `StaticLibrary` depended on an `ImportedTarget` with `-L`, `-pthread`, or other link flags, those incorrectly appeared in the archiver command. The archiver (`ar` / `lib.exe`) only accepts object files.

- **`pcons-fetch` git URL detection with `@ref` suffix**: URLs like `https://...repo.git@main` are now correctly detected as git repos. Previously `endswith(".git")` failed because the URL ends with `@main`.

- **`pcons-fetch`**: Prefer `.pc` files over directory scanning when generating package descriptions.

### Improved

- **Simplified examples**: Removed boilerplate from all 30+ example build scripts. The minimal hello_c example is now 6 lines of code with a single import.

- **Package management documentation**: New "Header-Only and Manual Packages" section in the user guide. `sync_profile()` reference with all parameters. Updated `ImportedTarget` docstring to show `find_package()` + `link()` pattern instead of manual flag copying.

- **ARCHITECTURE.md**: Replaced stale planned-API examples with current working API. Updated status notes for package management features that are now fully implemented.

## [0.10.0] - 2026-04-01

### Added

- **MSVC C++20 module support**: `.cppm` module interface units are now handled by `MsvcToolchain` using `cl.exe /scanDependencies` for dependency scanning and Ninja dyndep for correct build ordering. Includes a dedicated `MsvcCxxCompiler` with proper `$cxx.*` namespace and correct linker command handling (MSVC's `link.exe` is separate from `cl.exe`, unlike GCC/Clang).

- **`pcons-fetch` SHA-256 verification**: Archive downloads now support an optional `sha256` field in `deps.toml`. When present, the downloaded archive is verified before extraction and aborts on mismatch.

- **Safe archive extraction in `pcons-fetch`**: Archive extraction now rejects path traversal (`../`), absolute paths, and symlink/hardlink escape tricks in both tar and zip archives.

### Fixed

- **CLI environment restoration**: `run_script()` now properly saves and restores pre-existing environment variables instead of unconditionally deleting them. Previously, running pcons could clobber `PCONS_BUILD_DIR` or other env vars set by an outer invocation.

- **ReadTheDocs build**: Fixed import error by lazily importing `pbxproj` in `XcodeGenerator`, which is an optional dependency not available in the docs build environment.

- **pkg-config version comparison**: Strict inequalities (`>`, `<`) are now handled correctly with a custom version comparator, instead of being silently mapped to `>=`/`<=` via pkg-config flags.

- **Type safety cleanup**: Replaced `type: ignore` comments with proper `cast()` calls across toolchains, Xcode generator, and builder code. `BuildInfo` is now a dataclass instead of a raw dict.

## [0.9.0] - 2026-03-20

### Added

- **Fortran toolchain (`gfortran`)**: Full GNU Fortran support via `find_fortran_toolchain()`. Includes compiler, archiver, and linker tools; supports all standard Fortran source extensions (`.f90`, `.f95`, `.f03`, `.f08`, `.f18`, `.F`, `.F90`, `.f`, `.for`, `.ftn`).

- **Ninja dyndep for Fortran module dependencies**: Correct build ordering for projects using Fortran `MODULE` / `USE` statements. A configure-time manifest is written and a build-time Python scanner (`pcons.toolchains.fortran_scanner`) produces a `.dyndep` file consumed by Ninja (requires Ninja ≥ 1.10).

- **Mixed-language C++/Fortran builds**: `env.add_toolchain()` now supports mixing Fortran with C/C++ in a single target. Runtime libraries are automatically injected in both directions — gfortran as primary linker adds `-lc++`/`-lstdc++` for C++ objects; g++/clang++ as primary linker adds `-lgfortran` for Fortran objects. On macOS the gfortran library directory is also injected automatically.

- **C++20 named modules (LLVM toolchain)**: `.cppm` module interface units are now handled by `LlvmToolchain`. The single-step compile (`-fmodule-output=`) produces both the `.pcm` precompiled module and the object file, exactly like Fortran's `-J modules`. A build-time scanner (`pcons.toolchains.cxx_module_scanner`) calls `clang-scan-deps -format=p1689` to discover dependencies and writes a Ninja dyndep file for correct build ordering (requires `clang-scan-deps` and Ninja ≥ 1.10).

- **`after_resolve()` hook in toolchain protocol**: `BaseToolchain` now defines an optional `after_resolve(project, source_obj_by_language)` hook called after all targets are resolved but before command expansion. Toolchains override this to inspect or modify the build graph (used by both Fortran and C++20 module support).

- **Five new examples**:
  - `25_fortran_hello` — simple "Hello from Fortran!" program
  - `26_fortran_modules` — Fortran MODULE / USE with correct dyndep ordering
  - `27_fortran_calls_cxx` — Fortran primary calling C++ via `BIND(C)` (gfortran links, C++ runtime injected)
  - `28_cxx_calls_fortran` — C++ primary calling Fortran via `BIND(C)` (clang++/g++ links, Fortran runtime injected)
  - `29_cxx_modules` — C++20 named modules with a module interface unit (`.cppm`) and consumer (`.cpp`)

### Fixed

- **LLVM object suffix on Windows**: `LlvmToolchain` now correctly produces `.obj` files on Windows (COFF convention) instead of `.o`. Previously the Unix base class suffix was used even when targeting `x86_64-pc-windows-msvc`.

## [0.8.4] - 2026-03-19

### Added

- **`-C/--directory` option**: Like `make`, `cmake`, and `ninja`, pcons now supports `-C DIR` to change to a directory before doing anything else. The `-C` short flag previously used for `--reconfigure` has been removed; use `--reconfigure` instead.

### Fixed

- **`pcons init` template**: The generated template now uses the public API (`Generator`, `Project`, `find_c_toolchain`) matching the style of all examples, instead of internal imports (`NinjaGenerator`, `Configure`) and unnecessary boilerplate.

## [0.8.3] - 2026-03-14

### Fixed

- **`$ORIGIN` in linker flags**: Literal `$` in link flags (e.g., `-Wl,-rpath,$ORIGIN`) was silently interpreted as a pcons variable, producing broken output. Now raises a clear error with a hint to use `$$` for literal dollar signs. The ninja generator also properly escapes literal `$` as `\$$` so it survives both ninja and shell expansion.

- **Silent error swallowing**: Narrowed overly-broad exception handlers in module loading, CLI variable parsing, and config cache loading to avoid hiding real errors.

### Added

- **`--debug=configure` tracing**: All configure checks (`check_header`, `check_flag`, `try_compile`, etc.) now log detailed trace output including the command run, exit codes, compiler errors, caller file:line, and source code previews. When active, check source files are preserved in `build/.configure-checks/` for inspection.

- **`--debug=help`**: Lists available debug subsystems with descriptions. Unknown subsystem names now produce an error instead of being silently ignored.

- **`check_header()` gains `defines` and `extra_flags` parameters**: Allows specifying preprocessor defines needed to include a header, e.g., `check_header("ucontext.h", defines=["_XOPEN_SOURCE"])` for headers that require feature macros.

## [0.8.2] - 2026-03-13

### Added

- **`ToolChecks.try_compile()` public method**: Compile arbitrary source code snippets to probe for compiler features, struct members, intrinsics, etc. Handles compiler lookup and result caching automatically, matching the pattern of `check_header()` and `check_flag()`. Previously this required reaching for private `_try_compile()` and `_get_compiler()` methods.

## [0.8.1] - 2026-03-13

### Added

- **`configure_file()` for template-based config headers**: CMake-style template substitution with `@VAR@` replacement and `#cmakedefine` / `#cmakedefine01` directives. Generates config headers from `.in` templates during the build description phase.
  - New example `24_configure_file` demonstrating usage

- **Automatic install_name (macOS) and SONAME (Linux) for shared libraries**: Unix toolchains now automatically set `-Wl,-install_name,@rpath/<name>` on macOS and `-Wl,-soname,<name>` on Linux when building shared libraries. Override with `target.set_option("install_name", value)` or disable with `target.set_option("install_name", "")`.

- **Generic `target.set_option()` / `target.get_option()` API**: Targets now support arbitrary key-value metadata via `set_option(key, value)` and `get_option(key, default)`. This provides a clean extension point for toolchain-specific options without adding domain-specific properties to the core Target class.

- **`Toolchain.get_link_flags_for_target()` hook**: Toolchains can now inject target-specific link flags (e.g., install_name, SONAME) during build context creation. The hook receives the target, output filename, and existing flags.

- **Auto-generated toolchain and builder documentation**: `docs/toolchains.md` and `docs/builders.md` are now auto-generated from registered toolchains and builders.

### Fixed

- **Object file name collisions for same-basename sources**: Sources sharing a basename (e.g., `foo.c` and `foo.cpp`) no longer produce conflicting object files. Object filenames now include the source extension (`foo.c.o`, `foo.cpp.o`) and mirror the source directory structure under `obj.<target>/` (e.g., `src/lib/foo.cpp` → `obj.mylib/src/lib/foo.cpp.o`).

### Changed

- **Improved `Generator()` discoverability**: Better documentation and source organization for generator selection.

## [0.8.0] - 2026-03-13

### Added

- **WASI toolchain**: New `find_wasi_toolchain()` for compiling C/C++ to standalone WebAssembly (`.wasm`) using wasi-sdk. Supports `clang`, `clang++`, `llvm-ar` from wasi-sdk with automatic SDK discovery via `WASI_SDK_PATH` env var or common install locations. Includes `wasmtime` runner integration for executing built `.wasm` files.
  - New example `22_wasm_wasi` demonstrating WASI compilation and execution

- **Emscripten toolchain**: New `find_emscripten_toolchain()` for compiling C/C++ to WebAssembly + JavaScript using Emscripten. Uses `MultiOutputBuilder` for Program targets that produce both `.js` (primary) and `.wasm` (secondary) outputs. Supports Emscripten `-s` settings via `env.link.settings` list.
  - New example `23_wasm_emscripten` demonstrating Emscripten compilation and Node.js execution
  - Automatic SDK discovery via `EMSDK` env var, common install locations, or `emcc` in PATH

- **Multi-output Program support in resolver**: The resolver now generically supports Program builders that produce multiple outputs (e.g., `.js` + `.wasm`). When a toolchain's Program builder is a `MultiOutputBuilder`, secondary output nodes are automatically created and tracked. This is tool-agnostic — it introspects the builder, not the toolchain name.

- **CI testing for WebAssembly toolchains**: New `test-wasm` CI job tests both WASI and Emscripten examples on ubuntu-latest and macos-latest, installing emsdk, wasi-sdk, and wasmtime.

### Changed

- **`TargetPath.index` default changed from `0` to `None`**: This distinguishes "automatic" (`$out` in ninja, expanding to all outputs) from "explicit primary" (`$target_0`, expanding to first output only). Important for multi-output builds where `-o $out` would incorrectly expand to all outputs. `SourcePath.index` similarly changed.

- **CI actions pinned to commit SHAs**: All GitHub Actions in the CI workflow are now pinned to specific commit SHAs instead of version tags, improving supply-chain security.

### Fixed

- **`compile_commands.json` now uses actual compiler command**: Previously hardcoded generic names like `cc`/`c++` instead of reading the actual compiler path (e.g., `emcc`, `/usr/bin/gcc`) from the environment's tool configuration.

## [0.7.4] - 2026-02-23

### Added

- **Auto-generate `compile_commands.json` from build generators**: Ninja, Makefile, and Xcode generators now automatically generate `compile_commands.json` alongside build files for seamless IDE integration. A symlink is also created at the project root for tool discovery. No more manual `CompileCommandsGenerator()` calls needed. Opt out with `generator.generate(project, compile_commands=False)`.

### Fixed

- **`compile_commands.json` symlink on Windows cross-drive paths**: `os.path.relpath()` raises `ValueError` when source and build directories are on different Windows drives. Symlink creation is now skipped gracefully in that case, and also when `build_dir` is the project root (file already in place).

## [0.7.3] - 2026-02-16

### Fixed

- **C++ flags no longer leak to C compilation in mixed-language targets**: When a target contains both `.c` and `.cpp` sources (e.g., `Program("app", env, sources=["main.cpp", "util.c"])`), language-specific flags like `-std=c++20` set on `env.cxx.flags` no longer leak to `.c` file compilation. Previously, `compute_effective_requirements()` merged the primary tool's flags into a shared list applied to all sources. Now per-tool base flags are applied during command expansion where the tool name is correctly determined per-source file.

## [0.7.2] - 2026-02-15

### Added

- **`pcons.contrib.windows.msvcup` module**: Install MSVC compiler and Windows SDK without Visual Studio using [msvcup](https://github.com/marlersoft/msvcup). Ideal for CI environments, lightweight dev setups, or reproducible locked compiler versions.
  - `ensure_msvc()`: Downloads and installs MSVC + Windows SDK, sets up wrapper executables via `msvcup autoenv`
  - Version pinning: Specify exact MSVC and SDK versions for reproducible builds
  - Lock file support: Pin resolved versions for team consistency (`--lock-file`)
  - Manifest update control: `manifest_update` parameter (`"off"`, `"daily"`, `"always"`)
  - Integrates with pcons toolchain detection — msvcup-installed compiler is found automatically

### Changed

- **Dedicated CI job for msvcup testing**: msvcup example tests now run on a separate Windows runner without Visual Studio pre-installed, validating that msvcup can provide the full toolchain independently.
- **Configurable test timeouts**: Example integration tests now support a `timeout` field in `test.toml` for tests that need longer execution time (e.g., downloading toolchains).

### Internal

- Code simplification pass: reduced ~163 lines across 17 files with no functional changes.

## [0.7.1] - 2026-01-31

### Added

- **`$SRCDIR` variable for `env.Command()`**: Generator-agnostic variable for referencing the project source tree root in custom commands. Ninja replaces it with `$topdir`, Makefile replaces it with the absolute project root path. Useful for scripts and config files that live in the source tree: `command="python $SRCDIR/tools/gen.py $SOURCE -o $TARGET"`.
- **`target.depends()` method**: Add implicit dependencies to any target. Files added via `depends()` trigger rebuilds when changed but don't appear in `$in`/`$SOURCE`. Works with `str`, `Path`, `FileNode`, or `Target` arguments. Supports fluent chaining: `app.depends("version.txt", "config.yaml")`.
- **`depends=` parameter on `env.Command()`**: Shorthand for adding implicit dependencies at command creation time: `env.Command(target=..., source=..., command=..., depends=["tools/gen.py"])`.
- **`pcons build` auto-generates before building**: Running `pcons build` now automatically runs `pcons generate` first if needed, so a single command handles the full workflow.

### Changed

- **Project as single authority for node creation**: All `FileNode` objects in production code are now created through `project.node()`, which ensures the same canonical path always returns the same object. This eliminates duplicate-node bugs where metadata (like `_build_info`) was split across separate objects for the same file. The `_sync_output_nodes_to_project()` workaround has been removed.

### Fixed

- **Post-build `$out` expansion in ninja**: `$out` and `$in` in post-build commands are now left as literal ninja variables instead of being pre-expanded to project-root-relative paths. Since ninja runs from the build directory, pre-expanded paths were incorrect.
- **InstallDir child nodes as implicit deps**: Child nodes of `InstallDir` targets now appear as implicit dependencies (after `|` in ninja) instead of explicit inputs. This prevents them from polluting `$in`, which `copytree` expects to contain only the source directory.
- **Install directory deps and node deduplication for ninja paths**: Fixed ninja path generation for install directory dependencies and node deduplication.
- **`create_pkg` Install target name collisions**: Install targets created by `create_pkg()` now use unique names derived from the package name (e.g., `pkg_payload_MyApp` instead of `install_payload`), eliminating rename warnings when `create_pkg` is called multiple times.

## [0.7.0] - 2026-01-30

### Added

- **`pcons info --targets`**: New CLI option to list all build targets grouped by type. Shows aliases first, then targets organized by type (program, shared_library, etc.) with their output paths.
- **Auto-detect directory sources in Install builder**: `project.Install()` now automatically detects when a source is a directory (by checking the node graph for child nodes) and uses `copytreecmd` with depfile/stamp tracking instead of `copycmd`. This fixes `IsADirectoryError` when passing bundle directories through Install (e.g., from `create_pkg` sources).

### Changed

- **`Generator.generate()` no longer takes `output_dir` parameter**: The generator always uses `project.build_dir` as the output directory. Callers that were passing `output_dir` should remove the argument.
- **Improved `build_dir` prefix warning**: `normalize_target_path()` now provides clearer warnings when target paths start with the build directory name, explaining the double-prefix issue and suggesting the correct path. Accepts an optional `target_name` for better diagnostics.

### Fixed

- **Install directory detection**: Fixed `_has_child_nodes` failing to detect directory sources. Source paths passed as `project.build_dir / subdir / ...` include the build_dir prefix (e.g., `build/ofx-debug/bundle`), but node paths in `project._nodes` are build-dir-relative (e.g., `ofx-debug/bundle/Contents/...`). The build_dir prefix is now stripped before comparison, fixing both absolute and relative build_dir cases.
- **Graph generators: path-based labels**: Mermaid and DOT graph node labels now show full relative paths (e.g., `obj.floss2/floss-core.o`) instead of just filenames, disambiguating same-named files across targets.
- **Graph generators: directory containment edges**: Install target outputs inside a bundle directory now have edges drawn to that directory node, completing the dependency chain from sources through to installers.

## [0.6.1] - 2026-01-30

### Fixed

- **Alias() now resolves Target references lazily**: `Project.Alias()` no longer eagerly reads `target.output_nodes` at call time. Instead, `AliasNode.targets` is now a property that resolves Target references on access. This fixes aliases for `InstallDir` and other targets whose `output_nodes` are populated during `resolve()` — previously these aliases produced empty no-op phony rules in Ninja.

### Changed

- **`all` target includes every target**: `ninja all` / `make all` now builds every target in the project (commands, installers, archives, etc.), not just programs and libraries. The implicit default (when `project.Default()` is not called) remains programs and libraries only.

### Documentation

- Show version number in docs site heading via mkdocs-macros-plugin
- Clarify Feature Detection docs: separate ToolChecks from Configure
- Add Platform Installers section to user guide

## [0.6.0] - 2026-01-29

### Added

- **Compiler cache wrapping**: New `env.use_compiler_cache()` method wraps compile commands with ccache or sccache.
  - Auto-detects available cache tool (tries sccache, then ccache)
  - Explicit tool selection: `env.use_compiler_cache("ccache")`
  - Only wraps cc/cxx commands, never linker/archiver
  - Warns about ccache + MSVC incompatibility (use sccache instead)

- **Semantic presets**: New `env.apply_preset()` for common flag combinations.
  - `"warnings"`: All warnings + warnings-as-errors (`-Wall -Wextra -Wpedantic -Werror` / `/W4 /WX`)
  - `"sanitize"`: Address + undefined behavior sanitizers
  - `"profile"`: Profiling support (`-pg` / `/PROFILE`)
  - `"lto"`: Link-time optimization (`-flto` / `/GL` + `/LTCG`)
  - `"hardened"`: Security hardening flags (stack protector, FORTIFY_SOURCE, RELRO, etc.)
  - Toolchain-specific: Unix and MSVC each define their own flags

- **Cross-compilation presets**: New `env.apply_cross_preset()` for common cross-compilation targets.
  - `android(ndk, arch, api)`: Android NDK cross-compilation
  - `ios(arch, min_version, sdk)`: iOS cross-compilation
  - `wasm(emsdk)`: WebAssembly via Emscripten
  - `linux_cross(triple, sysroot)`: Generic Linux cross-compilation
  - `CrossPreset` dataclass for custom presets
  - Toolchains handle --target, --sysroot, /MACHINE flags automatically

- **`project.find_package()`**: One-liner to find and use external packages.
  - Searches using FinderChain (PkgConfig → System by default)
  - Returns ImportedTarget for use as dependency or with `env.use()`
  - Caches results for repeated lookups
  - `required=False` for optional dependencies
  - `project.add_package_finder()` to prepend custom finders (Conan, vcpkg)

- **Windows SxS manifest support**: Support for Windows Side-by-Side (SxS) manifests
  - **`.manifest` as source**: Add `.manifest` files to Program/SharedLibrary sources; automatically passed to linker via `/MANIFESTINPUT:`
  - **`pcons.contrib.windows.manifest`**: Helper module for generating manifests:
    - `create_app_manifest()`: Generate application manifests with DPI awareness, visual styles, UAC settings, and assembly dependencies
    - `create_assembly_manifest()`: Generate assembly manifests for private DLL assemblies
  - Works with both MSVC and clang-cl toolchains

- **Platform-specific installer generation**: New `pcons.contrib.installers` package for creating native installers
  - **macOS**: `create_pkg()` for .pkg installers, `create_dmg()` for disk images, `create_component_pkg()` for simple packages
  - **Windows**: `create_msix()` for MSIX packages (requires Windows SDK)
  - Auto-detects bundle vs non-bundle sources for proper macOS component plist handling
  - Signing helpers: `sign_pkg()`, `notarize_cmd()` for macOS code signing

- **CLI `uvx ninja` fallback**: When `ninja` isn't in PATH but `uvx` is available, `pcons build` and `pcons clean` automatically use `uvx ninja`

- **Targets as sources**: Targets can now be used as sources for `Install()`, `Command()`, and other builders. The target's outputs are resolved at build time, enabling auto-generated source files.

- **Test framework `build_targets` support**: Example tests can now specify platform-specific build targets via `build_targets_darwin`, `build_targets_windows`, etc.

### Fixed

- **macOS pkgbuild for non-bundle files**: Component plists are now only generated for .app bundles, fixing pkgbuild errors for CLI tools and libraries

## [0.5.0] - 2026-01-28

### Added

- **Add-on/Plugin module system**: New extensible module system for creating reusable domain-specific add-ons.
  - **Module discovery**: Auto-loads modules from `PCONS_MODULES_PATH`, `~/.pcons/modules/`, and `./pcons_modules/`
  - **`pcons.modules` namespace**: Access loaded modules via `from pcons.modules import mymodule`
  - **`--modules-path` CLI option**: Specify additional module search paths
  - **Module API convention**: Modules can define `__pcons_module__` metadata and `register()` function

- **`pcons.contrib` package**: Built-in helper modules for common tasks:
  - **`pcons.contrib.bundle`**: macOS bundle and flat bundle creation helpers
    - `generate_info_plist()` - Generate Info.plist content
    - `create_macos_bundle()` - Create macOS .bundle structure
    - `create_flat_bundle()` - Create flat directory bundles (Windows/Linux)
    - `get_arch_subdir()` - Get architecture subdirectory names (e.g., "MacOS-x86-64")
  - **`pcons.contrib.platform`**: Platform detection utilities
    - `is_macos()`, `is_linux()`, `is_windows()` - Platform checks
    - `get_shared_lib_extension()`, `format_shared_lib_name()` - Library naming
    - `get_arch()` - Get current architecture

### Documentation

- User guide: Added comprehensive "Add-on Modules" section with examples
- Architecture doc: Added Module System section with implementation details

## [0.4.3] - 2026-01-28

### Added

- **`FlagPair` marker class for explicit flag+argument pairs**: New `FlagPair` class allows users to explicitly mark flag+argument pairs that should be kept together during deduplication, even for custom flags not in the toolchain's `SEPARATED_ARG_FLAGS` list.
  - Usage: `env.cxx.flags.append(FlagPair("-custom-flag", "value"))`
  - Immutable, hashable, and iterable (can be unpacked: `flag, arg = FlagPair(...)`)
  - Exported from top-level `pcons` module

### Fixed

- **Flag pair deduplication for `-include` and similar flags**: Flags like `-include`, `-imacros`, and `-x` that take separate arguments are now properly handled during deduplication. Previously, `-include header1.h -include header2.h` would incorrectly deduplicate to `-include header1.h header2.h`. Added `-include`, `-imacros`, and `-x` to `SEPARATED_ARG_FLAGS` in Unix toolchains.

- **ToolConfig.as_namespace() mutation bug**: The `as_namespace()` method now returns copies of mutable values (lists, dicts) instead of references to the original. This prevents accidental mutation of tool configuration during variable substitution, which was causing flag accumulation bugs.

- **Resolver no longer double-merges flags**: The resolver now uses `extra_flags` and `ldflags` directly instead of merging them with existing tool flags. These values already include base environment flags via `compute_effective_requirements()`, so merging was duplicating flags.

### Documentation

- User guide: Added "Build Script Lifecycle" section explaining the three phases (configure, describe, generate)
- User guide: Clarified when to use `project.node()` vs raw paths
- User guide: Added "Default and Alias Targets" section with examples
- User guide: Added output naming defaults table for libraries and programs
- User guide: Improved environment cloning documentation
- User guide: Added examples for multiple commands and post-build commands

## [0.4.2] - 2026-01-28

### Fixed

- **Flag accumulation bug**: Context flags (includes, defines, compile_flags) were being appended to the shared tool_config, causing flags to accumulate exponentially across multiple source files in a target. Now uses temporary overrides passed via extra_vars to avoid mutating shared state.

- **C++ linker selection**: C++ programs and shared libraries now correctly use the C++ compiler (clang++/g++) as the linker instead of the C compiler (clang/gcc). This ensures proper C++ runtime linkage. The logic is in the toolchain layer (CompileLinkContext) to keep the core tool-agnostic.

- **InstallAs validation**: `InstallAs()` now raises a clear `BuilderError` when passed a list or tuple, directing users to use `Install()` for multiple files. Previously it would silently fail.

### Documentation

- Added practical example for `$$` escaping in subst.py docstring (useful for `$ORIGIN` in rpath)
- User guide: Documented `$$` for literal dollar signs with rpath example
- User guide: Clarified that `Install()` takes a list while `InstallAs()` takes a single source

## [0.4.1] - 2026-01-23

### Added

- **Debug/trace system for build script debugging**: New `--debug=<subsystems>` CLI flag or `PCONS_DEBUG` environment variable enables selective tracing. Available subsystems: `configure`, `resolve`, `generate`, `subst`, `env`, `deps`, `all`.
  - New `pcons/core/debug.py` module with `trace()`, `trace_value()`, `is_enabled()` functions
  - Enhanced `__str__` methods on Target, Environment, FileNode, Project for readable debug output
  - Source location tracking (`defined_at`) shown in debug output
  - Usage: `pcons --debug=resolve,subst` or `PCONS_DEBUG=all pcons`

- **Xcode project generator**: New `-G xcode` option generates native `.xcodeproj` bundles that can be built with `xcodebuild` or opened in Xcode IDE.
  - Supports Program, StaticLibrary, and SharedLibrary targets
  - Maps pcons include dirs, defines, and compile flags to Xcode build settings
  - Handles target dependencies between libraries and executables
  - Generates both Debug and Release configurations
  - Uses `pbxproj` library for robust project file generation

- **Multi-generator build support in CLI**: The `pcons build` command now auto-detects which generator was used and runs the appropriate build tool:
  - `build.ninja` → runs `ninja`
  - `Makefile` → runs `make`
  - `*.xcodeproj` → runs `xcodebuild`

- **Variant support for xcodebuild**: The `--variant` flag is passed to xcodebuild as `-configuration`, mapping variant names to Xcode configurations (e.g., `--variant debug` → `-configuration Debug`).

## [0.4.0] - 2026-01-22

### Changed

- **BREAKING: Typed path markers replace string escaping**: Command templates now use typed `SourcePath()` and `TargetPath()` marker objects instead of string patterns like `$$SOURCE`/`$$TARGET`. This provides type-safe path handling and eliminates fragile string manipulation.
  - All toolchains (GCC, LLVM) migrated to use markers
  - All standalone tools (install, archive, cuda) migrated to use markers
  - Generators convert markers to appropriate syntax: Ninja uses `$in`/`$out`, Makefile uses actual paths
  - Custom tools should now use markers in command templates (see `test_external_tool.py` for example)

- **Unified command expansion path**: Removed the dual mechanism where some tools used string patterns and others used markers. All tools now follow the same flow: markers → resolver → generators.

### Fixed

- **CommandBuilder now stores env in _build_info**: Fixes command expansion for nodes created via `env.cc.Object()` and similar APIs. Previously commands weren't expanded because the resolver couldn't find the environment.

- **Standalone tool commands properly converted**: `_get_standalone_tool_command()` now calls `_relativize_command_tokens()` to convert markers to Ninja variables.

- **Makefile generator handles markers in context overrides**: `_apply_context_overrides()` now properly passes through marker objects instead of trying to do string replacement on them.

### Removed

- **`_convert_command_variables()` from Ninja generator**: String-based `$SOURCE`/`$TARGET` conversion is no longer needed since all tools use typed markers.

## [0.3.0] - 2026-01-21

### Changed

- **BREAKING: Generator-agnostic command templates**: Toolchain command templates now use `$$SOURCE`/`$$TARGET` instead of Ninja-specific `$$in`/`$$out`. Each generator converts to its native syntax:
  - Ninja: `$in`/`$out`
  - Makefile: actual paths
  - Conventions: `$$SOURCE` (single input), `$$SOURCES` (multiple), `$$TARGET` (output), `$$TARGET.d` (depfile)

- **BREAKING: ToolchainContext API changed**: `get_variables()` replaced with `get_env_overrides()`. Values are now set on the environment's tool namespace before command expansion, rather than written as per-build Ninja variables. Return type changed from `dict[str, list[str]]` to `dict[str, object]`.

- **Command expansion moved to resolver**: Commands are now fully expanded at resolution time with all effective requirements baked in. Generators receive pre-expanded commands, simplifying generator implementation.

- **Unified builder/tool architecture**: Install and Archive builders are now implemented as `StandaloneTool` subclasses (`InstallTool`, `ArchiveTool`). Tools provide command templates via `default_vars()`, builders reference them via `command_var`. Enables customization: `env.install.copycmd = ["cp", "$$SOURCE", "$$TARGET"]`.

- **Shell quoting improvements**: Commands stored as token lists until final output. The `subst()` function handles shell-appropriate quoting based on target format (`shell="ninja"` or `shell="bash"`). Paths with spaces properly quoted.

- **Standardized on `$SOURCE`/`$TARGET` in user commands**: User-facing commands (e.g., `env.Command()`) use SCons-style `$SOURCE`/`$TARGET` variables. Generators convert to native syntax.

### Fixed

- **Compile flags no longer passed to linker**: The resolver now correctly separates `extra_flags` (compile-only) from `ldflags` (link-only). Fixes MSVC builds where `/W4` was incorrectly passed to the linker.

- **Windows platform suffixes in UnixToolchain**: `get_program_name()` and `get_shared_library_name()` now detect Windows and return `.exe`/`.dll` suffixes for GCC/MinGW builds.

- **Standalone tool context overrides**: Install and Archive tools now correctly apply context overrides (like `$install.destdir`) even when no Environment is present.

### Removed

- **Dead code cleanup**: Removed ~100 lines of unused code from ninja.py:
  - `_get_env_suffix()` - superseded by command hash-based rule naming
  - `_get_rule_command()` - superseded by pre-expanded commands
  - `_augment_command_with_effective_vars()` - values now baked into commands

### Documentation

- Updated ARCHITECTURE.md to reflect new `get_env_overrides()` pattern
- Updated CLAUDE.md with correct ToolchainContext file location

## [0.2.4] - 2026-01-20

### Added

- **`project.InstallDir()` for recursive directory installation**: Copies entire directory trees with proper incremental rebuild support using ninja's depfile mechanism. Stamp files stored in `build/.stamps/` to keep output directories clean.
  - Usage: `project.InstallDir("dist", src_dir / "assets")` (paths relative to build_dir)
  - New `copytree` command in `pcons.util.commands` with `--depfile` and `--stamp` options
- **`project.Command()` for API consistency**: Wrapper around `env.Command()` for users who prefer the project-centric API.
- **`PathResolver` for consistent path handling**: New centralized path resolution ensures all builders handle output paths consistently:
  - Target (output) paths: relative to `build_dir`
  - Source (input) paths: relative to project root
  - Absolute paths: pass through unchanged
  - Warns when relative path starts with build_dir name (e.g., `"build/foo"`)
- **Rebuild tests in example framework**: New `[[rebuild]]` sections in `test.toml` verify incremental build behavior:
  - `touch`: file to modify before rebuild
  - `expect_no_work`: verify ninja has nothing to do
  - `expect_rebuild` / `expect_no_rebuild`: verify specific targets
- **New example `14_install_dir`**: Demonstrates `InstallDir` for copying directory trees.

### Changed

- **Tarfile/Zipfile output paths now relative to build_dir**: No longer need `build_dir /` prefix. Use `output="file.tar.gz"` instead of `output=build_dir / "file.tar.gz"`.
- **Install/InstallDir destinations relative to build_dir**: Consistent with other builders.

## [0.2.3] - 2026-01-20

### Added

- **Auto-resolve in generators**: Generators now automatically call `project.resolve()` if the project hasn't been resolved yet. Users can still call `resolve()` explicitly (backward compatible), or simply omit it for simpler build scripts.
- **New example `12_env_override`**: Demonstrates using `env.override()` to compile specific source files with different flags (extra defines, include paths).
- **New example `13_subdirs`**: Demonstrates subdirectory builds where each subdir can be built standalone or as part of the parent project.
- **DotGenerator for GraphViz output**: New `DotGenerator` class for dependency graph visualization in DOT format. Use `pcons generate --graph` or import `DotGenerator` directly.
- **`all` phony target in ninja**: Generated ninja files now include an `all` target (standard Make convention). Default target is `all` unless user specifies defaults via `project.Default()`.

### Fixed

- **`env.override()` and `env.clone()` now work correctly with direct builder API**: Previously, nodes created in a cloned/overridden environment were registered with the original environment, causing per-environment compiler flags to be lost. Fixed by:
  - Cloned environments now register with the project
  - `BuilderMethod` instances are rebound to reference the new environment
  - Ninja generator creates per-environment rules for all environments
- **Command target dependencies now shown in graphs**: Both mermaid and dot generators now correctly show dependencies for `env.Command()` targets (previously showed outputs with no edges).

### Changed

- **`03_variants` example improved**: Now uses a Python loop to build both debug and release variants, demonstrating the power of Python for build configuration.
- **Example cleanups**: Removed verbose print statements from `05_multi_library`, `07_conan_example`, and `10_paths_with_spaces` examples.
- **Removed `project.dump_graph()`**: Replaced by `DotGenerator` class for consistency with other generators.

## [0.2.2] - 2026-01-19

### Added

- **Cross-platform command helpers** (`pcons.util.commands`): New module providing `copy` and `concat` commands that handle forward slashes and spaces in paths on all platforms
  - Usage: `python -m pcons.util.commands copy <src> <dest>`
  - Usage: `python -m pcons.util.commands concat <src1> [src2...] <dest>`
  - Used by Install/InstallAs builders and concat example

### Changed

- Install and InstallAs now use `pcons.util.commands copy` instead of platform-specific shell commands
- Concat example (01_concat) now uses `pcons.util.commands concat` for better cross-platform support

## [0.2.1] - 2026-01-19

### Added

- **Relative paths in ninja files**: Generated `build.ninja` files now use relative paths instead of absolute paths
  - New `topdir` variable points from build directory to project root (e.g., `topdir = ..`)
  - Source files use `$topdir/path/to/source.c` format
  - Include paths use `$topdir/` prefix (e.g., `-I$topdir/include`)
  - Build outputs remain relative to build directory
  - Makes ninja files portable and more readable
- **Proper escaping for paths with spaces**: `ToolchainContext.get_variables()` now returns `dict[str, list[str]]` so generators can properly escape each token
  - Ninja generator uses Ninja escaping (`$ ` for spaces) for cross-platform compatibility
  - Makefile generator uses appropriate quoting for Make
  - compile_commands.json uses `shlex.quote()` for POSIX compliance
  - All paths normalized to forward slashes (works on Windows)
- **New example `08_paths_with_spaces`**: Demonstrates building with spaces in directory names, filenames, and define values
- **UnixToolchain base class**: Shared implementation for GCC and LLVM toolchains (source handlers, separated arg flags, variant application, -fPIC handling)
- **BuildInfo TypedDict**: Type-safe dictionary for `node._build_info` with proper typing for tool, command, language, depfile, and context fields
- **Environment.name parameter**: Environments can now have names for more readable ninja rule names

### Changed

- **Per-environment ninja rules**: Each environment now generates its own ninja rules (e.g., `link_sharedcmd_release_abc123`) instead of sharing rules with `_effective` suffix. This fixes `env.Framework()` and other env-specific settings.
- **Test runner uses `ninja -C build`**: Changed from `ninja -f build/build.ninja` to the correct `ninja -C build` invocation per ninja best practices
- Source suffix handling now centralized through toolchain handlers with deprecation warnings for legacy `SOURCE_SUFFIX_MAP` fallback

### Fixed

- **env.Framework() now works correctly**: Framework flags are now properly baked into each environment's rules instead of requiring per-target overrides

### Documentation

- Added CLAUDE.md with project conventions and development guidelines

## [0.2.0] - 2025-01-19

### Added

- **Archive builders**: New `project.Tarfile()` and `project.Zipfile()` methods for creating tar and zip archives
  - Supports all common compression formats: `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, `.tar`, `.zip`
  - Compression auto-detected from output extension
  - Cross-platform using Python's built-in `tarfile`/`zipfile` modules
  - Returns `Target` objects that can be passed to `Install()` and other builders

### Changed

- **BREAKING: Renamed default build script from `build.py` to `pcons-build.py`**
  - CLI now looks for `pcons-build.py` by default instead of `build.py`
  - `pcons init` creates `pcons-build.py` instead of `build.py`
  - All examples updated to use `pcons-build.py`
  - Use `-b build.py` flag to run legacy scripts

- **BREAKING: `env.Command()` signature changed**: Now uses keyword-only arguments and returns `Target` instead of `list[FileNode]`
  - Old: `env.Command("output.txt", "input.txt", "cmd")`
  - New: `env.Command(target="output.txt", source="input.txt", command="cmd")`
  - Access output nodes via `target.output_nodes` instead of indexing the result
  - Optional `name` parameter for explicit target naming

- Merged `tests/examples/` into `examples/` - examples now serve as both tests and user documentation
- Example tests now verify both invocation methods: `python pcons-build.py` and `python -m pcons`

### Fixed

- Windows `Install` command now works correctly (uses `cmd /c copy` instead of bare `copy`)

### Documentation

- Added "All Build Outputs Are Targets" section to ARCHITECTURE.md documenting the design principle
- Added archive builders documentation to user guide
- New `07_archive_install` example demonstrating Tarfile builders and Install targets

## [0.1.4] - 2025-01-18

### Added

- **Multi-architecture build support**: New `env.set_target_arch()` method for building for different CPU architectures
  - macOS: Uses `-arch` flags for arm64/x86_64 builds, enabling universal binary creation
  - Windows MSVC: Uses `/MACHINE:` linker flags for x64/x86/arm64/arm64ec
  - Windows Clang-CL: Uses `--target` compiler flags plus `/MACHINE:` linker flags
- **macOS universal binary helper**: New `create_universal_binary()` function in `pcons.util.macos` combines architecture-specific binaries using `lipo`
- **`env.Command()` builder**: Run arbitrary shell commands with automatic variable substitution (`$SOURCE`, `$TARGET`, `$SOURCES`, `$TARGETS`, `${SOURCES[n]}`, `${TARGETS[n]}`)
- **macOS Framework linking**: New `env.Framework()` method and `-framework`/`-F` flag support in GCC/LLVM toolchains
- **`pairwise()` substitution function**: For flags that need interleaved prefix/value pairs (e.g., `-framework Foundation -framework Metal`)

### Changed

- **Build scripts run in-process**: CLI now uses `exec()` instead of subprocess, enabling access to `Project.build_dir` after script execution. This fixes issues where build scripts modify the build directory (e.g., `build_dir = PCONS_BUILD_DIR / variant`)
- **Toolchain-aware flag deduplication**: Flag merging now correctly handles flags with separate arguments (like `-F path`, `-framework Name`). Each toolchain defines its own separated-argument flags via `get_separated_arg_flags()`

### Fixed

- Flag deduplication no longer incorrectly merges `-F foo -F bar` into `-F foo bar`
- CLI `pcons` command now uses the actual build directory from the Project, not just the initial `PCONS_BUILD_DIR`

## [0.1.3] - 2025-01-18

### Added

- **Multi-toolchain support**: Environments can now have multiple toolchains for mixed-language builds (e.g., C++ with CUDA)
- **Clang-CL toolchain**: MSVC-compatible Clang driver for Windows with platform-aware defaults
- **AuxiliaryInputHandler**: New mechanism for files passed directly to downstream tools (e.g., `.def` files to linker)
- **Windows resource compiler**: MSVC toolchain now supports `.rc` files compiled to `.res`
- **Assembly support**: Added `.s`, `.S` (GCC/LLVM) and `.asm` (MASM) source file handling
- **Metal shader support**: Added `.metal` file compilation on macOS
- **User Guide**: Comprehensive documentation covering all pcons features

### Changed

- `find_c_toolchain()` now uses platform-aware defaults: prefers clang-cl/msvc on Windows, llvm/gcc on Unix
- Toolchains now provide `get_archiver_tool_name()` for correct archiver selection (MSVC uses `lib`, others use `ar`)

### Fixed

- Cross-platform support for C examples (02-06) now working on Windows with MSVC
- Concat example (01) now works on Windows using `cmd /c type`

### Infrastructure

- CI now runs MSVC tests on Windows
- Release workflow waits for CI to pass before publishing

## [0.1.2] - 2025-01-17

Initial public release with Ninja generator, GCC/LLVM/MSVC toolchains, and Conan integration.

[Unreleased]: https://github.com/DarkStarSystems/pcons/compare/v0.14.0...HEAD
[0.14.0]: https://github.com/DarkStarSystems/pcons/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/DarkStarSystems/pcons/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/DarkStarSystems/pcons/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/DarkStarSystems/pcons/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/DarkStarSystems/pcons/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/DarkStarSystems/pcons/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/DarkStarSystems/pcons/compare/v0.8.4...v0.9.0
[0.8.4]: https://github.com/DarkStarSystems/pcons/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/DarkStarSystems/pcons/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/DarkStarSystems/pcons/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/DarkStarSystems/pcons/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/DarkStarSystems/pcons/compare/v0.7.4...v0.8.0
[0.7.4]: https://github.com/DarkStarSystems/pcons/compare/v0.7.3...v0.7.4
[0.7.3]: https://github.com/DarkStarSystems/pcons/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/DarkStarSystems/pcons/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/DarkStarSystems/pcons/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DarkStarSystems/pcons/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/DarkStarSystems/pcons/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/DarkStarSystems/pcons/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/DarkStarSystems/pcons/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/DarkStarSystems/pcons/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/DarkStarSystems/pcons/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/DarkStarSystems/pcons/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/DarkStarSystems/pcons/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/DarkStarSystems/pcons/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/DarkStarSystems/pcons/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/DarkStarSystems/pcons/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/DarkStarSystems/pcons/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DarkStarSystems/pcons/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DarkStarSystems/pcons/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/DarkStarSystems/pcons/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/DarkStarSystems/pcons/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/DarkStarSystems/pcons/releases/tag/v0.1.2

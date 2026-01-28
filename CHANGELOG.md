# Changelog

All notable changes to pcons will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/garyo/pcons/compare/v0.4.3...HEAD
[0.4.3]: https://github.com/garyo/pcons/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/garyo/pcons/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/garyo/pcons/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/garyo/pcons/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/garyo/pcons/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/garyo/pcons/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/garyo/pcons/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/garyo/pcons/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/garyo/pcons/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/garyo/pcons/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/garyo/pcons/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/garyo/pcons/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/garyo/pcons/releases/tag/v0.1.2

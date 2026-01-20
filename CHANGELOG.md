# Changelog

All notable changes to pcons will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Auto-resolve in generators**: Generators now automatically call `project.resolve()` if the project hasn't been resolved yet. Users can still call `resolve()` explicitly (backward compatible), or simply omit it for simpler build scripts.
- **New example `12_env_override`**: Demonstrates using `env.override()` to compile specific source files with different flags (extra defines, include paths).
- **New example `13_subdirs`**: Demonstrates subdirectory builds where each subdir can be built standalone or as part of the parent project.

### Fixed

- **`env.override()` and `env.clone()` now work correctly with direct builder API**: Previously, nodes created in a cloned/overridden environment were registered with the original environment, causing per-environment compiler flags to be lost. Fixed by:
  - Cloned environments now register with the project
  - `BuilderMethod` instances are rebound to reference the new environment
  - Ninja generator creates per-environment rules for all environments

### Changed

- **`03_variants` example improved**: Now uses a Python loop to build both debug and release variants, demonstrating the power of Python for build configuration.
- **Example cleanups**: Removed verbose print statements from `05_multi_library`, `07_conan_example`, and `10_paths_with_spaces` examples.

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

[Unreleased]: https://github.com/garyo/pcons/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/garyo/pcons/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/garyo/pcons/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/garyo/pcons/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/garyo/pcons/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/garyo/pcons/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/garyo/pcons/releases/tag/v0.1.2

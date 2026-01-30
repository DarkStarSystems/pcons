# Pcons - Python Build System

An open source Python-based build system that generates Ninja (or Make) build files. Zero install, tool-agnostic core.
Designed for simplicity, maintainability and extensibility.

**Design philosophy**:
1. Configuration, not execution. Python scripts describe what to build; Ninja handles execution.
2. pcons core is completely tool-agnostic. It knows nothing about compilers or linkers; should be just as good at document preparation or game asset building or scientific dataflows. All tool-specific logic is in toolchains and tools.
3. pcons core should know nothing about any commands or toolchains; all of those should be registered at startup; consider them as add-ons that happen to be built in.
4. Always think about creating the simplest overall architecture, not just solving the problem of the moment. If you see a problem, that's an opportunity to think about simplifying the big picture. Look for "code smells" and aggressively eliminate them.
5. Always try to find the cleanest solution. As of now, we have almost no real users, so back compatibility is not important. Developing a clean, usable system is top priority.
6. This is 100% cross-platform software. Must work equally well on Linux, Windows and Mac. When fixing one OS, consider all.

## Critical Rules

**NEVER add tool-specific code to `pcons/core/`**. Compiler flags, tool names, and language-specific logic belong in `pcons/toolchains/` or `pcons/tools/`.

**NEVER check filesystem existence to determine if something is a target**

**ALL builders must return `Target` objects**, not raw FileNodes. This ensures consistency across Install(), dependencies, etc.

**ALL features must have tests.** The best tests are the user-visible examples in examples/, because they get tested on all platforms in CI as well as serving as references for users.

## Development Commands

```bash
make test              # Run all tests
make test-cov          # Tests with coverage report
make fmt               # Format code (ruff format + ruff check --fix)
make lint              # Lint and type check (ruff check + ty check)
make install-hooks     # Install pre-commit hook
```

Or directly:
```bash
uv run pytest tests/ -x -q     # Stop on first failure, quiet
uv run pytest tests/test_examples.py  # Run example integration tests
```

Pre-commit hooks run ruff check, ruff format, and ty (type checking) automatically.

## Architecture Quick Reference

**Three phases** (pcons only handles 1-3):
1. **Configure** - Tool detection, feature checks, caching
2. **Build Description** - Python scripts create dependency graph (fast, uses cached config)
3. **Generate** - Write Ninja/Make files
4. *Build* - User runs `ninja` (pcons not involved)

**Target resolution is lazy**: `lib.output_nodes` is empty until `project.resolve()` is called. This allows customizing `output_name` after target creation.

**Namespaced tools**: `env.cc.flags`, `env.cxx.cmd`, `env.link.libs` - no flat variable collisions.

**ToolchainContext protocol**: Decouples core from C/C++ specifics. Provides `get_env_overrides()` to set values on the environment before command expansion. Implementations (CompileLinkContext, MsvcCompileLinkContext) use toolchain-specific prefixes like `-I`, `/I`.

See `ARCHITECTURE.md` for full design documentation.

## Key Patterns

**Ninja paths**: All paths in `build.ninja` must be relative to the build directory. Use `$topdir` variable for source files outside the build dir (e.g., `$topdir/src/file.c`).

**Quoting**: Command lines are stored as lists of tokens until final output. Generators handle shell-appropriate quoting. Ninja generator uses `$ ` escaping for paths.

**Language propagation**: When linking, the "strongest" language's linker is used (e.g., C++ linker for mixed C/C++ objects).

**Usage requirements**: `target.public.*` propagates to dependents; `target.private.*` is local only.

## Path Handling

- **Node paths include build_dir prefix**: e.g., `build/obj.hello/hello.o`
- **Ninja generator strips build_dir prefix**: Runs from build directory, so paths are relative to build dir
- **Makefile generator keeps build_dir prefix**: Runs from project root, so paths need the build dir prefix
- **Use PathResolver for consistent path normalization**: Available via `project._path_resolver`
- **Source paths**: Relative to project root (use `path_resolver.make_project_relative()`)
- **Target paths**: Relative to build_dir (use `path_resolver.normalize_target_path()`)
- **Never check filesystem existence**: Trust node paths, don't use `path.exists()` checks

## Directory Structure

```
pcons/
├── core/           # Tool-agnostic: node.py, target.py, environment.py, resolver.py, subst.py
├── toolchains/     # GCC, LLVM, MSVC, clang-cl, CUDA, Cython
├── tools/          # Tool base classes and registry
├── generators/     # ninja.py, makefile.py, compile_commands.py, mermaid.py
├── configure/      # Configuration and tool detection
├── packages/       # External dependency management (pkg-config, Conan finders)
└── util/           # Path utilities, macOS helpers
```

## Key Files by Task

| Task | Files |
|------|-------|
| Add new builder | `pcons/core/resolver.py` (factory classes), `pcons/core/project.py` |
| Modify Ninja output | `pcons/generators/ninja.py` |
| Add toolchain | `pcons/toolchains/` (see gcc.py as template) |
| Change variable substitution | `pcons/core/subst.py` |
| Modify target resolution | `pcons/core/resolver.py` |
| Add compile/link flags | `pcons/tools/toolchain.py` (ToolchainContext) |
| Package management | `pcons/packages/finders/`, `pcons/packages/imported.py` |

## Testing

**Test structure:**
- `tests/core/` - Core system unit tests
- `tests/generators/` - Generator tests
- `tests/toolchains/` - Toolchain tests
- `tests/test_examples.py` - Integration tests running all examples

**Examples** (`examples/`): Each has `pcons-build.py` and `test.toml` with expected outputs and verification commands.

**Fixtures** (conftest.py): `tmp_project`, `sample_c_source`

### Local Windows Testing (Gary's Setup)

For quick Windows testing without CI, use the `tower1` Windows machine via SSH:

```bash
# Sync code to Windows (excludes .git, build, .venv, caches)
rsync -avz --exclude='.git' --exclude='build' --exclude='.venv' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.ruff_cache' \
  --exclude='.mypy_cache' --exclude='.pytest_cache' \
  /Users/garyo/src/pcons/ tower1:/e/src/pcons/

# First time: install dependencies and ninja
ssh tower1 'cd E:/src/pcons; uv sync --all-extras'
ssh tower1 'uv tool install ninja'

# Run unit tests (no PATH changes needed)
ssh tower1 'cd E:/src/pcons; uv run pytest tests/ -x -q --ignore=tests/test_examples.py'

# Run example tests (need ninja in PATH)
ssh tower1 '$env:PATH = "C:\Users\garyo\.local\bin;$env:PATH"; cd E:/src/pcons; uv run pytest tests/test_examples.py -v -k ninja'
```

**Results to expect:**
- Unit tests: ~1100 pass, macOS-specific tests skipped
- Example tests (ninja): ~26 pass, some fail due to `python3` not existing on Windows (it's `python`), some skipped (conan, xcode, makefile generator not Windows-compatible)

**Requirements:**
- SSH access to `tower1` configured in `~/.ssh/config`
- PowerShell profile must not output text for non-interactive sessions (breaks rsync/scp). The profile at `C:\Users\garyo\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1` should wrap any `Write-Host` calls:
  ```powershell
  if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
      Write-Host "# your message here"
  }
  ```
- Windows SDK installed for MSIX testing (MakeAppx.exe is at `C:\Program Files (x86)\Windows Kits\10\bin\...`)
- uv installed on Windows
- clang-cl available (LLVM toolchain) for C/C++ example tests

## Code Conventions

- **Python 3.11+** required
- **uv-first workflow** with PEP723 front matter dependencies
- **Type hints** everywhere (mypy strict mode)
- **SPDX headers**: `# SPDX-License-Identifier: MIT` on all files
- **Private attributes**: `_build_info`, `_tools`, `_vars`
- **Fail fast**: Missing sources, unknown variables, cycles all raise errors immediately

## Common Gotchas

1. **Target nodes empty until resolved**: Always call `project.resolve()` before accessing `target.output_nodes`
2. **Platform suffixes vary**: `.o` (Unix), `.obj` (MSVC) - get from toolchain, don't hardcode
3. **Circular variable refs detected**: `$foo` referencing `$bar` referencing `$foo` raises `CircularReferenceError`
4. **Commands as lists**: Keep commands as `["$cc.cmd", "$flags", ...]` not strings, for proper space handling

## Releasing

To create a new release (e.g., `v0.3.0`):

1. **Update version** in `pcons/__init__.py` (line ~25):
   ```python
   __version__ = "0.3.0"
   ```

2. **Update CHANGELOG.md**:
   - Ensure it has all the release notes for this release (check git)
   - Change `## [Unreleased]` to `## [0.3.0] - YYYY-MM-DD`
   - Add new empty `## [Unreleased]` section at top
   - Update links at bottom:
     ```markdown
     [Unreleased]: https://github.com/DarkStarSystems/pcons/compare/v0.3.0...HEAD
     [0.3.0]: https://github.com/DarkStarSystems/pcons/compare/v0.2.0...v0.3.0
     ```

3. **Commit and push**:
   ```bash
   git add pcons/__init__.py CHANGELOG.md
   git commit -m "Bump version to v0.3.0"
   git push
   ```

4. Use gh to wait for the CI build to complete successfully. If it does:

4. **Tag and push**:
   ```bash
   git tag v0.3.0
   git push && git push --tags
   ```

CI will run tests on all platforms, then automatically create a GitHub release and publish to PyPI.

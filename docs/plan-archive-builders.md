# Plan: Archive Builders (Tarfile and Zipfile) + Target Return Type Consistency

## Goal
1. Add Tarfile and Zipfile builders to pcons for creating tar archives (.tar, .tar.gz, .tar.bz2, .tar.xz) and zip archives (.zip) from a list of input files or directories.
2. Establish a consistent pattern: **all build outputs are Targets**, not raw FileNodes.
3. Fix `env.Command()` to also return `Target` for consistency.
4. Document this pattern in ARCHITECTURE.md.

## Design Principles
1. **Cross-platform**: Use Python's built-in `tarfile`/`zipfile` modules, not shell commands
2. **Consistent return types**: All builders return `Target` objects, enabling:
   - Passing outputs to `Install()` and other builders
   - Proper dependency tracking
   - Future usage requirements if needed
3. **Project-level methods**: Like `project.StaticLibrary()`, use `project.Tarfile()` for consistency

## User-Facing API

```python
from pcons import Project, NinjaGenerator

project = Project("myproject")
env = project.Environment()

# Create a gzipped tarball - returns Target
# Name is auto-derived from output path ("dist/docs" -> "dist/docs")
docs_tar = project.Tarfile(
    env,
    output="dist/docs.tar.gz",
    sources=["docs/", "README.md", "LICENSE"],
    compression="gzip",                   # None, "gzip", "bz2", "xz"
    base_dir=".",                         # Strip this prefix from archive paths
)

# Create a zip archive - returns Target
release_zip = project.Zipfile(
    env,
    output="dist/release.zip",
    sources=["bin/myapp", "lib/libcore.so", "README.md"],
    base_dir="build",
)

# Explicit name if needed for `ninja my_custom_name`
custom = project.Tarfile(
    env,
    output="out.tar.gz",
    sources=["data/"],
    name="my_custom_name",               # Optional explicit name
)

# Archives are Targets, so they work with Install
project.Install("packages/", [docs_tar, release_zip])

project.Default(docs_tar, release_zip)
project.resolve()
NinjaGenerator().generate(project, "build")
```

## Implementation Approach

### Use Python's Built-in Modules via Helper Script

Create a helper script (`pcons/util/archive_helper.py`) that handles archive creation. This is invoked by Ninja during the build:

```bash
python -m pcons.util.archive_helper --type tar --compression gzip --output $out --base-dir . $in
```

**Why this approach:**
- Cross-platform (works on Windows without tar/zip commands)
- Consistent behavior across all platforms
- Handles edge cases (spaces in paths, relative paths)
- Supports all compression formats

### Alternative Approaches Considered

1. **Shell commands (`tar`, `zip`)**: Rejected - not portable to Windows
2. **New Tool class**: Overkill - archives don't need tool configuration
3. **Inline Python in Ninja command**: Fragile with escaping and long file lists

## Files to Create

| File | Purpose |
|------|---------|
| `pcons/util/archive_helper.py` | Standalone script for archive creation |
| `tests/tools/test_archive.py` | Unit tests for archive builders |

## Files to Modify

| File | Change |
|------|--------|
| `pcons/core/project.py` | Add `Tarfile()` and `Zipfile()` methods returning Target |
| `pcons/core/environment.py` | Fix `Command()` to return Target instead of list[FileNode] |
| `pcons/generators/ninja.py` | Add archive rules (tarfile, zipfile) |
| `ARCHITECTURE.md` | Document "all build outputs are Targets" pattern |
| `docs/user-guide.md` | Document archive builders |

## Detailed Implementation

### 1. Helper Script (`pcons/util/archive_helper.py`)

```python
#!/usr/bin/env python3
"""Archive creation helper for pcons builds."""

import argparse
import os
import sys
import tarfile
import zipfile
from pathlib import Path


def create_tarfile(output: Path, files: list[Path], compression: str | None, base_dir: Path) -> None:
    """Create a tar archive."""
    mode = "w"
    if compression == "gzip":
        mode = "w:gz"
    elif compression == "bz2":
        mode = "w:bz2"
    elif compression == "xz":
        mode = "w:xz"

    with tarfile.open(output, mode) as tar:
        for f in files:
            arcname = f.relative_to(base_dir) if f.is_relative_to(base_dir) else f.name
            tar.add(f, arcname=arcname)


def create_zipfile(output: Path, files: list[Path], base_dir: Path) -> None:
    """Create a zip archive."""
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arcname = f.relative_to(base_dir) if f.is_relative_to(base_dir) else f.name
            zf.write(f, arcname=arcname)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create archive files")
    parser.add_argument("--type", choices=["tar", "zip"], required=True)
    parser.add_argument("--compression", choices=["gzip", "bz2", "xz"], default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, default=Path("."))
    parser.add_argument("files", nargs="+", type=Path)

    args = parser.parse_args()

    # Expand directories
    all_files = []
    for f in args.files:
        if f.is_dir():
            all_files.extend(p for p in f.rglob("*") if p.is_file())
        else:
            all_files.append(f)

    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.type == "tar":
        create_tarfile(args.output, all_files, args.compression, args.base_dir)
    else:
        create_zipfile(args.output, all_files, args.base_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 2. Project Methods (`pcons/core/project.py`)

Archive builders are Project methods (like `StaticLibrary`, `SharedLibrary`, `Program`) that return `Target` objects:

```python
def _name_from_output(output: str | Path, strip_suffixes: list[str]) -> str:
    """Derive target name from output path by stripping archive suffixes."""
    name = str(output)
    for suffix in strip_suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name  # e.g., "dist/docs.tar.gz" -> "dist/docs"

def Tarfile(
    self,
    env: Environment,
    *,
    output: str | Path,
    sources: list[str | Path | Node | Target] | None = None,
    compression: str | None = None,  # None, "gzip", "bz2", "xz"
    base_dir: str | Path | None = None,
    name: str | None = None,  # Optional, derived from output if not specified
) -> Target:
    """Create a tar archive from source files/directories.

    Args:
        env: Environment for this build
        output: Output archive path (.tar, .tar.gz, .tar.bz2, .tar.xz)
        sources: Input files, directories, and/or Targets
        compression: Compression type (None, "gzip", "bz2", "xz")
                    If None, inferred from output extension
        base_dir: Base directory for archive paths (default: common prefix)
        name: Optional target name for `ninja <name>`. Derived from output if not specified.

    Returns:
        Target representing the archive.

    Example:
        docs = project.Tarfile(env,
            output="dist/docs.tar.gz",
            sources=["docs/", "README.md"])
        project.Install("packages/", [docs])  # Works because it's a Target
    """
    if name is None:
        name = _name_from_output(output, [".tar.gz", ".tar.bz2", ".tar.xz", ".tar"])
    target = Target(name, self, env, target_type="archive")
    target.sources = sources or []
    target._build_info = {
        "tool": "tarfile",
        "output": str(output),
        "compression": compression,
        "base_dir": str(base_dir) if base_dir else ".",
    }
    self._targets.append(target)
    return target

def Zipfile(
    self,
    env: Environment,
    *,
    output: str | Path,
    sources: list[str | Path | Node | Target] | None = None,
    base_dir: str | Path | None = None,
    name: str | None = None,  # Optional, derived from output if not specified
) -> Target:
    """Create a zip archive from source files/directories.

    Args:
        env: Environment for this build
        output: Output archive path (.zip)
        sources: Input files, directories, and/or Targets
        base_dir: Base directory for archive paths (default: common prefix)
        name: Optional target name for `ninja <name>`. Derived from output if not specified.

    Returns:
        Target representing the archive.

    Example:
        release = project.Zipfile(env,
            output="dist/release.zip",
            sources=["bin/", "lib/", "README.md"])
    """
    if name is None:
        name = _name_from_output(output, [".zip"])
    target = Target(name, self, env, target_type="archive")
    target.sources = sources or []
    target._build_info = {
        "tool": "zipfile",
        "output": str(output),
        "base_dir": str(base_dir) if base_dir else ".",
    }
    self._targets.append(target)
    return target
```

### 3. Fix `env.Command()` to Return Target

Currently `env.Command()` returns `list[FileNode]`. For consistency, it should return `Target`:

**Current API (to be changed):**
```python
# Returns list[FileNode]
generated = env.Command(
    "generated.h",
    "schema.json",
    "python generate.py $SOURCE > $TARGET"
)
```

**New API:**
```python
# Returns Target - name derived from first target file
generated = env.Command(
    target="generated.h",            # Output file(s)
    source="schema.json",            # Input file(s)
    command="python generate.py $SOURCE > $TARGET"
)
# Can now be passed to Install
project.Install("include/", [generated])

# Explicit name if needed
generated = env.Command(
    target="out/generated.h",
    source="schema.json",
    command="...",
    name="gen_header",               # Optional explicit name
)
```

**Implementation changes in `pcons/core/environment.py`:**
```python
def Command(
    self,
    *,
    target: str | Path | list[str | Path],
    source: str | Path | list[str | Path] | None = None,
    command: str,
    name: str | None = None,  # Optional, derived from first target if not specified
) -> Target:
    """Run a shell command to produce target files.

    Args:
        target: Output file(s)
        source: Input file(s)
        command: Shell command with $SOURCE, $TARGET, etc. substitution
        name: Optional target name for `ninja <name>`. Derived from first target if not specified.

    Returns:
        Target object (not list[FileNode]).
    """
    targets_list = target if isinstance(target, list) else [target]
    if name is None:
        # Derive name from first target, stripping path
        name = Path(targets_list[0]).stem

    t = Target(name, self._project, self, target_type="command")
    t._pending_sources = source if isinstance(source, list) else [source] if source else []
    t._build_info = {
        "tool": "command",
        "command": command,
        "targets": [str(p) for p in targets_list],
    }
    self._project._targets.append(t)
    return t
```

**Breaking change note:** This changes the return type and signature. Existing code using positional args or `env.Command()[0]` will break. Consider:
- Adding a deprecation warning for the old signature
- Or bumping to 0.2.0 for the breaking change

### 4. Ninja Generator Rules

Add handling in `_ensure_rule()` for archive tools:

```python
elif tool_name == "tarfile":
    compression_flag = ""
    if build_info.get("compression"):
        compression_flag = f"--compression {build_info['compression']}"
    base_dir = build_info.get("base_dir", ".")
    command = f"python -m pcons.util.archive_helper --type tar {compression_flag} --output $out --base-dir {base_dir} $in"
    description = "TAR $out"

elif tool_name == "zipfile":
    base_dir = build_info.get("base_dir", ".")
    command = f"python -m pcons.util.archive_helper --type zip --output $out --base-dir {base_dir} $in"
    description = "ZIP $out"
```

## Directory Handling

When a directory is specified as a source:
1. At **configuration time**: Expand to list of files using `rglob("*")`
2. Store expanded list in build info for Ninja
3. Archive helper handles the actual archiving

This ensures Ninja tracks dependencies on individual files, not directories.

## Test Strategy

### Unit Tests (`tests/tools/test_archive.py`)
- Test Tarfile with various compression types
- Test Zipfile creation
- Test source expansion (files and directories)
- Test base_dir path stripping
- Test compression auto-detection from extension

### Integration Test
- Create actual archives and verify contents
- Test with mixed files and directories
- Verify cross-platform behavior

## Implementation Sequence

1. **Phase 1: Core Infrastructure**
   - Create `pcons/util/archive_helper.py`
   - Add `Tarfile()` and `Zipfile()` to Project (returning Target)
   - Add archive rules to Ninja generator

2. **Phase 2: Fix `env.Command()`**
   - Change return type from `list[FileNode]` to `Target`
   - Update signature: add `name` parameter, use keyword args for targets/sources/command
   - Update existing tests
   - Update examples that use `Command()`

3. **Phase 3: Testing**
   - Create unit tests for archive builders
   - Test archives can be passed to Install
   - Test on macOS, Linux, Windows

4. **Phase 4: Documentation**
   - Add "All Build Outputs Are Targets" section to ARCHITECTURE.md
   - Add archive builders to user guide
   - Update CHANGELOG (note breaking change to `env.Command()`)
   - Consider version bump to 0.2.0 for the breaking change

## Key Design Decisions

1. **Project methods returning Target**: For consistency with `StaticLibrary`, `SharedLibrary`, `Program`, etc. All build outputs should be Targets so they can be passed to `Install()` and other builders.
2. **Python modules, not shell commands**: Cross-platform compatibility
3. **Helper script, not inline Python**: Better maintainability, handles edge cases
4. **Expand directories at config time**: Proper Ninja dependency tracking
5. **Auto-detect compression from extension**: Convenient for common cases
6. **Fix `env.Command()` to return Target**: Part of the overall consistency push

## ARCHITECTURE.md Updates

Add a new section documenting the "all build outputs are Targets" pattern:

```markdown
### All Build Outputs Are Targets

**Design principle:** Every builder that creates output files should return a `Target` object, not raw `FileNode`s or `list[FileNode]`.

**Why this matters:**
- **Consistency**: Users can pass any build output to `Install()`, other builders, etc.
- **Dependency tracking**: Targets participate in the dependency resolution system
- **Future extensibility**: Targets can have usage requirements if needed later

**Correct pattern:**
```python
# Project methods return Target
lib = project.StaticLibrary("mylib", env, sources=["lib.c"])
archive = project.Tarfile("archive", env, sources=["docs/"])
generated = env.Command("gen", targets="out.h", sources="in.txt", command="...")

# All can be used uniformly
project.Install("dist/", [lib, archive, generated])
```

**Historical note:** Early versions of `env.Command()` returned `list[FileNode]` for simplicity. This was fixed in v0.2.0 to return `Target` for consistency.

**Implementation guideline:** When adding new builders:
1. Create a `Target` object with appropriate `target_type`
2. Store build info in `target._build_info`
3. Register with `project._targets.append(target)`
4. Return the `Target`, not the output nodes
```

## Enforcement Considerations

To prevent future inconsistencies, consider:

1. **Type checking**: Static type hints make it clear what should be returned
2. **Documentation**: Clear pattern documentation in ARCHITECTURE.md
3. **Code review**: Catch non-Target returns in PR review
4. **Runtime check (optional)**: Could add a test that scans all builder methods and verifies return types

For now, documentation + type hints + code review should be sufficient. Runtime enforcement may be overkill.

# Multi-Toolchain Support Plan

## Overview

This document outlines the plan for implementing multi-toolchain support in pcons,
allowing an Environment to have multiple toolchains (e.g., C++ + CUDA, or C++ + Fortran).

## Current State Analysis

### Environment (`pcons/core/environment.py`)
- Single `_toolchain` attribute stores the toolchain passed to the constructor
- `has_tool()` only checks `_tools` dict, not toolchain capabilities
- `set_variant()` only applies to the single toolchain
- `clone()` copies the single `_toolchain` reference

### Resolver (`pcons/core/resolver.py`)
- `ObjectNodeFactory.get_source_handler()` only queries `env._toolchain`
- `ObjectNodeFactory.get_object_path()` only uses `env._toolchain.get_object_suffix()`
- `OutputNodeFactory` methods only use `env._toolchain` for naming conventions
- `_determine_language()` only queries the single toolchain for source handlers

### Requirements (`pcons/core/requirements.py`)
- `_get_primary_tool()` only queries `env._toolchain` for source handlers

### Toolchain Base (`pcons/tools/toolchain.py`)
- `BaseToolchain.get_linker_for_languages()` determines linker based on language priorities
- Language priorities are defined in `DEFAULT_LANGUAGE_PRIORITY`
- This logic can work across multiple toolchains if we aggregate languages properly

### CUDA Toolchain (`pcons/toolchains/cuda.py`)
- Good example of a toolchain designed for use alongside C/C++ toolchains
- Handles `.cu` files with the "cuda" tool
- `_linker_for_language()` returns "cuda" for CUDA, delegates to base for others
- Documentation explicitly mentions using `env.add_toolchain(cuda)` (the API we're implementing)

## Design Decisions

### 1. Toolchain Storage

**Decision**: Keep `_toolchain` as the primary toolchain, add `_additional_toolchains: list[Toolchain]`.

**Rationale**:
- Maintains backward compatibility - existing code using single toolchain works unchanged
- The primary toolchain (from constructor) has highest precedence for naming, object suffix, etc.
- Additional toolchains only provide source handlers and tools

### 2. Source Handler Resolution Order

**Decision**: Check primary toolchain first, then additional toolchains in order added.

**Rationale**:
- Predictable behavior - order matches how toolchains were added
- Allows users to override handlers by adding a toolchain earlier
- First match wins (similar to PATH resolution)

### 3. Linker Selection

**Decision**: Aggregate languages across all toolchains, use highest-priority language's linker.

**Rationale**:
- When mixing C and CUDA, CUDA typically needs its own linker or the host compiler
- When mixing C and Fortran, Fortran linker is needed for runtime libraries
- The existing `get_linker_for_languages()` logic already handles this correctly

### 4. Object Suffix and Output Naming

**Decision**: Primary toolchain provides object suffix and output naming conventions.

**Rationale**:
- Mixed-language builds should have consistent object file naming
- The primary toolchain (usually C/C++) defines the platform conventions
- CUDA produces standard `.o` files, compatible with C/C++ toolchain

### 5. Variant Application

**Decision**: `set_variant()` applies to all toolchains that have tools in the environment.

**Rationale**:
- Debug/release settings should apply consistently across all compilers
- Each toolchain's `apply_variant()` only affects tools it knows about

## Implementation Plan

### Phase 1: Environment Changes

```python
# In Environment.__init__():
self._toolchain = toolchain  # Primary toolchain (unchanged)
self._additional_toolchains: list[Toolchain] = []

# New method:
def add_toolchain(self, toolchain: Toolchain) -> None:
    """Add an additional toolchain to this environment.

    Additional toolchains provide extra source handlers and tools.
    The primary toolchain (from constructor) has precedence for
    output naming conventions.

    Args:
        toolchain: Toolchain to add.

    Example:
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_toolchain(cuda_toolchain)  # Adds CUDA support
    """
    self._additional_toolchains.append(toolchain)
    toolchain.setup(self)

# New property:
@property
def toolchains(self) -> list[Toolchain]:
    """Return all toolchains (primary + additional)."""
    result: list[Toolchain] = []
    if self._toolchain is not None:
        result.append(self._toolchain)
    result.extend(self._additional_toolchains)
    return result

# Update clone():
def clone(self) -> Environment:
    # ... existing code ...
    new_env._toolchain = self._toolchain
    new_env._additional_toolchains = list(self._additional_toolchains)
    return new_env

# Update set_variant():
def set_variant(self, name: str, **kwargs: Any) -> None:
    for toolchain in self.toolchains:
        toolchain.apply_variant(self, name, **kwargs)
    # Still set variant name even if no toolchains
    if not self.toolchains:
        self.variant = name
```

### Phase 2: Resolver Changes

```python
# In ObjectNodeFactory.get_source_handler():
def get_source_handler(
    self, source: Path, env: Environment
) -> SourceHandler | None:
    """Get source handler from any of the environment's toolchains."""
    # Check all toolchains in order (primary first, then additional)
    for toolchain in env.toolchains:
        handler = toolchain.get_source_handler(source.suffix)
        if handler is not None:
            if env.has_tool(handler.tool_name):
                return handler
            else:
                logger.warning(
                    "Tool '%s' required for '%s' files is not available...",
                    handler.tool_name,
                    source.suffix,
                )
    return None

# In Resolver._determine_language():
def _determine_language(self, target: Target, env: Environment) -> str | None:
    """Determine the primary language for a target."""
    languages: set[str] = set()

    for source in target.sources:
        if isinstance(source, FileNode):
            # Try all toolchains
            for toolchain in env.toolchains:
                handler = toolchain.get_source_handler(source.path.suffix)
                if handler:
                    languages.add(handler.language)
                    break  # First handler wins for this source
            else:
                # Fallback to hardcoded suffixes
                # ... existing fallback code ...

    # Return highest priority language
    # ... existing priority logic ...
```

### Phase 3: Requirements Changes

```python
# In _get_primary_tool():
def _get_primary_tool(target: Target, env: Environment) -> str | None:
    """Determine the primary compilation tool for a target."""
    # ... existing language checks ...

    # Try all toolchains
    from pcons.core.node import FileNode

    for source in target.sources:
        if isinstance(source, FileNode):
            for toolchain in env.toolchains:
                handler = toolchain.get_source_handler(source.path.suffix)
                if handler is not None:
                    return handler.tool_name

    # ... existing fallback code ...
```

### Phase 4: Tests

Create `/Users/garyo/src/pcons/tests/core/test_multi_toolchain.py`:

```python
"""Tests for multi-toolchain support."""

class TestMultiToolchainEnvironment:
    def test_add_toolchain(self):
        """Test adding additional toolchains."""

    def test_toolchains_property_returns_all(self):
        """Test that toolchains property includes primary and additional."""

    def test_clone_preserves_additional_toolchains(self):
        """Test that clone copies additional toolchains."""

    def test_set_variant_applies_to_all_toolchains(self):
        """Test that set_variant calls apply_variant on all toolchains."""

class TestMultiToolchainResolver:
    def test_source_goes_to_correct_compiler(self):
        """Test that .cu files use nvcc, .cpp files use g++."""

    def test_first_toolchain_wins_for_conflicts(self):
        """Test that primary toolchain's handler takes precedence."""

    def test_linker_selection_with_multiple_languages(self):
        """Test correct linker selection when mixing C++ and CUDA."""

class TestCppPlusCuda:
    def test_mixed_cpp_cuda_sources(self):
        """Integration test: build target with both .cpp and .cu files."""
```

## Migration Path

1. Existing single-toolchain code continues to work unchanged
2. New `add_toolchain()` method is purely additive
3. The `toolchains` property provides a unified way to access all toolchains

## Edge Cases

1. **No toolchain**: `toolchains` returns empty list, resolver falls back to hardcoded handlers
2. **Conflicting source handlers**: First toolchain's handler wins (primary first)
3. **Missing tool**: Warning logged, source skipped (existing behavior)
4. **Multiple toolchains with same tool name**: First `setup()` wins, subsequent calls to `add_tool()` return existing tool

## Testing Strategy

1. Unit tests for each new method/property
2. Integration tests for common combinations (C++ + CUDA)
3. Regression tests for single-toolchain behavior
4. Test linker selection with various language combinations

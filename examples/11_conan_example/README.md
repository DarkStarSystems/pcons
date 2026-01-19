# Conan Package Example

This example demonstrates how to use Conan 2.x packages with pcons.

## Key Pattern: Applying Package Settings

When using packages from Conan (or any package finder), always apply all
settings from the `PackageDescription`:

```python
def apply_package_to_env(pkg, env):
    """Apply a package's compile/link settings to an environment."""
    # Compile settings
    for inc_dir in pkg.include_dirs:
        env.cxx.includes.append(str(inc_dir))
    for define in pkg.defines:          # <-- Important! Don't forget defines
        env.cxx.defines.append(define)
    for flag in pkg.compile_flags:
        env.cxx.flags.append(flag)

    # Link settings
    for lib_dir in pkg.library_dirs:
        env.link.libdirs.append(str(lib_dir))
    for lib in pkg.libraries:
        env.link.libs.append(lib)
    for flag in pkg.link_flags:
        env.link.flags.append(flag)
```

The `defines` field is especially important - many packages require specific
preprocessor definitions to work correctly. For example:
- `fmt` may require `FMT_HEADER_ONLY=1`
- `spdlog` may require `SPDLOG_FMT_EXTERNAL`

These defines are automatically extracted from the package's `.pc` file
`Cflags` field by `PkgConfigFinder`.

## Requirements

- Conan 2.x (or `uvx conan` will be used automatically)

## Usage

```bash
# Generate and build
uvx pcons

# Run
./build/hello_fmt
```

## Files

- `conanfile.txt` - Conan dependencies (fmt library)
- `pcons-build.py` - pcons build script showing the pattern
- `src/main.cpp` - Simple program using fmt

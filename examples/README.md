# Pcons Example Projects

This directory contains standalone example projects that serve two purposes:

1. **End-to-end tests** - Each project is built and verified during testing
2. **User documentation** - Examples of how to use pcons for various scenarios

## Directory Structure

Each example is a self-contained directory:

```
example_name/
├── pcons-build.py    # Pcons build script
├── test.toml         # Test configuration (what to verify)
├── src/              # Source files
│   └── ...
└── expected/         # (optional) Expected output files for comparison
```

## Test Configuration (test.toml)

Each example has a `test.toml` that describes how to test it:

```toml
[test]
description = "Brief description of what this example demonstrates"

# Files that should exist after build
expected_outputs = [
    "build/output.txt",
    "build/program",
]

# Optional: commands to run to verify the build
[verify]
commands = [
    { run = "build/program", expect_stdout = "Hello, World!" },
]

# Optional: skip conditions
[skip]
platforms = ["windows"]  # Skip on these platforms
requires = ["gcc"]       # Skip if these tools aren't available
```

## Running Examples

Examples are run as part of the test suite:

```bash
# Run all example tests
uv run pytest tests/test_examples.py -v

# Run a specific example (matches against its directory name)
uv run pytest tests/test_examples.py -v -k "01_hello_c"
```

## Adding New Examples

1. Create a new directory under `examples/`, named `NN_short_description` (see
   "Numbering" below for picking `NN`)
2. Add source files and a `pcons-build.py`
3. Add a `test.toml` describing expected outputs
4. Run the tests to verify it works

Examples should be simple and focused - each demonstrating one concept or use case.

### Numbering

Directories are prefixed with a two-digit number (`01_hello_c`, `02_multi_file`,
...) that roughly reflects the order examples were introduced, not a strict
ordering pcons relies on. A few prefixes were reused by mistake as examples
were added independently (`30_cxx_partitions`/`30_restat`,
`31_cxx_modules_optin`/`31_pkg_config`, `32_cxx_import_std`/`32_latex`). This is
harmless — tests key off the full directory name, not the numeric prefix — so
these are left as-is rather than renumbered. When adding a new example, just
pick the next unused number after the highest one currently in `examples/`.

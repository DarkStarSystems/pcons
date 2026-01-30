# How to Contribute to Pcons

Pcons welcomes contributions from the community.

**Requirements:** Python 3.11+ and [uv](https://docs.astral.sh/uv/)

## Setting Up Your Fork

1. On GitHub, click the `Fork` button to create your own fork
2. Clone your fork: `git clone git@github.com:YOUR_USERNAME/pcons.git`
3. Enter the directory: `cd pcons`
4. Add upstream remote: `git remote add upstream https://github.com/DarkStarSystems/pcons`

## Setting Up the Development Environment

```bash
# Install dependencies and create virtual environment
uv sync

# Verify the setup
uv run pcons --help
uv run pytest
```

## Development Workflow

### Create a Branch

```bash
git checkout -b my_contribution
```

### Run Tests

```bash
uv run pytest
```

### Run Linter and Type Checker

```bash
# Run all checks
make lint

# Or run individually
uv run ruff check pcons/ tests/
uv run mypy pcons/
```

### Format Code

```bash
# Auto-format
make fmt

# Or run directly
uv run ruff format pcons/ tests/
```

### Run Tests with Coverage

```bash
uv run pytest --cov=pcons --cov-report=html
```

## Commit Guidelines

This project uses [conventional commit messages](https://www.conventionalcommits.org/en/v1.0.0/).

Examples:
- `fix(resolver): handle empty source lists`
- `feat(toolchain): add LLVM/Clang support`
- `docs: update README examples`
- `test: add coverage for Windows paths`

## Submitting a Pull Request

1. Push your branch: `git push origin my_contribution`
2. On GitHub, open a Pull Request against `main`
3. Wait for CI to pass and a maintainer to review

## Makefile Targets

```bash
make help    # Show available targets
make lint    # Run linters (ruff, mypy)
make fmt     # Format code with ruff
make test    # Run tests with pytest
```

## License

By contributing to pcons, you agree that your contributions will be licensed under the MIT License.

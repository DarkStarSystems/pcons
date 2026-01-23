.PHONY: help
help:             ## Show the help.
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@fgrep "##" Makefile | fgrep -v fgrep

.PHONY: show
show:             ## Show the current environment.
	@echo "Current environment:"
	@uv run python -V
	@uv run python -m site

.PHONY: install
install:          ## Install the project in dev mode.
	uv sync

.PHONY: install-hooks
install-hooks:    ## Install git pre-commit hooks.
	cp scripts/pre-commit .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit
	@echo "Pre-commit hook installed."

.PHONY: fmt
fmt:              ## Format code using ruff.
	uv run ruff format pcons/ tests/ examples/
	uv run ruff check --fix pcons/ tests/ examples/

.PHONY: lint
lint:             ## Run ruff and ty linters.
	uv run ruff check pcons/ tests/ examples/
	uv run ruff format --check pcons/ tests/ examples/
	uvx ty check pcons/ examples/

.PHONY: test
test:             ## Run tests.
	uv run pytest

.PHONY: test-cov
test-cov:         ## Run tests with coverage report.
	uv run pytest --cov=pcons --cov-report=html --cov-report=xml
	@echo "Coverage report: htmlcov/index.html"

.PHONY: watch
watch:            ## Run tests on every change.
	ls pcons/**/*.py tests/**/*.py | entr uv run pytest -x

.PHONY: clean
clean:            ## Clean unused files.
	@find ./ -name '*.pyc' -exec rm -f {} \;
	@find ./ -name '__pycache__' -exec rm -rf {} \;
	@find ./ -name 'Thumbs.db' -exec rm -f {} \;
	@find ./ -name '*~' -exec rm -f {} \;
	@rm -rf .cache
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf .ruff_cache
	@rm -rf build
	@rm -rf dist
	@rm -rf *.egg-info
	@rm -rf htmlcov
	@rm -rf .tox/
	@rm -rf docs/_build

.PHONY: docs
docs:             ## Build the documentation.
	@echo "building documentation ..."
	cd docs && uv run python pcons-build.py
	@open docs/build/index.html || xdg-open docs/build/index.html

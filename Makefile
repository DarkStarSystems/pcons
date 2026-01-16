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

.PHONY: fmt
fmt:              ## Format code using ruff.
	uv run ruff format pcons/ tests/
	uv run ruff check --fix pcons/ tests/

.PHONY: lint
lint:             ## Run ruff and mypy linters.
	uv run ruff check pcons/ tests/
	uv run ruff format --check pcons/ tests/
	uv run mypy pcons/

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
	uv run mkdocs build
	@open site/index.html || xdg-open site/index.html

.PHONY: release
release:          ## Create a new tag for release.
	@echo "WARNING: This operation will create a version tag and push to github"
	@read -p "Version? (provide the next x.y.z semver) : " TAG && \
	echo "Creating release for version $${TAG}" && \
	git add -A && \
	git commit -m "release: version $${TAG}" && \
	git tag "v$${TAG}" && \
	git push -u origin HEAD --tags
	@echo "Github Actions will detect the new tag and release the new version."

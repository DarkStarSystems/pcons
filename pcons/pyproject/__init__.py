# SPDX-License-Identifier: MIT
"""PEP 517 build backend for pcons.

Allows using pcons as the build system for Python packages with native
extensions, by setting in pyproject.toml:

    [build-system]
    requires = ["pcons"]
    build-backend = "pcons.pyproject"
"""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import sys
import sysconfig
import zipfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_pyproject(source_dir: Path) -> dict[str, Any]:
    """Load and return the full pyproject.toml as a dict."""
    import tomllib

    pyproject = source_dir / "pyproject.toml"
    if not pyproject.exists():
        raise FileNotFoundError(f"pyproject.toml not found in {source_dir}")

    with open(pyproject, "rb") as f:
        return tomllib.load(f)


# PEP 621 [project] fields the backend renders into METADATA.
# Any other non-dynamic field present in [project]
# is rejected rather than silently dropped,
# so a package never installs with metadata that quietly lost its
# dependencies, entry points, license, etc.
_HONORED_PROJECT_FIELDS = frozenset(
    {"name", "version", "requires-python", "dependencies"}
)


def _render_metadata(name: str, version: str, project: dict[str, Any]) -> str:
    """Render the wheel METADATA file from the ``[project]`` table.

    Honors ``Name``, ``Version``, ``Requires-Python`` and ``Requires-Dist``.
    PEP 621 requires the backend to honor every non-dynamic ``[project]`` field,
    so any other field present (``description``, ``readme``, ``license``,
    ``authors``, ``optional-dependencies``, entry points, ...) raises instead of
    being silently dropped.
    """
    unsupported = sorted(
        field
        for field, value in project.items()
        if field not in _HONORED_PROJECT_FIELDS and value
    )
    if unsupported:
        raise RuntimeError(
            "pcons build backend cannot yet honor these pyproject [project] "
            f"fields and refuses to drop them silently: {', '.join(unsupported)}. "
            "Remove them, or add support in pcons.pyproject."
        )

    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    requires_python = project.get("requires-python")
    if requires_python:
        lines.append(f"Requires-Python: {requires_python}")
    for dep in project.get("dependencies", []):
        lines.append(f"Requires-Dist: {dep}")
    return "\n".join(lines) + "\n"


# Directories never shipped in an sdist: build outputs, VCS data and tooling
# caches. Matched by name at any depth, so e.g. a nested __pycache__ is skipped.
_SDIST_EXCLUDE_DIRS = frozenset(
    {
        "build",
        "dist",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
        "node_modules",
    }
)


def _sdist_files(source_dir: Path) -> list[Path]:
    """Return every source file to ship in the sdist, recursively.

    Walks the whole project tree (so package subdirectories like ``src/`` are
    included, not just top-level files) and skips build artifacts, VCS data and
    tooling caches listed in :data:`_SDIST_EXCLUDE_DIRS`.
    """
    files = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        if any(part in _SDIST_EXCLUDE_DIRS for part in rel.parts):
            continue
        files.append(path)
    return sorted(files)


def _wheel_tag() -> tuple[str, str, str]:
    """Return (python_tag, abi_tag, platform_tag) for the running interpreter."""
    vi = sys.version_info
    python_tag = f"cp{vi.major}{vi.minor}"
    abi_tag = python_tag
    platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return python_tag, abi_tag, platform_tag


def _sha256_record(data: bytes) -> str:
    digest = (
        base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    )
    return f"sha256={digest}"


def _run_pcons(
    source_dir: Path,
    build_dir: Path,
    variant: str | None = None,
    variables: dict[str, str] | None = None,
) -> None:
    """Run pcons-build.py via pcons.cli.run_script (in-process)."""
    from pcons.cli import run_script

    build_script = source_dir / "pcons-build.py"
    if not build_script.exists():
        raise FileNotFoundError(f"pcons-build.py not found in {source_dir}")

    exit_code, _ = run_script(
        build_script, build_dir, variables=variables, variant=variant
    )
    if exit_code != 0:
        raise RuntimeError(f"pcons-build.py exited with code {exit_code}")


def _run_ninja(build_dir: Path, targets: list[str] | None = None) -> None:
    """Run ninja in *build_dir* via pcons.cli.run_ninja.

    If *targets* is given, only those ninja targets are built (e.g. an
    ``install`` alias), otherwise everything is built.
    """
    from pcons.cli import run_ninja

    exit_code = run_ninja(build_dir, targets=targets)
    if exit_code != 0:
        raise RuntimeError(f"ninja exited with code {exit_code}")


def _write_wheel(
    wheel_path: Path,
    name: str,
    version: str,
    files: list[Path],
    root: Path,
    metadata: str,
    python_tag: str,
    abi_tag: str,
    platform_tag: str,
) -> None:
    """Create the .whl file (a zip) at *wheel_path*.

    *root* is the staging directory that serves as the site-packages image, the structure is preserved.
    *metadata* is the rendered dist-info/METADATA text (see :func:`_render_metadata`).
    """
    dist_info = f"{name}-{version}.dist-info"

    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: pcons\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}\n"
    )
    pkg_metadata = metadata

    # (arcname, hash, size) for RECORD
    record: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            data = file.read_bytes()
            arcname = file.relative_to(root).as_posix()
            zf.writestr(arcname, data)
            record.append((arcname, _sha256_record(data), len(data)))

        # dist-info/WHEEL
        wheel_meta_bytes = wheel_meta.encode()
        arcname = f"{dist_info}/WHEEL"
        zf.writestr(arcname, wheel_meta_bytes)
        record.append(
            (arcname, _sha256_record(wheel_meta_bytes), len(wheel_meta_bytes))
        )

        # dist-info/METADATA
        pkg_meta_bytes = pkg_metadata.encode()
        arcname = f"{dist_info}/METADATA"
        zf.writestr(arcname, pkg_meta_bytes)
        record.append((arcname, _sha256_record(pkg_meta_bytes), len(pkg_meta_bytes)))

        # dist-info/RECORD (no hash for the record file itself)
        record_lines = [f"{arc},{h},{sz}" for arc, h, sz in record]
        record_lines.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(record_lines) + "\n")


def _write_editable_wheel(
    wheel_path: Path,
    name: str,
    version: str,
    build_dir: Path,
    metadata: str,
    python_tag: str,
    abi_tag: str,
    platform_tag: str,
) -> None:
    """Create an editable wheel containing only a .pth file pointing at build_dir.

    When pip installs this wheel, the .pth file is placed in site-packages and
    processed by Python's site module, which adds build_dir to sys.path.
    Imports then resolve directly to the compiled extensions in build_dir, so
    re-running ninja is enough to pick up rebuilt extensions without reinstalling.

    *metadata* is the rendered dist-info/METADATA text (see :func:`_render_metadata`).
    """
    dist_info = f"{name}-{version}.dist-info"
    pth_name = f"_{name}_editable.pth"
    pth_content = str(build_dir.resolve()) + "\n"

    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: pcons\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}\n"
    )
    pkg_metadata = metadata

    record: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        pth_bytes = pth_content.encode()
        zf.writestr(pth_name, pth_bytes)
        record.append((pth_name, _sha256_record(pth_bytes), len(pth_bytes)))

        wheel_meta_bytes = wheel_meta.encode()
        arcname = f"{dist_info}/WHEEL"
        zf.writestr(arcname, wheel_meta_bytes)
        record.append(
            (arcname, _sha256_record(wheel_meta_bytes), len(wheel_meta_bytes))
        )

        pkg_meta_bytes = pkg_metadata.encode()
        arcname = f"{dist_info}/METADATA"
        zf.writestr(arcname, pkg_meta_bytes)
        record.append((arcname, _sha256_record(pkg_meta_bytes), len(pkg_meta_bytes)))

        record_lines = [f"{arc},{h},{sz}" for arc, h, sz in record]
        record_lines.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(record_lines) + "\n")


# ---------------------------------------------------------------------------
# PEP 517 hooks
# ---------------------------------------------------------------------------

__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
]


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return extra requirements needed to build the wheel."""
    return []


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    return []


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    return []


def _prepare_metadata(metadata_directory: str, *, editable: bool) -> str:
    source_dir = Path.cwd()
    meta_dir = Path(metadata_directory)

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name = str(project.get("name", "unknown")).replace("-", "_")
    version = str(project.get("version", "0.0.1"))

    python_tag, abi_tag, platform_tag = _wheel_tag()
    tag = f"{python_tag}-{abi_tag}-{platform_tag}"
    purelib = "true" if editable else "false"

    dist_info_name = f"{name}-{version}.dist-info"
    dist_info_dir = meta_dir / dist_info_name
    dist_info_dir.mkdir(parents=True, exist_ok=True)

    (dist_info_dir / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: pcons\n"
        f"Root-Is-Purelib: {purelib}\n"
        f"Tag: {tag}\n"
    )
    (dist_info_dir / "METADATA").write_text(_render_metadata(name, version, project))

    return dist_info_name


def _build(wheel_directory: str, *, editable: bool) -> str:
    source_dir = Path.cwd()
    wheel_dir = Path(wheel_directory)
    build_dir = source_dir / "build"

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name = str(project.get("name", "unknown")).replace("-", "_")
    version = str(project.get("version", "0.0.1"))

    pcons_cfg = pyproject.get("tool", {}).get("pcons", {})
    variant = pcons_cfg.get("variant")
    variables = dict(pcons_cfg.get("variables") or {})
    # Ninja target (alias) that stages the files to package into the wheel.
    install_target = str(pcons_cfg.get("install-target", "wheel"))

    python_tag, abi_tag, platform_tag = _wheel_tag()
    wheel_name = f"{name}-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl"
    wheel_dir.mkdir(parents=True, exist_ok=True)

    # Render (and validate) metadata up front so an unsupported [project] field
    # fails the build before any compilation happens.
    metadata = _render_metadata(name, version, project)

    if editable:
        _run_pcons(source_dir, build_dir, variant=variant, variables=variables or None)
        _run_ninja(build_dir)
        _write_editable_wheel(
            wheel_dir / wheel_name,
            name,
            version,
            build_dir,
            metadata,
            python_tag,
            abi_tag,
            platform_tag,
        )
    else:
        # Install the project into a clean staging directory, then package the
        # extension modules and stubs that land there.  Pointing
        # PCONS_INSTALL_PREFIX at the staging dir makes the project's Install
        # targets copy their outputs into it; running the install-target alias
        # builds and stages exactly what belongs in the wheel.
        staging_dir = build_dir / ".wheel-staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        variables["PCONS_INSTALL_PREFIX"] = str(staging_dir)
        # Signal the build script that this is a wheel build so it can lay out
        # its Install() targets as the site-packages image,
        # eg.: install to the root of the prefix rather than the usual bin/lib convention.
        variables["PCONS_BUILD_WHEEL"] = "1"

        _run_pcons(source_dir, build_dir, variant=variant, variables=variables)
        _run_ninja(build_dir, targets=[install_target])

        # The staging directory IS the wheel payload: package everything the
        # install target put there (the extension(s), stubs, and any dependent
        # shared libraries it pulled in), not just files matching a pattern.
        staged = sorted(p for p in staging_dir.rglob("*") if p.is_file())

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")

        if ext_suffix and not any(p.name.endswith(ext_suffix) for p in staged):
            raise RuntimeError(
                f"No extension modules (with suffix {ext_suffix!r})"
                f" found in staging directory {staging_dir} after building"
                f" install-target {install_target!r}"
            )
        _write_wheel(
            wheel_dir / wheel_name,
            name,
            version,
            staged,
            staging_dir,
            metadata,
            python_tag,
            abi_tag,
            platform_tag,
        )

    return wheel_name


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Write .dist-info directory without building the wheel."""
    return _prepare_metadata(metadata_directory, editable=False)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel and return its filename."""
    return _build(wheel_directory, editable=False)


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Write .dist-info for the editable wheel (pure-Python tag, purelib root)."""
    return _prepare_metadata(metadata_directory, editable=True)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel.

    Compiles extensions into build_dir, then installs a .pth file that adds
    build_dir to sys.path.  Re-running ninja in the build directory is enough
    to pick up rebuilt extensions without reinstalling.
    """
    return _build(wheel_directory, editable=True)


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build a source distribution (tarball) and return its filename."""
    import tarfile

    source_dir = Path.cwd()
    sdist_dir = Path(sdist_directory)

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name = str(project.get("name", "unknown")).replace("-", "_")
    version = str(project.get("version", "0.0.1"))

    sdist_name = f"{name}-{version}.tar.gz"
    sdist_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{name}-{version}"
    # PKG-INFO uses the core-metadata format, same content as the wheel METADATA.
    pkg_info = _render_metadata(name, version, project).encode()
    files = _sdist_files(source_dir)

    with tarfile.open(sdist_dir / sdist_name, "w:gz") as tf:
        # The sdist spec requires a PKG-INFO at the root of the tree.
        info = tarfile.TarInfo(f"{prefix}/PKG-INFO")
        info.size = len(pkg_info)
        tf.addfile(info, io.BytesIO(pkg_info))

        for f in files:
            arcname = f"{prefix}/{f.relative_to(source_dir).as_posix()}"
            tf.add(f, arcname=arcname)

    return sdist_name

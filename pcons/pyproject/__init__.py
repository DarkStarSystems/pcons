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


def _find_extensions(build_dir: Path) -> list[Path]:
    """Glob for compiled extension modules under *build_dir*."""
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    return sorted(build_dir.rglob(f"*{ext_suffix}"))


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


def _run_ninja(build_dir: Path) -> None:
    """Run ninja in *build_dir* via pcons.cli.run_ninja."""
    from pcons.cli import run_ninja

    exit_code = run_ninja(build_dir)
    if exit_code != 0:
        raise RuntimeError(f"ninja exited with code {exit_code}")


def _write_wheel(
    wheel_path: Path,
    name: str,
    version: str,
    extensions: list[Path],
    python_tag: str,
    abi_tag: str,
    platform_tag: str,
) -> None:
    """Create the .whl file (a zip) at *wheel_path*."""
    dist_info = f"{name}-{version}.dist-info"

    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: pcons\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}\n"
    )
    pkg_metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"

    # (arcname, hash, size) for RECORD
    record: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Extension modules
        for ext in extensions:
            data = ext.read_bytes()
            arcname = ext.name
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


# ---------------------------------------------------------------------------
# PEP 517 hooks
# ---------------------------------------------------------------------------


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


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Write .dist-info directory without building the wheel."""
    source_dir = Path.cwd()
    meta_dir = Path(metadata_directory)

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name = str(project.get("name", "unknown")).replace("-", "_")
    version = str(project.get("version", "0.0.1"))
    python_tag, abi_tag, platform_tag = _wheel_tag()

    dist_info_name = f"{name}-{version}.dist-info"
    dist_info_dir = meta_dir / dist_info_name
    dist_info_dir.mkdir(parents=True, exist_ok=True)

    (dist_info_dir / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: pcons\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}\n"
    )
    (dist_info_dir / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )

    return dist_info_name


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel and return its filename."""
    source_dir = Path.cwd()
    wheel_dir = Path(wheel_directory)
    build_dir = source_dir / "build"

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name = str(project.get("name", "unknown")).replace("-", "_")
    version = str(project.get("version", "0.0.1"))

    pcons_cfg = pyproject.get("tool", {}).get("pcons", {})
    variant = pcons_cfg.get("variant")
    variables = pcons_cfg.get("variables") or None

    _run_pcons(source_dir, build_dir, variant=variant, variables=variables)
    _run_ninja(build_dir)

    extensions = _find_extensions(build_dir)
    if not extensions:
        raise RuntimeError(
            f"No extension modules (with suffix {sysconfig.get_config_var('EXT_SUFFIX')!r})"
            f" found in {build_dir}"
        )

    python_tag, abi_tag, platform_tag = _wheel_tag()
    wheel_name = f"{name}-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    _write_wheel(
        wheel_dir / wheel_name,
        name,
        version,
        extensions,
        python_tag,
        abi_tag,
        platform_tag,
    )

    return wheel_name


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel.

    Native extensions cannot be made truly editable; we build normally and
    install the compiled artifact directly, same as build_wheel.
    """
    return build_wheel(wheel_directory, config_settings, metadata_directory)


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
    with tarfile.open(sdist_dir / sdist_name, "w:gz") as tf:
        for pattern in ("*.py", "*.cpp", "*.c", "*.h", "*.toml", "*.txt", "*.md"):
            for f in source_dir.glob(pattern):
                if f.is_file():
                    tf.add(f, arcname=f"{prefix}/{f.name}")

    return sdist_name

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
import glob
import hashlib
import io
import os
import shutil
import sys
import sysconfig
import zipfile
from email.headerregistry import Address
from pathlib import Path, PurePosixPath, PureWindowsPath
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


def _name_version(project: dict[str, Any]) -> tuple[str, str]:
    """Return the (normalized name, version) from the ``[project]`` table.

    Both are required: a missing or empty ``name``/``version`` raises an error.
    """
    name = project.get("name")
    version = project.get("version")
    missing = [f for f, v in (("name", name), ("version", version)) if not v]
    if missing:
        raise RuntimeError(
            "pyproject.toml [project] is missing required "
            f"{' and '.join(missing)}. pcons cannot build a distribution "
            "without it."
        )
    return str(name).replace("-", "_"), str(version)


def _ninja_requirement() -> list[str]:
    """Ask the frontend to install ninja if the build won't otherwise find it.

    The build shells out to ninja, so request the ninja wheel unless one is
    already reachable: a plain ``ninja`` on PATH, or a ``NINJA`` override naming
    a runner on PATH or an absolute path.
    """
    override = os.environ.get("NINJA")
    if override and (shutil.which(override) or Path(override).is_absolute()):
        return []
    if shutil.which("ninja"):
        return []
    return ["ninja"]


_HONORED_PROJECT_FIELDS = frozenset(
    {
        "name",
        "version",
        "requires-python",
        "dependencies",
        "description",
        "readme",
        "license",
        "license-files",
        "authors",
        "maintainers",
        "keywords",
        "classifiers",
        "urls",
    }
)

_README_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".rst": "text/x-rst",
    ".txt": "text/plain",
}


def _header(field: str, value: str) -> str:
    """Return one METADATA header line, folding a multi-line *value*."""
    return f"{field}: " + value.replace("\n", "\n        ")


def _read_inside(source_dir: Path, relative: str, field: str) -> str:
    """Read the UTF-8 text of *relative*, which must stay inside *source_dir*."""
    root = source_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError(
            f"pyproject [project] {field}: {relative!r} is outside the project."
        )
    if not path.is_file():
        raise RuntimeError(
            f"pyproject [project] {field}: file {relative!r} does not exist."
        )
    return path.read_text(encoding="utf-8")


def _readme(project: dict[str, Any], source_dir: Path) -> tuple[str, str] | None:
    """Return the (body, content type) of the ``readme`` field, or None."""
    readme = project.get("readme")
    if not readme:
        return None
    if isinstance(readme, str):
        content_type = _README_CONTENT_TYPES.get(Path(readme).suffix.lower())
        if content_type is None:
            raise RuntimeError(
                f"pyproject [project] readme: cannot tell the content type of "
                f"{readme!r}, use .md, .rst or .txt, or the table form."
            )
        return _read_inside(source_dir, readme, "readme"), content_type
    content_type = readme.get("content-type")
    if not content_type:
        raise RuntimeError("pyproject [project] readme: 'content-type' is required.")
    if ("file" in readme) == ("text" in readme):
        raise RuntimeError(
            "pyproject [project] readme: give exactly one of 'file' and 'text'."
        )
    if "file" in readme:
        return _read_inside(source_dir, readme["file"], "readme"), content_type
    return str(readme["text"]), content_type


def _people(role: str, entries: list[dict[str, str]]) -> list[str]:
    """Render ``authors`` or ``maintainers`` as ``<role>`` and ``<role>-email``."""
    names = [e["name"] for e in entries if e.get("name") and not e.get("email")]
    emails = [
        str(Address(display_name=e.get("name", ""), addr_spec=e["email"]))
        for e in entries
        if e.get("email")
    ]
    lines = []
    if names:
        lines.append(_header(role, ", ".join(names)))
    if emails:
        lines.append(_header(f"{role}-email", ", ".join(emails)))
    return lines


def _license_files(project: dict[str, Any], source_dir: Path) -> list[str]:
    """Return the sorted project-relative paths matched by ``license-files``.

    A pattern must be relative and free of ``..``, a drive and backslashes. Each
    match is resolved, so a symlink leaving the project is refused too.
    """
    root = source_dir.resolve()
    found: set[str] = set()
    for pattern in project.get("license-files") or []:
        as_windows = PureWindowsPath(pattern)
        if (
            "\\" in pattern
            or Path(pattern).is_absolute()
            or as_windows.anchor
            or ".." in PurePosixPath(pattern).parts
        ):
            raise RuntimeError(
                f"pyproject [project] license-files: {pattern!r} is outside the project."
            )
        matches: set[str] = set()
        for m in glob.glob(pattern, root_dir=root, recursive=True):
            path = (root / m).resolve()
            if not path.is_file():
                continue
            if not path.is_relative_to(root):
                raise RuntimeError(
                    f"pyproject [project] license-files: {m!r} resolves outside "
                    "the project."
                )
            matches.add(Path(m).as_posix())
        if not matches:
            raise RuntimeError(
                f"pyproject [project] license-files: {pattern!r} matches no file."
            )
        found |= matches
    return sorted(found)


def _license_lines(project: dict[str, Any], source_dir: Path) -> list[str]:
    """Render the ``license`` field, as an SPDX expression or the legacy table."""
    license_ = project.get("license")
    if not license_:
        return []
    if isinstance(license_, str):
        return [_header("License-Expression", license_)]
    if ("file" in license_) == ("text" in license_):
        raise RuntimeError(
            "pyproject [project] license: give exactly one of 'file' and 'text'."
        )
    if "file" in license_:
        return [
            _header("License", _read_inside(source_dir, license_["file"], "license"))
        ]
    return [_header("License", str(license_["text"]))]


def _dist_info_extras(project: dict[str, Any], source_dir: Path) -> dict[str, bytes]:
    """Return the dist-info files beyond METADATA, WHEEL and RECORD.

    Keys are paths relative to the ``.dist-info`` directory.
    """
    extras: dict[str, bytes] = {}
    for relative in _license_files(project, source_dir):
        extras[f"licenses/{relative}"] = (source_dir / relative).read_bytes()
    return extras


def _render_metadata(
    name: str, version: str, project: dict[str, Any], source_dir: Path
) -> str:
    """Render the wheel METADATA file from the ``[project]`` table.

    PEP 621 requires the backend to honor every non-dynamic ``[project]`` field,
    so a field the backend does not know, and any non-empty ``dynamic``, raise
    instead of being silently dropped. *source_dir* resolves ``readme``,
    ``license`` and ``license-files``.
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
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {version}",
    ]
    description = project.get("description")
    if description:
        if any(c in description for c in "\r\n"):
            raise RuntimeError("pyproject [project] description must be a single line.")
        lines.append(f"Summary: {description}")
    if project.get("keywords"):
        lines.append(_header("Keywords", ",".join(project["keywords"])))
    lines += _people("Author", project.get("authors") or [])
    lines += _people("Maintainer", project.get("maintainers") or [])
    lines += _license_lines(project, source_dir)
    lines += [f"License-File: {p}" for p in _license_files(project, source_dir)]
    lines += [f"Classifier: {c}" for c in project.get("classifiers") or []]
    lines += [
        _header("Project-URL", f"{label}, {url}")
        for label, url in (project.get("urls") or {}).items()
    ]
    requires_python = project.get("requires-python")
    if requires_python:
        lines.append(f"Requires-Python: {requires_python}")
    readme = _readme(project, source_dir)
    if readme:
        lines.append(f"Description-Content-Type: {readme[1]}")
    for dep in project.get("dependencies", []):
        lines.append(f"Requires-Dist: {dep}")
    text = "\n".join(lines) + "\n"
    if readme:
        text += "\n" + readme[0]
    return text


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
    extras: dict[str, bytes] | None = None,
) -> None:
    """Create the .whl file (a zip) at *wheel_path*.

    *root* is the staging directory that serves as the site-packages image, the structure is preserved.
    *metadata* is the rendered dist-info/METADATA text (see :func:`_render_metadata`).
    *extras* are further dist-info files (see :func:`_dist_info_extras`).
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

        for relative, data in sorted((extras or {}).items()):
            arcname = f"{dist_info}/{relative}"
            zf.writestr(arcname, data)
            record.append((arcname, _sha256_record(data), len(data)))

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
    extras: dict[str, bytes] | None = None,
) -> None:
    """Create an editable wheel containing only a .pth file pointing at build_dir.

    When pip installs this wheel, the .pth file is placed in site-packages and
    processed by Python's site module, which adds build_dir to sys.path.
    Imports then resolve directly to the compiled extensions in build_dir, so
    re-running ninja is enough to pick up rebuilt extensions without reinstalling.

    *metadata* is the rendered dist-info/METADATA text (see :func:`_render_metadata`).
    *extras* are further dist-info files (see :func:`_dist_info_extras`).
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

        for relative, data in sorted((extras or {}).items()):
            arcname = f"{dist_info}/{relative}"
            zf.writestr(arcname, data)
            record.append((arcname, _sha256_record(data), len(data)))

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
    """Return extra requirements needed to build the wheel.

    The build runs ninja, so request it when it isn't already on PATH.
    """
    return _ninja_requirement()


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    # An sdist is just a tarball of sources; no ninja (or anything) needed.
    return []


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """The editable build runs ninja too; request it when it isn't on PATH."""
    return _ninja_requirement()


def _prepare_metadata(metadata_directory: str, *, editable: bool) -> str:
    source_dir = Path.cwd()
    meta_dir = Path(metadata_directory)

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name, version = _name_version(project)

    python_tag, abi_tag, platform_tag = _wheel_tag()
    tag = f"{python_tag}-{abi_tag}-{platform_tag}"
    purelib = "true" if editable else "false"

    dist_info_name = f"{name}-{version}.dist-info"
    dist_info_dir = meta_dir / dist_info_name
    dist_info_dir.mkdir(parents=True, exist_ok=True)

    (dist_info_dir / "WHEEL").write_bytes(
        (
            "Wheel-Version: 1.0\n"
            "Generator: pcons\n"
            f"Root-Is-Purelib: {purelib}\n"
            f"Tag: {tag}\n"
        ).encode()
    )
    (dist_info_dir / "METADATA").write_bytes(
        _render_metadata(name, version, project, source_dir).encode()
    )
    for relative, data in _dist_info_extras(project, source_dir).items():
        target = dist_info_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    return dist_info_name


def _build(wheel_directory: str, *, editable: bool) -> str:
    source_dir = Path.cwd()
    wheel_dir = Path(wheel_directory)
    build_dir = source_dir / "build"

    pyproject = _load_pyproject(source_dir)
    project = pyproject.get("project", {})
    name, version = _name_version(project)

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
    metadata = _render_metadata(name, version, project, source_dir)
    extras = _dist_info_extras(project, source_dir)

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
            extras,
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
            extras,
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
    name, version = _name_version(project)

    sdist_name = f"{name}-{version}.tar.gz"
    sdist_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{name}-{version}"
    # PKG-INFO uses the core-metadata format, same content as the wheel METADATA.
    pkg_info = _render_metadata(name, version, project, source_dir).encode()
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

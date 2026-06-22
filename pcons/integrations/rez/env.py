# SPDX-License-Identifier: MIT
"""Read a rez resolve from environment variables.

When a ``pcons-build.py`` runs inside a ``rez-env`` shell, rez exposes the
resolved packages to the build process via environment variables:

- ``REZ_USED_RESOLVE`` — space-separated ``name-version`` pairs.
- ``REZ_<PKG_UPPER>_ROOT`` — install root of each resolved package.
- ``REZ_<PKG_UPPER>_VERSION`` — version of each resolved package.

This module reads those variables and builds :class:`PackageDescription`
objects from each package's install root using a convention-based scan
(``<root>/include``, ``<root>/lib``, plus a pkg-config fallback when
``<root>/lib/pkgconfig/*.pc`` files are present).

Packages that don't follow the default ``include``/``lib`` layout can be
described explicitly with a :class:`RezLayout` (see ``layouts`` on
:func:`rez_environment` and :class:`RezFinder`).

The rez Python API is used to read the resolve when it's importable (the
resolved context is authoritative); otherwise the documented ``REZ_*``
environment variables are parsed, so no rez install is required.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from pcons.packages.description import PackageDescription

if TYPE_CHECKING:
    from pcons.core.environment import Environment


class ResolvedPackage(NamedTuple):
    """A single entry from ``REZ_USED_RESOLVE`` paired with its root."""

    name: str
    version: str
    root: Path


class RezLayout(NamedTuple):
    """Explicit install layout for a rez package, relative to its root.

    Use this when a package doesn't follow the default
    ``<root>/include`` + ``<root>/lib`` convention — multi-arch lib dirs,
    nested header trees, or a multi-library package. Pass it via the
    ``layouts`` argument of :func:`rez_environment` or :class:`RezFinder`.

    A caller-supplied layout is trusted: its directories are recorded
    verbatim (no existence check), so a typo surfaces as a compiler or
    linker error rather than being silently dropped. ``libraries=None``
    keeps the ``lib<name>`` auto-detection; pass an explicit (possibly
    empty) tuple to list the libraries to link instead.
    """

    include_dirs: tuple[str, ...] = ("include",)
    library_dirs: tuple[str, ...] = ("lib",)
    libraries: tuple[str, ...] | None = None


_RESOLVE_VAR = "REZ_USED_RESOLVE"
_DEFAULT_LAYOUT = RezLayout()


def is_in_rez_resolve() -> bool:
    """Return True iff the current process is inside a rez-env shell."""
    return bool(os.environ.get(_RESOLVE_VAR))


def _root_env_var(name: str) -> str:
    """Convert a package name to its ``REZ_<NAME>_ROOT`` env var name.

    Rez uppercases package names to form the env var. Package names
    cannot contain ``-`` (rez restricts them to ``[a-zA-Z_][a-zA-Z0-9_]*``)
    so straightforward uppercasing suffices.
    """
    return f"REZ_{name.upper()}_ROOT"


def resolved_packages() -> list[ResolvedPackage]:
    """Return every resolved rez package paired with its install root.

    When the rez Python API is importable it is authoritative: the active
    resolved context (loaded from the current ``.rxt`` file) is the source
    of truth. Otherwise this falls back to parsing the documented
    ``REZ_USED_RESOLVE`` / ``REZ_<NAME>_ROOT`` environment variables, which
    works even when rez itself isn't importable in the build interpreter.

    Packages with no install root (implicit version-range packages, etc.)
    are skipped in both paths.
    """
    if not is_in_rez_resolve():
        return []
    from_api = _resolved_packages_from_api()
    if from_api is not None:
        return from_api
    return _resolved_packages_from_env()


def _resolved_packages_from_api() -> list[ResolvedPackage] | None:
    """Read the resolve via rez's Python API, or ``None`` if unavailable.

    Returns ``None`` — signaling the env-var fallback — when rez isn't
    importable or reports no active context. ``rez.status.status`` and
    ``ResolvedContext.resolved_packages`` are stable rez API.
    """
    try:
        from rez.status import status  # ty: ignore[unresolved-import]
    except ImportError:
        return None

    context = status.context
    if context is None:
        return None

    out: list[ResolvedPackage] = []
    for pkg in context.resolved_packages:
        root = getattr(pkg, "root", None)
        if not root:
            continue
        out.append(ResolvedPackage(pkg.name, str(pkg.version), Path(root)))
    return out


def _resolved_packages_from_env() -> list[ResolvedPackage]:
    """Read the resolve from ``REZ_USED_RESOLVE`` / ``REZ_<NAME>_ROOT``.

    Entries whose ``REZ_<NAME>_ROOT`` is unset are skipped (this filters
    out implicit packages like ``~arch==arm64`` that have no install root).
    """
    resolve = os.environ.get(_RESOLVE_VAR, "").strip()
    if not resolve:
        return []
    out: list[ResolvedPackage] = []
    for entry in resolve.split():
        # Format is "name-version". Package names cannot contain '-' but
        # versions can (e.g. "1.0-beta"), so split on the FIRST '-'.
        name, sep, version = entry.partition("-")
        if not sep:
            continue
        root = os.environ.get(_root_env_var(name))
        if not root:
            continue
        out.append(ResolvedPackage(name, version, Path(root)))
    return out


_LIB_PREFIXES = ("lib",)
_LIB_SUFFIXES = (".a", ".dylib", ".so")
_WIN_LIB_SUFFIX = ".lib"


def _detect_library(lib_dir: Path, pkg_name: str) -> str | None:
    """Return the library name to link if a matching file exists in lib_dir.

    Looks for ``lib<pkg_name>.{a,dylib,so}`` (Unix) or ``<pkg_name>.lib``
    (Windows). The convention is intentionally narrow: studios that ship
    multi-library packages should write their own ``.pc`` file or expose
    settings via the rez package's ``commands()`` block.
    """
    for prefix in _LIB_PREFIXES:
        for suffix in _LIB_SUFFIXES:
            if (lib_dir / f"{prefix}{pkg_name}{suffix}").exists():
                return pkg_name
    if (lib_dir / f"{pkg_name}{_WIN_LIB_SUFFIX}").exists():
        return pkg_name
    return None


def _pkgconfig_dirs(root: Path) -> list[Path]:
    """Return any pkg-config search dirs under a rez install root."""
    out: list[Path] = []
    for sub in ("lib/pkgconfig", "share/pkgconfig"):
        d = root / sub
        if d.is_dir() and any(d.glob("*.pc")):
            out.append(d)
    return out


def package_description(
    name: str,
    version: str,
    root: Path,
    layout: RezLayout | None = None,
) -> PackageDescription:
    """Build a :class:`PackageDescription` for one rez package.

    If ``layout`` is given, it wins outright: the package is described
    exactly as the caller declared (pkg-config and the convention scan
    are skipped). This is the escape hatch for packages that don't follow
    the default layout — see :class:`RezLayout`.

    Otherwise, if ``<root>/lib/pkgconfig/*.pc`` or
    ``<root>/share/pkgconfig/*.pc`` is present, defer to
    :class:`PkgConfigFinder` — a ``.pc`` file is the authoritative
    declaration of what the package exports, so it wins. The result's
    ``prefix`` is set to the rez install root and ``found_by`` to
    ``"rez+pkg-config"``.

    Otherwise fall back to a convention scan:

    - ``<root>/include`` (if it exists) is added to ``include_dirs``.
    - ``<root>/lib`` (if it exists) is added to ``library_dirs``.
    - If ``<root>/lib/lib<name>.{a,dylib,so}`` (or ``<name>.lib`` on
      Windows) exists, ``<name>`` is added to ``libraries``.
    """
    if layout is not None:
        return _scan_layout(name, version, root, layout, trust=True)

    pc_dirs = _pkgconfig_dirs(root)
    if pc_dirs:
        pd = _from_pkgconfig(name, version, root, pc_dirs)
        if pd is not None:
            return pd

    return _scan_layout(name, version, root, _DEFAULT_LAYOUT, trust=False)


def _scan_layout(
    name: str,
    version: str,
    root: Path,
    layout: RezLayout,
    *,
    trust: bool,
) -> PackageDescription:
    """Build a :class:`PackageDescription` from ``root`` and ``layout``.

    ``trust=False`` (the default convention) records only directories that
    actually exist, mirroring auto-discovery. ``trust=True`` (an explicit
    caller-supplied layout) records the declared directories verbatim.

    When ``layout.libraries`` is ``None`` the library is auto-detected
    (``lib<name>.{a,dylib,so}`` / ``<name>.lib``) in the first existing
    library dir; otherwise the listed libraries are used as given.
    """
    pd = PackageDescription(
        name=name,
        version=version,
        prefix=str(root),
        found_by="rez",
    )

    for sub in layout.include_dirs:
        d = root / sub
        if trust or d.is_dir():
            pd.include_dirs.append(str(d))

    for sub in layout.library_dirs:
        d = root / sub
        if trust or d.is_dir():
            pd.library_dirs.append(str(d))

    if layout.libraries is None:
        for sub in layout.library_dirs:
            lib_dir = root / sub
            if lib_dir.is_dir():
                lib_name = _detect_library(lib_dir, name)
                if lib_name is not None:
                    pd.libraries.append(lib_name)
                    break
    else:
        pd.libraries.extend(layout.libraries)

    return pd


def _from_pkgconfig(
    name: str,
    fallback_version: str,
    root: Path,
    pc_dirs: list[Path],
) -> PackageDescription | None:
    """Resolve ``name`` via pkg-config with ``pc_dirs`` on ``PKG_CONFIG_PATH``.

    Returns the pkg-config result with ``prefix`` set to the rez install
    ``root`` and ``found_by`` set to ``"rez+pkg-config"``. ``version``
    falls back to ``fallback_version`` if pkg-config didn't set one.
    Returns ``None`` if pkg-config isn't installed or can't resolve
    ``name``, signaling the caller to fall back to the convention scan.

    Lazy-imports :class:`PkgConfigFinder` so this module stays cheap to
    import on systems without pkg-config.
    """
    from pcons.packages.finders.pkgconfig import PkgConfigFinder

    finder = PkgConfigFinder()
    if not finder.is_available():
        return None
    extra_path = os.pathsep.join(str(d) for d in pc_dirs)
    saved = os.environ.get("PKG_CONFIG_PATH")
    os.environ["PKG_CONFIG_PATH"] = (
        f"{extra_path}{os.pathsep}{saved}" if saved else extra_path
    )
    try:
        pc_pkg = finder.find(name)
    finally:
        if saved is None:
            os.environ.pop("PKG_CONFIG_PATH", None)
        else:
            os.environ["PKG_CONFIG_PATH"] = saved
    if pc_pkg is None:
        return None

    pc_pkg.prefix = str(root)
    pc_pkg.found_by = "rez+pkg-config"
    if not pc_pkg.version:
        pc_pkg.version = fallback_version
    return pc_pkg


def rez_environment(
    env: Environment,
    *,
    packages: list[str] | None = None,
    layouts: dict[str, RezLayout] | None = None,
) -> None:
    """Apply every resolved rez package's settings to ``env`` via ``env.use``.

    For each package in :func:`resolved_packages` (filtered to ``packages``
    if given), build a :class:`PackageDescription` and call ``env.use(pd)``
    so its include/lib/define settings live on the :class:`Environment`
    and apply to every target built from it.

    If the current process is not inside a rez-env shell, returns
    immediately and leaves ``env`` untouched.

    For per-target control — e.g. linking ``boost`` to one program but
    not another — register :class:`RezFinder` and use
    ``project.find_package("boost")`` + ``app.link(boost)`` instead.
    Don't combine ``rez_environment`` with ``app.link()`` of the same
    packages: flags would be applied twice.

    Args:
        env: The pcons :class:`Environment` to mutate.
        packages: Optional whitelist. If given, only packages with names
            in this list are applied; others are ignored.
        layouts: Optional ``{package_name: RezLayout}`` map. A package
            listed here is described from its :class:`RezLayout` instead
            of the default convention scan — see :class:`RezLayout`.
    """
    for pkg in resolved_packages():
        if packages is not None and pkg.name not in packages:
            continue
        layout = layouts.get(pkg.name) if layouts else None
        pd = package_description(pkg.name, pkg.version, pkg.root, layout)
        env.use(pd)

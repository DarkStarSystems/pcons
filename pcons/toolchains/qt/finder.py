# SPDX-License-Identifier: MIT
"""Qt 6 discovery.

:func:`find_qt` locates Qt modules (as :class:`ImportedTarget`s), the code
generation tools (moc/uic/rcc/...), and the Qt version, probing in order:

1. **pkg-config** — ``Qt6Core.pc``/``Qt6Widgets.pc``. Present on Linux
   distro packages and Homebrew macOS (where the .pc files even encode
   framework linking), restored upstream in Qt 6.2.5/6.3.1/6.4. The tool
   directory comes from ``pkg-config --variable=libexecdir Qt6Core``.
2. **qtpaths/qmake introspection** — ``qtpaths6 -query`` (or ``qmake6``),
   for installs without .pc files (official installer, Windows).
   ``QT_HOST_LIBEXECS``/``QT_HOST_BINS`` give the tool directory,
   ``QT_INSTALL_LIBS``/``QT_INSTALL_HEADERS`` the libraries.

Discovery is cached per project (each Qt module becomes exactly one
ImportedTarget, so target identity is stable across the build script);
repeated calls may add modules.

Platform requirements are baked into the returned module targets so users
never see them: MSVC-style compilers get ``/Zc:__cplusplus /permissive-``
(Qt headers require both), Windows debug builds link the ``d``-suffixed
libraries, and macOS framework builds carry ``-F``/``-framework`` flags.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Literal, overload

from pcons.configure.platform import get_platform
from pcons.core.errors import ConfigureError
from pcons.packages.description import PackageDescription
from pcons.packages.finders.pkgconfig import PkgConfigFinder
from pcons.packages.imported import ImportedTarget

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project

logger = logging.getLogger(__name__)


class QtNotFoundError(ConfigureError):
    """Qt (or a requested Qt module) could not be located."""


# Inter-module dependencies, used by the qtpaths fallback so usage
# requirements propagate transitively (pkg-config handles this itself via
# Requires: chains). Unlisted modules implicitly depend on Core.
_MODULE_DEPS: dict[str, tuple[str, ...]] = {
    "Core": (),
    "Gui": ("Core",),
    "Widgets": ("Gui",),
    "Network": ("Core",),
    "Concurrent": ("Core",),
    "OpenGL": ("Gui",),
    "OpenGLWidgets": ("OpenGL", "Widgets"),
    "PrintSupport": ("Widgets",),
    "Qml": ("Network",),
    "Quick": ("Qml", "Gui"),
    "QuickControls2": ("Quick",),
    "QuickWidgets": ("Quick", "Widgets"),
    "Sql": ("Core",),
    "Svg": ("Gui",),
    "SvgWidgets": ("Svg", "Widgets"),
    "Test": ("Core",),
    "Xml": ("Core",),
    "Multimedia": ("Gui", "Network"),
    "MultimediaWidgets": ("Multimedia", "Widgets"),
}

# One QtPackage per project: Qt module targets register with the project,
# so discovery must not run twice. Weak keys let projects be collected.
_qt_installs: weakref.WeakKeyDictionary[Project, QtPackage] = (
    weakref.WeakKeyDictionary()
)


class QtPackage:
    """A located Qt installation: modules, tools, and version.

    Attributes:
        version: Qt version string (e.g. "6.9.3").
        prefix: Installation prefix.
        bin_dir: Directory holding user-facing tools (designer, lupdate...).
        libexec_dir: Directory holding build tools (moc, uic, rcc...);
            equals bin_dir on Windows.
        is_framework: True for macOS framework builds.
        found_via: "pkg-config" or a qtpaths/qmake command name.
        modules: Located modules as ImportedTargets, keyed by short name
            ("Widgets"). Also available as attributes: ``qt.Widgets``.
    """

    def __init__(
        self,
        *,
        version: str,
        prefix: Path,
        bin_dir: Path,
        libexec_dir: Path,
        is_framework: bool,
        found_via: str,
        modules: dict[str, ImportedTarget],
        module_factory: Callable[[str], ImportedTarget | None],
    ) -> None:
        self.version = version
        self.prefix = prefix
        self.bin_dir = bin_dir
        self.libexec_dir = libexec_dir
        self.is_framework = is_framework
        self.found_via = found_via
        self.modules = modules
        self._module_factory = module_factory
        # Per-module bookkeeping so requirements land exactly once even
        # as later find_qt() calls add modules or pass different envs.
        self._dsuffix_applied: set[str] = set()
        self._debug_seen = False
        self._msvc_flags_applied = False
        self._arm_acle_applied = False
        self._private_applied: set[str] = set()

    def __getattr__(self, name: str) -> ImportedTarget:
        # Module sugar: qt.Widgets. __getattr__ is only consulted for
        # names not found normally, so real attributes are unaffected.
        modules = self.__dict__.get("modules", {})
        if name in modules:
            return modules[name]
        raise AttributeError(
            f"Qt module '{name}' was not requested. "
            f"Available: {', '.join(sorted(modules))}. "
            f"Add it to find_qt(modules=[...])."
        )

    def _ensure_module(self, name: str) -> ImportedTarget | None:
        """Get or create a module target, resolving via this install's route."""
        if name in self.modules:
            return self.modules[name]
        target = self._module_factory(name)
        if target is not None:
            self.modules[name] = target
        return target

    def metatypes_files(self) -> list[Path]:
        """Qt's own metatypes JSON files (qmltyperegistrar --foreign-types).

        Layout varies: Homebrew uses <prefix>/share/qt/metatypes, most
        Linux distros and the official installer use <libs>/metatypes or
        <prefix>/metatypes. Empty when none found (the registrar then
        works without foreign-type revision info).
        """
        candidates = [
            self.prefix / "share" / "qt" / "metatypes",
            self.prefix / "lib" / "metatypes",
            self.prefix / "lib64" / "metatypes",
            self.prefix / "metatypes",
        ]
        for directory in candidates:
            if directory.is_dir():
                return sorted(directory.glob("*_metatypes.json"))
        return []

    def tool_path(self, name: str, *, required: bool = False) -> Path | None:
        """Path of a Qt tool (moc, uic, rcc, lrelease, ...), or None.

        Args:
            name: Tool name without extension.
            required: Raise QtNotFoundError instead of returning None.
        """
        exe = f"{name}.exe" if get_platform().is_windows else name
        for directory in (self.libexec_dir, self.bin_dir):
            candidate = directory / exe
            if candidate.is_file():
                return candidate
        if required:
            raise QtNotFoundError(
                f"Qt tool '{name}' not found in {self.libexec_dir} or "
                f"{self.bin_dir} (Qt {self.version} at {self.prefix})."
            )
        return None

    def __repr__(self) -> str:
        return (
            f"QtPackage(version={self.version!r}, prefix={str(self.prefix)!r}, "
            f"modules={sorted(self.modules)}, via={self.found_via!r})"
        )


# required=True (the default) never returns None — give callers the
# narrowed type so `qt.Widgets` needs no None-check.
@overload
def find_qt(
    project: Project,
    env: Environment | None = None,
    *,
    modules: Sequence[str],
    version: str | None = None,
    qt_root: str | Path | None = None,
    private_headers: Sequence[str] = (),
    required: Literal[True] = True,
) -> QtPackage: ...


@overload
def find_qt(
    project: Project,
    env: Environment | None = None,
    *,
    modules: Sequence[str],
    version: str | None = None,
    qt_root: str | Path | None = None,
    private_headers: Sequence[str] = (),
    required: Literal[False],
) -> QtPackage | None: ...


def find_qt(
    project: Project,
    env: Environment | None = None,
    *,
    modules: Sequence[str],
    version: str | None = None,
    qt_root: str | Path | None = None,
    private_headers: Sequence[str] = (),
    required: bool = True,
) -> QtPackage | None:
    """Locate Qt 6 and return its modules, tools, and version.

    Args:
        project: The project; module targets register with it, and
            discovery is cached on it (repeat calls may add modules).
        env: When given, the ``qt`` toolchain is added to this environment
            with moc/uic/rcc paths configured, enabling ``env.qt.*``
            builders and ``project.QtProgram(...)``. MSVC-style toolchains
            also get Qt's required compiler flags on the Core module.
        modules: Qt module short names, e.g. ["Widgets", "Network"].
            Core is always included.
        version: Optional constraint, e.g. ">=6.4".
        qt_root: Explicit Qt prefix (overrides probing); also taken from
            the PCONS_QT_ROOT environment variable. First call wins: the
            cached install is reused by later calls.
        private_headers: Modules whose private headers should be added to
            the include path (e.g. ["Core"] for QtCore/x.y.z/private).
        required: If True (default), raise QtNotFoundError when Qt or any
            requested module is missing; if False, return None.

    Returns:
        A QtPackage, or None (only when required=False).
    """
    wanted = list(dict.fromkeys(["Core", *modules]))  # dedupe, Core first
    if qt_root is None:
        env_root = os.environ.get("PCONS_QT_ROOT", "").strip()
        qt_root = Path(env_root) if env_root else None
    else:
        qt_root = Path(qt_root)
    if qt_root is not None and not qt_root.is_dir():
        raise QtNotFoundError(
            f"qt_root {qt_root} does not exist (from "
            f"{'$PCONS_QT_ROOT' if 'PCONS_QT_ROOT' in os.environ else 'qt_root='})."
        )

    qt = _qt_installs.get(project)
    if qt is not None and qt_root is not None and not qt.prefix.is_relative_to(qt_root):
        logger.warning(
            "find_qt: qt_root=%s ignored — Qt %s at %s is already located "
            "for this project (discovery is cached; the first call wins).",
            qt_root,
            qt.version,
            qt.prefix,
        )
    if qt is None:
        qt = _probe_pkgconfig(wanted, version, qt_root)
        if qt is None:
            qt = _probe_qtpaths(wanted, version, qt_root)
        if qt is None:
            if not required:
                return None
            raise QtNotFoundError(_not_found_message(wanted, version, qt_root))
        _qt_installs[project] = qt
    elif version is not None and not _version_satisfies(qt.version, version):
        if not required:
            return None
        raise QtNotFoundError(
            f"Qt {qt.version} (already located at {qt.prefix}) does not "
            f"satisfy the requested version {version}."
        )

    for name in wanted:
        if qt._ensure_module(name) is None:
            if not required:
                return None
            raise QtNotFoundError(
                f"Qt module '{name}' not found in Qt {qt.version} at "
                f"{qt.prefix} (located via {qt.found_via})."
            )

    _apply_platform_requirements(qt, env)
    _apply_private_headers(qt, private_headers)

    if env is not None and not any(t.name == "qt" for t in env.toolchains):
        from pcons.toolchains.qt.toolchain import QtToolchain

        env.add_toolchain(QtToolchain.from_package(qt))

    return qt


def qt_module_available(name: str, qt_root: str | Path | None = None) -> bool:
    """Cheap existence probe for one Qt module (no targets created).

    Used by test harnesses and feature guards; find_qt() is the real
    discovery entry point.
    """
    root = Path(qt_root) if qt_root else None
    finder = _pkgconfig_finder(root)
    if finder.is_available() and finder.find(f"Qt6{name}") is not None:
        return True
    query = _find_qtpaths_query(root)
    if query is None:
        return False  # no Qt at all
    prefix = Path(query.get("QT_INSTALL_PREFIX", ""))
    libs = Path(query.get("QT_INSTALL_LIBS", prefix / "lib"))
    headers = Path(query.get("QT_INSTALL_HEADERS", prefix / "include"))
    is_framework = (libs / "QtCore.framework").is_dir()
    return (
        _module_package(name, query.get("QT_VERSION", ""), libs, headers, is_framework)
        is not None
    )


def _not_found_message(
    wanted: list[str], version: str | None, qt_root: Path | None
) -> str:
    probes = [
        "pkg-config " + ", ".join(f"Qt6{m}" for m in wanted),
        "qtpaths6/qtpaths/qmake6/qmake -query",
    ]
    lines = [
        f"Qt 6 not found (need modules: {', '.join(wanted)}"
        + (f", version {version}" if version else "")
        + ")."
    ]
    lines.append("Probed: " + "; ".join(probes) + ".")
    if qt_root:
        lines.append(f"qt_root was set to {qt_root}.")
    lines.append(
        "Install Qt (brew install qt / apt install qt6-base-dev / "
        "https://www.qt.io) or point PCONS_QT_ROOT (or qt_root=) at its prefix."
    )
    return "\n".join(lines)


# =============================================================================
# pkg-config probe
# =============================================================================


def _pkgconfig_finder(qt_root: Path | None) -> PkgConfigFinder:
    """A PkgConfigFinder scoped to qt_root when one is given.

    With qt_root, PKG_CONFIG_LIBDIR *restricts* the search to that
    prefix — a pinned root must never silently fall back to some other
    Qt the system pkg-config happens to know about. The override is
    per-finder; the process environment is never mutated.
    """
    if qt_root is not None:
        pc_dir = qt_root / "lib" / "pkgconfig"
        return PkgConfigFinder(env_overrides={"PKG_CONFIG_LIBDIR": str(pc_dir)})
    return PkgConfigFinder()


def _probe_pkgconfig(
    wanted: list[str], version: str | None, qt_root: Path | None
) -> QtPackage | None:
    """Locate Qt via Qt6*.pc files.

    All requested modules must resolve, otherwise return None so the
    qtpaths probe gets a chance (incomplete .pc coverage happens; e.g.
    Ubuntu 24.04 shipped qt6-base without .pc files for a while). No
    targets are created until the whole probe succeeds.
    """
    finder = _pkgconfig_finder(qt_root)
    if not finder.is_available():
        logger.debug("Qt probe: pkg-config not available")
        return None

    descriptions: dict[str, PackageDescription] = {}
    core = finder.find("Qt6Core", version=version)
    if core is None:
        logger.debug(
            "Qt probe: pkg-config has no Qt6Core%s",
            f" (restricted to {qt_root})" if qt_root else "",
        )
        return None
    descriptions["Core"] = core
    for name in wanted:
        if name == "Core":
            continue
        pkg = finder.find(f"Qt6{name}")
        if pkg is None:
            return None  # incomplete install; let qtpaths try
        descriptions[name] = pkg

    modules = {
        name: ImportedTarget.from_package(pkg) for name, pkg in descriptions.items()
    }
    # Every module depends on Core: the .pc files flatten compile/link
    # flags, but pcons-added platform requirements live on the Core
    # *target* and must propagate to whatever users actually link.
    for name, target in modules.items():
        if name != "Core":
            target.link(modules["Core"])

    def factory(name: str) -> ImportedTarget | None:
        pkg = finder.find(f"Qt6{name}")
        if pkg is None:
            return None
        target = ImportedTarget.from_package(pkg)
        target.link(modules["Core"])
        return target

    prefix = Path(finder.get_variable("Qt6Core", "prefix") or "/usr")
    bin_dir = Path(finder.get_variable("Qt6Core", "bindir") or prefix / "bin")
    libexec = finder.get_variable("Qt6Core", "libexecdir")

    return QtPackage(
        version=core.version,
        prefix=prefix,
        bin_dir=bin_dir,
        libexec_dir=Path(libexec) if libexec else bin_dir,
        is_framework="-framework" in core.link_flags,
        found_via="pkg-config",
        modules=modules,
        module_factory=factory,
    )


# =============================================================================
# qtpaths / qmake introspection probe
# =============================================================================


def _qtpaths_candidates(qt_root: Path | None) -> list[tuple[str, list[Path]]]:
    """(command, hint dirs) pairs to try, most specific first."""
    hints: list[Path] = []
    if qt_root is not None:
        hints.append(qt_root / "bin")
    platform = get_platform()
    if platform.is_macos:
        hints += [Path("/opt/homebrew/opt/qt/bin"), Path("/usr/local/opt/qt/bin")]
    elif platform.is_windows:
        # Official installer layout: C:\Qt\6.x.y\<toolchain>\bin —
        # newest first by *numeric* version (lexicographic sorting
        # would prefer 6.9 over 6.10).
        def version_key(bin_dir: Path) -> list[int]:
            return [int(n) for n in re.findall(r"\d+", bin_dir.parts[-3])]

        for qt_dir in sorted(
            Path("C:/Qt").glob("6.*/*/bin"), key=version_key, reverse=True
        ):
            hints.append(qt_dir)
    else:
        hints += [Path("/usr/lib/qt6/bin"), Path("/usr/lib64/qt6/bin")]
    return [(name, hints) for name in ("qtpaths6", "qtpaths", "qmake6", "qmake")]


def _run_query(command: Path) -> dict[str, str] | None:
    """Run `<command> -query` and parse KEY:value lines."""
    try:
        result = subprocess.run(
            [str(command), "-query"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    query: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            query[key.strip()] = value.strip()
    return query or None


def _version_satisfies(found: str, constraint: str) -> bool:
    """Check a version string against a constraint like ">=6.4".

    Raises:
        ValueError: If the constraint has no numeric version to compare
            against (fail fast on typos like version="banana").
    """
    match = re.match(r"(>=|<=|=|>|<)?\s*(.+)", constraint)
    if not match or not re.search(r"\d", match.group(2)):
        raise ValueError(
            f"Invalid Qt version constraint {constraint!r} "
            f"(expected e.g. '>=6.4', '=6.7.2')."
        )
    op = match.group(1) or "="
    want = match.group(2).strip()

    def key(v: str) -> list[int]:
        return [int(p) for p in re.findall(r"\d+", v)]

    fk, wk = key(found), key(want)
    # Pad to equal length so 6.9 == 6.9.0
    length = max(len(fk), len(wk))
    fk += [0] * (length - len(fk))
    wk += [0] * (length - len(wk))
    cmp = (fk > wk) - (fk < wk)
    return {">=": cmp >= 0, ">": cmp > 0, "<=": cmp <= 0, "<": cmp < 0, "=": cmp == 0}[
        op
    ]


def _find_qtpaths_query(qt_root: Path | None) -> dict[str, str] | None:
    """Locate a working qtpaths/qmake and return its -query result.

    The result carries the tool's name under the "" key for found_via.
    """
    platform = get_platform()
    for name, hints in _qtpaths_candidates(qt_root):
        candidates = [
            d / (f"{name}.exe" if platform.is_windows else name) for d in hints
        ]
        which = shutil.which(name)
        if which:
            candidates.append(Path(which))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            query = _run_query(candidate)
            if query and query.get("QT_VERSION", "").startswith("6"):
                query[""] = name
                logger.debug(
                    "Qt probe: %s reports Qt %s at %s",
                    candidate,
                    query.get("QT_VERSION"),
                    query.get("QT_INSTALL_PREFIX"),
                )
                return query
            logger.debug("Qt probe: %s is not a Qt 6 install", candidate)
    return None


def _probe_qtpaths(
    wanted: list[str], version: str | None, qt_root: Path | None
) -> QtPackage | None:
    """Locate Qt by querying qtpaths/qmake and inspecting the install tree."""
    query = _find_qtpaths_query(qt_root)
    if query is None:
        return None
    found_via = query.get("", "qtpaths")

    qt_version = query["QT_VERSION"]
    if version is not None and not _version_satisfies(qt_version, version):
        return None

    prefix = Path(query.get("QT_INSTALL_PREFIX", ""))
    libs = Path(query.get("QT_INSTALL_LIBS", prefix / "lib"))
    headers = Path(query.get("QT_INSTALL_HEADERS", prefix / "include"))
    bin_dir = Path(
        query.get("QT_HOST_BINS") or query.get("QT_INSTALL_BINS") or prefix / "bin"
    )
    libexec_dir = Path(query.get("QT_HOST_LIBEXECS") or bin_dir)
    is_framework = (libs / "QtCore.framework").is_dir()

    # Validate before creating any targets: descriptions for the wanted
    # modules and their implicit deps must all resolve.
    order = _closure_in_dep_order(wanted)
    descriptions: dict[str, PackageDescription] = {}
    for name in order:
        pkg = _module_package(name, qt_version, libs, headers, is_framework)
        if pkg is None:
            if name in wanted:
                return None
            continue  # missing implicit dep of an unrequested module
        descriptions[name] = pkg

    modules: dict[str, ImportedTarget] = {}

    def add_module(name: str, pkg: PackageDescription) -> ImportedTarget:
        target = ImportedTarget.from_package(pkg)
        for dep in _MODULE_DEPS.get(name, ("Core",)):
            if dep in modules:
                target.link(modules[dep])
        modules[name] = target
        return target

    for name in order:
        if name in descriptions:
            add_module(name, descriptions[name])

    def factory(name: str) -> ImportedTarget | None:
        # Create implicit deps first so link() targets exist.
        for dep in _MODULE_DEPS.get(name, ("Core",)):
            if dep not in modules and factory(dep) is None:
                return None
        if name in modules:
            return modules[name]
        pkg = _module_package(name, qt_version, libs, headers, is_framework)
        return add_module(name, pkg) if pkg is not None else None

    return QtPackage(
        version=qt_version,
        prefix=prefix,
        bin_dir=bin_dir,
        libexec_dir=libexec_dir,
        is_framework=is_framework,
        found_via=found_via,
        modules=modules,
        module_factory=factory,
    )


def _closure_in_dep_order(wanted: list[str]) -> list[str]:
    """Requested modules plus implicit deps, dependencies first."""
    order: list[str] = []

    def visit(name: str) -> None:
        if name in order:
            return
        for dep in _MODULE_DEPS.get(name, ("Core",)):
            visit(dep)
        order.append(name)

    visit("Core")
    for name in wanted:
        visit(name)
    return order


def _module_package(
    name: str, qt_version: str, libs: Path, headers: Path, is_framework: bool
) -> PackageDescription | None:
    """PackageDescription for one Qt module from an introspected install."""
    define = f"QT_{name.upper()}_LIB"
    if is_framework:
        framework_dir = libs / f"Qt{name}.framework"
        if not framework_dir.is_dir():
            return None
        return PackageDescription(
            name=f"Qt6{name}",
            version=qt_version,
            include_dirs=[str(framework_dir / "Headers")],
            defines=[define],
            frameworks=[f"Qt{name}"],
            framework_dirs=[str(libs)],
            prefix=str(libs.parent),
        )
    module_headers = headers / f"Qt{name}"
    if not module_headers.is_dir():
        return None
    return PackageDescription(
        name=f"Qt6{name}",
        version=qt_version,
        include_dirs=[str(headers), str(module_headers)],
        library_dirs=[str(libs)],
        libraries=[f"Qt6{name}"],
        defines=[define],
        prefix=str(libs.parent),
    )


# =============================================================================
# Platform requirements & private headers
# =============================================================================


def _apply_platform_requirements(qt: QtPackage, env: Environment | None) -> None:
    """Bake required platform flags into the module targets.

    Re-entrant and per-module: every find_qt() call applies whatever is
    newly applicable (a later call may add modules, or be the first to
    carry an MSVC env or a debug variant).

    - MSVC-style compilers: Qt 6 headers require ``/Zc:__cplusplus`` (real
      ``__cplusplus`` value), ``/permissive-`` (conformant two-phase
      lookup), and ``/EHsc`` (exception handling — Qt headers use throw;
      cl and clang-cl disable exceptions by default while CMake's default
      flags quietly include /EHsc). Applied on Core so every dependent
      inherits them.
    - Windows debug variant: Qt import libraries carry a ``d`` suffix.
      Limitation: decided by the variant seen at find_qt() time — on
      Windows call find_qt() *after* env.set_variant(), and build debug
      and release in separate pcons runs.
    - Apple Silicon with Qt < 6.10: qyieldcpu.h calls the ACLE intrinsic
      ``__yield()`` bare; clang treats that as an implicit declaration
      error unless <arm_acle.h> was included first (fixed upstream in
      Qt 6.10). Pre-include it so users never see the error.
    """
    platform = get_platform()
    if not platform.is_windows:
        if (
            not qt._arm_acle_applied
            and platform.is_macos
            and getattr(platform, "arch", "") == "arm64"
            and _version_satisfies(qt.version, "<6.10")
        ):
            qt.modules["Core"].public.compile_flags.extend(["-include", "arm_acle.h"])
            qt._arm_acle_applied = True
        return

    toolchain_name = ""
    if env is not None and env.toolchain is not None:
        toolchain_name = env.toolchain.name
    core = qt.modules.get("Core")
    if (
        not qt._msvc_flags_applied
        and core is not None
        and toolchain_name in ("msvc", "clang-cl")
    ):
        core.public.compile_flags.extend(["/Zc:__cplusplus", "/permissive-", "/EHsc"])
        qt._msvc_flags_applied = True

    if env is not None and getattr(env, "variant", "") == "debug":
        qt._debug_seen = True
    if qt._debug_seen:
        for name, target in qt.modules.items():
            if name in qt._dsuffix_applied:
                continue
            target.public.link_libs = [
                f"{lib}d" if isinstance(lib, str) and lib.startswith("Qt6") else lib
                for lib in target.public.link_libs
            ]
            qt._dsuffix_applied.add(name)


def _apply_private_headers(qt: QtPackage, private_headers: Sequence[str]) -> None:
    """Add <Module>/x.y.z/<Module>[/private] include dirs for named modules."""
    for name in private_headers:
        if name in qt._private_applied:
            continue
        target = qt.modules.get(name)
        if target is None:
            raise ValueError(
                f"private_headers names '{name}', which is not in modules=[...]."
            )
        probed: list[Path] = []
        for base in list(target.public.include_dirs):
            versioned = Path(base) / qt.version / f"Qt{name}"
            probed.append(versioned)
            if versioned.is_dir():
                target.public.include_dirs.extend([versioned, versioned / "private"])
                qt._private_applied.add(name)
                break
        else:
            raise QtNotFoundError(
                f"Private headers for Qt{name} not found (probed: "
                + ", ".join(str(p) for p in probed)
                + "). Some distributions package them separately "
                "(e.g. qt6-base-private-dev)."
            )

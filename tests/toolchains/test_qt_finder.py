# SPDX-License-Identifier: MIT
"""Tests for Qt discovery (find_qt) — no Qt installation required.

Both probe routes are exercised against synthetic data: pkg-config via a
mocked PkgConfigFinder (Linux flag shape and Homebrew macOS framework
shape), qtpaths via a fake install tree plus a mocked -query result.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

import pcons.toolchains.qt.finder as qt_finder
from pcons.core.project import Project
from pcons.packages.description import PackageDescription
from pcons.toolchains.qt import QtNotFoundError, QtProbe, find_qt
from pcons.toolchains.qt.finder import (
    _apply_platform_requirements,
    _closure_in_dep_order,
    _version_satisfies,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Project("qt-test", root_dir=tmp_path, build_dir=tmp_path / "build")


# =============================================================================
# Synthetic pkg-config data
# =============================================================================

_LINUX_PCS = {
    "Qt6Core": PackageDescription(
        name="Qt6Core",
        version="6.7.2",
        include_dirs=["/usr/include/qt6", "/usr/include/qt6/QtCore"],
        library_dirs=["/usr/lib64"],
        libraries=["Qt6Core"],
        defines=["QT_CORE_LIB"],
        prefix="/usr",
    ),
    "Qt6Widgets": PackageDescription(
        name="Qt6Widgets",
        version="6.7.2",
        include_dirs=[
            "/usr/include/qt6",
            "/usr/include/qt6/QtWidgets",
            "/usr/include/qt6/QtGui",
            "/usr/include/qt6/QtCore",
        ],
        library_dirs=["/usr/lib64"],
        libraries=["Qt6Widgets", "Qt6Gui", "Qt6Core"],
        defines=["QT_WIDGETS_LIB", "QT_GUI_LIB", "QT_CORE_LIB"],
        prefix="/usr",
    ),
}

# Homebrew's framework build: no -l libraries at all; frameworks are
# encoded as raw flags in the .pc (verified against Qt 6.9.3).
_MAC_PCS = {
    "Qt6Core": PackageDescription(
        name="Qt6Core",
        version="6.9.3",
        include_dirs=["/opt/homebrew/lib/QtCore.framework/Headers"],
        defines=["QT_CORE_LIB"],
        compile_flags=["-F/opt/homebrew/lib"],
        link_flags=["-F/opt/homebrew/lib", "-framework", "QtCore"],
        prefix="/opt/homebrew",
    ),
    "Qt6Widgets": PackageDescription(
        name="Qt6Widgets",
        version="6.9.3",
        include_dirs=["/opt/homebrew/lib/QtWidgets.framework/Headers"],
        defines=["QT_WIDGETS_LIB"],
        compile_flags=["-F/opt/homebrew/lib"],
        link_flags=["-F/opt/homebrew/lib", "-framework", "QtWidgets"],
        prefix="/opt/homebrew",
    ),
}


class _FakePkgConfig:
    """Stands in for PkgConfigFinder with a fixed .pc universe."""

    def __init__(self, pcs, variables=None, available=True):
        self._pcs = pcs
        self._vars = variables or {}
        self._available = available

    def is_available(self):
        return self._available

    def find(self, name, version=None, components=None):
        pkg = self._pcs.get(name)
        if pkg is None:
            return None
        if version is not None and not _version_satisfies(pkg.version, version):
            return None
        return pkg

    def get_variable(self, name, var):
        return self._vars.get(var)


def _patch_pkgconfig(fake):
    return patch.object(qt_finder, "_pkgconfig_finder", lambda qt_root: fake)


def _no_qtpaths():
    return patch.object(qt_finder, "_probe_qtpaths", lambda *a: None)


# =============================================================================
# pkg-config route
# =============================================================================


class TestPkgConfigRoute:
    def test_linux_shape(self, project):
        fake = _FakePkgConfig(
            _LINUX_PCS,
            variables={"prefix": "/usr", "libexecdir": "/usr/lib64/qt6/libexec"},
        )
        with _patch_pkgconfig(fake):
            qt = find_qt(project, modules=["Widgets"])
        assert qt is not None
        assert qt.version == "6.7.2"
        assert qt.found_via == "pkg-config"
        assert qt.libexec_dir == Path("/usr/lib64/qt6/libexec")
        assert not qt.is_framework
        widgets = qt.Widgets
        assert "Qt6Widgets" in widgets.public.link_libs
        assert Path("/usr/include/qt6/QtWidgets") in [
            Path(p) for p in widgets.public.include_dirs
        ]

    def test_homebrew_framework_shape(self, project):
        fake = _FakePkgConfig(
            _MAC_PCS,
            variables={
                "prefix": "/opt/homebrew",
                "bindir": "/opt/homebrew/bin",
                "libexecdir": "/opt/homebrew/share/qt/libexec",
            },
        )
        with _patch_pkgconfig(fake):
            qt = find_qt(project, modules=["Widgets"])
        assert qt is not None
        assert qt.is_framework
        # Framework linking must survive into the target's link flags.
        flags = qt.Widgets.public.link_flags
        assert "-framework" in flags
        assert "QtWidgets" in flags
        assert qt.libexec_dir == Path("/opt/homebrew/share/qt/libexec")

    def test_version_constraint_rejects(self, project):
        fake = _FakePkgConfig(_LINUX_PCS, variables={"prefix": "/usr"})
        with _patch_pkgconfig(fake), _no_qtpaths():
            assert (
                find_qt(project, modules=["Widgets"], version=">=6.8", required=False)
                is None
            )

    def test_missing_module_falls_through_to_error(self, project):
        fake = _FakePkgConfig(
            {"Qt6Core": _LINUX_PCS["Qt6Core"]}, variables={"prefix": "/usr"}
        )
        with _patch_pkgconfig(fake), _no_qtpaths():
            with pytest.raises(QtNotFoundError) as exc_info:
                find_qt(project, modules=["Widgets"])
        assert "Widgets" in str(exc_info.value)

    def test_not_required_returns_none(self, project):
        fake = _FakePkgConfig({}, available=False)
        with _patch_pkgconfig(fake), _no_qtpaths():
            assert find_qt(project, modules=["Core"], required=False) is None

    def test_cached_across_calls_and_module_growth(self, project):
        fake = _FakePkgConfig(
            _LINUX_PCS, variables={"prefix": "/usr", "libexecdir": "/usr/libexec"}
        )
        with _patch_pkgconfig(fake):
            first = find_qt(project, modules=["Core"])
            second = find_qt(project, modules=["Widgets"])
        assert first is second
        assert sorted(second.modules) == ["Core", "Widgets"]
        # Version check applies to the cached install too.
        with pytest.raises(QtNotFoundError):
            find_qt(project, modules=["Core"], version=">=6.8")


# =============================================================================
# qtpaths route
# =============================================================================


def _make_qt_tree(
    root: Path, *, framework: bool, modules: list[str], abi: str | None = None
) -> dict[str, str]:
    """Create a fake Qt install tree; return the -query dict for it.

    @p abi mimics a Qt for Android: every library file is named after the
    ABI and the query reports the android-clang spec.
    """
    libs = root / "lib"
    headers = root / "include"
    libexec = root / "libexec"
    bins = root / "bin"
    for d in (libs, headers, libexec, bins):
        d.mkdir(parents=True, exist_ok=True)
    for mod in modules:
        if framework:
            (libs / f"Qt{mod}.framework" / "Headers").mkdir(parents=True)
        else:
            (headers / f"Qt{mod}").mkdir(parents=True)
        if abi is not None and not framework:
            (libs / f"libQt6{mod}{abi}.so").write_text("")
    from pcons.configure.platform import get_platform

    exe_suffix = ".exe" if get_platform().is_windows else ""
    for tool in ("moc", "uic", "rcc"):
        tool_file = libexec / f"{tool}{exe_suffix}"
        tool_file.write_text("#!/bin/sh\n")
        tool_file.chmod(0o755)
    query = {
        "QT_VERSION": "6.6.1",
        "QT_INSTALL_PREFIX": str(root),
        "QT_INSTALL_LIBS": str(libs),
        "QT_INSTALL_HEADERS": str(headers),
        "QT_HOST_BINS": str(bins),
        "QT_HOST_LIBEXECS": str(libexec),
    }
    if abi is not None:
        query["QMAKE_XSPEC"] = "android-clang"
    return query


def _patch_qtpaths(query: dict[str, str], tmp_path: Path):
    """Force the qtpaths probe to find a fake qtpaths6 returning `query`."""
    from pcons.configure.platform import get_platform

    exe = "qtpaths6.exe" if get_platform().is_windows else "qtpaths6"
    fake_tool = tmp_path / "fakebin" / exe
    fake_tool.parent.mkdir(exist_ok=True)
    fake_tool.write_text("#!/bin/sh\n")
    fake_tool.chmod(0o755)
    return (
        patch.object(
            qt_finder,
            "_qtpaths_candidates",
            lambda qt_root: [("qtpaths6", [fake_tool.parent])],
        ),
        patch.object(qt_finder, "_run_query", lambda cmd: dict(query)),
    )


class TestQtPathsRoute:
    def test_unix_library_shape(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt", framework=False, modules=["Core", "Gui", "Widgets"]
        )
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            qt = find_qt(project, modules=["Widgets"])
        assert qt is not None
        assert qt.found_via == "qtpaths6"
        assert qt.version == "6.6.1"
        assert not qt.is_framework
        from pcons.configure.platform import get_platform

        exe = "moc.exe" if get_platform().is_windows else "moc"
        assert qt.libexec_dir == tmp_path / "qt" / "libexec"
        assert qt.tool_path("moc") == tmp_path / "qt" / "libexec" / exe
        widgets = qt.Widgets
        assert "Qt6Widgets" in widgets.public.link_libs
        # Transitive deps: Widgets links the Gui target, Gui links Core.
        dep_targets = [t for t in widgets.public.link_libs if not isinstance(t, str)]
        assert [t.name for t in dep_targets] == ["Qt6Gui"]

    def test_framework_shape(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt", framework=True, modules=["Core", "Gui", "Widgets"]
        )
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            qt = find_qt(project, modules=["Widgets"])
        assert qt is not None
        assert qt.is_framework
        flags = qt.Widgets.public.link_flags
        assert "-framework" in flags and "QtWidgets" in flags

    def test_missing_module_dir(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            with pytest.raises(QtNotFoundError):
                find_qt(project, modules=["Widgets"])


class TestAndroidAbiSuffix:
    """Qt for Android names every library after the ABI it was built for.

    Measured on Qt 6.11.1: an ``android_arm64_v8a`` install holds only
    ``libQt6Core_arm64-v8a.so``, with no unsuffixed file for any module,
    so the host spelling cannot link. The suffix is read back from the
    file names rather than derived from the environment's architecture:
    a mismatched Qt then names a library that exists and fails at link
    with a real architecture error, instead of looking like a broken
    install.
    """

    def _find(self, project, tmp_path, query, modules=("Core",)):
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            return find_qt(project, modules=list(modules))

    @pytest.mark.parametrize("abi", ["_arm64-v8a", "_x86_64", "_armeabi-v7a"])
    def test_module_libraries_carry_the_abi(self, project, tmp_path, abi):
        query = _make_qt_tree(
            tmp_path / "qt", framework=False, modules=["Core", "Gui"], abi=abi
        )
        qt = self._find(project, tmp_path, query, modules=["Gui"])
        assert qt is not None
        assert f"Qt6Gui{abi}" in qt.Gui.public.link_libs
        assert f"Qt6Core{abi}" in qt.Core.public.link_libs

    def test_an_android_install_with_no_suffixed_core_yields_nothing(
        self, project, tmp_path
    ):
        """The gate says Android and the layout disagrees.

        Better to fall back to the bare name than to invent a suffix: the
        probe validates modules before creating targets, so an install this
        odd fails as a module that is not there.
        """
        from pcons.toolchains.qt import finder as qt_finder

        libs = tmp_path / "empty-lib"
        libs.mkdir()

        suffix = qt_finder._android_abi_suffix({"QMAKE_XSPEC": "android-clang"}, libs)

        assert suffix == ""

    def test_the_suffix_reaches_the_link_line(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt", framework=False, modules=["Core"], abi="_arm64-v8a"
        )
        qt = self._find(project, tmp_path, query)
        assert qt is not None
        assert "-lQt6Core_arm64-v8a" in qt.Core.link_flags

    def test_the_target_keeps_its_unsuffixed_identity(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt", framework=False, modules=["Core"], abi="_arm64-v8a"
        )
        qt = self._find(project, tmp_path, query)
        assert qt is not None
        assert qt.Core.name == "Qt6Core"

    def test_a_non_android_install_is_never_probed(self, project, tmp_path):
        """The gate is QMAKE_XSPEC, not the presence of suffixed files.

        A Linux install pays no directory scan, so the tree here carries
        ABI-named libraries that must be ignored outright.
        """
        query = _make_qt_tree(
            tmp_path / "qt", framework=False, modules=["Core"], abi="_arm64-v8a"
        )
        query["QMAKE_XSPEC"] = "linux-g++"
        qt = self._find(project, tmp_path, query)
        assert qt is not None
        assert qt.Core.public.link_libs == ["Qt6Core"]

    def test_a_module_with_no_suffixed_library_is_missing(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt",
            framework=False,
            modules=["Core", "Gui", "Widgets"],
            abi="_arm64-v8a",
        )
        (tmp_path / "qt" / "lib" / "libQt6Widgets_arm64-v8a.so").unlink()
        with pytest.raises(QtNotFoundError):
            self._find(project, tmp_path, query, modules=["Widgets"])

    def test_a_header_only_module_needs_no_library(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt",
            framework=False,
            modules=["Core", "Network", "Qml", "QmlIntegration"],
            abi="_arm64-v8a",
        )
        (tmp_path / "qt" / "lib" / "libQt6QmlIntegration_arm64-v8a.so").unlink()
        qt = self._find(project, tmp_path, query, modules=["Qml"])
        assert qt is not None
        libs = qt.QmlIntegration.public.link_libs
        assert [lib for lib in libs if isinstance(lib, str)] == []


# =============================================================================
# Header-only modules
# =============================================================================


class TestHeaderOnlyModules:
    """QmlIntegration ships headers and no library, and Qml requires it."""

    def _tree(self, project, tmp_path, modules, wanted, framework=False):
        query = _make_qt_tree(tmp_path / "qt", framework=framework, modules=modules)
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            return find_qt(project, modules=wanted)

    def test_qml_closure_carries_qmlintegration(self, project, tmp_path):
        qt = self._tree(
            project,
            tmp_path,
            ["Core", "Network", "Qml", "QmlIntegration"],
            ["Qml"],
        )
        assert qt is not None
        integration = qt.QmlIntegration
        assert [
            lib for lib in integration.public.link_libs if isinstance(lib, str)
        ] == []
        assert not integration.public.link_dirs
        assert str(tmp_path / "qt" / "include" / "QtQmlIntegration") in [
            str(d) for d in integration.public.include_dirs
        ]
        assert "QT_QMLINTEGRATION_LIB" in integration.public.defines
        deps = [t for t in qt.Qml.public.link_libs if not isinstance(t, str)]
        assert "Qt6QmlIntegration" in [t.name for t in deps]
        assert "Qt6Qml" in qt.Qml.public.link_libs

    def test_requested_directly(self, project, tmp_path):
        qt = self._tree(
            project, tmp_path, ["Core", "QmlIntegration"], ["QmlIntegration"]
        )
        assert qt is not None
        assert [
            lib for lib in qt.QmlIntegration.public.link_libs if isinstance(lib, str)
        ] == []

    def test_requested_directly_but_absent_raises(self, project, tmp_path):
        with pytest.raises(QtNotFoundError):
            self._tree(project, tmp_path, ["Core"], ["QmlIntegration"])

    def test_framework_install_without_it_still_resolves_qml(self, project, tmp_path):
        qt = self._tree(
            project,
            tmp_path,
            ["Core", "Network", "Qml"],
            ["Qml"],
            framework=True,
        )
        assert qt is not None
        assert "QmlIntegration" not in qt.modules

    def test_added_by_a_later_call_without_it(self, project, tmp_path):
        query = _make_qt_tree(
            tmp_path / "qt", framework=True, modules=["Core", "Network", "Qml"]
        )
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig({}, available=False)), p1, p2:
            find_qt(project, modules=["Core"])
            qt = find_qt(project, modules=["Qml"])
        assert qt is not None
        assert "Qml" in qt.modules

    def test_pkgconfig_route_unchanged(self, project):
        pcs = dict(_LINUX_PCS)
        pcs["Qt6Qml"] = PackageDescription(
            name="Qt6Qml",
            version="6.7.2",
            include_dirs=["/usr/include/qt6", "/usr/include/qt6/QtQmlIntegration"],
            library_dirs=["/usr/lib64"],
            libraries=["Qt6Qml", "Qt6Network", "Qt6Core"],
            defines=["QT_QML_LIB", "QT_QMLINTEGRATION_LIB"],
            prefix="/usr",
        )
        fake = _FakePkgConfig(pcs, variables={"prefix": "/usr"})
        with _patch_pkgconfig(fake), _no_qtpaths():
            qt = find_qt(project, modules=["Qml"])
        assert qt is not None
        assert sorted(qt.modules) == ["Core", "Qml"]


# =============================================================================
# Platform requirements
# =============================================================================


class TestPlatformRequirements:
    def _qt_with_core(self, project, lib="Qt6Core"):
        core = qt_finder.ImportedTarget.from_package(
            PackageDescription(name=lib, version="6.7.0", libraries=[lib])
        )
        return qt_finder.QtPackage(
            version="6.7.0",
            prefix=Path("/qt"),
            bin_dir=Path("/qt/bin"),
            libexec_dir=Path("/qt/libexec"),
            is_framework=False,
            found_via="test",
            modules={"Core": core},
            module_factory=lambda name: None,
        )

    def test_msvc_flags_on_windows(self, project):
        qt = self._qt_with_core(project)
        env = SimpleNamespace(toolchain=SimpleNamespace(name="msvc"), variant="release")
        fake_platform = SimpleNamespace(is_windows=True)
        with patch.object(qt_finder, "get_platform", lambda: fake_platform):
            _apply_platform_requirements(qt, env)  # type: ignore[arg-type]
        flags = qt.modules["Core"].public.compile_flags
        assert "/Zc:__cplusplus" in flags
        assert "/permissive-" in flags
        assert "/EHsc" in flags  # Qt headers use throw; off by default on MSVC

    def test_debug_d_suffix_on_windows(self, project):
        qt = self._qt_with_core(project)
        env = SimpleNamespace(toolchain=SimpleNamespace(name="msvc"), variant="debug")
        fake_platform = SimpleNamespace(is_windows=True)
        with patch.object(qt_finder, "get_platform", lambda: fake_platform):
            _apply_platform_requirements(qt, env)  # type: ignore[arg-type]
        assert "Qt6Cored" in qt.modules["Core"].public.link_libs

    def test_no_flags_off_windows(self, project):
        qt = self._qt_with_core(project)
        env = SimpleNamespace(toolchain=SimpleNamespace(name="llvm"), variant="debug")
        fake_platform = SimpleNamespace(is_windows=False, is_macos=False)
        with patch.object(qt_finder, "get_platform", lambda: fake_platform):
            _apply_platform_requirements(qt, env)  # type: ignore[arg-type]
        assert qt.modules["Core"].public.compile_flags == []
        assert "Qt6Cored" not in qt.modules["Core"].public.link_libs

    @pytest.mark.parametrize(
        ("qt_version", "expected"),
        [("6.9.3", True), ("6.10.0", False)],
    )
    def test_apple_silicon_arm_acle_workaround(self, project, qt_version, expected):
        # Qt < 6.10's qyieldcpu.h calls __yield() bare; clang errors
        # unless <arm_acle.h> is pre-included. Fixed upstream in 6.10.
        qt = self._qt_with_core(project)
        qt.version = qt_version
        fake_platform = SimpleNamespace(is_windows=False, is_macos=True, arch="arm64")
        with patch.object(qt_finder, "get_platform", lambda: fake_platform):
            _apply_platform_requirements(qt, None)
        flags = qt.modules["Core"].public.compile_flags
        assert (["-include", "arm_acle.h"] == flags[-2:]) is expected

    def test_applied_once(self, project):
        qt = self._qt_with_core(project)
        env = SimpleNamespace(toolchain=SimpleNamespace(name="msvc"), variant="release")
        fake_platform = SimpleNamespace(is_windows=True)
        with patch.object(qt_finder, "get_platform", lambda: fake_platform):
            _apply_platform_requirements(qt, env)  # type: ignore[arg-type]
            _apply_platform_requirements(qt, env)  # type: ignore[arg-type]
        assert qt.modules["Core"].public.compile_flags.count("/permissive-") == 1


# =============================================================================
# Helpers
# =============================================================================


class TestPlatformRequirementsReentrant:
    """Requirements apply per-module across repeated find_qt calls."""

    def _package(self, libs_by_module):
        modules = {
            name: qt_finder.ImportedTarget.from_package(
                PackageDescription(name=f"Qt6{name}", libraries=libs)
            )
            for name, libs in libs_by_module.items()
        }
        return qt_finder.QtPackage(
            version="6.7.0",
            prefix=Path("/qt"),
            bin_dir=Path("/qt/bin"),
            libexec_dir=Path("/qt/libexec"),
            is_framework=False,
            found_via="test",
            modules=modules,
            module_factory=lambda name: None,
        )

    def test_dsuffix_applies_to_modules_added_later(self, project):
        qt = self._package({"Core": ["Qt6Core"]})
        env = SimpleNamespace(toolchain=SimpleNamespace(name="msvc"), variant="debug")
        fake = SimpleNamespace(is_windows=True, is_macos=False)
        with patch.object(qt_finder, "get_platform", lambda: fake):
            _apply_platform_requirements(qt, env)
            # A later find_qt call adds a module: it must get the suffix.
            qt.modules["Network"] = qt_finder.ImportedTarget.from_package(
                PackageDescription(name="Qt6Network", libraries=["Qt6Network"])
            )
            _apply_platform_requirements(qt, env)
        assert "Qt6Cored" in qt.modules["Core"].public.link_libs
        assert "Qt6Networkd" in qt.modules["Network"].public.link_libs

    def test_dsuffix_handles_names_ending_in_d(self, project):
        # Qt6VirtualKeyboard genuinely ends in 'd'; per-module tracking
        # (not string sniffing) keeps the rewrite exact and idempotent.
        qt = self._package({"VirtualKeyboard": ["Qt6VirtualKeyboard"]})
        env = SimpleNamespace(toolchain=SimpleNamespace(name="msvc"), variant="debug")
        fake = SimpleNamespace(is_windows=True, is_macos=False)
        with patch.object(qt_finder, "get_platform", lambda: fake):
            _apply_platform_requirements(qt, env)
            _apply_platform_requirements(qt, env)
        assert qt.modules["VirtualKeyboard"].public.link_libs == ["Qt6VirtualKeyboardd"]

    def test_msvc_flags_apply_when_msvc_env_arrives_later(self, project):
        qt = self._package({"Core": ["Qt6Core"]})
        fake = SimpleNamespace(is_windows=True, is_macos=False)
        with patch.object(qt_finder, "get_platform", lambda: fake):
            _apply_platform_requirements(qt, None)  # first call: no env
            assert "/permissive-" not in qt.modules["Core"].public.compile_flags
            env = SimpleNamespace(
                toolchain=SimpleNamespace(name="clang-cl"), variant="release"
            )
            _apply_platform_requirements(qt, env)
        assert "/permissive-" in qt.modules["Core"].public.compile_flags


class TestQtRoot:
    def test_nonexistent_qt_root_raises(self, project):
        with pytest.raises(QtNotFoundError, match="does not exist"):
            find_qt(project, modules=["Core"], qt_root="/nope/qt")

    def test_invalid_version_constraint_raises(self, project):
        fake = _FakePkgConfig(_LINUX_PCS, variables={"prefix": "/usr"})
        with _patch_pkgconfig(fake), _no_qtpaths():
            with pytest.raises(ValueError, match="version constraint"):
                find_qt(project, modules=["Core"], version="banana")


class TestVersionSatisfies:
    @pytest.mark.parametrize(
        ("found", "constraint", "expected"),
        [
            ("6.9.3", ">=6.4", True),
            ("6.9.3", ">=6.9.3", True),
            ("6.9.3", ">=7", False),
            ("6.9", "=6.9.0", True),
            ("6.3.1", "<6.4", True),
            ("6.3.1", ">6.3.1", False),
            ("6.10.0", ">=6.9", True),  # numeric, not lexicographic
        ],
    )
    def test_constraints(self, found, constraint, expected):
        assert _version_satisfies(found, constraint) is expected


class TestClosureOrder:
    def test_deps_before_dependents(self):
        order = _closure_in_dep_order(["Widgets"])
        assert order.index("Core") < order.index("Gui") < order.index("Widgets")

    def test_unknown_module_depends_on_core(self):
        order = _closure_in_dep_order(["WebEngineWidgets"])
        assert order.index("Core") < order.index("WebEngineWidgets")


# =============================================================================
# Per-environment discovery
# =============================================================================


def _qt_env(project, name=None):
    from tests.toolchains._qt_test_utils import fake_qt_toolchain

    return project.Environment(toolchain=fake_qt_toolchain(), name=name)


def _prefixed_pcs(prefix):
    return {
        name: PackageDescription(
            name=name,
            version=pkg.version,
            include_dirs=[f"{prefix}/include/qt6"],
            library_dirs=[f"{prefix}/lib"],
            libraries=list(pkg.libraries),
            defines=list(pkg.defines),
            prefix=prefix,
        )
        for name, pkg in _LINUX_PCS.items()
    }


class _PerPrefixPkgConfig:
    """A pkg-config stand-in whose answers change between calls."""

    def __init__(self, prefixes):
        self._prefixes = list(prefixes)
        self.calls = 0

    def _current(self):
        return self._prefixes[min(self.calls, len(self._prefixes) - 1)]

    def is_available(self):
        return True

    def find(self, name, version=None, components=None):
        return _prefixed_pcs(self._current()).get(name)

    def get_variable(self, name, var):
        return {"prefix": self._current()}.get(var)


class TestPerEnvironmentInstalls:
    def test_two_named_environments_get_two_installs(self, project):
        host = _qt_env(project, "host")
        mcu = _qt_env(project, "mcu")
        fake = _PerPrefixPkgConfig(["/usr", "/opt/cross"])
        with _patch_pkgconfig(fake), _no_qtpaths():
            first = find_qt(project, host, modules=["Core"])
            fake.calls = 1
            second = find_qt(project, mcu, modules=["Core"])
        assert first is not second
        assert first.prefix == Path("/usr")
        assert second.prefix == Path("/opt/cross")
        assert qt_finder.qt_install(project, host) is first
        assert qt_finder.qt_install(project, mcu) is second

    def test_module_targets_belong_to_their_environment(self, project):
        host = _qt_env(project, "host")
        mcu = _qt_env(project, "mcu")
        fake = _PerPrefixPkgConfig(["/usr", "/opt/cross"])
        with _patch_pkgconfig(fake), _no_qtpaths():
            first = find_qt(project, host, modules=["Core"])
            fake.calls = 1
            second = find_qt(project, mcu, modules=["Core"])
        assert first.Core.env is host
        assert second.Core.env is mcu
        assert project.get_target("Qt6Core@host") is first.Core
        assert project.get_target("Qt6Core@mcu") is second.Core

    def test_same_environment_probes_once(self, project):
        host = _qt_env(project, "host")
        fake = _PerPrefixPkgConfig(["/usr"])
        with _patch_pkgconfig(fake), _no_qtpaths():
            first = find_qt(project, host, modules=["Core"])
            second = find_qt(project, host, modules=["Widgets"])
        assert first is second
        assert sorted(second.modules) == ["Core", "Widgets"]

    def test_unnamed_environments_share_one_install(self, project):
        one = _qt_env(project)
        two = _qt_env(project)
        fake = _PerPrefixPkgConfig(["/usr", "/opt/cross"])
        with _patch_pkgconfig(fake), _no_qtpaths():
            first = find_qt(project, one, modules=["Core"])
            fake.calls = 1
            second = find_qt(project, two, modules=["Core"])
        assert first is second

    def test_no_environment_uses_the_inherited_one(self, project):
        host = _qt_env(project, "host")
        fake = _PerPrefixPkgConfig(["/usr", "/opt/cross"])
        with _patch_pkgconfig(fake), _no_qtpaths():
            bare = find_qt(project, modules=["Core"])
            fake.calls = 1
            named = find_qt(project, host, modules=["Core"])
        assert bare is named
        assert bare.Core.env is host

    def test_qt_install_is_none_before_discovery(self, project):
        assert qt_finder.qt_install(project) is None
        assert qt_finder.qt_install(project, _qt_env(project, "host")) is None


def _spy(name: str, log: list[str] | None = None):
    """Patch a module-level probe with a delegating recorder.

    Returns (patcher, calls); `calls` grows one entry per call, so a test
    can assert a probe never ran rather than only that its result was
    unused. A shared `log` records the order several probes ran in.
    """
    real = getattr(qt_finder, name)
    calls: list[tuple] = []

    def wrapper(*args):
        calls.append(args)
        if log is not None:
            log.append(name)
        return real(*args)

    return patch.object(qt_finder, name, wrapper), calls


class TestProbeSelection:
    """`probe=` picks the discovery route.

    A cross Qt ships .pc files describing the target and keeps its
    moc/uic/rcc for the build machine, which pkg-config cannot express:
    only the qtpaths probe reads QT_HOST_BINS/QT_HOST_LIBEXECS, and
    pkg-config answers first by default.
    """

    def test_qtpaths_skips_pkgconfig_entirely(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        spy, calls = _spy("_probe_pkgconfig")
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), p1, p2, spy:
            qt = find_qt(project, modules=["Core"], probe="qtpaths")
        assert calls == []
        assert qt.found_via == "qtpaths6"
        assert qt.prefix == tmp_path / "qt"

    def test_pkgconfig_skips_qtpaths_entirely(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        spy, calls = _spy("_probe_qtpaths")
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), p1, p2, spy:
            qt = find_qt(project, modules=["Core"], probe="pkg-config")
        assert calls == []
        assert qt.found_via == "pkg-config"

    def test_pkgconfig_only_fails_instead_of_falling_back(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        spy, calls = _spy("_probe_qtpaths")
        empty = _FakePkgConfig({}, available=False)
        with _patch_pkgconfig(empty), p1, p2, spy:
            with pytest.raises(QtNotFoundError, match="pkg-config"):
                find_qt(project, modules=["Core"], probe="pkg-config")
        assert calls == []

    def test_the_default_is_auto_and_keeps_the_order(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        order: list[str] = []
        pc_patch, _ = _spy("_probe_pkgconfig", order)
        qp_patch, _ = _spy("_probe_qtpaths", order)
        p1, p2 = _patch_qtpaths(query, tmp_path)
        empty = _FakePkgConfig({}, available=False)
        with _patch_pkgconfig(empty), p1, p2, pc_patch, qp_patch:
            qt = find_qt(project, modules=["Core"])
        assert order == ["_probe_pkgconfig", "_probe_qtpaths"]
        assert qt.found_via == "qtpaths6"

    def test_auto_still_prefers_pkgconfig(self, project, tmp_path):
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        spy, calls = _spy("_probe_qtpaths")
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), p1, p2, spy:
            qt = find_qt(project, modules=["Core"], probe="auto")
        assert calls == []
        assert qt.found_via == "pkg-config"

    def test_one_probe_per_environment(self, project, tmp_path):
        """The cross shape: host via pkg-config, target via qtpaths."""
        host = _qt_env(project, "host")
        cross = _qt_env(project, "cross")
        query = _make_qt_tree(tmp_path / "qt", framework=False, modules=["Core"])
        p1, p2 = _patch_qtpaths(query, tmp_path)
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), p1, p2:
            on_host = find_qt(project, host, modules=["Core"])
            on_cross = find_qt(project, cross, modules=["Core"], probe="qtpaths")
        assert on_host is not on_cross
        assert on_host.found_via == "pkg-config"
        assert on_cross.found_via == "qtpaths6"
        assert qt_finder.qt_install(project, cross) is on_cross

    def test_an_unknown_probe_raises(self, project):
        with pytest.raises(ValueError, match="probe='qmake'"):
            find_qt(project, modules=["Core"], probe=cast(QtProbe, "qmake"))

    def test_a_probe_disagreeing_with_the_cache_warns(self, project, caplog):
        host = _qt_env(project, "host")
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), _no_qtpaths():
            first = find_qt(project, host, modules=["Core"])
            with caplog.at_level("WARNING", logger=qt_finder.logger.name):
                again = find_qt(project, host, modules=["Core"], probe="qtpaths")
        assert again is first
        assert "probe='qtpaths'" in caplog.text
        assert "already" in caplog.text

    def test_a_qt_root_disagreeing_with_the_cache_warns(
        self, project, tmp_path, caplog
    ):
        """Discovery is cached per environment, so a later root cannot move it."""
        host = _qt_env(project, "host")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), _no_qtpaths():
            first = find_qt(project, host, modules=["Core"])
            with caplog.at_level("WARNING", logger=qt_finder.logger.name):
                again = find_qt(project, host, modules=["Core"], qt_root=elsewhere)
        assert again is first
        assert f"qt_root={elsewhere}" in caplog.text
        assert "already" in caplog.text

    def test_both_disagreeing_are_named_together(self, project, tmp_path, caplog):
        host = _qt_env(project, "host")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), _no_qtpaths():
            find_qt(project, host, modules=["Core"])
            with caplog.at_level("WARNING", logger=qt_finder.logger.name):
                find_qt(
                    project,
                    host,
                    modules=["Core"],
                    qt_root=elsewhere,
                    probe="qtpaths",
                )
        assert f"qt_root={elsewhere} and probe='qtpaths'" in caplog.text

    def test_a_matching_probe_does_not_warn(self, project, caplog):
        host = _qt_env(project, "host")
        with _patch_pkgconfig(_FakePkgConfig(_LINUX_PCS)), _no_qtpaths():
            find_qt(project, host, modules=["Core"])
            with caplog.at_level("WARNING", logger=qt_finder.logger.name):
                find_qt(project, host, modules=["Core"], probe="pkg-config")
        assert caplog.text == ""

# SPDX-License-Identifier: MIT
"""Tests for pcons.integrations.rez.env.

Uses monkeypatch.setenv + tmp_path to fake a rez resolve, so these tests
run without rez installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.integrations.rez import env as rez_env
from pcons.integrations.rez.env import (
    ResolvedPackage,
    is_in_rez_resolve,
    package_description,
    resolved_packages,
    rez_environment,
)


def _make_pkg_install(
    tmp_path: Path,
    name: str,
    version: str = "0.1.0",
    *,
    with_include: bool = True,
    with_lib: bool = True,
    with_lib_archive: bool = True,
    pkgconfig: bool = False,
) -> Path:
    """Create a fake rez install root for a package and return its path."""
    root = tmp_path / "rez_packages" / name / version
    root.mkdir(parents=True)
    if with_include:
        (root / "include").mkdir()
        (root / "include" / f"{name}.h").write_text(f"// header for {name}\n")
    if with_lib:
        (root / "lib").mkdir()
        if with_lib_archive:
            (root / "lib" / f"lib{name}.a").write_bytes(b"\x00")
    if pkgconfig:
        pc_dir = root / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True, exist_ok=True)
        (pc_dir / f"{name}.pc").write_text(
            f"Name: {name}\nDescription: test fixture\nVersion: {version}\n"
            f"Cflags: -I{root}/include\nLibs: -L{root}/lib -l{name}\n"
        )
    return root


def _set_rez_resolve(
    monkeypatch: pytest.MonkeyPatch,
    *packages: tuple[str, str, Path],
) -> None:
    """Set REZ_USED_RESOLVE and per-package REZ_<NAME>_ROOT."""
    monkeypatch.setenv(
        "REZ_USED_RESOLVE",
        " ".join(f"{n}-{v}" for n, v, _ in packages),
    )
    for name, _version, root in packages:
        env_name = name.upper().replace("-", "_")
        monkeypatch.setenv(f"REZ_{env_name}_ROOT", str(root))


class TestIsInRezResolve:
    def test_false_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REZ_USED_RESOLVE", raising=False)
        assert is_in_rez_resolve() is False

    def test_false_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REZ_USED_RESOLVE", "")
        assert is_in_rez_resolve() is False

    def test_true_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REZ_USED_RESOLVE", "foo-1.0")
        assert is_in_rez_resolve() is True


class TestResolvedPackages:
    def test_empty_outside_rez(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REZ_USED_RESOLVE", raising=False)
        assert resolved_packages() == []

    def test_single_package(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = _make_pkg_install(tmp_path, "hello_lib")
        _set_rez_resolve(monkeypatch, ("hello_lib", "0.1.0", root))

        result = resolved_packages()

        assert result == [ResolvedPackage("hello_lib", "0.1.0", root)]

    def test_multiple_packages(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        a_root = _make_pkg_install(tmp_path, "alpha", "1.0")
        b_root = _make_pkg_install(tmp_path, "beta", "2.3.4")
        _set_rez_resolve(
            monkeypatch,
            ("alpha", "1.0", a_root),
            ("beta", "2.3.4", b_root),
        )

        result = resolved_packages()

        assert len(result) == 2
        assert ResolvedPackage("alpha", "1.0", a_root) in result
        assert ResolvedPackage("beta", "2.3.4", b_root) in result

    def test_skips_entries_without_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Implicits like "~platform==osx" have no REZ_<NAME>_ROOT and should be ignored.
        root = _make_pkg_install(tmp_path, "real_pkg")
        monkeypatch.setenv("REZ_USED_RESOLVE", "real_pkg-0.1.0 implicit-9.9")
        monkeypatch.setenv("REZ_REAL_PKG_ROOT", str(root))
        monkeypatch.delenv("REZ_IMPLICIT_ROOT", raising=False)

        result = resolved_packages()

        assert result == [ResolvedPackage("real_pkg", "0.1.0", root)]

    def test_version_with_embedded_dash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Rez versions can contain '-' (pre-release/build identifiers).
        # Package names cannot, so the FIRST '-' is the separator.
        root = _make_pkg_install(tmp_path, "foo", version="1.0-beta.2")
        monkeypatch.setenv("REZ_USED_RESOLVE", "foo-1.0-beta.2")
        monkeypatch.setenv("REZ_FOO_ROOT", str(root))

        result = resolved_packages()

        assert result == [ResolvedPackage("foo", "1.0-beta.2", root)]


class TestPackageDescription:
    def test_builds_includes_and_libs(self, tmp_path: Path) -> None:
        root = _make_pkg_install(tmp_path, "hello_lib")

        pd = package_description("hello_lib", "0.1.0", root)

        assert pd.name == "hello_lib"
        assert pd.version == "0.1.0"
        assert pd.prefix == str(root)
        assert pd.found_by == "rez"
        assert str(root / "include") in pd.include_dirs
        assert str(root / "lib") in pd.library_dirs
        assert "hello_lib" in pd.libraries

    def test_omits_missing_dirs(self, tmp_path: Path) -> None:
        root = _make_pkg_install(
            tmp_path,
            "headers_only",
            with_lib=False,
        )

        pd = package_description("headers_only", "1.0", root)

        assert str(root / "include") in pd.include_dirs
        assert pd.library_dirs == []
        assert pd.libraries == []

    def test_omits_lib_when_no_archive(self, tmp_path: Path) -> None:
        # lib/ exists but has no lib<name>.* — directory is recorded but the
        # library isn't auto-added.
        root = _make_pkg_install(
            tmp_path,
            "lib_no_archive",
            with_lib_archive=False,
        )

        pd = package_description("lib_no_archive", "1.0", root)

        assert str(root / "lib") in pd.library_dirs
        assert pd.libraries == []


class TestPkgConfigOverride:
    """Cover the pkg-config path: a ``.pc`` file overrides convention scan."""

    @pytest.fixture(autouse=True)
    def _require_pkg_config(self) -> None:
        import shutil

        if shutil.which("pkg-config") is None:
            pytest.skip("pkg-config not installed")

    def test_pkgconfig_overrides_convention(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When a .pc file is present, it's the authoritative source."""
        # Convention scan would find: include/, lib/, libfancy.a (i.e. -lfancy).
        # The .pc file declares a *different* lib name (-lfancy_alt) plus a
        # define and an extra include dir. The override semantics mean we see
        # exactly what the .pc file lists, not a merged superset.
        root = _make_pkg_install(tmp_path, "fancy", pkgconfig=False)
        extra_inc = root / "include" / "fancy-2"
        extra_inc.mkdir(parents=True)
        pc_dir = root / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True)
        (pc_dir / "fancy.pc").write_text(
            f"Name: fancy\n"
            f"Description: test fixture\n"
            f"Version: 0.1.0\n"
            f"Cflags: -I{extra_inc} -DUSING_FANCY\n"
            f"Libs: -L{root}/lib -lfancy_alt\n"
        )

        monkeypatch.setenv("PKG_CONFIG_PATH", str(pc_dir))

        pd = package_description("fancy", "0.1.0", root)

        assert pd.found_by == "rez+pkg-config"
        assert pd.prefix == str(root)
        # Only what the .pc file declared:
        assert pd.include_dirs == [str(extra_inc)]
        assert pd.libraries == ["fancy_alt"]
        assert "USING_FANCY" in pd.defines

    def test_falls_back_to_convention_when_pkg_config_misses(
        self, tmp_path: Path
    ) -> None:
        """No matching .pc file → return the convention-scan PD."""
        # Create a pkgconfig directory with an UNRELATED .pc file so
        # _pkgconfig_dirs() triggers but pkg-config can't resolve our package.
        root = _make_pkg_install(tmp_path, "lonely")
        pc_dir = root / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True)
        (pc_dir / "other.pc").write_text(
            "Name: other\nDescription: test fixture\nVersion: 0.0.0\nCflags:\nLibs:\n"
        )

        pd = package_description("lonely", "0.1.0", root)

        assert pd.found_by == "rez"
        assert "lonely" in pd.libraries


class TestRezEnvironment:
    def test_no_rez_is_noop(
        self, monkeypatch: pytest.MonkeyPatch, gcc_toolchain
    ) -> None:
        from pcons.core.environment import Environment

        monkeypatch.delenv("REZ_USED_RESOLVE", raising=False)
        env = Environment(toolchain=gcc_toolchain)
        # Snapshot include list to confirm env is untouched.
        before = list(env.cxx.includes)

        assert rez_environment(env) is None
        assert env.cxx.includes == before

    def test_applies_to_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        gcc_toolchain,
    ) -> None:
        from pcons.core.environment import Environment

        root = _make_pkg_install(tmp_path, "hello_lib")
        _set_rez_resolve(monkeypatch, ("hello_lib", "0.1.0", root))

        env = Environment(toolchain=gcc_toolchain)
        assert rez_environment(env) is None

        assert str(root / "include") in env.cxx.includes
        assert str(root / "lib") in env.link.libdirs
        assert "hello_lib" in env.link.libs

    def test_filters_by_packages_arg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        gcc_toolchain,
    ) -> None:
        from pcons.core.environment import Environment

        a_root = _make_pkg_install(tmp_path, "alpha")
        b_root = _make_pkg_install(tmp_path, "beta")
        _set_rez_resolve(
            monkeypatch,
            ("alpha", "1.0", a_root),
            ("beta", "1.0", b_root),
        )

        env = Environment(toolchain=gcc_toolchain)
        rez_environment(env, packages=["alpha"])

        assert "alpha" in env.link.libs
        assert "beta" not in env.link.libs


# Sanity: the module's public API is what __init__.py re-exports.
def test_module_exports_match_init() -> None:
    from pcons.integrations import rez

    for name in (
        "ResolvedPackage",
        "is_in_rez_resolve",
        "package_description",
        "resolved_packages",
        "rez_environment",
    ):
        assert hasattr(rez, name), f"pcons.integrations.rez missing: {name}"
        assert getattr(rez, name) is getattr(rez_env, name) or name == "RezFinder"

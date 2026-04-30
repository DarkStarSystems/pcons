# SPDX-License-Identifier: MIT
"""Tests for pcons.integrations.rez.finder.RezFinder."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from pcons.integrations.rez import RezFinder


def _make_pkg(tmp_path: Path, name: str, version: str = "0.1.0") -> Path:
    """Same shape as the helper in test_rez_env, kept local to avoid coupling."""
    root = tmp_path / "rez_packages" / name / version
    (root / "include").mkdir(parents=True)
    (root / "include" / f"{name}.h").write_text("// header\n")
    (root / "lib").mkdir()
    (root / "lib" / f"lib{name}.a").write_bytes(b"\x00")
    return root


def _set_resolve(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv("REZ_USED_RESOLVE", f"{name}-0.1.0")
    monkeypatch.setenv(f"REZ_{name.upper()}_ROOT", str(root))


def test_name_property() -> None:
    assert RezFinder().name == "rez"


def test_is_available_outside_rez(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REZ_USED_RESOLVE", raising=False)
    assert RezFinder().is_available() is False


def test_is_available_inside_rez(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REZ_USED_RESOLVE", "foo-1.0")
    assert RezFinder().is_available() is True


def test_find_outside_rez_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REZ_USED_RESOLVE", raising=False)
    assert RezFinder().find("anything") is None


def test_find_returns_resolved_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_pkg(tmp_path, "hello_lib")
    _set_resolve(monkeypatch, "hello_lib", root)

    pd = RezFinder().find("hello_lib")

    assert pd is not None
    assert pd.name == "hello_lib"
    assert pd.found_by == "rez"
    assert "hello_lib" in pd.libraries


def test_find_unknown_package_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_pkg(tmp_path, "hello_lib")
    _set_resolve(monkeypatch, "hello_lib", root)

    assert RezFinder().find("not_in_resolve") is None


def test_components_arg_emits_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_pkg(tmp_path, "hello_lib")
    _set_resolve(monkeypatch, "hello_lib", root)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pd = RezFinder().find("hello_lib", components=["foo"])

    assert pd is not None
    assert any("components" in str(w.message) for w in caught)


def test_integrates_with_finder_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RezFinder should plug into FinderChain just like other finders."""
    from pcons.packages.finders.base import FinderChain

    root = _make_pkg(tmp_path, "hello_lib")
    _set_resolve(monkeypatch, "hello_lib", root)

    chain = FinderChain([RezFinder()])
    pd = chain.find("hello_lib")

    assert pd is not None
    assert pd.found_by == "rez"

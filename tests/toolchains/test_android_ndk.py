# SPDX-License-Identifier: MIT
"""Tests for the android_ndk fixture and the NDK search behind it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import support
from tests.support import NO_ANDROID_NDK, find_android_ndk, is_android_ndk

_ALL_VARS = (
    "ANDROID_NDK_HOME",
    "ANDROID_NDK_ROOT",
    "ANDROID_NDK",
    "ANDROID_HOME",
    "ANDROID_SDK_ROOT",
)


@pytest.fixture
def clean_android_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def make_fake_ndk(root: Path, host_tag: str = "linux-x86_64") -> Path:
    """A directory shaped enough like an NDK for the search to accept it."""
    (root / "toolchains" / "llvm" / "prebuilt" / host_tag / "bin").mkdir(parents=True)
    return root


def test_a_directory_without_a_prebuilt_toolchain_is_not_an_ndk(tmp_path: Path) -> None:
    (tmp_path / "toolchains" / "llvm" / "prebuilt").mkdir(parents=True)
    assert not is_android_ndk(tmp_path)
    assert not is_android_ndk(tmp_path / "missing")


def test_a_prebuilt_toolchain_makes_it_an_ndk(tmp_path: Path) -> None:
    assert is_android_ndk(make_fake_ndk(tmp_path))


def test_any_host_tag_counts(tmp_path: Path) -> None:
    assert is_android_ndk(make_fake_ndk(tmp_path, host_tag="darwin-x86_64"))


@pytest.mark.parametrize("var", ["ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "ANDROID_NDK"])
def test_each_environment_variable_is_honoured(
    var: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    ndk = make_fake_ndk(tmp_path / "ndk")
    monkeypatch.setenv(var, str(ndk))
    assert find_android_ndk() == ndk


def test_the_environment_wins_over_the_conventional_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    fallback = make_fake_ndk(tmp_path / "opt")
    chosen = make_fake_ndk(tmp_path / "chosen")
    monkeypatch.setattr(support, "ANDROID_NDK_FALLBACKS", (fallback,))
    monkeypatch.setenv("ANDROID_NDK_HOME", str(chosen))
    assert find_android_ndk() == chosen


def test_a_stale_variable_falls_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    fallback = make_fake_ndk(tmp_path / "opt")
    monkeypatch.setattr(support, "ANDROID_NDK_FALLBACKS", (fallback,))
    monkeypatch.setenv("ANDROID_NDK_HOME", str(tmp_path / "uninstalled"))
    assert find_android_ndk() == fallback


@pytest.mark.parametrize("var", ["ANDROID_HOME", "ANDROID_SDK_ROOT"])
def test_the_sdk_supplies_the_highest_revision(
    var: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    sdk = tmp_path / "sdk"
    for revision in ("27.3.13750724", "28.2.13676358", "29.0.14206865"):
        make_fake_ndk(sdk / "ndk" / revision)
    monkeypatch.setenv(var, str(sdk))
    found = find_android_ndk()
    assert found is not None
    assert found.name == "29.0.14206865"


def test_an_unusable_revision_is_skipped_for_a_usable_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    sdk = tmp_path / "sdk"
    (sdk / "ndk" / "29.0.14206865").mkdir(parents=True)
    make_fake_ndk(sdk / "ndk" / "28.2.13676358")
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    found = find_android_ndk()
    assert found is not None
    assert found.name == "28.2.13676358"


def test_a_non_numeric_revision_does_not_break_the_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    sdk = tmp_path / "sdk"
    make_fake_ndk(sdk / "ndk" / "side-by-side")
    make_fake_ndk(sdk / "ndk" / "28.2.13676358")
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    found = find_android_ndk()
    assert found is not None
    assert found.name == "28.2.13676358"


def test_nothing_anywhere_finds_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    monkeypatch.setattr(support, "ANDROID_NDK_FALLBACKS", (tmp_path / "absent",))
    monkeypatch.setenv("ANDROID_NDK_HOME", str(tmp_path / "absent"))
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path / "absent"))
    assert find_android_ndk() is None


def test_the_fixture_skips_rather_than_fails(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_android_env: None,
) -> None:
    monkeypatch.setattr(support, "ANDROID_NDK_FALLBACKS", (tmp_path / "absent",))
    with pytest.raises(pytest.skip.Exception, match="no Android NDK found"):
        request.getfixturevalue("android_ndk")


def test_the_skip_reason_names_what_to_set() -> None:
    assert "ANDROID_NDK_HOME" in NO_ANDROID_NDK
    assert "ANDROID_HOME/ndk" in NO_ANDROID_NDK


def test_the_fixture_yields_a_usable_ndk(android_ndk: Path) -> None:
    assert is_android_ndk(android_ndk)
    assert (android_ndk / "source.properties").is_file()


@pytest.mark.skipif(
    not os.environ.get("PCONS_REQUIRE_ANDROID_NDK"),
    reason="only a runner that promises an NDK may demand one",
)
def test_an_ndk_is_present_where_one_was_promised() -> None:
    assert find_android_ndk() is not None, NO_ANDROID_NDK

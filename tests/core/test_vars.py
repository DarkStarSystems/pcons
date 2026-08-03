# SPDX-License-Identifier: MIT
"""Tests for pcons core vars."""

from __future__ import annotations

import json
from pathlib import Path

from pcons import (
    get_var,
    get_variant,
)
from pcons.core.cache import CACHE_FILE, BuildCache
from pcons.core.vars import _clear_cli_vars


class TestGetVar:
    """Tests for get_var and get_variant functions."""

    def test_get_var_default(self, monkeypatch) -> None:
        """Test get_var returns default when not set."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", "default_value") == "default_value"

    def test_get_var_no_default_returns_none(self, monkeypatch) -> None:
        """Test get_var returns None when not set and no default given."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR") is None

    def test_get_var_with_none_default(self, monkeypatch) -> None:
        """Test get_var returns None when default is None."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", None) is None

    def test_get_var_from_env(self, monkeypatch) -> None:
        """Test get_var reads from environment variable."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.setenv("TEST_VAR", "env_value")

        assert get_var("TEST_VAR", "default") == "env_value"

    def test_get_var_from_pcons_vars(self, monkeypatch) -> None:
        """Test get_var reads from PCONS_VARS JSON."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.setenv("PCONS_VARS", '{"TEST_VAR": "cli_value"}')
        monkeypatch.setenv("TEST_VAR", "env_value")  # Should be overridden

        assert get_var("TEST_VAR", "default") == "cli_value"

    def test_get_variant_default(self, monkeypatch) -> None:
        """Test get_variant returns default when not set."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "release"

    def test_get_variant_from_pcons_variant(self, monkeypatch) -> None:
        """Test get_variant reads from PCONS_VARIANT (CLI sets this)."""
        monkeypatch.setenv("PCONS_VARIANT", "debug")
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "debug"

    def test_get_variant_from_variant_env(self, monkeypatch) -> None:
        """Test get_variant falls back to VARIANT env var."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("release") == "debug"

    def test_get_variant_pcons_variant_takes_precedence(self, monkeypatch) -> None:
        """Test PCONS_VARIANT takes precedence over VARIANT."""
        monkeypatch.setenv("PCONS_VARIANT", "release")
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("default") == "release"


class TestPersistedVars:
    """Tests for cross-run persistence of CLI-configured vars via the build cache.

    Precedence: PCONS_VARS (current CLI) > OS env > persisted cache > default.
    """

    def _reset(self, monkeypatch, build_dir: Path) -> None:
        """Simulate a fresh run: clear cached state, point at build_dir."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("MY_VAR", raising=False)
        monkeypatch.setenv("PCONS_BUILD_DIR", str(build_dir))

    def _persist_vars(self, build_dir: Path, variables: dict[str, str]) -> None:
        """Persist configured vars into the build cache, merging with existing."""
        cache = BuildCache(build_dir)
        cache.set("vars", {**cache.get("vars", {}), **variables})

    def test_reads_persisted_cache(self, monkeypatch, tmp_path) -> None:
        """A value configured in a prior run is read back with no CLI/env set."""
        self._reset(monkeypatch, tmp_path)
        self._persist_vars(tmp_path, {"MY_VAR": "42"})
        _clear_cli_vars()  # next run

        assert get_var("MY_VAR") == "42"

    def test_cli_overrides_cached(self, monkeypatch, tmp_path) -> None:
        """Current CLI PCONS_VARS wins over the persisted cache."""
        self._reset(monkeypatch, tmp_path)
        self._persist_vars(tmp_path, {"MY_VAR": "42"})
        _clear_cli_vars()
        monkeypatch.setenv("PCONS_VARS", '{"MY_VAR": "7"}')

        assert get_var("MY_VAR") == "7"

    def test_env_over_cache(self, monkeypatch, tmp_path) -> None:
        """OS env wins over the persisted cache (chosen precedence)."""
        self._reset(monkeypatch, tmp_path)
        self._persist_vars(tmp_path, {"MY_VAR": "42"})
        _clear_cli_vars()
        monkeypatch.setenv("MY_VAR", "7")

        assert get_var("MY_VAR") == "7"

    def test_cli_overwrites_cache_value(self, monkeypatch, tmp_path) -> None:
        """Re-configuring a var updates the persisted value."""
        self._reset(monkeypatch, tmp_path)
        self._persist_vars(tmp_path, {"MY_VAR": "42"})
        self._persist_vars(tmp_path, {"MY_VAR": "7"})

        cache = json.loads((tmp_path / CACHE_FILE).read_text())
        assert cache["vars"]["MY_VAR"] == "7"

    def test_default_not_persisted(self, monkeypatch, tmp_path) -> None:
        """A default-only fall-through writes no cache file."""
        self._reset(monkeypatch, tmp_path)

        assert get_var("MY_VAR", "d") == "d"
        assert not (tmp_path / CACHE_FILE).exists()

    def test_env_not_persisted(self, monkeypatch, tmp_path) -> None:
        """An OS-env-derived value is not persisted (CMake has no env)."""
        self._reset(monkeypatch, tmp_path)
        monkeypatch.setenv("MY_VAR", "env_value")

        assert get_var("MY_VAR") == "env_value"
        assert not (tmp_path / CACHE_FILE).exists()

    def test_corrupt_cache_degrades(self, monkeypatch, tmp_path, caplog) -> None:
        """A corrupt cache file logs a warning and falls back to default."""
        self._reset(monkeypatch, tmp_path)
        (tmp_path / CACHE_FILE).write_text("{not valid json")

        assert get_var("MY_VAR", "d") == "d"
        assert any(CACHE_FILE in r.message for r in caplog.records)

    def test_non_dict_vars_cache_ignored(self, monkeypatch, tmp_path) -> None:
        """A hand-corrupted non-dict "vars" entry is ignored, not crashed on."""
        self._reset(monkeypatch, tmp_path)
        BuildCache(tmp_path).set("vars", "not-a-dict")
        _clear_cli_vars()

        assert get_var("MY_VAR", "d") == "d"

    def test_missing_build_dir_reads_default_build(self, monkeypatch, tmp_path) -> None:
        """No PCONS_BUILD_DIR -> cache falls back to ./build (empty here)."""
        monkeypatch.chdir(tmp_path)  # ./build does not exist under tmp
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("MY_VAR", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)

        assert get_var("MY_VAR") is None


class TestPersistedVariant:
    """Tests for cross-run persistence of the build variant via the cache."""

    def _reset(self, monkeypatch, build_dir: Path) -> None:
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("VARIANT", raising=False)
        monkeypatch.setenv("PCONS_BUILD_DIR", str(build_dir))

    def test_reads_persisted_variant(self, monkeypatch, tmp_path) -> None:
        """A variant configured in a prior run is reused by a bare run."""
        self._reset(monkeypatch, tmp_path)
        BuildCache(tmp_path).set("variant", "debug")
        _clear_cli_vars()

        assert get_variant() == "debug"

    def test_pcons_variant_over_cache(self, monkeypatch, tmp_path) -> None:
        """Current CLI PCONS_VARIANT wins over the persisted variant."""
        self._reset(monkeypatch, tmp_path)
        BuildCache(tmp_path).set("variant", "debug")
        _clear_cli_vars()
        monkeypatch.setenv("PCONS_VARIANT", "release")

        assert get_variant() == "release"

    def test_env_variant_over_cache(self, monkeypatch, tmp_path) -> None:
        """VARIANT env wins over the persisted variant."""
        self._reset(monkeypatch, tmp_path)
        BuildCache(tmp_path).set("variant", "debug")
        _clear_cli_vars()
        monkeypatch.setenv("VARIANT", "release")

        assert get_variant() == "release"

    def test_default_when_nothing_persisted(self, monkeypatch, tmp_path) -> None:
        """Falls back to default when no variant configured or cached."""
        self._reset(monkeypatch, tmp_path)

        assert get_variant("release") == "release"

    def test_non_str_variant_cache_ignored(self, monkeypatch, tmp_path) -> None:
        """A non-str cached variant is ignored, preserving the -> str contract."""
        self._reset(monkeypatch, tmp_path)
        BuildCache(tmp_path).set("variant", 123)
        _clear_cli_vars()

        assert get_variant("release") == "release"

# SPDX-License-Identifier: MIT
"""Tests for the per-build-directory cache."""

from __future__ import annotations

import json
from pathlib import Path

from pcons.core.cache import (
    CACHE_FILE,
    BuildCache,
    get_cache,
    reset_cache,
)


class TestBuildCache:
    """Unit tests for BuildCache."""

    def test_set_get_roundtrip(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.set("key", "value")

        assert cache.get("key") == "value"
        assert BuildCache(tmp_path).get("key") == "value"  # reloaded

    def test_get_default(self, tmp_path) -> None:
        assert BuildCache(tmp_path).get("missing", "d") == "d"

    def test_set_is_write_through(self, tmp_path) -> None:
        BuildCache(tmp_path).set("a", 1)

        data = json.loads((tmp_path / CACHE_FILE).read_text())
        assert data == {"a": 1}

    def test_update_single_write(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.update({"a": 1, "b": 2})

        assert cache.get("a") == 1
        assert cache.get("b") == 2

    def test_update_empty_is_noop(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.update({})

        assert cache.path is not None
        assert not cache.path.exists()

    def test_delete(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.set("a", 1)
        cache.delete("a")

        assert cache.get("a") is None
        assert json.loads((tmp_path / CACHE_FILE).read_text()) == {}

    def test_clear(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.update({"a": 1, "b": 2})
        cache.clear()

        assert cache.get("a") is None

    def test_arbitrary_nested_data(self, tmp_path) -> None:
        payload = {"list": [1, 2, 3], "nested": {"x": "y"}}
        BuildCache(tmp_path).set("blob", payload)

        assert BuildCache(tmp_path).get("blob") == payload

    def test_no_file_on_pure_read(self, tmp_path) -> None:
        cache = BuildCache(tmp_path)
        cache.get("anything")

        assert cache.path is not None
        assert not cache.path.exists()

    def test_corrupt_file_degrades(self, tmp_path, caplog) -> None:
        (tmp_path / CACHE_FILE).write_text("{not valid json")

        cache = BuildCache(tmp_path)
        assert cache.get("a", "d") == "d"
        assert any(CACHE_FILE in r.message for r in caplog.records)

    def test_non_object_file_ignored(self, tmp_path, caplog) -> None:
        (tmp_path / CACHE_FILE).write_text("[1, 2, 3]")

        cache = BuildCache(tmp_path)
        assert cache.get("a", "d") == "d"
        assert any(CACHE_FILE in r.message for r in caplog.records)

    def test_in_memory_when_no_build_dir(self) -> None:
        cache = BuildCache(None)
        cache.set("a", 1)  # save() is a no-op, no crash

        assert cache.path is None
        assert cache.get("a") == 1  # still readable in-session


class TestCacheSingleton:
    """Tests for the get_cache/reset_cache singleton."""

    def test_get_cache_uses_build_dir_env(self, monkeypatch, tmp_path) -> None:
        reset_cache()
        monkeypatch.setenv("PCONS_BUILD_DIR", str(tmp_path))

        get_cache().set("k", "v")
        assert json.loads((tmp_path / CACHE_FILE).read_text()) == {"k": "v"}

    def test_reset_rebinds_to_new_build_dir(self, monkeypatch, tmp_path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()

        reset_cache()
        monkeypatch.setenv("PCONS_BUILD_DIR", str(first))
        get_cache().set("k", "1")

        reset_cache()
        monkeypatch.setenv("PCONS_BUILD_DIR", str(second))
        assert get_cache().get("k") is None  # fresh build dir

    def test_missing_build_dir_defaults_to_build(self, monkeypatch, tmp_path) -> None:
        # Matches Project's fallback so the direct-run flow sees the cache.
        monkeypatch.chdir(tmp_path)
        reset_cache()
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)

        cache = get_cache()
        assert cache.path == Path("build") / CACHE_FILE  # relative to cwd
        cache.set("k", "v")
        assert json.loads((tmp_path / "build" / CACHE_FILE).read_text()) == {"k": "v"}

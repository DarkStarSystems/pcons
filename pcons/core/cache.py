# SPDX-License-Identifier: MIT
"""Per-build-directory persistent cache.

A small key/value store persisted as JSON in the build directory
(``pcons_cache.json``), CMakeCache-like. Used internally to carry CLI-configured
settings (build variables, variant, generator) across runs. Not public API: build
scripts read settings through ``get_var`` / ``get_variant``, never this store.

The cache is separate from :class:`pcons.configure.config.Configure`
(``pcons_config.json``), which stores configure-check results with its own
invalidation lifecycle.

Reserved top-level keys used by pcons itself: ``vars`` (dict of build variables),
``variant`` (str), ``generator`` (str), and ``source_dir`` (str, the source tree
the cache was written for, used to detect a copied or moved build dir).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_FILE = "pcons_cache.json"


class BuildCache:
    """A JSON-backed key/value store scoped to a build directory.

    When ``build_dir`` is ``None`` the cache is in-memory only: reads see whatever
    was set this session and :meth:`save` is a no-op. This preserves legacy behavior
    when ``PCONS_BUILD_DIR`` is not set. To cache many entries in a loop, prefer
    :meth:`update` (one write) over repeated :meth:`set` (a full-file write each).
    """

    def __init__(self, build_dir: Path | str | None) -> None:
        """Create a cache bound to ``build_dir`` (in-memory when ``None``)."""
        self.build_dir = Path(build_dir) if build_dir is not None else None
        self._data: dict[str, Any] = {}
        self._load()

    @property
    def path(self) -> Path | None:
        """Path to the cache file, or ``None`` for an in-memory cache."""
        return self.build_dir / CACHE_FILE if self.build_dir is not None else None

    def _load(self) -> None:
        """Load the cache file when present, degrading gracefully on corruption."""
        cache_path = self.path
        if cache_path is None or not cache_path.exists():
            return
        try:
            with open(cache_path) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Corrupt or unreadable %s: %s - ignoring", cache_path, e)
            return
        if not isinstance(raw, dict):
            logger.warning("Ignoring %s: expected a JSON object", cache_path)
            return
        self._data = raw

    def save(self) -> None:
        """Persist the cache to disk atomically. No-op for an in-memory cache.

        Writes a temp file then os.replace, so a crash never leaves truncated
        JSON. Values must be JSON-serializable (no silent str() coercion).
        """
        cache_path = self.path
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(cache_path.name + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, cache_path)

    @property
    def is_empty(self) -> bool:
        """Whether nothing has been cached for this build directory yet.

        True for a build directory no run has written to, so a caller that must
        refresh a key without ever *creating* a cache file can tell the two
        apart.
        """
        return not self._data

    def get(self, key: str, default: Any = None) -> Any:
        """Return the cached value for ``key``, or ``default`` if absent."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set ``key`` to ``value`` and persist (write-through)."""
        self._data[key] = value
        self.save()

    def update(self, mapping: dict[str, Any]) -> None:
        """Merge ``mapping`` into the cache and persist in a single write."""
        if not mapping:
            return
        self._data.update(mapping)
        self.save()

    def delete(self, key: str) -> None:
        """Remove ``key`` if present and persist."""
        if key in self._data:
            del self._data[key]
            self.save()

    def discard(self) -> None:
        """Forget the cached data without touching the file.

        For a run that must read as though the cache were empty and still leave
        the build directory as it found it.
        """
        self._data = {}

    def clear(self) -> None:
        """Drop all cached data and persist."""
        self.discard()
        self.save()


_cache: BuildCache | None = None


def get_cache() -> BuildCache:
    """Return the build-dir cache singleton.

    The build directory is taken from ``PCONS_BUILD_DIR``. When that is unset
    (the ``python pcons-build.py`` direct-run flow), the cache is in-memory only
    and nothing is read from or written to disk, so a stray ``pcons_cache.json``
    in some default directory can't leak into an unrelated build. Call
    :func:`reset_cache` to rebind after the environment changes, for example
    between build-script runs.
    """
    global _cache
    if _cache is None:
        _cache = BuildCache(os.environ.get("PCONS_BUILD_DIR"))
    return _cache


def reset_cache() -> None:
    """Drop the cached singleton so it is rebuilt on next access. For tests/CLI."""
    global _cache
    _cache = None

# SPDX-License-Identifier: MIT
"""Unit tests for the C++ module scan cache and its depfile parser."""

from __future__ import annotations

import os
import pickle
from pathlib import Path

from pcons.toolchains._scan_cache import CACHE_FILE, ScanCache, parse_depfile


class TestParseDepfile:
    """Depfile syntax, which is make's, not whitespace-separated."""

    def test_single_line(self) -> None:
        assert parse_depfile("x.o: a.cpp b.hpp\n") == ["a.cpp", "b.hpp"]

    def test_line_continuations(self) -> None:
        text = "x.o: a.cpp \\\n  b.hpp \\\n  c.hpp\n"
        assert parse_depfile(text) == ["a.cpp", "b.hpp", "c.hpp"]

    def test_escaped_space_keeps_one_path(self) -> None:
        """The Windows case: `C:/Program Files/...` arrives as `Program\\ Files`."""
        assert parse_depfile("x.o: /opt/Program\\ Files/a.hpp\n") == [
            "/opt/Program Files/a.hpp"
        ]

    def test_escaped_hash_and_backslash(self) -> None:
        assert parse_depfile("x.o: a\\#b.hpp c\\\\d.hpp\n") == ["a#b.hpp", "c\\d.hpp"]

    def test_target_is_not_a_prerequisite(self) -> None:
        assert "x.o" not in parse_depfile("x.o: a.cpp\n")

    def test_empty(self) -> None:
        assert parse_depfile("") == []

    def test_no_prerequisites(self) -> None:
        assert parse_depfile("x.o:\n") == []

    def test_a_backslash_that_escapes_nothing_stays(self) -> None:
        """Only ` \\t#\\` are escapes; anything else is part of the path."""
        assert parse_depfile("x.o: a\\b.hpp\n") == ["a\\b.hpp"]

    def test_a_last_path_without_a_trailing_newline(self) -> None:
        """Nothing guarantees the compiler ends the depfile with one."""
        assert parse_depfile("x.o: a.cpp b.hpp") == ["a.cpp", "b.hpp"]


class TestScanCache:
    @staticmethod
    def _sources(tmp_path: Path) -> tuple[Path, Path]:
        src = tmp_path / "a.cppm"
        header = tmp_path / "a.hpp"
        src.write_text("export module a;\n")
        header.write_text("#pragma once\n")
        return src, header

    def test_a_stored_result_comes_back(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = ScanCache.key("g++", ["-std=c++23"], str(src))
        cache.put(key, {"rules": []}, [str(src), str(header)])
        assert cache.get(key) == {"rules": []}

    def test_a_touched_prerequisite_misses(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        cache.put(key, {"rules": []}, [str(src), str(header)])

        stamp = header.stat().st_mtime_ns
        os.utime(header, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))

        assert cache.get(key) is None

    def test_a_prerequisite_that_changed_size_misses(self, tmp_path: Path) -> None:
        """mtime alone would miss a same-second rewrite of a different length."""
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        cache.put(key, {"rules": []}, [str(src), str(header)])

        stamp = header.stat().st_mtime_ns
        header.write_text("#pragma once\n// longer now\n")
        os.utime(header, ns=(stamp, stamp))

        assert cache.get(key) is None

    def test_a_deleted_prerequisite_misses(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        cache.put(key, {"rules": []}, [str(src), str(header)])
        header.unlink()
        assert cache.get(key) is None

    def test_different_flags_are_a_different_entry(self, tmp_path: Path) -> None:
        """Not an invalidation: the old answer is still right for the old flags."""
        src, _ = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        one = ScanCache.key("g++", ["-std=c++20"], str(src))
        other = ScanCache.key("g++", ["-std=c++23"], str(src))
        assert one != other

        cache.put(one, {"rules": ["twenty"]}, [str(src)])
        cache.put(other, {"rules": ["twentythree"]}, [str(src)])
        assert cache.get(one) == {"rules": ["twenty"]}
        assert cache.get(other) == {"rules": ["twentythree"]}

    def test_a_different_compiler_is_a_different_entry(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        assert ScanCache.key("g++", [], str(src)) != ScanCache.key(
            "g++-15", [], str(src)
        )

    def test_the_scan_recipe_is_part_of_the_key(self, tmp_path: Path) -> None:
        """A pcons whose scan command changed must not trust the old answers.

        Nothing else would notice: the recipe is invisible to the caller, so a
        cache written by an older scan command would look perfectly valid.
        """
        import pcons.toolchains._scan_cache as sc

        src, _ = self._sources(tmp_path)
        before = ScanCache.key("g++", [], str(src))
        original = sc.RECIPE
        try:
            sc.RECIPE = original + "-changed"
            assert ScanCache.key("g++", [], str(src)) != before
        finally:
            sc.RECIPE = original

    def test_it_survives_a_round_trip_through_the_file(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        key = ScanCache.key("g++", [], str(src))

        first = ScanCache(tmp_path)
        first.put(key, {"rules": [{"primary-output": "a.o"}]}, [str(src), str(header)])
        first.save()
        assert (tmp_path / CACHE_FILE).exists()

        assert ScanCache(tmp_path).get(key) == {"rules": [{"primary-output": "a.o"}]}

    def test_nothing_stored_writes_nothing(self, tmp_path: Path) -> None:
        ScanCache(tmp_path).save()
        assert not (tmp_path / CACHE_FILE).exists()

    def test_a_corrupt_file_is_a_miss_not_a_crash(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).write_bytes(b"this is not a pickle")

        cache = ScanCache(tmp_path)
        assert cache.get(ScanCache.key("g++", [], str(src))) is None

    def test_a_missing_prerequisite_is_not_stored(self, tmp_path: Path) -> None:
        """An entry that could never hit is worse than no entry."""
        src, _ = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        cache.put(key, {"rules": []}, [str(src), str(tmp_path / "gone.hpp")])
        assert cache.get(key) is None
        cache.save()
        assert not (tmp_path / CACHE_FILE).exists()

    def test_a_file_of_the_wrong_shape_is_a_miss(self, tmp_path: Path) -> None:
        """Readable pickle, but not one this pcons wrote."""
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).write_bytes(pickle.dumps(["entries", "please"]))

        cache = ScanCache(tmp_path)
        assert cache.get(ScanCache.key("g++", [], str(src))) is None

    def test_an_entry_of_the_wrong_shape_is_a_miss(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        (tmp_path / CACHE_FILE).write_bytes(
            pickle.dumps({"entries": {key: {"prereqs": str(src), "stamps": []}}})
        )

        assert ScanCache(tmp_path).get(key) is None

    def test_stamps_that_do_not_line_up_are_a_miss(self, tmp_path: Path) -> None:
        """One stamp per prerequisite, or there is no telling which is which."""
        src, header = self._sources(tmp_path)
        key = ScanCache.key("g++", [], str(src))
        (tmp_path / CACHE_FILE).write_bytes(
            pickle.dumps(
                {
                    "entries": {
                        key: {
                            "p1689": {"rules": []},
                            "prereqs": [str(src), str(header)],
                            "stamps": [[src.stat().st_mtime_ns, src.stat().st_size]],
                        }
                    }
                }
            )
        )

        assert ScanCache(tmp_path).get(key) is None

    def test_a_cache_that_cannot_be_written_is_a_warning(self, tmp_path: Path) -> None:
        """A cache that cannot be written is a slow build, not a failed one."""
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).mkdir()  # write_bytes onto a directory fails

        cache = ScanCache(tmp_path)
        cache.put(ScanCache.key("g++", [], str(src)), {"rules": []}, [str(src)])
        cache.save()

        assert (tmp_path / CACHE_FILE).is_dir()

    def test_relative_prerequisites_are_resolved(self, tmp_path: Path) -> None:
        """A depfile names paths as the compiler saw them, from its own cwd."""
        src, header = self._sources(tmp_path)
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            cache = ScanCache(tmp_path)
            key = ScanCache.key("g++", [], str(src))
            cache.put(key, {"rules": []}, ["a.cppm", "a.hpp"])
            cache.save()
        finally:
            os.chdir(cwd)

        # Read back from a different working directory: the stored paths must
        # still name the same files.
        assert ScanCache(tmp_path).get(key) == {"rules": []}
        assert header.exists()

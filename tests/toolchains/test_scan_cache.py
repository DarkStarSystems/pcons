# SPDX-License-Identifier: MIT
"""Unit tests for the C++ module scan cache and its depfile parser."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pcons.toolchains._scan_cache import (
    CACHE_FILE,
    ScanCache,
    compiler_binary,
    parse_depfile,
)

RECIPE = "recipe"


def _key(src: Path, *, flags: list[str] | None = None, obj: str = "a.o") -> str:
    return ScanCache.key(RECIPE, "g++", flags or [], str(src), obj)


def _age(path: Path) -> None:
    """Backdate *path*, so a scan starting now started after it was written.

    A file written in the same clock tick as the scan is one the cache
    refuses to record, and the tick is 15 ms wide on Windows.
    """
    stamp = path.stat().st_mtime_ns - 5_000_000_000
    os.utime(path, ns=(stamp, stamp))


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


class TestCompilerBinary:
    def test_a_command_on_path_resolves_to_an_absolute_path(self) -> None:
        resolved = compiler_binary("python" if os.name == "nt" else "sh")
        assert resolved is not None
        assert os.path.isabs(resolved)

    def test_a_command_that_is_not_there_resolves_to_nothing(self) -> None:
        assert compiler_binary("no-such-compiler-anywhere") is None


class TestScanCache:
    @staticmethod
    def _sources(tmp_path: Path) -> tuple[Path, Path]:
        src = tmp_path / "a.cppm"
        header = tmp_path / "a.hpp"
        src.write_text("export module a;\n")
        header.write_text("#pragma once\n")
        _age(src)
        _age(header)
        return src, header

    def test_a_stored_result_comes_back(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src, flags=["-std=c++23"])
        cache.put(
            key, {"rules": []}, [str(src), str(header)], scan_started_ns=time.time_ns()
        )
        assert cache.get(key) == {"rules": []}

    def test_a_touched_prerequisite_misses(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src)
        cache.put(
            key, {"rules": []}, [str(src), str(header)], scan_started_ns=time.time_ns()
        )

        stamp = header.stat().st_mtime_ns
        os.utime(header, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))

        assert cache.get(key) is None

    def test_a_prerequisite_that_changed_size_misses(self, tmp_path: Path) -> None:
        """mtime alone would miss a same-second rewrite of a different length."""
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src)
        cache.put(
            key, {"rules": []}, [str(src), str(header)], scan_started_ns=time.time_ns()
        )

        stamp = header.stat().st_mtime_ns
        header.write_text("#pragma once\n// longer now\n")
        os.utime(header, ns=(stamp, stamp))

        assert cache.get(key) is None

    def test_a_deleted_prerequisite_misses(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src)
        cache.put(
            key, {"rules": []}, [str(src), str(header)], scan_started_ns=time.time_ns()
        )
        header.unlink()
        assert cache.get(key) is None

    def test_a_prerequisite_written_during_the_scan_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Its stamp would claim the scan read an edit the compiler never saw.

        The entry would then hit forever, answering with the module graph from
        before the edit.
        """
        src, header = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src)

        started = header.stat().st_mtime_ns - 1
        cache.put(key, {"rules": []}, [str(src), str(header)], scan_started_ns=started)

        assert cache.get(key) is None
        cache.save()
        assert not (tmp_path / CACHE_FILE).exists()

    def test_different_flags_are_a_different_entry(self, tmp_path: Path) -> None:
        """Not an invalidation: the old answer is still right for the old flags."""
        src, _ = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        one = _key(src, flags=["-std=c++20"])
        other = _key(src, flags=["-std=c++23"])
        assert one != other

        now = time.time_ns()
        cache.put(one, {"rules": ["twenty"]}, [str(src)], scan_started_ns=now)
        cache.put(other, {"rules": ["twentythree"]}, [str(src)], scan_started_ns=now)
        assert cache.get(one) == {"rules": ["twenty"]}
        assert cache.get(other) == {"rules": ["twentythree"]}

    def test_a_different_compiler_is_a_different_entry(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        assert ScanCache.key(RECIPE, "g++", [], str(src), "a.o") != ScanCache.key(
            RECIPE, "g++-15", [], str(src), "a.o"
        )

    def test_a_different_object_is_a_different_entry(self, tmp_path: Path) -> None:
        """The p1689 payload names the object, so two of them cannot share one.

        One source compiled into two targets with the same flags would
        otherwise be served the other target's `primary-output`.
        """
        src, _ = self._sources(tmp_path)
        assert _key(src, obj="one/a.o") != _key(src, obj="two/a.o")

    def test_the_scan_recipe_is_part_of_the_key(self, tmp_path: Path) -> None:
        """A pcons whose scan command changed must not trust the old answers.

        Nothing else would notice: the recipe is invisible to the caller, so a
        cache written by an older scan command would look perfectly valid.
        """
        src, _ = self._sources(tmp_path)
        assert ScanCache.key(RECIPE, "g++", [], str(src), "a.o") != ScanCache.key(
            RECIPE + "-changed", "g++", [], str(src), "a.o"
        )

    def test_it_survives_a_round_trip_through_the_file(self, tmp_path: Path) -> None:
        src, header = self._sources(tmp_path)
        key = _key(src)

        first = ScanCache(tmp_path)
        first.put(
            key,
            {"rules": [{"primary-output": "a.o"}]},
            [str(src), str(header)],
            scan_started_ns=time.time_ns(),
        )
        first.save()
        assert (tmp_path / CACHE_FILE).exists()

        assert ScanCache(tmp_path).get(key) == {"rules": [{"primary-output": "a.o"}]}

    def test_it_is_written_as_json(self, tmp_path: Path) -> None:
        """The two other build-dir stores are JSON, and so is the payload.

        A pickle in a build directory that may be restored from CI or shared
        is a code-execution vector, and a truncated one raises where JSON
        merely fails to parse.
        """
        src, _ = self._sources(tmp_path)
        key = _key(src)
        cache = ScanCache(tmp_path)
        cache.put(key, {"rules": []}, [str(src)], scan_started_ns=time.time_ns())
        cache.save()

        data = json.loads((tmp_path / CACHE_FILE).read_text(encoding="utf-8"))
        assert data["entries"][key]["p1689"] == {"rules": []}

    def test_a_truncated_file_is_a_miss_not_a_crash(self, tmp_path: Path) -> None:
        """Ctrl-C during a write used to leave a file that broke every run."""
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).write_text('{"entries": {"a": ', encoding="utf-8")

        assert ScanCache(tmp_path).get(_key(src)) is None

    def test_the_file_is_replaced_into_place(self, tmp_path: Path) -> None:
        """An interrupted write must not be readable as the cache."""
        src, _ = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        cache.put(_key(src), {"rules": []}, [str(src)], scan_started_ns=time.time_ns())
        cache.save()

        assert not (tmp_path / (CACHE_FILE + ".tmp")).exists()

    def test_nothing_stored_writes_nothing(self, tmp_path: Path) -> None:
        ScanCache(tmp_path).save()
        assert not (tmp_path / CACHE_FILE).exists()

    def test_a_corrupt_file_is_a_miss_not_a_crash(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).write_bytes(b"this is not json")

        cache = ScanCache(tmp_path)
        assert cache.get(_key(src)) is None

    def test_a_missing_prerequisite_is_not_stored(self, tmp_path: Path) -> None:
        """An entry that could never hit is worse than no entry."""
        src, _ = self._sources(tmp_path)
        cache = ScanCache(tmp_path)
        key = _key(src)
        cache.put(
            key,
            {"rules": []},
            [str(src), str(tmp_path / "gone.hpp")],
            scan_started_ns=time.time_ns(),
        )
        assert cache.get(key) is None
        cache.save()
        assert not (tmp_path / CACHE_FILE).exists()

    def test_a_file_of_the_wrong_shape_is_a_miss(self, tmp_path: Path) -> None:
        """Readable JSON, but not what this pcons wrote."""
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).write_text(
            json.dumps(["entries", "please"]), encoding="utf-8"
        )

        cache = ScanCache(tmp_path)
        assert cache.get(_key(src)) is None

    def test_an_entry_of_the_wrong_shape_is_a_miss(self, tmp_path: Path) -> None:
        src, _ = self._sources(tmp_path)
        key = _key(src)
        (tmp_path / CACHE_FILE).write_text(
            json.dumps({"entries": {key: {"prereqs": str(src), "stamps": []}}}),
            encoding="utf-8",
        )

        assert ScanCache(tmp_path).get(key) is None

    def test_stamps_that_do_not_line_up_are_a_miss(self, tmp_path: Path) -> None:
        """One stamp per prerequisite, or there is no telling which is which."""
        src, header = self._sources(tmp_path)
        key = _key(src)
        (tmp_path / CACHE_FILE).write_text(
            json.dumps(
                {
                    "entries": {
                        key: {
                            "p1689": {"rules": []},
                            "prereqs": [str(src), str(header)],
                            "stamps": [[src.stat().st_mtime_ns, src.stat().st_size]],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        assert ScanCache(tmp_path).get(key) is None

    def test_a_cache_that_cannot_be_written_is_a_warning(self, tmp_path: Path) -> None:
        """A cache that cannot be written is a slow build, not a failed one."""
        src, _ = self._sources(tmp_path)
        (tmp_path / CACHE_FILE).mkdir()  # os.replace onto a directory fails

        cache = ScanCache(tmp_path)
        cache.put(_key(src), {"rules": []}, [str(src)], scan_started_ns=time.time_ns())
        cache.save()

        assert (tmp_path / CACHE_FILE).is_dir()
        assert not (tmp_path / (CACHE_FILE + ".tmp")).exists()

    def test_relative_prerequisites_are_resolved(self, tmp_path: Path) -> None:
        """A depfile names paths as the compiler saw them, from its own cwd."""
        src, header = self._sources(tmp_path)
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            cache = ScanCache(tmp_path)
            key = _key(src)
            cache.put(
                key, {"rules": []}, ["a.cppm", "a.hpp"], scan_started_ns=time.time_ns()
            )
            cache.save()
        finally:
            os.chdir(cwd)

        # Read back from a different working directory: the stored paths must
        # still name the same files.
        assert ScanCache(tmp_path).get(key) == {"rules": []}
        assert header.exists()

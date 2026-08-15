# SPDX-License-Identifier: MIT
"""Unit tests for the configure-time C++ module scanner API.

These tests pre-canned P1689R5 JSON dicts directly into TuScanResult so the
classification logic (is_module_provider, is_interface, logical_name,
required_logical_names), the module-name -> file-path map, and the dyndep
output can be exercised without invoking a real scanner.
"""

from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from pcons.toolchains._scan_cache import CACHE_FILE, ScanCache
from pcons.toolchains.cxx_module_scanner import (
    CxxModuleScannerNotFound,
    StdModuleFlagSpec,
    TuScanResult,
    TuScanSpec,
    _scan_workers,
    bmi_key_for_flags,
    build_keyed_entries,
    build_module_map,
    map_module_providers,
    merge_scan_compile_flags,
    module_file_for,
    run_scan_deps,
    run_scan_deps_gcc,
    run_scan_deps_msvc,
    scan_translation_units,
    select_modules_scope,
    select_std_module_flags,
    wire_std_into_targets,
    write_dyndep,
    write_dyndep_entries,
    write_dyndep_from_results,
)


def _spec(obj_rel: str) -> TuScanSpec:
    """Build a minimal scan spec; src and compiler are placeholders."""
    return TuScanSpec(
        src=Path(f"/src/{obj_rel}.cpp"),
        obj_rel=obj_rel,
        compiler="cl.exe",
        compile_flags=[],
    )


def _result(
    obj_rel: str,
    *,
    provides_name: str | None = None,
    is_interface: bool = True,
    requires: list[str] | None = None,
) -> TuScanResult:
    """Build a TuScanResult with a synthesized P1689 payload."""
    rule: dict = {"primary-output": obj_rel}
    if provides_name is not None:
        rule["provides"] = [
            {"logical-name": provides_name, "is-interface": is_interface}
        ]
    if requires:
        rule["requires"] = [{"logical-name": ln} for ln in requires]
    return TuScanResult(spec=_spec(obj_rel), p1689={"rules": [rule]})


class TestTuScanResultClassification:
    def test_consumer_only(self) -> None:
        r = _result("consumer.o", requires=["MyMod"])
        assert not r.is_module_provider
        assert r.logical_name == ""
        assert r.required_logical_names == ["MyMod"]

    def test_primary_interface(self) -> None:
        r = _result("MyMod.o", provides_name="MyMod", is_interface=True)
        assert r.is_module_provider
        assert r.is_interface
        assert r.logical_name == "MyMod"

    def test_partition_interface(self) -> None:
        r = _result("Constants.o", provides_name="Calc:Constants", is_interface=True)
        assert r.is_module_provider
        assert r.is_interface
        assert r.logical_name == "Calc:Constants"

    def test_internal_partition(self) -> None:
        # A `module M:P;` (no export) — scanner reports is-interface=false.
        r = _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False)
        assert r.is_module_provider
        assert not r.is_interface

    def test_failed_scan(self) -> None:
        r = TuScanResult(spec=_spec("foo.o"), p1689=None)
        assert not r.is_module_provider
        assert r.logical_name == ""
        assert r.required_logical_names == []


class TestModuleFileFor:
    def test_primary_module(self) -> None:
        assert (
            module_file_for("MyMod", "cxx_modules", ".pcm") == "cxx_modules/MyMod.pcm"
        )

    def test_partition_replaces_colon(self) -> None:
        # ':' would be an invalid filename character on Windows; replaced with '-'.
        assert (
            module_file_for("Calc:Constants", "cxx_modules", ".ifc")
            == "cxx_modules/Calc-Constants.ifc"
        )


class TestBuildModuleMap:
    def test_only_providers_appear(self) -> None:
        results = [
            _result("MyMod.o", provides_name="MyMod"),
            _result("consumer.o", requires=["MyMod"]),
        ]
        m = build_module_map(results, "cxx_modules", ".ifc")
        assert m == {"MyMod": "cxx_modules/MyMod.ifc"}

    def test_partitions_get_dash_filename(self) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False),
        ]
        m = build_module_map(results, "mods", ".ifc")
        assert m == {
            "Calc": "mods/Calc.ifc",
            "Calc:Constants": "mods/Calc-Constants.ifc",
            "Calc:Helpers": "mods/Calc-Helpers.ifc",
        }


class TestWriteDyndep:
    def test_full_partition_graph(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False),
            _result(
                "main.o",
                requires=["Calc"],
            ),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        write_dyndep_from_results(results, m, out)

        text = out.read_text()
        assert text.startswith("ninja_dyndep_version = 1")
        # Provides are emitted as implicit outputs; requires as implicit inputs.
        assert "build Calc.o | mods/Calc.pcm: dyndep | mods/Calc-Constants.pcm" in text
        assert "build Constants.o | mods/Calc-Constants.pcm: dyndep" in text
        assert "build Helpers.o | mods/Calc-Helpers.pcm: dyndep" in text
        assert "build main.o: dyndep | mods/Calc.pcm" in text

    def test_unresolved_requires_dropped(self, tmp_path: Path) -> None:
        # If a required logical name has no provider in the result set
        # (e.g. `std` before std-module support is wired up), it must be
        # silently dropped rather than emitted as an unbuildable dep.
        results = [_result("user.o", requires=["std"])]
        m = build_module_map(results, "mods", ".ifc")
        out = tmp_path / "deps.dyndep"
        write_dyndep_from_results(results, m, out)
        assert "std" not in out.read_text()

    def test_write_if_unchanged_keeps_mtime(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"

        write_dyndep_from_results(results, m, out)
        first_mtime = out.stat().st_mtime_ns

        # Re-emitting identical content must not rewrite the file.
        write_dyndep_from_results(results, m, out)
        second_mtime = out.stat().st_mtime_ns

        assert first_mtime == second_mtime

    def test_write_creates_digest_file(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"

        write_dyndep_from_results(results, m, out)

        digest_file = tmp_path / "deps.dyndep.sha256"
        assert digest_file.exists()
        assert len(digest_file.read_bytes()) == 32

    def test_stale_digest_same_content_rewrites(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        digest_file = tmp_path / "deps.dyndep.sha256"

        write_dyndep_from_results(results, m, out)
        first_content = out.read_text(encoding="utf-8")

        digest_file.write_bytes(b"\x00" * 32)
        write_dyndep_from_results(results, m, out)

        assert out.read_text(encoding="utf-8") == first_content
        assert digest_file.read_bytes() != b"\x00" * 32

    def test_stale_digest_different_size_rewrites(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        digest_file = tmp_path / "deps.dyndep.sha256"

        write_dyndep_from_results(results, m, out)
        out.write_text("x\n", encoding="utf-8")
        digest_file.write_bytes(b"bad\n")

        write_dyndep_from_results(results, m, out)

        assert out.read_text(encoding="utf-8").startswith("ninja_dyndep_version = 1")
        assert len(digest_file.read_bytes()) == 32

    def test_deterministic_output_with_result_reordering(self, tmp_path: Path) -> None:
        base_results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("main.o", requires=["Calc"]),
        ]
        module_map = build_module_map(base_results, "mods", ".pcm")

        out_a = tmp_path / "a.dyndep"
        out_b = tmp_path / "b.dyndep"

        write_dyndep_from_results(base_results, module_map, out_a)
        write_dyndep_from_results(list(reversed(base_results)), module_map, out_b)

        assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")

    def test_fallback_to_manifest_mod_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate scanner failure for an interface TU: write_dyndep should
        # still emit the manifest-provided module file as an implicit output.
        def _scan_fail(
            scanner: str,
            compiler: str,
            compile_flags: list[str],
            src: str,
            obj: str,
        ) -> None:
            return None

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps", _scan_fail
        )

        out = tmp_path / "deps.dyndep"
        manifest = [
            {
                "src": str(tmp_path / "MyMod.cppm"),
                "obj": "obj/MyMod.o",
                "is_module_interface": True,
                "pcm": "mods/MyMod.pcm",
                "compiler": "clang++",
                "compile_flags": ["-std=c++20"],
            }
        ]

        write_dyndep(manifest, "mods", str(out), "clang-scan-deps", "clang")
        text = out.read_text(encoding="utf-8")
        assert "ninja_dyndep_version = 1" in text
        assert "build obj/MyMod.o | mods/MyMod.pcm: dyndep" in text


class _FakeCxxNamespace:
    """Stand-in for env.cxx with just the `modules` attribute the helper reads."""

    def __init__(self, modules: bool) -> None:
        self.modules = modules


class _FakeEnv:
    def __init__(self, modules: bool) -> None:
        self.cxx = _FakeCxxNamespace(modules)


class _FakeObj:
    """Stand-in FileNode-ish duck-type for select_modules_scope."""

    def __init__(self, env: _FakeEnv) -> None:
        self._build_info = {"env": env}


class TestSelectModulesScope:
    def test_no_module_extensions_no_optin_skips(self) -> None:
        env = _FakeEnv(modules=False)
        obj = _FakeObj(env)
        # cxx_pairs only — no .cppm/.ixx, env didn't opt in.
        scope = select_modules_scope({"cxx": [(Path("/src/main.cpp"), obj)]})
        assert scope == ([], [])

    def test_extension_implicit_optin_includes_cxx_pairs(self) -> None:
        env = _FakeEnv(modules=False)
        mod_obj = _FakeObj(env)
        cxx_obj = _FakeObj(env)
        # The .cppm in this env qualifies; sibling .cpp files in the same
        # env come along so partition units in .cpp can be detected.
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx_module": [(Path("/src/MyMod.cppm"), mod_obj)],
                "cxx": [(Path("/src/Helper.cpp"), cxx_obj)],
            }
        )
        assert len(m_pairs) == 1
        assert len(c_pairs) == 1

    def test_explicit_optin_without_extensions(self) -> None:
        env = _FakeEnv(modules=True)
        cxx_obj = _FakeObj(env)
        m_pairs, c_pairs = select_modules_scope(
            {"cxx": [(Path("/src/main.cpp"), cxx_obj)]}
        )
        assert m_pairs == []
        assert len(c_pairs) == 1

    def test_other_envs_filtered_out(self) -> None:
        # Two envs in the same project — only one opted in. The other env's
        # TUs must NOT be scanned (would slow the build and may produce
        # spurious flags).
        env_modules = _FakeEnv(modules=True)
        env_plain = _FakeEnv(modules=False)
        m_obj = _FakeObj(env_modules)
        p_obj = _FakeObj(env_plain)
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx": [
                    (Path("/m.cpp"), m_obj),
                    (Path("/p.cpp"), p_obj),
                ],
            }
        )
        assert m_pairs == []
        assert len(c_pairs) == 1
        assert c_pairs[0][1] is m_obj


class TestScannerNotFound:
    """A missing scanner executable must raise an actionable error.

    Configure used to silently warn and return None when the scanner wasn't
    on PATH; that produced empty dyndep files and confusing downstream
    failures. Now we raise CxxModuleScannerNotFound with install hints.
    """

    def test_clang_scan_deps_missing_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _enoent(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _enoent
        )
        with pytest.raises(CxxModuleScannerNotFound, match="clang-scan-deps"):
            run_scan_deps(
                "clang-scan-deps", "clang++", ["-std=c++20"], "/x/y.cpp", "y.o"
            )

    def test_msvc_cl_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _enoent(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _enoent
        )
        with pytest.raises(CxxModuleScannerNotFound, match="vcvars64"):
            run_scan_deps_msvc("cl.exe", ["/std:c++20"], "C:/x/y.cpp")


class TestScanTranslationUnitsOrder:
    """Results must line up with their specs however the scans finished.

    `build_module_map` and the dyndep writer index results against specs, so a
    pool that yielded completions instead of preserving input order would wire
    a module to the wrong object file.
    """

    @staticmethod
    def _specs(n: int) -> list[TuScanSpec]:
        return [
            TuScanSpec(
                src=Path(f"/src/tu{i}.cppm"),
                obj_rel=f"tu{i}.o",
                compiler="g++",
                compile_flags=[],
            )
            for i in range(n)
        ]

    def test_results_follow_spec_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The first spec sleeps longest, so completion order is reversed."""
        import time

        def _slow(
            compiler: str,
            flags: list[str],
            src: str,
            obj: str,
            prereqs_out: list[str] | None = None,
        ) -> dict[str, object]:
            index = int(obj.removeprefix("tu").removesuffix(".o"))
            time.sleep((8 - index) * 0.01)
            return {"rules": [{"primary-output": obj}]}

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps_gcc", _slow
        )
        specs = self._specs(8)

        results = scan_translation_units(specs, "unused", scanner_style="gcc")

        assert [r.spec.obj_rel for r in results] == [s.obj_rel for s in specs]
        # The payload has to travel with its own spec, not just be in the right
        # slot: a swap of both together would pass the check above.
        assert [(r.p1689 or {})["rules"][0]["primary-output"] for r in results] == [
            s.obj_rel for s in specs
        ]

    def test_a_single_spec_takes_the_direct_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One TU is not worth a pool, and must still scan."""
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps_gcc",
            lambda *a, **kw: {"rules": [{"primary-output": a[3]}]},
        )
        results = scan_translation_units(self._specs(1), "unused", scanner_style="gcc")
        assert [r.spec.obj_rel for r in results] == ["tu0.o"]

    def test_no_specs(self) -> None:
        assert scan_translation_units([], "unused", scanner_style="gcc") == []


class TestGccScanReportsWhatItRead:
    """The depfile GCC is already asked for is the cache invalidation key."""

    @staticmethod
    def _run_with_depfile(
        monkeypatch: pytest.MonkeyPatch, depfile_text: str | None
    ) -> tuple[dict | None, list[str]]:
        class _Ok:
            returncode = 0
            stderr = ""

        def _capture(cmd: list[str], *args: object, **kwargs: object) -> _Ok:
            deps = next(a.split("=", 1)[1] for a in cmd if a.startswith("-fdeps-file="))
            Path(deps).write_text("{}", encoding="utf-8")
            depfile = cmd[cmd.index("-MF") + 1]
            if depfile_text is None:
                Path(depfile).unlink()
            else:
                Path(depfile).write_text(depfile_text, encoding="utf-8")
            return _Ok()

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _capture
        )
        prereqs: list[str] = []
        p1689 = run_scan_deps_gcc("g++", [], "/x/y.cppm", "y.o", prereqs_out=prereqs)
        return p1689, prereqs

    def test_the_depfile_prerequisites_come_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p1689, prereqs = self._run_with_depfile(
            monkeypatch, "y.o: /x/y.cppm /x/dep.hpp\n"
        )
        assert p1689 == {}
        assert prereqs == ["/x/y.cppm", "/x/dep.hpp"]

    def test_no_depfile_leaves_the_list_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The scan still answers; the caller just must not cache it."""
        p1689, prereqs = self._run_with_depfile(monkeypatch, None)
        assert p1689 == {}
        assert prereqs == []


class TestScanTranslationUnitsCache:
    """A TU whose prerequisites are untouched must not reach the compiler.

    `ScanCache` is covered on its own in test_scan_cache.py; what is checked
    here is the wiring: that a hit skips the scan, that only the GCC style
    consults the cache, and that a result with nothing to invalidate against
    is not stored.
    """

    @staticmethod
    def _pass(
        specs: list[TuScanSpec],
        scanner: str,
        scanner_style: str,
        cache_dir: Path | None,
    ) -> list[TuScanResult]:
        """One scan pass, over a cache loaded and saved the way a toolchain does."""
        cache = ScanCache(cache_dir) if cache_dir is not None else None
        results = scan_translation_units(
            specs, scanner, scanner_style=scanner_style, cache=cache
        )
        if cache is not None:
            cache.save()
        return results

    @staticmethod
    def _specs(tmp_path: Path, n: int) -> list[TuScanSpec]:
        specs = []
        for i in range(n):
            src = tmp_path / f"tu{i}.cppm"
            src.write_text(f"export module tu{i};\n")
            # Backdated: a prerequisite written in the same clock tick as the
            # scan is one the cache refuses to record, and that tick is 15 ms
            # wide on Windows.
            aged = src.stat().st_mtime_ns - 5_000_000_000
            os.utime(src, ns=(aged, aged))
            specs.append(
                TuScanSpec(
                    src=src,
                    obj_rel=f"tu{i}.o",
                    compiler="g++",
                    compile_flags=["-std=c++23"],
                )
            )
        return specs

    @staticmethod
    def _counting_scanner(
        monkeypatch: pytest.MonkeyPatch, *, prereqs: bool = True
    ) -> list[str]:
        """Stand in for the GCC runner, recording which sources it scanned."""
        scanned: list[str] = []

        def _scan(
            compiler: str,
            flags: list[str],
            src: str,
            obj: str,
            prereqs_out: list[str] | None = None,
        ) -> dict[str, object]:
            scanned.append(src)
            if prereqs_out is not None and prereqs:
                prereqs_out.append(src)
            return {"rules": [{"primary-output": obj}]}

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps_gcc", _scan
        )
        return scanned

    def test_a_second_pass_reuses_the_stored_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scanned = self._counting_scanner(monkeypatch)
        specs = self._specs(tmp_path, 3)

        first = self._pass(specs, "unused", "gcc", tmp_path)
        assert len(scanned) == 3
        assert (tmp_path / CACHE_FILE).exists()

        scanned.clear()
        second = self._pass(specs, "unused", "gcc", tmp_path)

        assert scanned == []
        assert [r.p1689 for r in second] == [r.p1689 for r in first]
        assert [r.spec.obj_rel for r in second] == [s.obj_rel for s in specs]

    def test_a_touched_source_is_scanned_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scanned = self._counting_scanner(monkeypatch)
        specs = self._specs(tmp_path, 2)
        self._pass(specs, "unused", "gcc", tmp_path)

        scanned.clear()
        stamp = specs[1].src.stat().st_mtime_ns + 1_000_000_000
        os.utime(specs[1].src, ns=(stamp, stamp))

        self._pass(specs, "unused", "gcc", tmp_path)

        assert scanned == [str(specs[1].src)]

    def test_without_a_cache_nothing_is_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scanned = self._counting_scanner(monkeypatch)
        specs = self._specs(tmp_path, 2)

        self._pass(specs, "unused", "gcc", None)
        self._pass(specs, "unused", "gcc", None)

        assert len(scanned) == 4
        assert not (tmp_path / CACHE_FILE).exists()

    def test_a_result_with_no_prerequisites_is_not_stored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to invalidate against, so the answer is used but not kept."""
        scanned = self._counting_scanner(monkeypatch, prereqs=False)
        specs = self._specs(tmp_path, 2)

        first = self._pass(specs, "unused", "gcc", tmp_path)
        assert all(r.p1689 is not None for r in first)

        scanned.clear()
        self._pass(specs, "unused", "gcc", tmp_path)

        assert len(scanned) == 2
        assert not (tmp_path / CACHE_FILE).exists()

    def test_the_msvc_style_does_not_cache_either(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scanned: list[str] = []

        def _scan(compiler: str, flags: list[str], src: str) -> dict[str, object]:
            scanned.append(src)
            return {"rules": [{"primary-output": "x.obj"}]}

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps_msvc", _scan
        )
        specs = self._specs(tmp_path, 2)

        self._pass(specs, "cl.exe", "msvc", tmp_path)
        self._pass(specs, "cl.exe", "msvc", tmp_path)

        assert len(scanned) == 4
        assert not (tmp_path / CACHE_FILE).exists()

    def test_only_the_gcc_style_caches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """clang-scan-deps and cl.exe do not report what they read."""
        scanned: list[str] = []

        def _scan(
            scanner: str, compiler: str, flags: list[str], src: str, obj: str
        ) -> dict[str, object]:
            scanned.append(src)
            return {"rules": [{"primary-output": obj}]}

        monkeypatch.setattr("pcons.toolchains.cxx_module_scanner.run_scan_deps", _scan)
        specs = self._specs(tmp_path, 2)

        self._pass(specs, "clang-scan-deps", "clang", tmp_path)
        self._pass(specs, "clang-scan-deps", "clang", tmp_path)

        assert len(scanned) == 4
        assert not (tmp_path / CACHE_FILE).exists()


class TestScanWorkers:
    """How many compilers a scan pass keeps in flight."""

    def test_one_per_core(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PCONS_JOBS", raising=False)
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.os.cpu_count", lambda: 4
        )
        assert _scan_workers(100) == 4

    def test_never_more_than_there_is_to_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_JOBS", raising=False)
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.os.cpu_count", lambda: 8
        )
        assert _scan_workers(2) == 2

    def test_jobs_caps_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`pcons -j 2` means two compilers at once, configure included."""
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.os.cpu_count", lambda: 8
        )
        monkeypatch.setenv("PCONS_JOBS", "2")
        assert _scan_workers(100) == 2

    @pytest.mark.parametrize("value", ["", "0", "-1", "many"])
    def test_a_jobs_value_that_says_nothing_usable_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.os.cpu_count", lambda: 4
        )
        monkeypatch.setenv("PCONS_JOBS", value)
        assert _scan_workers(100) == 4


@functools.lru_cache(maxsize=1)
def _gcc_scans_modules() -> bool:
    """Whether the g++ on this host answers a p1689 scan."""
    if shutil.which("g++") is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.cppm"
        src.write_text("export module probe;\n", encoding="utf-8")
        return run_scan_deps_gcc("g++", ["-std=c++20"], str(src), "probe.o") is not None


@pytest.mark.skipif(
    not _gcc_scans_modules(), reason="no g++ that answers a p1689 module scan"
)
class TestScanCacheAgainstARealCompiler:
    """The cache decided against what a compiler really reported reading.

    Everything else here stubs the runner out, so the depfile it invalidates
    against is one the test wrote. These tests take GCC's own.
    """

    @staticmethod
    def _project(tmp_path: Path, include: str) -> TuScanSpec:
        (tmp_path / "dep.hpp").write_text("#pragma once\n", encoding="utf-8")
        src = tmp_path / "m.cppm"
        src.write_text(
            f"module;\n{include}\nexport module m;\nexport int f();\n", encoding="utf-8"
        )
        for path in (tmp_path / "dep.hpp", src):
            aged = path.stat().st_mtime_ns - 5_000_000_000
            os.utime(path, ns=(aged, aged))
        return TuScanSpec(
            src=src, obj_rel="m.o", compiler="g++", compile_flags=["-std=c++20"]
        )

    @staticmethod
    def _scan(spec: TuScanSpec, tmp_path: Path) -> list[TuScanResult]:
        cache = ScanCache(tmp_path)
        results = scan_translation_units(
            [spec], "g++", scanner_style="gcc", cache=cache
        )
        cache.save()
        return results

    def test_a_second_pass_does_not_run_the_compiler(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = self._project(tmp_path, '#include "dep.hpp"')
        first = self._scan(spec, tmp_path)
        assert first[0].logical_name == "m"

        def _no_scanning(*args: object, **kwargs: object) -> object:
            raise AssertionError("the compiler ran for a TU nothing touched")

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _no_scanning
        )
        second = self._scan(spec, tmp_path)

        assert second[0].p1689 == first[0].p1689

    def test_touching_a_header_the_compiler_read_rescans(self, tmp_path: Path) -> None:
        spec = self._project(tmp_path, '#include "dep.hpp"')
        self._scan(spec, tmp_path)

        (tmp_path / "dep.hpp").write_text(
            "#pragma once\nimport nothing_at_all;\n", encoding="utf-8"
        )
        again = self._scan(spec, tmp_path)

        assert "nothing_at_all" in again[0].required_logical_names

    def test_a_header_named_by_a_macro_is_still_a_prerequisite(
        self, tmp_path: Path
    ) -> None:
        """`-fdirectives-only` skips macro expansion, but not in directives.

        A computed `#include` still resolves, so the header it names lands in
        the depfile and an edit to it still invalidates the entry. Left
        untested, the scan would answer from a header it no longer matches.
        """
        spec = self._project(tmp_path, '#define WHICH "dep.hpp"\n#include WHICH')
        self._scan(spec, tmp_path)

        (tmp_path / "dep.hpp").write_text(
            "#pragma once\nimport computed;\n", encoding="utf-8"
        )
        again = self._scan(spec, tmp_path)

        assert "computed" in again[0].required_logical_names


class TestGccScanDiscardsPreprocessedOutput:
    """The GCC scan wants the p1689 JSON, not the preprocessed text.

    `-E` writes the whole translation unit somewhere, and that somewhere used
    to be a temp file nothing ever read: measured at 3.2 MB for one real C++26
    source, to extract 91 bytes of JSON.
    """

    @staticmethod
    def _captured_argv(monkeypatch: pytest.MonkeyPatch) -> list[str]:
        seen: list[list[str]] = []

        class _Ok:
            returncode = 0
            stderr = ""

        def _capture(cmd: list[str], *args: object, **kwargs: object) -> _Ok:
            seen.append(list(cmd))
            # The deps file is named in the command; write valid JSON there so
            # the runner parses rather than warning.
            deps = next(a.split("=", 1)[1] for a in cmd if a.startswith("-fdeps-file="))
            Path(deps).write_text("{}", encoding="utf-8")
            return _Ok()

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _capture
        )
        assert run_scan_deps_gcc("g++", ["-std=c++23"], "/x/y.cppm", "y.o") == {}
        return seen[0]

    def test_the_scan_skips_macro_expansion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the module declarations are wanted, not an expanded TU."""
        assert "-fdirectives-only" in self._captured_argv(monkeypatch)

    def test_the_scan_does_not_touch_the_flags_it_was_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The BMI-compatibility invariant, guarded where it could break.

        `bmi_key_for_flags` hashes a TU's compile flags, and the caller passes
        the very same list here. Appending a scan-only flag to it in place
        would change every BMI key and every compile line, so the module
        interfaces on disk would no longer match what consumes them.
        """
        seen: list[list[str]] = []

        class _Ok:
            returncode = 0
            stderr = ""

        def _capture(cmd: list[str], *args: object, **kwargs: object) -> _Ok:
            seen.append(list(cmd))
            deps = next(a.split("=", 1)[1] for a in cmd if a.startswith("-fdeps-file="))
            Path(deps).write_text("{}", encoding="utf-8")
            return _Ok()

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _capture
        )
        flags = ["-std=c++23", "-fmodules-ts"]
        run_scan_deps_gcc("g++", flags, "/x/y.cppm", "y.o")

        assert flags == ["-std=c++23", "-fmodules-ts"]
        assert "-fdirectives-only" in seen[0]

    def test_output_goes_to_the_null_device(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        argv = self._captured_argv(monkeypatch)
        assert argv[-2:] == ["-o", os.devnull]

    def test_no_temp_file_is_created_for_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """os.devnull, not a literal: on Windows it is NUL, not /dev/null."""
        argv = self._captured_argv(monkeypatch)
        assert not any(a.endswith(".ii") for a in argv)


class _FakeNode:
    """Minimal stand-in for FileNode for wire_std_into_targets tests."""

    def __init__(self) -> None:
        self.explicit_deps: list[_FakeNode] = []


class _FakeTarget:
    def __init__(
        self, intermediates: list[_FakeNode], outputs: list[_FakeNode]
    ) -> None:
        self.intermediate_nodes = intermediates
        self.output_nodes = outputs


class _FakeProject:
    def __init__(self, targets: list[_FakeTarget]) -> None:
        self.targets = targets


class TestWireStdIntoTargets:
    """The shared helper that links synthesized std-module .obj/.o files
    into every target whose TUs `import std;` (or `import std.compat;`).

    Toolchain-agnostic: the same logic is correct for MSVC and clang.
    """

    def test_links_std_into_importing_target(self) -> None:
        consumer_obj = _FakeNode()  # this TU's `import std;` requirement
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        results = [
            TuScanResult(
                spec=consumer_spec,
                p1689={"rules": [{"requires": [{"logical-name": "std"}]}]},
            )
        ]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)

        assert std_obj in target.intermediate_nodes
        assert std_obj in target_output.explicit_deps

    def test_skips_targets_that_do_not_import_std(self) -> None:
        consumer_obj = _FakeNode()
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        # No `requires` — this TU doesn't import std.
        results = [TuScanResult(spec=consumer_spec, p1689={"rules": [{}]})]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        assert std_obj not in target.intermediate_nodes
        assert std_obj not in target_output.explicit_deps

    def test_idempotent(self) -> None:
        # Running twice must not duplicate the std obj on the target.
        consumer_obj = _FakeNode()
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        results = [
            TuScanResult(
                spec=consumer_spec,
                p1689={"rules": [{"requires": [{"logical-name": "std"}]}]},
            )
        ]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        assert target.intermediate_nodes.count(std_obj) == 1
        assert target_output.explicit_deps.count(std_obj) == 1


_CLANG_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"-frtti", "-fno-rtti", "-fexperimental-library"}),
    prefixes=("-std=", "-stdlib=", "-isysroot="),
    paired=frozenset({"-target", "-isysroot"}),
    define_prefix="-D",
    define_glob_prefixes=("_LIBCPP_",),
)


_MSVC_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"/MD", "/MDd", "/MT", "/MTd", "/EHsc", "/GR-"}),
    prefixes=("/std:", "/Zc:", "/arch:"),
    paired=frozenset(),
    define_prefix="/D",
    define_glob_prefixes=("_HAS_", "_ITERATOR_DEBUG_LEVEL"),
)


class TestSelectStdModuleFlags:
    """Picks ABI-affecting flags from a user's compile flags so the
    std-module compile and consumer TUs agree on the std library's ABI.

    Mismatches here range from silent corruption (mismatched RTTI) to
    iterator heap corruption (`_ITERATOR_DEBUG_LEVEL`) — the spec is
    load-bearing.
    """

    def test_clang_minimum_set(self) -> None:
        # User flags carry the things every std-module compile needs.
        out = select_std_module_flags(
            ["-std=c++23", "-stdlib=libc++", "-O2", "-Wall", "-fno-rtti"],
            _CLANG_LIKE_SPEC,
        )
        assert "-std=c++23" in out
        assert "-stdlib=libc++" in out
        assert "-fno-rtti" in out
        # Optimization and warning flags are not ABI-relevant; they must
        # NOT propagate (or `-Werror` would turn libc++'s deliberate
        # warnings into hard errors).
        assert "-O2" not in out
        assert "-Wall" not in out

    def test_libcxx_define_propagates(self) -> None:
        # `_LIBCPP_HARDENING_MODE` is the canonical example: the std
        # module must be compiled with the same value as consumer TUs,
        # otherwise libc++ ABI varies between them.
        out = select_std_module_flags(
            [
                "-std=c++23",
                "-D_LIBCPP_HARDENING_MODE=fast",
                "-DAPP_VERSION=42",
                "-DFOO",
            ],
            _CLANG_LIKE_SPEC,
        )
        assert "-D_LIBCPP_HARDENING_MODE=fast" in out
        # User-app defines unrelated to libc++ must NOT propagate — they
        # could break the std-module compile or change preprocessor state.
        assert "-DAPP_VERSION=42" not in out
        assert "-DFOO" not in out

    def test_paired_flag_carries_value_token(self) -> None:
        # GCC-style `-target X86_64-...` and `-isysroot /sdk/path`: both
        # halves must propagate together, in order.
        out = select_std_module_flags(
            ["-std=c++23", "-target", "x86_64-apple-darwin", "-O2"],
            _CLANG_LIKE_SPEC,
        )
        i_target = out.index("-target")
        assert out[i_target + 1] == "x86_64-apple-darwin"

    def test_paired_flag_at_end_is_dropped(self) -> None:
        # If a paired flag appears as the last token (no value), drop it
        # rather than spilling off the end.
        out = select_std_module_flags(["-target"], _CLANG_LIKE_SPEC)
        assert out == []

    def test_msvc_runtime_library_propagates(self) -> None:
        # `/MDd` vs `/MD` is the canonical MSVC ABI footgun: a debug-CRT
        # consumer linked with a release-CRT std module is undefined
        # behavior. The spec MUST carry it.
        out = select_std_module_flags(
            ["/std:c++latest", "/MDd", "/Zc:char8_t-", "/Wall", "/O2"],
            _MSVC_LIKE_SPEC,
        )
        assert "/std:c++latest" in out
        assert "/MDd" in out
        assert "/Zc:char8_t-" in out
        assert "/Wall" not in out
        assert "/O2" not in out

    def test_msvc_iterator_debug_level_propagates(self) -> None:
        # `_ITERATOR_DEBUG_LEVEL` mismatch corrupts the heap. Must propagate.
        out = select_std_module_flags(
            ["/std:c++latest", "/D_ITERATOR_DEBUG_LEVEL=2", "/DUSER_FOO=1"],
            _MSVC_LIKE_SPEC,
        )
        assert "/D_ITERATOR_DEBUG_LEVEL=2" in out
        assert "/DUSER_FOO=1" not in out

    def test_preserves_input_order(self) -> None:
        # Order matters for prefixes that override later (e.g., the user
        # writing `-stdlib=libstdc++` after pcons inserts `-stdlib=libc++`).
        out = select_std_module_flags(
            ["-stdlib=libc++", "-std=c++20", "-D_LIBCPP_FOO=1", "-frtti"],
            _CLANG_LIKE_SPEC,
        )
        assert out == [
            "-stdlib=libc++",
            "-std=c++20",
            "-D_LIBCPP_FOO=1",
            "-frtti",
        ]


class TestBmiKeyForFlags:
    """A BMI's on-disk directory is keyed by the hash of its BMI-sensitive
    flags so compatible compiles share one interface and incompatible ones
    (e.g. different C++ dialects) stay separate.
    """

    def test_identical_bmi_flags_share_key(self) -> None:
        a = bmi_key_for_flags(["-std=c++23", "-O2"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-std=c++23", "-O0"], _CLANG_LIKE_SPEC)
        # -O level is not BMI-sensitive, so the key is the same.
        assert a == b

    def test_different_dialect_gives_different_key(self) -> None:
        a = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-std=c++26"], _CLANG_LIKE_SPEC)
        assert a != b

    def test_order_independent(self) -> None:
        a = bmi_key_for_flags(["-std=c++23", "-frtti"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(["-frtti", "-std=c++23"], _CLANG_LIKE_SPEC)
        assert a == b

    def test_non_bmi_flags_ignored(self) -> None:
        # Unrelated includes/defines do not change the key.
        a = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        b = bmi_key_for_flags(
            ["-std=c++23", "-I/some/inc", "-DUSER_FOO=1"], _CLANG_LIKE_SPEC
        )
        assert a == b

    def test_key_is_short_hex(self) -> None:
        key = bmi_key_for_flags(["-std=c++23"], _CLANG_LIKE_SPEC)
        assert len(key) == 12
        assert all(c in "0123456789abcdef" for c in key)


class TestWriteDyndepEntries:
    """Per-key dyndep emission: a single logical module can resolve to
    different BMI paths in different compatibility classes, which a flat
    {logical: path} map cannot express.
    """

    def test_keyed_provides_and_requires(self, tmp_path: Path) -> None:
        out = tmp_path / "x.dyndep"
        write_dyndep_entries(
            [
                (
                    "obj.lib1/provider.cppm.o",
                    ["cxx_modules/aaa/provider.gcm"],
                    [],
                ),
                (
                    "obj.lib1/consumer.cpp.o",
                    [],
                    ["cxx_modules/aaa/provider.gcm"],
                ),
                (
                    "obj.lib3/provider.cppm.o",
                    ["cxx_modules/bbb/provider.gcm"],
                    [],
                ),
            ],
            out,
        )
        text = out.read_text()
        assert text.startswith("ninja_dyndep_version = 1")
        assert (
            "build obj.lib1/provider.cppm.o | cxx_modules/aaa/provider.gcm: dyndep"
            in text
        )
        assert (
            "build obj.lib3/provider.cppm.o | cxx_modules/bbb/provider.gcm: dyndep"
            in text
        )
        assert (
            "build obj.lib1/consumer.cpp.o: dyndep | cxx_modules/aaa/provider.gcm"
            in text
        )


def _keyed_setup(
    *entries: tuple[TuScanResult, str],
) -> tuple[list[TuScanResult], dict[int, object], dict[int, str]]:
    """Build (results, spec_to_obj, obj_key) from (result, bmi_key) pairs."""
    results: list[TuScanResult] = []
    spec_to_obj: dict[int, object] = {}
    obj_key: dict[int, str] = {}
    for r, key in entries:
        obj_node = object()  # stand-in for a FileNode; only identity matters
        results.append(r)
        spec_to_obj[id(r.spec)] = obj_node
        obj_key[id(obj_node)] = key
    return results, spec_to_obj, obj_key


class TestMapModuleProviders:
    def test_maps_providers_per_key(self) -> None:
        results, spec_to_obj, obj_key = _keyed_setup(
            (_result("obj.lib1/provider.cppm.o", provides_name="provider"), "aaa"),
            (_result("obj.lib3/provider.cppm.o", provides_name="provider"), "bbb"),
            (_result("obj.lib1/consumer.cpp.o", requires=["provider"]), "aaa"),
        )
        providers = map_module_providers(
            results, spec_to_obj, obj_key, "cxx_modules", ".gcm"
        )
        assert providers == {
            ("aaa", "provider"): "obj.lib1/provider.cppm.o",
            ("bbb", "provider"): "obj.lib3/provider.cppm.o",
        }

    def test_same_class_collision_raises(self) -> None:
        results, spec_to_obj, obj_key = _keyed_setup(
            (_result("obj.lib1/provider.cppm.o", provides_name="provider"), "aaa"),
            (_result("obj.lib2/other.cppm.o", provides_name="provider"), "aaa"),
        )
        with pytest.raises(RuntimeError, match="two different objects"):
            map_module_providers(results, spec_to_obj, obj_key, "cxx_modules", ".gcm")

    def test_unregistered_spec_skipped(self) -> None:
        r = _result("obj.lib1/provider.cppm.o", provides_name="provider")
        providers = map_module_providers([r], {}, {}, "cxx_modules", ".gcm")
        assert providers == {}


class TestBuildKeyedEntries:
    def test_provides_and_requires_keyed_per_class(self) -> None:
        results, spec_to_obj, obj_key = _keyed_setup(
            (_result("obj.lib1/provider.cppm.o", provides_name="provider"), "aaa"),
            (_result("obj.lib1/consumer.cpp.o", requires=["provider"]), "aaa"),
        )
        providers = map_module_providers(
            results, spec_to_obj, obj_key, "cxx_modules", ".pcm"
        )
        entries = build_keyed_entries(
            results, spec_to_obj, obj_key, providers, "cxx_modules", ".pcm"
        )
        assert entries == [
            ("obj.lib1/provider.cppm.o", ["cxx_modules/aaa/provider.pcm"], []),
            ("obj.lib1/consumer.cpp.o", [], ["cxx_modules/aaa/provider.pcm"]),
        ]

    def test_import_with_provider_only_in_other_class_raises(self) -> None:
        results, spec_to_obj, obj_key = _keyed_setup(
            (_result("obj.lib1/provider.cppm.o", provides_name="provider"), "aaa"),
            (_result("obj.lib3/consumer.cpp.o", requires=["provider"]), "bbb"),
        )
        providers = map_module_providers(
            results, spec_to_obj, obj_key, "cxx_modules", ".pcm"
        )
        with pytest.raises(RuntimeError) as exc:
            build_keyed_entries(
                results, spec_to_obj, obj_key, providers, "cxx_modules", ".pcm"
            )
        msg = str(exc.value)
        assert "'provider'" in msg
        assert "obj.lib3/consumer.cpp.o" in msg
        assert "obj.lib1/provider.cppm.o" in msg

    def test_import_of_external_module_passed_through(self) -> None:
        # A module not provided anywhere in the project may be satisfied
        # externally; no entry and no error.
        results, spec_to_obj, obj_key = _keyed_setup(
            (_result("obj.lib1/consumer.cpp.o", requires=["vendor.sdk"]), "aaa"),
        )
        entries = build_keyed_entries(
            results, spec_to_obj, obj_key, {}, "cxx_modules", ".pcm"
        )
        assert entries == [("obj.lib1/consumer.cpp.o", [], [])]


class TestMergeScanCompileFlags:
    """Tests for merge_scan_compile_flags."""

    def test_dedups_extra_and_context_flags(self) -> None:
        from types import SimpleNamespace

        ctx = SimpleNamespace(
            flags=["-O2", "-std=c++23"],  # -std dup vs base, kept once
            includes=["inc", "/abs/inc"],
            defines=["FOO=1", "BAR"],
        )
        result = merge_scan_compile_flags(
            ["-std=c++23"], ctx, extra_flags=("-fmodules", "-fmodules")
        )
        assert result == [
            "-std=c++23",
            "-fmodules",
            "-O2",
            "-Iinc",
            "-I/abs/inc",
            "-DFOO=1",
            "-DBAR",
        ]

    def test_no_context(self) -> None:
        result = merge_scan_compile_flags(["-std=c++20"], None, extra_flags=("-x",))
        assert result == ["-std=c++20", "-x"]

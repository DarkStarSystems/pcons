# SPDX-License-Identifier: MIT
"""Unit tests for the configure-time C++ module scanner API.

These tests pre-canned P1689R5 JSON dicts directly into TuScanResult so the
classification logic (is_module_provider, is_interface, logical_name,
required_logical_names), the module-name -> file-path map, and the dyndep
output can be exercised without invoking a real scanner.
"""

from __future__ import annotations

from pathlib import Path

from pcons.toolchains.cxx_module_scanner import (
    TuScanResult,
    TuScanSpec,
    build_module_map,
    module_file_for,
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

# SPDX-License-Identifier: MIT
"""Tests for the C++20 modules collate step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pcons.core.collate import MANIFEST_VERSION
from pcons.toolchains.cxx_collate import (
    EXPORTS_VERSION,
    P1689_VERSION,
    bmi_path,
    collate,
    main,
)

KEY = "k1"
OTHER_KEY = "k2"
MODDIR = "cxx_modules/app"


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def ddi(
    build_dir: Path,
    rel: str,
    *,
    provides: list[dict[str, Any]] | None = None,
    requires: list[str] | None = None,
    version: int = P1689_VERSION,
) -> str:
    """Write one P1689R5 scan output and return its build-relative path."""
    rule: dict[str, Any] = {"primary-output": rel[: -len(".ddi")]}
    if provides is not None:
        rule["provides"] = provides
    if requires is not None:
        rule["requires"] = [{"logical-name": name} for name in requires]
    write_json(build_dir / rel, {"version": version, "rules": [rule]})
    return rel


def edge(
    build_dir: Path,
    out: str,
    *,
    provides: list[dict[str, Any]] | None = None,
    requires: list[str] | None = None,
    key: str = KEY,
    info_version: int = P1689_VERSION,
) -> dict[str, Any]:
    """Write an edge's scan output and return its manifest entry."""
    return {
        "out": out,
        "declared_outputs": [out],
        "info": ddi(
            build_dir,
            out + ".ddi",
            provides=provides,
            requires=requires,
            version=info_version,
        ),
        "args_file": out + ".modmap",
        "extra": {"key": key, "module_suffix": bool(provides)},
    }


def manifest(
    *,
    edges: list[dict[str, Any]],
    scope: str = "app",
    imports: list[str] | None = None,
    moddir: str = MODDIR,
    bmi_ext: str = ".pcm",
    style: str = "clang",
    edge_args: bool = True,
    std_exports: list[str] | None = None,
    std_error: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a manifest dict with sensible defaults."""
    extra: dict[str, Any] = {"style": style, "bmi_ext": bmi_ext, "moddir": moddir}
    if std_exports is not None:
        extra["std_exports"] = std_exports
    if std_error is not None:
        extra["std_error"] = std_error
    data: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "scanner": "cxx-modules",
        "scope": scope,
        "dyndep": f"scan/cxx-modules/{scope}.dyndep",
        "exports_out": f"scan/cxx-modules/{scope}.exports.json",
        "imports": imports or [],
        "edge_args": {"suffix": ".modmap", "var": None} if edge_args else None,
        "extra": extra,
        "edges": edges,
    }
    data.update(kwargs)
    return data


def exports_file(
    build_dir: Path,
    rel: str,
    modules: dict[str, dict[str, Any]],
    *,
    scope: str = "lib",
    version: int = EXPORTS_VERSION,
    std_objs: list[str] | None = None,
) -> str:
    """Write an exports file (as an upstream scope would) and return its path."""
    data: dict[str, Any] = {
        "version": version,
        "scanner": "cxx-modules",
        "scope": scope,
        "modules": modules,
    }
    if std_objs is not None:
        data["std_objs"] = std_objs
    write_json(build_dir / rel, data)
    return rel


def upstream_module(
    logical: str,
    *,
    key: str = KEY,
    scope: str = "lib",
    requires: list[str] | None = None,
) -> dict[str, Any]:
    """One entry of an upstream scope's exports."""
    return {
        "logical": logical,
        "key": key,
        "bmi": bmi_path(logical, f"cxx_modules/{scope}", key, ".pcm"),
        "obj": f"obj.{scope}/{logical}.cppm.o",
        "is_interface": True,
        "requires": requires or [],
    }


def std_exports_file(
    build_dir: Path,
    *,
    key: str = KEY,
    compat: bool = True,
) -> str:
    """Write the configure-written exports describing the std module."""
    modules: dict[str, dict[str, Any]] = {
        "std": {
            "logical": "std",
            "key": key,
            "bmi": f"cxx_modules/std/{key}/std.pcm",
            "obj": f"cxx_modules/std/{key}/std.o",
            "is_interface": True,
            "requires": [],
        }
    }
    if compat:
        modules["std.compat"] = {
            "logical": "std.compat",
            "key": key,
            "bmi": f"cxx_modules/std/{key}/std.compat.pcm",
            "obj": f"cxx_modules/std/{key}/std.compat.o",
            "is_interface": True,
            "requires": ["std"],
        }
    return exports_file(
        build_dir,
        f"scan/cxx-modules/std.{key}.exports.json",
        modules,
        scope=f"std/{key}",
    )


def dyndep_text(build_dir: Path, m: dict[str, Any]) -> str:
    """Read the dyndep file a manifest wrote."""
    return (build_dir / m["dyndep"]).read_text(encoding="utf-8")


def modmap(build_dir: Path, out: str) -> str:
    """Read the modmap collate wrote for one governed edge."""
    return (build_dir / (out + ".modmap")).read_text(encoding="utf-8")


def exports(build_dir: Path, m: dict[str, Any]) -> dict[str, Any]:
    """Parse the exports file a manifest wrote."""
    data = json.loads((build_dir / m["exports_out"]).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def two_tu_scope(build_dir: Path, **kwargs: Any) -> dict[str, Any]:
    """A provider of module M plus a TU that imports it."""
    return manifest(
        edges=[
            edge(
                build_dir,
                "obj.app/mod.cppm.o",
                provides=[{"logical-name": "M", "is-interface": True}],
            ),
            edge(build_dir, "obj.app/main.cpp.o", requires=["M"]),
        ],
        **kwargs,
    )


class TestBmiPath:
    """Keyed BMI paths."""

    @pytest.mark.parametrize(
        ("logical", "expected"),
        [
            ("M", "cxx_modules/app/k1/M.pcm"),
            ("jt.Math", "cxx_modules/app/k1/jt.Math.pcm"),
            ("jt.Math:BigUInt", "cxx_modules/app/k1/jt.Math-BigUInt.pcm"),
        ],
    )
    def test_shape(self, logical: str, expected: str) -> None:
        assert bmi_path(logical, MODDIR, KEY, ".pcm") == expected


class TestHappyPath:
    """One scope: an interface unit and an importer."""

    def test_golden_dyndep(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build obj.app/main.cpp.o: dyndep | cxx_modules/app/k1/M.pcm\n"
            "\n"
            "build obj.app/mod.cppm.o | cxx_modules/app/k1/M.pcm: dyndep\n"
        )

    def test_provider_modmap(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/mod.cppm.o") == (
            "-x c++-module\n-fmodule-output=cxx_modules/app/k1/M.pcm\n"
        )

    def test_importer_modmap(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=M=cxx_modules/app/k1/M.pcm\n"
        )

    def test_exports(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)

        assert collate(m, tmp_path) == 0
        assert exports(tmp_path, m) == {
            "version": EXPORTS_VERSION,
            "scanner": "cxx-modules",
            "scope": "app",
            "modules": {
                "M": {
                    "logical": "M",
                    "key": KEY,
                    "bmi": "cxx_modules/app/k1/M.pcm",
                    "obj": "obj.app/mod.cppm.o",
                    "is_interface": True,
                    "requires": [],
                }
            },
            "std_objs": [],
        }

    def test_bmi_directory_created(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "cxx_modules/app/k1").is_dir()

    def test_internal_partition_is_not_an_interface(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(
                    tmp_path,
                    "obj.app/impl.cppm.o",
                    provides=[{"logical-name": "M:Impl", "is-interface": False}],
                )
            ]
        )

        assert collate(m, tmp_path) == 0
        entry = exports(tmp_path, m)["modules"]["M:Impl"]
        assert entry["is_interface"] is False
        assert entry["bmi"] == "cxx_modules/app/k1/M-Impl.pcm"

    def test_partition_name_sanitized_everywhere(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(
                    tmp_path,
                    "obj.app/part.cppm.o",
                    provides=[{"logical-name": "M:Part"}],
                ),
                edge(tmp_path, "obj.app/mod.cppm.o", requires=["M:Part"]),
            ]
        )

        assert collate(m, tmp_path) == 0
        assert "cxx_modules/app/k1/M-Part.pcm" in dyndep_text(tmp_path, m)
        assert modmap(tmp_path, "obj.app/mod.cppm.o") == (
            "-fmodule-file=M:Part=cxx_modules/app/k1/M-Part.pcm\n"
        )


class TestCrossScope:
    """Imports resolved through a dependency scope's exports."""

    def test_import_resolves_and_is_referenced_verbatim(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build obj.app/main.cpp.o: dyndep | cxx_modules/lib/k1/L.pcm\n"
        )
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=L=cxx_modules/lib/k1/L.pcm\n"
        )

    def test_imported_modules_are_not_re_exported(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert exports(tmp_path, m)["modules"] == {}

    def test_own_scope_shadows_import(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"M": upstream_module("M")},
            )
        ]
        m = two_tu_scope(tmp_path, imports=imports)

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=M=cxx_modules/app/k1/M.pcm\n"
        )

    def test_conflicting_imports_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        one = upstream_module("L", scope="lib")
        other = upstream_module("L", scope="other")
        imports = [
            exports_file(tmp_path, "scan/cxx-modules/lib.exports.json", {"L": one}),
            exports_file(
                tmp_path,
                "scan/cxx-modules/other.exports.json",
                {"L": other},
                scope="other",
            ),
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "pcons cxx-collate:" in err
        assert "'lib'" in err and "'other'" in err


class TestTransitiveClosure:
    """A modmap names every module reachable from the TU's imports."""

    def test_within_scope(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(tmp_path, "obj.app/c.cppm.o", provides=[{"logical-name": "C"}]),
                edge(
                    tmp_path,
                    "obj.app/b.cppm.o",
                    provides=[{"logical-name": "B"}],
                    requires=["C"],
                ),
                edge(tmp_path, "obj.app/a.cpp.o", requires=["B"]),
            ]
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/a.cpp.o") == (
            "-fmodule-file=B=cxx_modules/app/k1/B.pcm\n"
            "-fmodule-file=C=cxx_modules/app/k1/C.pcm\n"
        )

    def test_dyndep_lists_only_direct_requires(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(tmp_path, "obj.app/c.cppm.o", provides=[{"logical-name": "C"}]),
                edge(
                    tmp_path,
                    "obj.app/b.cppm.o",
                    provides=[{"logical-name": "B"}],
                    requires=["C"],
                ),
                edge(tmp_path, "obj.app/a.cpp.o", requires=["B"]),
            ]
        )

        assert collate(m, tmp_path) == 0
        assert (
            "build obj.app/a.cpp.o: dyndep | cxx_modules/app/k1/B.pcm\n"
            in dyndep_text(tmp_path, m)
        )

    def test_through_an_import(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {
                    "B": upstream_module("B", requires=["C"]),
                    "C": upstream_module("C"),
                },
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/a.cpp.o", requires=["B"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/a.cpp.o") == (
            "-fmodule-file=B=cxx_modules/lib/k1/B.pcm\n"
            "-fmodule-file=C=cxx_modules/lib/k1/C.pcm\n"
        )

    def test_cycle_terminates(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {
                    "B": upstream_module("B", requires=["C"]),
                    "C": upstream_module("C", requires=["B"]),
                },
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/a.cpp.o", requires=["B"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/a.cpp.o").count("-fmodule-file=") == 2

    def test_unresolvable_transitive_is_skipped(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"B": upstream_module("B", requires=["vendor.prebuilt"])},
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/a.cpp.o", requires=["B"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/a.cpp.o") == (
            "-fmodule-file=B=cxx_modules/lib/k1/B.pcm\n"
        )


class TestExternalModules:
    """A name nothing provides may still be satisfied by the compiler.

    So it is a warning, not an error — but a warning that names the missing
    edge, since the usual cause is a missing dependency on the target that
    does provide the module.
    """

    def test_warns_but_still_builds(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["vendor.prebuilt"])]
        )

        assert collate(m, tmp_path) == 0
        err = capsys.readouterr().err
        assert "obj.app/main.cpp.o imports module 'vendor.prebuilt'" in err
        assert "scope 'app'" in err
        assert "link() or depends()" in err
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n\nbuild obj.app/main.cpp.o: dyndep\n"
        )
        assert modmap(tmp_path, "obj.app/main.cpp.o") == ""

    def test_resolved_imports_are_silent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert collate(two_tu_scope(tmp_path), tmp_path) == 0
        assert capsys.readouterr().err == ""

    def test_transitive_unresolvable_does_not_warn(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The scope that compiles that TU is the one that warns about it."""
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"B": upstream_module("B", requires=["vendor.prebuilt"])},
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/a.cpp.o", requires=["B"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 0
        assert capsys.readouterr().err == ""


class TestModmapWriting:
    """Every governed edge gets a modmap, even an empty one."""

    def test_empty_modmap_is_written(self, tmp_path: Path) -> None:
        m = manifest(edges=[edge(tmp_path, "obj.app/plain.cpp.o")])

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "obj.app/plain.cpp.o.modmap").read_text() == ""

    def test_no_modmaps_without_edge_args(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path, edge_args=False)

        assert collate(m, tmp_path) == 0
        assert not (tmp_path / "obj.app/main.cpp.o.modmap").exists()

    def test_missing_args_file_path_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = edge(tmp_path, "obj.app/main.cpp.o")
        del spec["args_file"]

        assert collate(manifest(edges=[spec]), tmp_path) == 1
        assert "args_file" in capsys.readouterr().err

    def test_paths_with_spaces_are_quoted(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(tmp_path, "obj.app/mod.cppm.o", provides=[{"logical-name": "M"}]),
                edge(tmp_path, "obj.app/main.cpp.o", requires=["M"]),
            ],
            moddir="my modules/app",
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            '"-fmodule-file=M=my modules/app/k1/M.pcm"\n'
        )


class TestKeyedCompatibility:
    """BMI keys separate incompatible compilations of the same module."""

    def test_same_module_under_two_keys_is_allowed(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(
                    tmp_path,
                    "obj.app/mod.cppm.o",
                    provides=[{"logical-name": "M"}],
                ),
                edge(
                    tmp_path,
                    "obj.other/mod.cppm.o",
                    provides=[{"logical-name": "M"}],
                    key=OTHER_KEY,
                ),
                edge(tmp_path, "obj.other/main.cpp.o", requires=["M"], key=OTHER_KEY),
            ]
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.other/main.cpp.o") == (
            "-fmodule-file=M=cxx_modules/app/k2/M.pcm\n"
        )
        modules = exports(tmp_path, m)["modules"]
        assert set(modules) == {"M", f"M#{OTHER_KEY}"}
        assert modules[f"M#{OTHER_KEY}"]["bmi"] == "cxx_modules/app/k2/M.pcm"

    def test_same_scope_collision_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[
                edge(tmp_path, "obj.app/one.cppm.o", provides=[{"logical-name": "M"}]),
                edge(tmp_path, "obj.app/two.cppm.o", provides=[{"logical-name": "M"}]),
            ]
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "two different objects" in err
        assert "obj.app/one.cppm.o" in err and "obj.app/two.cppm.o" in err
        assert "cxx_modules/app/k1/M.pcm" in err

    def test_key_mismatch_within_scope_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[
                edge(
                    tmp_path,
                    "obj.app/mod.cppm.o",
                    provides=[{"logical-name": "M"}],
                    key=OTHER_KEY,
                ),
                edge(tmp_path, "obj.app/main.cpp.o", requires=["M"]),
            ]
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "imported by obj.app/main.cpp.o" in err
        assert "different BMI-sensitive flags" in err
        assert "obj.app/mod.cppm.o" in err

    def test_key_mismatch_via_import_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L", key=OTHER_KEY)},
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "imported by obj.app/main.cpp.o" in err
        assert "obj.lib/L.cppm.o" in err


class TestStandardLibraryModule:
    """`import std;` resolves through the configure-written std exports."""

    def test_std_resolves(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std"])],
            std_exports=[std_exports_file(tmp_path)],
        )

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build obj.app/main.cpp.o: dyndep | cxx_modules/std/k1/std.pcm\n"
        )
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=std=cxx_modules/std/k1/std.pcm\n"
        )

    def test_std_compat_closure_pulls_std(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std.compat"])],
            std_exports=[std_exports_file(tmp_path)],
        )

        assert collate(m, tmp_path) == 0
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=std=cxx_modules/std/k1/std.pcm\n"
            "-fmodule-file=std.compat=cxx_modules/std/k1/std.compat.pcm\n"
        )
        # Only the direct import orders the build; std comes with it.
        assert "| cxx_modules/std/k1/std.compat.pcm\n" in dyndep_text(tmp_path, m)

    def test_a_module_interface_may_import_std(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(
                    tmp_path,
                    "obj.app/mod.cppm.o",
                    provides=[{"logical-name": "M"}],
                    requires=["std"],
                ),
                edge(tmp_path, "obj.app/main.cpp.o", requires=["M"]),
            ],
            std_exports=[std_exports_file(tmp_path)],
        )

        assert collate(m, tmp_path) == 0
        # The importer needs std's BMI too: it is in M's transitive closure.
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=M=cxx_modules/app/k1/M.pcm\n"
            "-fmodule-file=std=cxx_modules/std/k1/std.pcm\n"
        )

    def test_unavailable_std_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std"])])

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "no standard-library module is available" in err
        assert "docs/user-guide.md" in err

    def test_unavailable_std_compat_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std.compat"])],
            std_exports=[std_exports_file(tmp_path, compat=False)],
        )

        assert collate(m, tmp_path) == 1
        assert "no standard-library module is available" in capsys.readouterr().err

    def test_toolchain_hint_replaces_the_default_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std"])],
            std_error="install Homebrew LLVM: Apple clang has no std module",
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "pcons cxx-collate: install Homebrew LLVM" in err
        assert "docs/user-guide.md" not in err

    def test_std_key_mismatch_keeps_the_key_mismatch_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std"])],
            std_exports=[std_exports_file(tmp_path, key=OTHER_KEY)],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "different BMI-sensitive flags" in err
        assert "no standard-library module is available" not in err


class TestLinkArgsFile:
    """The response file naming the std module objects a link needs."""

    def test_written_for_a_local_import(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std.compat"])],
            std_exports=[std_exports_file(tmp_path)],
            link_args_file="app.stdmods.rsp",
        )

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "app.stdmods.rsp").read_text() == (
            "cxx_modules/std/k1/std.compat.o\ncxx_modules/std/k1/std.o\n"
        )

    def test_inherited_from_an_imported_scope(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
                std_objs=["cxx_modules/std/k1/std.o"],
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
            link_args_file="app.stdmods.rsp",
        )

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "app.stdmods.rsp").read_text() == (
            "cxx_modules/std/k1/std.o\n"
        )

    def test_union_of_local_and_inherited(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
                std_objs=["cxx_modules/std/k1/std.o", "other/legacy.o"],
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L", "std.compat"])],
            imports=imports,
            std_exports=[std_exports_file(tmp_path)],
            link_args_file="app.stdmods.rsp",
        )

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "app.stdmods.rsp").read_text() == (
            "cxx_modules/std/k1/std.compat.o\n"
            "cxx_modules/std/k1/std.o\n"
            "other/legacy.o\n"
        )

    def test_empty_when_nothing_uses_std(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path, link_args_file="app.stdmods.rsp")

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "app.stdmods.rsp").read_text() == ""

    def test_absent_from_manifest_writes_nothing(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["std"])],
            std_exports=[std_exports_file(tmp_path)],
        )

        assert collate(m, tmp_path) == 0
        assert list(tmp_path.glob("*.rsp")) == []

    def test_exports_propagate_std_objs(self, tmp_path: Path) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
                std_objs=["other/legacy.o"],
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L", "std"])],
            imports=imports,
            std_exports=[std_exports_file(tmp_path)],
        )

        assert collate(m, tmp_path) == 0
        assert exports(tmp_path, m)["std_objs"] == [
            "cxx_modules/std/k1/std.o",
            "other/legacy.o",
        ]


class TestValidation:
    """Nothing is written unless every input checks out."""

    def test_manifest_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = two_tu_scope(tmp_path)
        m["version"] = 99

        assert collate(m, tmp_path) == 1
        assert "expected 1" in capsys.readouterr().err

    def test_scan_output_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", info_version=99)],
        )

        assert collate(m, tmp_path) == 1
        assert "obj.app/main.cpp.o.ddi" in capsys.readouterr().err

    def test_import_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        imports = [
            exports_file(
                tmp_path,
                "scan/cxx-modules/lib.exports.json",
                {"L": upstream_module("L")},
                version=99,
            )
        ]
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o", requires=["L"])],
            imports=imports,
        )

        assert collate(m, tmp_path) == 1
        assert "lib.exports.json" in capsys.readouterr().err

    def test_missing_scan_output_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[
                {
                    "out": "obj.app/main.cpp.o",
                    "info": "obj.app/main.cpp.o.ddi",
                    "args_file": "obj.app/main.cpp.o.modmap",
                    "extra": {"key": KEY},
                }
            ]
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "obj.app/main.cpp.o" in err

    def test_invalid_scan_output_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(edges=[edge(tmp_path, "obj.app/main.cpp.o")])
        (tmp_path / "obj.app/main.cpp.o.ddi").write_text("{ not json", encoding="utf-8")

        assert collate(m, tmp_path) == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_missing_import_file_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[edge(tmp_path, "obj.app/main.cpp.o")],
            imports=["scan/cxx-modules/gone.exports.json"],
        )

        assert collate(m, tmp_path) == 1
        assert "gone.exports.json" in capsys.readouterr().err

    def test_missing_key_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = edge(tmp_path, "obj.app/main.cpp.o")
        spec["extra"] = {}

        assert collate(manifest(edges=[spec]), tmp_path) == 1
        assert "extra.key" in capsys.readouterr().err

    def test_missing_moddir_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = two_tu_scope(tmp_path)
        m["extra"] = {"style": "clang", "bmi_ext": ".pcm"}

        assert collate(m, tmp_path) == 1
        assert "moddir" in capsys.readouterr().err

    def test_unknown_style_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = two_tu_scope(tmp_path, style="sunpro")

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "not implemented yet" in err
        assert "'sunpro'" in err

    def test_msvc_style_modmap(self, tmp_path: Path) -> None:
        """MSVC gets /interface + /ifcOutput on providers and explicit
        /reference name=path lines everywhere, replacing /ifcSearchDir."""
        m = two_tu_scope(tmp_path, style="msvc", bmi_ext=".ifc")

        assert collate(m, tmp_path) == 0
        provider = (tmp_path / "obj.app/mod.cppm.o.modmap").read_text().splitlines()
        importer = (tmp_path / "obj.app/main.cpp.o.modmap").read_text().splitlines()
        assert provider[0] == "/interface"
        # Flag and argument share a line: cl.exe does not join across
        # response-file lines (D8004).
        assert any(
            line.startswith("/ifcOutput ") and line.endswith(".ifc")
            for line in provider
        )
        assert any(
            line.startswith("/reference M=") and line.endswith(".ifc")
            for line in importer
        )

    def test_gcc_style_modmap_is_a_module_mapper(self, tmp_path: Path) -> None:
        """GCC gets the libcody mapper dialect: provides and the transitive
        import closure as `<logical> <path>` lines, anchored by `$root .` —
        GCC writes a provider's BMI where the mapper says, so the provide
        line replaces clang's -fmodule-output."""
        m = two_tu_scope(tmp_path, style="gcc", bmi_ext=".gcm")

        assert collate(m, tmp_path) == 0
        provider = (tmp_path / "obj.app/mod.cppm.o.modmap").read_text().splitlines()
        importer = (tmp_path / "obj.app/main.cpp.o.modmap").read_text().splitlines()
        assert provider[0] == "$root ."
        assert any(line.startswith("M ") and line.endswith(".gcm") for line in provider)
        assert importer[0] == "$root ."
        assert any(line.startswith("M ") and line.endswith(".gcm") for line in importer)
        assert not any(line.startswith("-x") for line in provider)

    def test_missing_dyndep_path_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = two_tu_scope(tmp_path)
        del m["dyndep"]

        assert collate(m, tmp_path) == 1
        assert "dyndep" in capsys.readouterr().err

    def test_error_leaves_previous_outputs_in_place(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = two_tu_scope(tmp_path)
        assert collate(m, tmp_path) == 0
        good_dyndep = dyndep_text(tmp_path, m)
        good_modmap = modmap(tmp_path, "obj.app/main.cpp.o")

        # Break it: a second provider of M with the same key.
        m["edges"].append(
            edge(tmp_path, "obj.app/two.cppm.o", provides=[{"logical-name": "M"}])
        )
        assert collate(m, tmp_path) == 1
        assert capsys.readouterr().err

        assert dyndep_text(tmp_path, m) == good_dyndep
        assert modmap(tmp_path, "obj.app/main.cpp.o") == good_modmap
        assert not (tmp_path / "obj.app/two.cppm.o.modmap").exists()

    def test_unknown_keys_are_ignored(self, tmp_path: Path) -> None:
        m = two_tu_scope(tmp_path)
        m["future_field"] = {"anything": 1}
        m["edges"][0]["future_field"] = True

        assert collate(m, tmp_path) == 0


class TestPathNormalization:
    """Emitted paths always use forward slashes."""

    def test_backslashes_normalized(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                edge(tmp_path, "obj.app/mod.cppm.o", provides=[{"logical-name": "M"}]),
                {
                    "out": "obj.app\\main.cpp.o",
                    "declared_outputs": ["obj.app\\main.cpp.o"],
                    "info": ddi(tmp_path, "obj.app/main.cpp.o.ddi", requires=["M"]),
                    "args_file": "obj.app\\main.cpp.o.modmap",
                    "extra": {"key": KEY},
                },
            ]
        )

        assert collate(m, tmp_path) == 0
        assert "\\" not in dyndep_text(tmp_path, m)
        assert modmap(tmp_path, "obj.app/main.cpp.o") == (
            "-fmodule-file=M=cxx_modules/app/k1/M.pcm\n"
        )


class TestMain:
    """The CLI wrapper."""

    def test_runs_from_the_build_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        m = two_tu_scope(tmp_path)
        write_json(tmp_path / "scan/cxx-modules/app.manifest.json", m)
        monkeypatch.chdir(tmp_path)

        assert main(["--manifest", "scan/cxx-modules/app.manifest.json"]) == 0
        assert (tmp_path / m["dyndep"]).exists()
        assert (tmp_path / m["exports_out"]).exists()

    def test_missing_manifest_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)

        assert main(["--manifest", "nope.json"]) == 1
        assert "nope.json" in capsys.readouterr().err

    def test_malformed_manifest_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert main(["--manifest", "bad.json"]) == 1
        assert "bad.json" in capsys.readouterr().err

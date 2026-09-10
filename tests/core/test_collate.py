# SPDX-License-Identifier: MIT
"""Tests for the generic scanner collate step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pcons.core.collate import (
    EXPORTS_VERSION,
    MANIFEST_VERSION,
    SCAN_INFO_VERSION,
    collate,
    main,
    read_dyndep_entries,
    sanitize_logical_name,
    write_dyndep_entries,
    write_text_if_changed,
)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def scan_info(
    build_dir: Path,
    rel: str,
    *,
    provides: list[dict[str, str]] | None = None,
    requires: list[str] | None = None,
    extra_deps: list[str] | None = None,
    extra_outputs: list[str] | None = None,
    version: int = SCAN_INFO_VERSION,
) -> str:
    """Write one scan-info file and return its build-relative path."""
    data: dict[str, Any] = {"version": version}
    if provides is not None:
        data["provides"] = provides
    if requires is not None:
        data["requires"] = requires
    if extra_deps is not None:
        data["extra_deps"] = extra_deps
    if extra_outputs is not None:
        data["extra_outputs"] = extra_outputs
    write_json(build_dir / rel, data)
    return rel


def manifest(
    *,
    edges: list[dict[str, Any]],
    scope: str = "pack_level1",
    scanner: str = "scene-refs",
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a manifest dict with sensible defaults."""
    data: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "scanner": scanner,
        "scope": scope,
        "dyndep": f"scan/{scanner}/{scope}.dyndep",
        "exports_out": f"scan/{scanner}/{scope}.exports.json",
        "edges": edges,
    }
    data.update(kwargs)
    return data


def exports_file(
    build_dir: Path,
    rel: str,
    provides: dict[str, str],
    *,
    scope: str = "pack_common",
    version: int = EXPORTS_VERSION,
) -> str:
    """Write an exports file (as an upstream scope would) and return its path."""
    write_json(
        build_dir / rel,
        {
            "version": version,
            "scanner": "scene-refs",
            "scope": scope,
            "provides": provides,
        },
    )
    return rel


def dyndep_text(build_dir: Path, m: dict[str, Any]) -> str:
    """Read the dyndep file a manifest wrote."""
    return (build_dir / m["dyndep"]).read_text(encoding="utf-8")


def test_dyndep_paths_with_spaces_are_escaped(tmp_path):
    """A discovered path may carry spaces (an install category); unescaped
    they would split into two paths in the dyndep syntax."""
    from pcons.core.collate import write_dyndep_entries

    out = tmp_path / "x.dyndep"
    write_dyndep_entries(
        [("stage/My Plugin.stamp", ["staging/Sapphire Lighting/S_Glow.plugin"], [])],
        out,
    )
    text = out.read_text()
    assert (
        "build stage/My$ Plugin.stamp | staging/Sapphire$ Lighting/S_Glow.plugin: dyndep"
        in text
    )


class TestWriteTextIfChanged:
    """The content-addressed write helper."""

    def test_writes_and_records_digest(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "out.txt"
        write_text_if_changed(target, "hello\n")

        assert target.read_text() == "hello\n"
        assert (tmp_path / "sub" / "out.txt.sha256").exists()

    def test_identical_rewrite_is_a_noop(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        write_text_if_changed(target, "hello\n")
        before = target.stat().st_mtime_ns

        write_text_if_changed(target, "hello\n")

        assert target.stat().st_mtime_ns == before

    def test_changed_content_is_rewritten(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        write_text_if_changed(target, "hello\n")
        write_text_if_changed(target, "goodbye\n")

        assert target.read_text() == "goodbye\n"


class TestWriteDyndepEntries:
    """Golden text of the dyndep writer."""

    def test_sorted_deduped_and_versioned(self, tmp_path: Path) -> None:
        out = tmp_path / "x.dyndep"
        write_dyndep_entries(
            [("b.o", ["b.pcm", "b.pcm"], []), ("a.o", [], ["z.pcm", "a.pcm"])],
            out,
        )

        assert out.read_text() == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build a.o: dyndep | a.pcm z.pcm\n"
            "\n"
            "build b.o | b.pcm: dyndep\n"
        )


class TestReadDyndepEntries:
    """Reading a dyndep file back, for anyone reporting on a finished build."""

    def test_round_trip(self, tmp_path: Path) -> None:
        out = tmp_path / "x.dyndep"
        entries = [("b.o", ["b.pcm"], []), ("a.o", [], ["a.pcm", "z.pcm"])]
        write_dyndep_entries([(o, list(p), list(r)) for o, p, r in entries], out)

        assert read_dyndep_entries(out) == sorted(entries)

    def test_escaped_paths_come_back_whole(self, tmp_path: Path) -> None:
        out = tmp_path / "x.dyndep"
        write_dyndep_entries([("my obj.o", [], ["c:/x/a b.pcm"])], out)

        assert read_dyndep_entries(out) == [("my obj.o", [], ["c:/x/a b.pcm"])]

    def test_missing_file_reads_as_nothing(self, tmp_path: Path) -> None:
        assert read_dyndep_entries(tmp_path / "never-built.dyndep") == []


class TestSanitizeLogicalName:
    """Logical-name to filename-fragment mapping."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("plain", "plain"),
            ("jt.Math:BigUInt", "jt.Math-BigUInt"),
            ("art/props/crate", "art_props_crate"),
            ("a:b/c", "a-b_c"),
        ],
    )
    def test_substitutions(self, name: str, expected: str) -> None:
        assert sanitize_logical_name(name) == expected


class TestHappyPath:
    """A two-edge scope resolving one requirement."""

    def test_golden_dyndep(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}],
                        extra_outputs=["packs/alpha.pack"],
                    ),
                },
                {
                    "out": "b.pack",
                    "declared_outputs": ["b.pack"],
                    "info": scan_info(tmp_path, "b.scaninfo.json", requires=["alpha"]),
                },
            ],
        )

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build a.pack | packs/alpha.pack: dyndep\n"
            "\n"
            "build b.pack: dyndep | packs/alpha.pack\n"
        )

    def test_explicit_path_wins_over_template(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[
                            {"name": "alpha", "path": "custom/alpha.bin"},
                            {"name": "beta"},
                        ],
                        extra_outputs=["custom/alpha.bin", "packs/beta.pack"],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert "custom/alpha.bin" in dyndep_text(tmp_path, m)
        assert "packs/beta.pack" in dyndep_text(tmp_path, m)

    def test_per_edge_template_overrides(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "provide_template": "special/{scanner}/{scope}/{name}.bin",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}],
                        extra_outputs=["special/scene-refs/pack_level1/alpha.bin"],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert "special/scene-refs/pack_level1/alpha.bin" in dyndep_text(tmp_path, m)

    def test_template_sanitizes_logical_name(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="bmi/{name}.ifc",
            edges=[
                {
                    "out": "a.o",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "jt.Math:BigUInt"}],
                        extra_outputs=["bmi/jt.Math-BigUInt.ifc"],
                    ),
                },
                {
                    "out": "b.o",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", requires=["jt.Math:BigUInt"]
                    ),
                },
            ],
        )

        assert collate(m, tmp_path) == 0
        text = dyndep_text(tmp_path, m)
        assert "build a.o | bmi/jt.Math-BigUInt.ifc: dyndep" in text
        assert "build b.o: dyndep | bmi/jt.Math-BigUInt.ifc" in text

    def test_declared_output_is_not_an_implicit_out(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha", "path": "a.pack"}],
                    ),
                },
                {
                    "out": "b.pack",
                    "declared_outputs": ["b.pack"],
                    "info": scan_info(tmp_path, "b.scaninfo.json", requires=["alpha"]),
                },
            ],
        )

        assert collate(m, tmp_path) == 0
        text = dyndep_text(tmp_path, m)
        assert "build a.pack: dyndep\n" in text  # no implicit out
        assert "build b.pack: dyndep | a.pack\n" in text  # still resolves

    def test_extra_deps_and_outputs(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        extra_deps=["assets/tex.png"],
                        extra_outputs=["a.pack", "a.pack.meta"],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build a.pack | a.pack.meta: dyndep | assets/tex.png\n"
        )


class TestProvideMustBeWritten:
    """A provide path has to be a file the edge actually writes."""

    def test_provide_equal_to_declared_output(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack", "a.side"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha", "path": "a.side"}],
                    ),
                },
                {
                    "out": "b.pack",
                    "declared_outputs": ["b.pack"],
                    "info": scan_info(tmp_path, "b.scaninfo.json", requires=["alpha"]),
                },
            ]
        )

        assert collate(m, tmp_path) == 0
        assert dyndep_text(tmp_path, m) == (
            "ninja_dyndep_version = 1\n"
            "\n"
            "build a.pack: dyndep\n"
            "\n"
            "build b.pack: dyndep | a.side\n"
        )

    def test_provide_backed_by_extra_output_appears_once(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}],
                        extra_outputs=["packs/alpha.pack"],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        line = "build a.pack | packs/alpha.pack: dyndep\n"
        text = dyndep_text(tmp_path, m)
        assert line in text
        assert text.count("packs/alpha.pack") == 1

    def test_unwritten_provide_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "declared_outputs": ["a.pack"],
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[
                            {"name": "alpha", "path": "a.pack"},
                            {"name": "ghost"},
                        ],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "edge 'a.pack' provides 'ghost' at 'packs/ghost.pack'" in err
        assert "extra_outputs" in err
        assert "rebuild forever" in err
        assert not (tmp_path / m["dyndep"]).exists()


class TestImports:
    """Cross-scope resolution through exports files."""

    def test_import_resolves_requirement(self, tmp_path: Path) -> None:
        imported = exports_file(
            tmp_path, "scan/common.exports.json", {"alpha": "packs/alpha.pack"}
        )
        m = manifest(
            imports=[imported],
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(tmp_path, "b.scaninfo.json", requires=["alpha"]),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert "build b.pack: dyndep | packs/alpha.pack" in dyndep_text(tmp_path, m)

    def test_own_scope_shadows_import(self, tmp_path: Path) -> None:
        imported = exports_file(
            tmp_path, "scan/common.exports.json", {"alpha": "upstream/alpha.pack"}
        )
        m = manifest(
            imports=[imported],
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha", "path": "local/alpha.pack"}],
                        extra_outputs=["local/alpha.pack"],
                    ),
                },
                {
                    "out": "b.pack",
                    "info": scan_info(tmp_path, "b.scaninfo.json", requires=["alpha"]),
                },
            ],
        )

        assert collate(m, tmp_path) == 0
        text = dyndep_text(tmp_path, m)
        assert "build b.pack: dyndep | local/alpha.pack" in text
        assert "upstream" not in text

    def test_agreeing_imports_are_fine(self, tmp_path: Path) -> None:
        first = exports_file(
            tmp_path, "scan/one.exports.json", {"alpha": "packs/alpha.pack"}
        )
        second = exports_file(
            tmp_path,
            "scan/two.exports.json",
            {"alpha": "packs/alpha.pack", "beta": "packs/beta.pack"},
            scope="pack_other",
        )
        m = manifest(
            imports=[first, second],
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", requires=["alpha", "beta"]
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0

    def test_conflicting_imports_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first = exports_file(
            tmp_path, "scan/one.exports.json", {"alpha": "one/alpha.pack"}
        )
        second = exports_file(
            tmp_path,
            "scan/two.exports.json",
            {"alpha": "two/alpha.pack"},
            scope="pack_other",
        )
        m = manifest(
            imports=[first, second],
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(tmp_path, "b.scaninfo.json"),
                }
            ],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "one/alpha.pack" in err
        assert "two/alpha.pack" in err
        assert "pack_common" in err and "pack_other" in err
        assert not (tmp_path / m["dyndep"]).exists()

    def test_missing_import_file_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            imports=["scan/nope.exports.json"],
            edges=[{"out": "b.pack", "info": scan_info(tmp_path, "b.scaninfo.json")}],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "scan/nope.exports.json" in err
        assert "pack_level1" in err

    def test_import_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = exports_file(
            tmp_path, "scan/one.exports.json", {"alpha": "a.pack"}, version=99
        )
        m = manifest(
            imports=[bad],
            edges=[{"out": "b.pack", "info": scan_info(tmp_path, "b.scaninfo.json")}],
        )

        assert collate(m, tmp_path) == 1
        assert "version" in capsys.readouterr().err


class TestErrors:
    """Validation failures leave every output untouched."""

    def test_duplicate_provider_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path, "a.scaninfo.json", provides=[{"name": "alpha"}]
                    ),
                },
                {
                    "out": "b.pack",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", provides=[{"name": "alpha"}]
                    ),
                },
            ],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "a.pack" in err and "b.pack" in err
        assert "alpha" in err

    def test_missing_scan_info_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(edges=[{"out": "a.pack", "info": "missing.scaninfo.json"}])

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "a.pack" in err and "missing.scaninfo.json" in err

    def test_scan_info_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(tmp_path, "a.scaninfo.json", version=7),
                }
            ]
        )

        assert collate(m, tmp_path) == 1
        assert "version" in capsys.readouterr().err

    def test_manifest_version_mismatch_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(edges=[])
        m["version"] = 42

        assert collate(m, tmp_path) == 1
        assert "manifest" in capsys.readouterr().err

    def test_provide_without_path_or_template_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path, "a.scaninfo.json", provides=[{"name": "alpha"}]
                    ),
                }
            ]
        )

        assert collate(m, tmp_path) == 1
        assert "provide_template" in capsys.readouterr().err

    def test_error_leaves_previous_outputs_in_place(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        good = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha", "path": "packs/alpha.pack"}],
                        extra_outputs=["packs/alpha.pack"],
                    ),
                }
            ]
        )
        assert collate(good, tmp_path) == 0
        before = dyndep_text(tmp_path, good)

        bad = manifest(edges=[{"out": "a.pack", "info": "gone.scaninfo.json"}])
        assert collate(bad, tmp_path) == 1

        assert dyndep_text(tmp_path, good) == before
        capsys.readouterr()

    def test_bad_on_unresolved_mode_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(on_unresolved="explode", edges=[])

        assert collate(m, tmp_path) == 1
        assert "on_unresolved" in capsys.readouterr().err


class TestOnUnresolved:
    """The three unresolved-requirement policies."""

    def _manifest(self, tmp_path: Path, mode: str) -> dict[str, Any]:
        return manifest(
            on_unresolved=mode,
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", requires=["nowhere"]
                    ),
                }
            ],
        )

    def test_ignore(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        m = self._manifest(tmp_path, "ignore")

        assert collate(m, tmp_path) == 0
        assert capsys.readouterr().err == ""
        assert "build b.pack: dyndep\n" in dyndep_text(tmp_path, m)

    def test_default_is_ignore(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", requires=["nowhere"]
                    ),
                }
            ]
        )

        assert collate(m, tmp_path) == 0

    def test_warn(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        m = self._manifest(tmp_path, "warn")

        assert collate(m, tmp_path) == 0
        err = capsys.readouterr().err
        assert "nowhere" in err and "b.pack" in err
        assert (tmp_path / m["dyndep"]).exists()

    def test_error_lists_every_unresolved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            on_unresolved="error",
            edges=[
                {
                    "out": "b.pack",
                    "info": scan_info(
                        tmp_path, "b.scaninfo.json", requires=["nowhere", "alsogone"]
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 1
        err = capsys.readouterr().err
        assert "nowhere" in err and "alsogone" in err
        assert not (tmp_path / m["dyndep"]).exists()


class TestExports:
    """The exports file this scope writes for its dependents."""

    def test_contents(self, tmp_path: Path) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            imports=[
                exports_file(
                    tmp_path, "scan/common.exports.json", {"other": "up/other.pack"}
                )
            ],
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}, {"name": "art/crate"}],
                        requires=["other"],
                        extra_outputs=["packs/alpha.pack", "packs/art_crate.pack"],
                    ),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        data = json.loads((tmp_path / m["exports_out"]).read_text())
        assert data == {
            "version": EXPORTS_VERSION,
            "scanner": "scene-refs",
            "scope": "pack_level1",
            "provides": {
                "alpha": "packs/alpha.pack",
                "art/crate": "packs/art_crate.pack",
            },
        }

    def test_ends_with_newline(self, tmp_path: Path) -> None:
        m = manifest(edges=[])

        assert collate(m, tmp_path) == 0
        assert (tmp_path / m["exports_out"]).read_text().endswith("}\n")


class TestEdgeArgs:
    """Per-edge argument files."""

    def _manifest(self, tmp_path: Path, include: str) -> dict[str, Any]:
        return manifest(
            provide_template="packs/{name}.pack",
            edge_args={
                "suffix": ".modmap",
                "var": "SCAN_ARGS",
                "format": {
                    "header": ["$root .", "# generated"],
                    "line": "{name}={path}",
                },
                "include": include,
            },
            edges=[
                {
                    "out": "a.pack",
                    "args_file": "a.modmap",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}],
                        extra_outputs=["packs/alpha.pack"],
                    ),
                },
                {
                    "out": "b.pack",
                    "args_file": "b.modmap",
                    "info": scan_info(
                        tmp_path,
                        "b.scaninfo.json",
                        provides=[{"name": "beta"}],
                        requires=["alpha"],
                        extra_outputs=["packs/beta.pack"],
                    ),
                },
            ],
        )

    def test_requires_only(self, tmp_path: Path) -> None:
        m = self._manifest(tmp_path, "requires")

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "b.modmap").read_text() == (
            "$root .\n# generated\nalpha=packs/alpha.pack\n"
        )

    def test_requires_plus_provides(self, tmp_path: Path) -> None:
        m = self._manifest(tmp_path, "requires+provides")

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "b.modmap").read_text() == (
            "$root .\n# generated\nbeta=packs/beta.pack\nalpha=packs/alpha.pack\n"
        )

    def test_header_only_file_still_written(self, tmp_path: Path) -> None:
        m = manifest(
            edge_args={"format": {"header": ["$root ."], "line": "{name} {path}"}},
            edges=[
                {
                    "out": "a.pack",
                    "args_file": "sub/a.modmap",
                    "info": scan_info(tmp_path, "a.scaninfo.json"),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "sub" / "a.modmap").read_text() == "$root .\n"

    def test_empty_file_still_written(self, tmp_path: Path) -> None:
        m = manifest(
            edge_args={"format": {"line": "{name} {path}"}},
            edges=[
                {
                    "out": "a.pack",
                    "args_file": "a.modmap",
                    "info": scan_info(tmp_path, "a.scaninfo.json"),
                }
            ],
        )

        assert collate(m, tmp_path) == 0
        assert (tmp_path / "a.modmap").read_text() == ""

    def test_missing_args_file_path_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = manifest(
            edge_args={"format": {"line": "{name} {path}"}},
            edges=[{"out": "a.pack", "info": scan_info(tmp_path, "a.scaninfo.json")}],
        )

        assert collate(m, tmp_path) == 1
        assert "args_file" in capsys.readouterr().err

    def test_bad_include_mode_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        m = self._manifest(tmp_path, "everything")

        assert collate(m, tmp_path) == 1
        assert "include" in capsys.readouterr().err


class TestMain:
    """The CLI wrapper."""

    def test_runs_from_the_build_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        m = manifest(
            provide_template="packs/{name}.pack",
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha"}],
                        extra_outputs=["packs/alpha.pack"],
                    ),
                }
            ],
        )
        write_json(tmp_path / "scan/level1.manifest.json", m)
        monkeypatch.chdir(tmp_path)

        assert main(["--manifest", "scan/level1.manifest.json"]) == 0
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


class TestPathNormalization:
    """Emitted paths always use forward slashes."""

    def test_backslashes_normalized(self, tmp_path: Path) -> None:
        m = manifest(
            edges=[
                {
                    "out": "a.pack",
                    "info": scan_info(
                        tmp_path,
                        "a.scaninfo.json",
                        provides=[{"name": "alpha", "path": "packs\\alpha.pack"}],
                        extra_deps=["assets\\tex.png"],
                        extra_outputs=["packs\\alpha.pack"],
                    ),
                }
            ]
        )

        assert collate(m, tmp_path) == 0
        text = dyndep_text(tmp_path, m)
        assert "\\" not in text
        assert "build a.pack | packs/alpha.pack: dyndep | assets/tex.png" in text

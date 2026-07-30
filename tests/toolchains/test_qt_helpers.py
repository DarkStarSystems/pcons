# SPDX-License-Identifier: MIT
"""Tests for the Qt build-time helper scripts (_scan_check, _moc_predefs,
_stamped) — run in-process against synthetic trees."""

from __future__ import annotations

import json
import sys

import pytest

from pcons.toolchains.qt import _moc_predefs, _scan_check, _stamped
from pcons.toolchains.qt.scan import QtScanner


@pytest.fixture
def scan_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "widget.h").write_text("#pragma once\nclass W { };\n")
    (src / "main.cpp").write_text('#include "widget.h"\nint main() { return 0; }\n')
    return tmp_path


def _write_manifest(tmp_path, scan_tree, **overrides):
    scan = QtScanner(scan_tree).scan_target_sources([scan_tree / "src" / "main.cpp"])
    manifest = {
        "version": 1,
        "target": "app",
        "project_root": str(scan_tree),
        "sources": [str(scan_tree / "src" / "main.cpp")],
        "include_dirs": [],
        "no_moc": [],
        "moc_headers": [str(p) for p in scan.moc_headers],
        "moc_sources": [str(p) for p in scan.moc_sources],
    }
    manifest.update(overrides)
    path = tmp_path / "scan-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class TestScanCheck:
    def test_unchanged_scan_passes_and_writes_stamp(self, tmp_path, scan_tree):
        manifest = _write_manifest(tmp_path, scan_tree)
        stamp = tmp_path / "scan.ok"
        depfile = tmp_path / "scan.ok.d"
        rc = _scan_check.main(
            ["--manifest", str(manifest), "-o", str(stamp), "--depfile", str(depfile)]
        )
        assert rc == 0
        assert stamp.exists()
        deps = depfile.read_text(encoding="utf-8")
        assert "main.cpp" in deps
        # Directories are dependencies too (new-file detection).
        assert str(scan_tree / "src").replace("\\", "/") in deps

    def test_header_gaining_macro_fails_loudly(self, tmp_path, scan_tree, capsys):
        manifest = _write_manifest(tmp_path, scan_tree)
        (scan_tree / "src" / "widget.h").write_text(
            "#pragma once\nclass W : public QObject { Q_OBJECT };\n"
        )
        rc = _scan_check.main(
            [
                "--manifest",
                str(manifest),
                "-o",
                str(tmp_path / "scan.ok"),
                "--depfile",
                str(tmp_path / "scan.ok.d"),
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "widget.h" in err
        assert "Re-run pcons" in err

    def test_unknown_manifest_version_fails_cleanly(self, tmp_path, scan_tree, capsys):
        manifest = _write_manifest(tmp_path, scan_tree, version=99)
        rc = _scan_check.main(
            [
                "--manifest",
                str(manifest),
                "-o",
                str(tmp_path / "scan.ok"),
                "--depfile",
                str(tmp_path / "scan.ok.d"),
            ]
        )
        assert rc == 1
        assert "re-run pcons" in capsys.readouterr().err.lower()

    def test_stamp_not_rewritten_when_unchanged(self, tmp_path, scan_tree):
        manifest = _write_manifest(tmp_path, scan_tree)
        stamp = tmp_path / "scan.ok"
        args = [
            "--manifest",
            str(manifest),
            "-o",
            str(stamp),
            "--depfile",
            str(tmp_path / "scan.ok.d"),
        ]
        assert _scan_check.main(args) == 0
        first_mtime = stamp.stat().st_mtime_ns
        assert _scan_check.main(args) == 0
        # restat contract: untouched stamp means dependents stay clean.
        assert stamp.stat().st_mtime_ns == first_mtime


class TestStamped:
    def test_success_touches_stamp(self, tmp_path):
        stamp = tmp_path / "out" / "done.stamp"
        rc = _stamped.main(["--stamp", str(stamp), "--", sys.executable, "-c", "pass"])
        assert rc == 0
        assert stamp.exists()

    def test_failure_propagates_and_skips_stamp(self, tmp_path):
        stamp = tmp_path / "done.stamp"
        rc = _stamped.main(
            [
                "--stamp",
                str(stamp),
                "--",
                sys.executable,
                "-c",
                "import sys; sys.exit(3)",
            ]
        )
        assert rc == 3
        assert not stamp.exists()


class TestMocPredefs:
    def test_captures_predefined_macros(self, tmp_path):
        import shutil

        cxx = shutil.which("clang++") or shutil.which("g++")
        if cxx is None:
            pytest.skip("no C++ compiler")
        out = tmp_path / "moc_predefs.h"
        rc = _moc_predefs.main(["--cxx", cxx, "-o", str(out)])
        assert rc == 0
        assert "#define" in out.read_text(encoding="utf-8")

    def test_compiler_flags_pass_through(self, tmp_path):
        import shutil

        cxx = shutil.which("clang++") or shutil.which("g++")
        if cxx is None:
            pytest.skip("no C++ compiler")
        out = tmp_path / "moc_predefs.h"
        # Leading-dash flags must not be eaten by argument parsing.
        rc = _moc_predefs.main(
            ["--cxx", cxx, "-o", str(out), "-std=c++17", "-DPREDEF_PROBE=7"]
        )
        assert rc == 0
        assert "PREDEF_PROBE" in out.read_text(encoding="utf-8")

    def test_unchanged_output_keeps_mtime(self, tmp_path):
        import shutil

        cxx = shutil.which("clang++") or shutil.which("g++")
        if cxx is None:
            pytest.skip("no C++ compiler")
        out = tmp_path / "moc_predefs.h"
        assert _moc_predefs.main(["--cxx", cxx, "-o", str(out)]) == 0
        first = out.stat().st_mtime_ns
        assert _moc_predefs.main(["--cxx", cxx, "-o", str(out)]) == 0
        assert out.stat().st_mtime_ns == first

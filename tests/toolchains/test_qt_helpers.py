# SPDX-License-Identifier: MIT
"""Tests for the Qt build-time helper scripts (_automoc, _moc_predefs,
_stamped) — run in-process against synthetic trees.

The automoc tests stand in a fake moc: what matters here is which files the
tool decides to run moc on, which outputs it leaves behind, and what its
depfile says. Whether the real moc produces working meta-object code is
tests/toolchains/test_qt_automoc.py's question.
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from pcons.toolchains.qt import _automoc, _moc_predefs, _stamped

FAKE_MOC = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import sys
    from pathlib import Path

    args = sys.argv[1:]
    out = Path(args[args.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    if "--collect-json" in args:
        out.write_text("[collected]\\n")
        sys.exit(0)
    source = Path(args[-1])
    dep = Path(args[args.index("--dep-file-path") + 1])
    out.write_text(f"// moc of {source.name}\\n")
    dep.write_text(f"{out}: {source}\\n")
    if "--output-json" in args:
        out.with_name(out.name + ".json").write_text("{}\\n")
    """
)


@pytest.fixture
def scan_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "widget.h").write_text("#pragma once\nclass W { };\n")
    (src / "main.cpp").write_text('#include "widget.h"\nint main() { return 0; }\n')
    (tmp_path / "moc.py").write_text(FAKE_MOC)
    return tmp_path


def _write_spec(tmp_path, scan_tree, **overrides):
    spec = {
        "version": 1,
        "target": "app",
        "project_root": str(scan_tree),
        "gen_dir": str(tmp_path / "gen"),
        "sources": [str(scan_tree / "src" / "main.cpp")],
        "include_dirs": [],
        "no_moc": [],
        "moc": [sys.executable, str(scan_tree / "moc.py")],
        "moc_args": [],
        "moc_deps": [],
        "has_includes": True,
        "metatypes": None,
    }
    spec.update(overrides)
    path = tmp_path / "automoc.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _run(spec, tmp_path):
    return _automoc.main(
        [
            "--spec",
            str(spec),
            "-o",
            str(tmp_path / "gen" / "mocs_compilation.cpp"),
            "--depfile",
            str(tmp_path / "gen" / "mocs_compilation.cpp.d"),
        ]
    )


def _with_q_object(scan_tree):
    (scan_tree / "src" / "widget.h").write_text(
        "#pragma once\nclass W : public QObject { Q_OBJECT };\n"
    )


class TestAutomoc:
    def test_a_tree_with_no_macros_writes_an_empty_aggregator(
        self, tmp_path, scan_tree
    ):
        assert _run(_write_spec(tmp_path, scan_tree), tmp_path) == 0
        body = (tmp_path / "gen" / "mocs_compilation.cpp").read_text()
        assert "#include" not in body
        assert body.strip().endswith(";")

    def test_the_depfile_names_the_files_and_the_directories_read(
        self, tmp_path, scan_tree
    ):
        assert _run(_write_spec(tmp_path, scan_tree), tmp_path) == 0
        deps = (tmp_path / "gen" / "mocs_compilation.cpp.d").read_text()
        assert "main.cpp" in deps
        assert "widget.h" in deps
        assert str(scan_tree / "src").replace("\\", "/") in deps

    def test_a_header_with_q_object_is_mocd_into_the_aggregator(
        self, tmp_path, scan_tree
    ):
        _with_q_object(scan_tree)
        assert _run(_write_spec(tmp_path, scan_tree), tmp_path) == 0
        assert (tmp_path / "gen" / "src" / "moc_widget.cpp").exists()
        body = (tmp_path / "gen" / "mocs_compilation.cpp").read_text()
        assert '#include "src/moc_widget.cpp"' in body

    def test_no_moc_excludes_a_header(self, tmp_path, scan_tree):
        _with_q_object(scan_tree)
        spec = _write_spec(
            tmp_path, scan_tree, no_moc=[str(scan_tree / "src" / "widget.h")]
        )
        assert _run(spec, tmp_path) == 0
        assert not (tmp_path / "gen" / "src" / "moc_widget.cpp").exists()

    def test_a_header_that_loses_q_object_loses_its_moc_output(
        self, tmp_path, scan_tree
    ):
        _with_q_object(scan_tree)
        spec = _write_spec(tmp_path, scan_tree)
        assert _run(spec, tmp_path) == 0
        stale = tmp_path / "gen" / "src" / "moc_widget.cpp"
        assert stale.exists()

        (scan_tree / "src" / "widget.h").write_text("#pragma once\nclass W { };\n")
        assert _run(spec, tmp_path) == 0
        assert not stale.exists()
        assert not stale.with_name(stale.name + ".d").exists()

    def test_a_source_missing_its_moc_include_fails_loudly(
        self, tmp_path, scan_tree, capsys
    ):
        (scan_tree / "src" / "main.cpp").write_text(
            "class B : public QObject { Q_OBJECT };\nint main() { return 0; }\n"
        )
        assert _run(_write_spec(tmp_path, scan_tree), tmp_path) == 1
        assert '#include "main.moc"' in capsys.readouterr().err

    def test_an_unchanged_tree_leaves_the_aggregator_alone(self, tmp_path, scan_tree):
        _with_q_object(scan_tree)
        spec = _write_spec(tmp_path, scan_tree)
        assert _run(spec, tmp_path) == 0
        aggregator = tmp_path / "gen" / "mocs_compilation.cpp"
        first = aggregator.stat().st_mtime_ns
        assert _run(spec, tmp_path) == 0
        assert aggregator.stat().st_mtime_ns == first

    def test_unknown_spec_version_fails_cleanly(self, tmp_path, scan_tree, capsys):
        spec = _write_spec(tmp_path, scan_tree, version=99)
        assert _run(spec, tmp_path) == 1
        assert "re-run pcons" in capsys.readouterr().err.lower()

    def test_missing_include_paths_warn(self, tmp_path, scan_tree, capsys):
        _with_q_object(scan_tree)
        spec = _write_spec(tmp_path, scan_tree, has_includes=False)
        assert _run(spec, tmp_path) == 0
        assert "no include paths" in capsys.readouterr().err

    def test_metatypes_is_collected_when_asked(self, tmp_path, scan_tree):
        _with_q_object(scan_tree)
        metatypes = tmp_path / "gen" / "app_metatypes.json"
        spec = _write_spec(
            tmp_path,
            scan_tree,
            metatypes=str(metatypes),
            moc_args=["--output-json"],
        )
        assert _run(spec, tmp_path) == 0
        assert metatypes.read_text() == "[collected]\n"

    def test_metatypes_is_an_empty_document_when_nothing_was_mocd(
        self, tmp_path, scan_tree
    ):
        """It is a declared output, so it exists even with no moc'ed types.

        ``moc --collect-json`` exits 1 on an empty input list, which would
        fail the build of any QML module whose C++ declares no Q_OBJECT.
        """
        metatypes = tmp_path / "gen" / "app_metatypes.json"
        spec = _write_spec(tmp_path, scan_tree, metatypes=str(metatypes))
        assert _run(spec, tmp_path) == 0
        assert metatypes.read_text() == "[]\n"


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

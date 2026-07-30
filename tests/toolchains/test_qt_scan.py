# SPDX-License-Identifier: MIT
"""Tests for the Qt automoc scanner (generate-time Q_OBJECT discovery)."""

from __future__ import annotations

import pytest

from pcons.toolchains.qt.scan import MocIncludeError, QtScanner


@pytest.fixture
def tree(tmp_path):
    """A small project tree with a variety of moc situations."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.h").write_text("#pragma once\nint f();\n")
    (src / "widget.h").write_text(
        "#pragma once\n#include <QObject>\n"
        "class Widget : public QObject {\n    Q_OBJECT\npublic:\n    Widget();\n};\n"
    )
    (src / "widget.cpp").write_text('#include "widget.h"\nWidget::Widget() {}\n')
    # One-liner declaration (CMake's AUTOMOC regex misses these).
    (src / "oneliner.h").write_text(
        "#pragma once\n#include <QObject>\nclass One : public QObject { Q_OBJECT };\n"
    )
    (src / "main.cpp").write_text(
        '#include "widget.h"\n#include "oneliner.h"\nint main() { return 0; }\n'
    )
    return tmp_path


class TestMacroDetection:
    def test_header_with_q_object(self, tree):
        scanner = QtScanner(tree)
        assert scanner.scan_file(tree / "src" / "widget.h").macros == ("Q_OBJECT",)

    def test_plain_header(self, tree):
        scanner = QtScanner(tree)
        assert scanner.scan_file(tree / "src" / "plain.h").macros == ()

    def test_oneliner_class(self, tree):
        scanner = QtScanner(tree)
        assert scanner.scan_file(tree / "src" / "oneliner.h").macros == ("Q_OBJECT",)

    @pytest.mark.parametrize("macro", ["Q_GADGET", "Q_NAMESPACE", "Q_NAMESPACE_EXPORT"])
    def test_other_macros(self, tree, macro):
        path = tree / "src" / "other.h"
        path.write_text(f"#pragma once\n{macro}\n")
        assert macro in QtScanner(tree).scan_file(path).macros

    def test_macro_in_comment_ignored(self, tree):
        path = tree / "src" / "commented.h"
        path.write_text("#pragma once\n// Q_OBJECT\n/* Q_OBJECT */\nint x;\n")
        assert QtScanner(tree).scan_file(path).macros == ()

    def test_macro_in_string_ignored(self, tree):
        path = tree / "src" / "stringy.cpp"
        path.write_text('const char *s = "contains Q_OBJECT macro";\n')
        assert QtScanner(tree).scan_file(path).macros == ()

    def test_word_boundary(self, tree):
        path = tree / "src" / "boundary.h"
        path.write_text("#define MY_Q_OBJECTISH 1\nint Q_OBJECTS[3];\n")
        assert QtScanner(tree).scan_file(path).macros == ()


class TestTargetScan:
    def test_finds_headers_via_includes(self, tree):
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "main.cpp"])
        names = [p.name for p in scan.moc_headers]
        assert "widget.h" in names
        assert "oneliner.h" in names
        assert "plain.h" not in names

    def test_same_basename_header_scanned_without_include(self, tree):
        # widget.cpp includes widget.h, but even a cpp that doesn't
        # include its own header still gets the sibling scanned.
        (tree / "src" / "silent.h").write_text(
            "#pragma once\n#include <QObject>\nclass S : public QObject { Q_OBJECT };\n"
        )
        (tree / "src" / "silent.cpp").write_text("int silent() { return 1; }\n")
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "silent.cpp"])
        assert [p.name for p in scan.moc_headers] == ["silent.h"]

    def test_cpp_with_q_object(self, tree):
        (tree / "src" / "inline.cpp").write_text(
            "#include <QObject>\nclass I : public QObject { Q_OBJECT };\n"
            '#include "inline.moc"\n'
        )
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "inline.cpp"])
        assert [p.name for p in scan.moc_sources] == ["inline.cpp"]

    def test_no_moc_exclusion(self, tree):
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources(
            [tree / "src" / "main.cpp"], no_moc=[tree / "src" / "oneliner.h"]
        )
        names = [p.name for p in scan.moc_headers]
        assert "widget.h" in names
        assert "oneliner.h" not in names

    def test_out_of_project_ignored(self, tree, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (outside / "ext.h").write_text("Q_OBJECT\n")
        (tree / "src" / "uses_ext.cpp").write_text(f'#include "{outside}/ext.h"\n')
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "uses_ext.cpp"])
        assert scan.moc_headers == []

    def test_include_dirs_resolution(self, tree):
        inc = tree / "include"
        inc.mkdir()
        (inc / "faraway.h").write_text(
            "#include <QObject>\nclass F : public QObject { Q_OBJECT };\n"
        )
        (tree / "src" / "uses_far.cpp").write_text('#include "faraway.h"\n')
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources(
            [tree / "src" / "uses_far.cpp"], include_dirs=[inc]
        )
        assert [p.name for p in scan.moc_headers] == ["faraway.h"]

    def test_scanned_dirs_recorded(self, tree):
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "main.cpp"])
        assert (tree / "src").resolve() in scan.scanned_dirs


class TestCache:
    def test_cache_roundtrip(self, tree, tmp_path_factory):
        cache_dir = tmp_path_factory.mktemp("cache")
        scanner = QtScanner(tree, cache_dir=cache_dir)
        first = scanner.scan_file(tree / "src" / "widget.h")
        scanner.save_cache()
        assert (cache_dir / "qt-scan-cache.json").exists()

        fresh = QtScanner(tree, cache_dir=cache_dir)
        second = fresh.scan_file(tree / "src" / "widget.h")
        assert second.macros == first.macros
        assert second.includes == first.includes

    def test_cache_invalidated_on_change(self, tree, tmp_path_factory):
        import os

        cache_dir = tmp_path_factory.mktemp("cache")
        path = tree / "src" / "plain.h"
        scanner = QtScanner(tree, cache_dir=cache_dir)
        assert scanner.scan_file(path).macros == ()
        scanner.save_cache()

        path.write_text("#pragma once\nQ_OBJECT\n")
        # Force a different mtime even on coarse filesystems.
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        fresh = QtScanner(tree, cache_dir=cache_dir)
        assert fresh.scan_file(path).macros == ("Q_OBJECT",)


class TestMocIncludeCheck:
    def test_missing_include_raises_with_fix(self, tree):
        path = tree / "src" / "broken.cpp"
        path.write_text("#include <QObject>\nclass B : public QObject { Q_OBJECT };\n")
        scanner = QtScanner(tree)
        with pytest.raises(MocIncludeError, match=r'#include "broken\.moc"'):
            scanner.check_moc_include(path)

    def test_present_include_passes(self, tree):
        path = tree / "src" / "good.cpp"
        path.write_text(
            "#include <QObject>\nclass G : public QObject { Q_OBJECT };\n"
            '#include "good.moc"\n'
        )
        QtScanner(tree).check_moc_include(path)  # no raise


class TestDiscoveryGaps:
    """Regressions for the adversarial-review discovery findings."""

    def test_angle_include_resolved_via_include_dirs(self, tree):
        # Library-style self-includes: #include <obj.h> with -Isrc.
        (tree / "src" / "angle.h").write_text(
            "#pragma once\n#include <QObject>\nclass A : public QObject { Q_OBJECT };\n"
        )
        (tree / "src" / "uses_angle.cpp").write_text("#include <angle.h>\n")
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources(
            [tree / "src" / "uses_angle.cpp"], include_dirs=[tree / "src"]
        )
        assert [p.name for p in scan.moc_headers] == ["angle.h"]

    def test_angle_include_not_resolved_from_source_dir(self, tree):
        # Without an include dir, <...> must not resolve (preprocessor
        # semantics: angle form skips the including file's directory).
        (tree / "src" / "angle2.h").write_text("Q_OBJECT\n")
        (tree / "src" / "uses_angle2.cpp").write_text("#include <angle2.h>\n")
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "uses_angle2.cpp"])
        assert scan.moc_headers == []

    def test_out_of_project_header_via_include_dir(self, tree, tmp_path_factory):
        # Sibling-repo layout: -I../sibling-lib with a Q_OBJECT header.
        sibling = tmp_path_factory.mktemp("sibling-lib")
        (sibling / "remote.h").write_text(
            "#pragma once\n#include <QObject>\nclass R : public QObject { Q_OBJECT };\n"
        )
        (tree / "src" / "uses_remote.cpp").write_text('#include "remote.h"\n')
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources(
            [tree / "src" / "uses_remote.cpp"], include_dirs=[sibling]
        )
        assert [p.name for p in scan.moc_headers] == ["remote.h"]
        # The out-of-project file is tracked for staleness too.
        assert (sibling / "remote.h").resolve() in scan.scanned

    def test_headers_listed_in_sources_are_scanned(self, tree):
        # qt_add_executable(app main.cpp obj.h) convention.
        scan = QtScanner(tree).scan_target_sources(
            [tree / "src" / "widget.h"]  # a header passed as a "source"
        )
        assert [p.name for p in scan.moc_headers] == ["widget.h"]


class TestIncludeCycle:
    def test_mutually_including_headers_terminate(self, tree):
        (tree / "src" / "a.h").write_text('#include "b.h"\nQ_OBJECT\n')
        (tree / "src" / "b.h").write_text('#include "a.h"\n')
        (tree / "src" / "cyc.cpp").write_text('#include "a.h"\n')
        scanner = QtScanner(tree)
        scan = scanner.scan_target_sources([tree / "src" / "cyc.cpp"])
        assert [p.name for p in scan.moc_headers] == ["a.h"]

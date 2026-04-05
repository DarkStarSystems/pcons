# SPDX-License-Identifier: MIT
"""Tests for the LaTeX depfile conversion helper."""

from pathlib import Path

from pcons.util.latex_deps import _collect_fdb_deps, _collect_fls_deps, fls_to_depfile


class TestCollectFlsDeps:
    """Test .fls file parsing."""

    def test_extracts_input_lines(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text(
            "PWD /build\n"
            "INPUT ../src/main.tex\n"
            "INPUT /usr/share/texlive/article.cls\n"
            "OUTPUT main.pdf\n"
        )
        deps = _collect_fls_deps(fls)
        assert "../src/main.tex" in deps
        assert "/usr/share/texlive/article.cls" in deps

    def test_filters_intermediates(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text(
            "INPUT ../src/main.tex\n"
            "INPUT ./main.aux\n"
            "INPUT ./main.log\n"
            "INPUT ./main.toc\n"
            "INPUT ./main.bbl\n"
        )
        deps = _collect_fls_deps(fls)
        assert "../src/main.tex" in deps
        assert len(deps) == 1

    def test_deduplicates(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text(
            "INPUT ../src/main.tex\nINPUT ../src/main.tex\nINPUT ../src/main.tex\n"
        )
        deps = _collect_fls_deps(fls)
        assert deps == ["../src/main.tex"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        deps = _collect_fls_deps(tmp_path / "nonexistent.fls")
        assert deps == []


class TestCollectFdbDeps:
    """Test .fdb_latexmk file parsing."""

    def test_extracts_bib_files(self, tmp_path: Path) -> None:
        fdb = tmp_path / "main.fdb_latexmk"
        fdb.write_text(
            '["pdflatex"] 12345 "main.tex"\n'
            '  "/path/to/refs.bib" 1775424615.20727 138 aec0009dc "" \n'
            '  "/usr/share/texlive/plain.bst" 1234 42 abc123 ""\n'
            '  "main.aux" 9999 10 def456 ""\n'
        )
        deps = _collect_fdb_deps(fdb)
        assert "/path/to/refs.bib" in deps
        assert "/usr/share/texlive/plain.bst" in deps
        # .aux should not be included (not a bib-related ext)
        assert "main.aux" not in deps

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        deps = _collect_fdb_deps(tmp_path / "nonexistent.fdb_latexmk")
        assert deps == []


class TestFlsToDepfile:
    """Test combined depfile generation."""

    def test_writes_ninja_depfile(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text(
            "INPUT ../src/main.tex\n"
            "INPUT /usr/share/texlive/article.cls\n"
            "INPUT ./main.aux\n"
        )
        depfile = tmp_path / "main.pdf.d"
        fls_to_depfile(fls, depfile, "main.pdf")

        content = depfile.read_text()
        assert content.startswith("main.pdf:")
        assert "../src/main.tex" in content
        assert "/usr/share/texlive/article.cls" in content
        assert "main.aux" not in content

    def test_includes_bib_from_fdb(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text("INPUT ../src/main.tex\n")
        fdb = tmp_path / "main.fdb_latexmk"
        fdb.write_text('  "/path/to/refs.bib" 123 45 abc ""\n')

        depfile = tmp_path / "main.pdf.d"
        fls_to_depfile(fls, depfile, "main.pdf")

        content = depfile.read_text()
        assert "../src/main.tex" in content
        assert "/path/to/refs.bib" in content

    def test_escapes_spaces(self, tmp_path: Path) -> None:
        fls = tmp_path / "main.fls"
        fls.write_text("INPUT ../my docs/main.tex\n")

        depfile = tmp_path / "main.pdf.d"
        fls_to_depfile(fls, depfile, "main.pdf")

        content = depfile.read_text()
        assert "my\\ docs/main.tex" in content

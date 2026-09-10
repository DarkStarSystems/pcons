# SPDX-License-Identifier: MIT
"""Graph generators over a scanned project.

A scanner's own files (scan infos, manifest, dyndep, exports, argument files)
are machinery, not build products, so the plain graph elides them and marks
the edges they govern instead. ``include_scan`` draws them; ``include_discovered``
draws what a finished build's dyndep files record.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from pcons import EdgeArgsSpec
from pcons.core.collate import write_dyndep_entries
from pcons.generators.dot import DotGenerator
from pcons.generators.mermaid import MermaidGenerator
from tests.core.test_scan import make_scanner, reference_project


@pytest.fixture
def scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two governed packs, ``b`` after ``a``, resolved."""
    project, _scanner, _a, _b = reference_project(tmp_path, monkeypatch)
    return project


def render(project: Any, **options: Any) -> str:
    out = io.StringIO()
    DotGenerator(**options).write(project, out)
    return out.getvalue()


class TestPlainGraph:
    """The default view: no machinery, but the governed edges say so."""

    def test_elides_scanner_files(self, scanned):
        output = render(scanned)
        assert ".dyndep" not in output
        assert ".scaninfo" not in output
        assert "manifest.json" not in output
        assert "exports.json" not in output

    def test_marks_governed_outputs(self, scanned):
        output = render(scanned)
        assert 'label="packs/a.pack\\nscanned: scene-refs"' in output
        assert 'label="packs/b.pack\\nscanned: scene-refs"' in output

    def test_ungoverned_nodes_are_unmarked(self, scanned):
        assert "a.scene\\nscanned" not in render(scanned)

    def test_a_governed_output_keeps_its_own_shape(self, scanned):
        """Being scanned is a note on the node, not a downgrade of it: a
        dashed pack would read as provisional next to the solid ones."""
        line = next(
            line for line in render(scanned).splitlines() if "packs/a.pack" in line
        )
        assert "dashed" not in line
        assert "shape=box" in line

    def test_mermaid_marks_governed_outputs(self, scanned):
        out = io.StringIO()
        MermaidGenerator().write(scanned, out)
        output = out.getvalue()
        assert "packs/a.pack<br>scanned: scene-refs" in output
        assert ":::" not in next(
            line for line in output.splitlines() if "packs/a.pack" in line
        )
        assert ".dyndep" not in output


class TestIncludeScan:
    """include_scan draws the machinery, with its producers."""

    def test_draws_scan_and_collate_files(self, scanned):
        output = render(scanned, include_scan=True)
        assert "packs/a.pack.scaninfo.json" in output
        assert "t.pack_a.dyndep" in output
        assert "t.pack_a.manifest.json" in output
        assert "t.pack_a.exports.json" in output

    def test_scan_files_are_not_sources(self, scanned):
        """The dyndep file has a producer, so it must not read as a leaf."""
        output = render(scanned, include_scan=True)
        dyndep = [
            line
            for line in output.splitlines()
            if "t.pack_a.dyndep" in line and "[" in line
        ]
        assert dyndep and "shape=note" not in dyndep[0]

    def test_collate_orders_the_governed_edge(self, scanned):
        output = render(scanned, include_scan=True)
        assert (
            "  scan_scene_refs_t_pack_a_dyndep -> packs_a_pack "
            '[style=dashed color="#999999"];' in output
        )

    def test_scan_reads_the_scanned_source(self, scanned):
        output = render(scanned, include_scan=True)
        assert "  a_scene -> packs_a_pack_scaninfo_json " in output

    def test_the_args_file_reaches_the_edge_that_reads_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An args file is named by a per-edge variable, not a dependency,
        so nothing in the node graph would show it being consumed. Drawn as
        an unread output, it would look like a file the build forgot."""
        project, _s, _a, _b = reference_project(
            tmp_path,
            monkeypatch,
            make_scanner(edge_args=EdgeArgsSpec(suffix=".refs")),
        )
        output = render(project, include_scan=True)
        assert (
            '  packs_a_pack_refs -> packs_a_pack [style=dashed color="#999999"];'
            in output
        )

    def test_a_link_args_file_reaches_its_reader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """This one *is* a declared dep, but of a node the traversal skips."""
        project, _s, _a, _b = reference_project(
            tmp_path,
            monkeypatch,
            make_scanner(link_args=EdgeArgsSpec(suffix=".linkargs")),
        )
        output = render(project, include_scan=True)
        assert (
            "  scan_scene_refs_t_pack_a_linkargs -> packs_a_pack "
            '[style=dashed color="#999999"];' in output
        )


class TestIncludeDiscovered:
    """include_discovered reads back what the build discovered."""

    def test_nothing_before_a_build(self, scanned):
        output = render(scanned, include_discovered=True)
        assert "1f7a4d" not in output

    def test_draws_requires_and_provides(self, scanned, tmp_path):
        scope = scanned._scan_scopes[("scene-refs", "t::pack_b")]
        write_dyndep_entries(
            [("packs/b.pack", ["packs/b.digest"], ["packs/a.pack"])],
            tmp_path / "build" / scope.dyndep_rel,
        )
        output = render(scanned, include_discovered=True)
        # a.pack must precede b.pack, and b.pack also writes b.digest --
        # neither fact is in the build script.
        assert (
            '  packs_a_pack -> packs_b_pack [style=dashed color="#1f7a4d"];' in output
        )
        assert (
            '  packs_b_pack -> packs_b_digest [style=dashed color="#1f7a4d"];' in output
        )
        assert 'packs_b_digest [label="packs/b.digest"' in output

    def test_mermaid_labels_discovered_edges(self, scanned, tmp_path):
        scope = scanned._scan_scopes[("scene-refs", "t::pack_b")]
        write_dyndep_entries(
            [("packs/b.pack", [], ["packs/a.pack"])],
            tmp_path / "build" / scope.dyndep_rel,
        )
        out = io.StringIO()
        MermaidGenerator(include_discovered=True).write(scanned, out)
        assert "packs_a_pack -. discovered .-> packs_b_pack" in out.getvalue()

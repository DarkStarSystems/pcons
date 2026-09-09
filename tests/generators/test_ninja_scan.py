# SPDX-License-Identifier: MIT
"""Tests for the ninja generator's rendering of scanned (dyndep) builds.

Only the ninja generator can express dyndep, so this is where the Scanner
primitive's static half becomes build text: one shared scan rule, one shared
collate rule, and a ``dyndep =`` binding on every governed edge.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

from pcons import EdgeArgsSpec, Project, Scanner
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison."""
    return p.replace("\\", "/")


def make_scanner(**kwargs: Any) -> Scanner:
    """A scanner over ``.scene`` sources, with test-friendly defaults."""
    kwargs.setdefault("source_suffixes", [".scene"])
    kwargs.setdefault(
        "scan_command",
        [sys.executable, "$SRCDIR/tools/scan.py", "$SOURCE", "$TARGET"],
    )
    kwargs.setdefault("scan_deps", ["tools/scan.py"])
    kwargs.setdefault("provide_template", "packs/{name}.pack")
    kwargs.setdefault("on_unresolved", "error")
    return Scanner("scene-refs", **kwargs)


def build_ninja(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scanner: Scanner | None = None,
) -> str:
    """Generate the reference two-pack scanned project and return build.ninja."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "scan.py").write_text("# scan tool stub\n")
    for name in ("a.scene", "b.scene"):
        (tmp_path / name).write_text(f"contents of {name}\n")
    monkeypatch.chdir(tmp_path)

    project = Project("t", root_dir=tmp_path, build_dir="build")
    env = project.Environment()

    def pack(letter: str) -> Target:
        return env.Command(
            target=f"packs/{letter}.pack",
            source=[f"{letter}.scene"],
            command=["cp", "$SOURCE", "$TARGET"],
            name=f"pack_{letter}",
        )

    a, b = pack("a"), pack("b")
    b.depends(a)
    (scanner if scanner is not None else make_scanner()).attach(a, b)
    project.resolve()

    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return normalize_path((tmp_path / "build" / "build.ninja").read_text())


def statement(content: str, prefix: str) -> list[str]:
    """The build statement starting with *prefix*, plus its indented bindings."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            block = [line]
            for tail in lines[i + 1 :]:
                if not tail.startswith((" ", "\t")):
                    break
                block.append(tail.strip())
            return block
    raise AssertionError(f"no build statement starting with {prefix!r}")


def rule_block(content: str, prefix: str) -> list[str]:
    """The rule declaration whose name starts with *prefix*, plus its bindings."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"rule {prefix}"):
            block = [line]
            for tail in lines[i + 1 :]:
                if not tail.startswith((" ", "\t")):
                    break
                block.append(tail.strip())
            return block
    raise AssertionError(f"no rule starting with {prefix!r}")


class TestScanRules:
    """A constant description and a var-carried manifest keep rules shared."""

    def test_one_scan_rule_for_every_governed_edge(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        rules = re.findall(r"^rule scan_scene_refs_\S+$", content, re.MULTILINE)

        assert len(rules) == 1

    def test_one_collate_rule_for_every_scope(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        rules = re.findall(r"^rule collate_scene_refs_\S+$", content, re.MULTILINE)

        assert len(rules) == 1

    def test_collate_rule_references_the_manifest_var(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        block = rule_block(content, "collate_scene_refs_")

        assert any("--manifest $SCAN_MANIFEST" in line for line in block)

    def test_collate_rule_restats(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        assert "restat = 1" in rule_block(content, "collate_scene_refs_")

    def test_scan_rule_carries_the_depfile(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch, make_scanner(scan_depfile=".d"))

        block = rule_block(content, "scan_scene_refs_")

        assert "depfile = $out.d" in block
        assert "deps = gcc" in block

    def test_scan_rule_has_no_depfile_by_default(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        block = rule_block(content, "scan_scene_refs_")

        assert not any(line.startswith("depfile") for line in block)


class TestGovernedStatement:
    """Every governed edge names its target's dyndep file, twice."""

    def test_dyndep_is_an_implicit_dep_and_a_binding(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        block = statement(content, "build packs/b.pack:")

        assert "| scan/scene-refs/t.pack_b.dyndep" in block[0]
        assert "dyndep = scan/scene-refs/t.pack_b.dyndep" in block

    def test_each_target_gets_its_own_dyndep_file(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        assert "dyndep = scan/scene-refs/t.pack_a.dyndep" in statement(
            content, "build packs/a.pack:"
        )


class TestScanStatement:
    def test_scan_statement_inputs(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        line = statement(content, "build packs/b.pack.scaninfo.json:")[0]

        # b.depends(a) makes b's pack wait for a.pack (order-only:
        # its dyndep discovers what it reads). The scan inherits the wait,
        # and with no dependency tracking of its own holds it as implicit.
        assert re.match(
            r"^build packs/b\.pack\.scaninfo\.json: scan_scene_refs_\S+ "
            r"\$topdir/b\.scene \| \$topdir/tools/scan\.py packs/a\.pack$",
            line,
        ), line


class TestCollateStatement:
    def test_collate_outputs_and_inputs(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        line = statement(
            content,
            "build scan/scene-refs/t.pack_b.dyndep "
            "| scan/scene-refs/t.pack_b.exports.json:",
        )[0]

        assert "packs/b.pack.scaninfo.json" in line
        assert "scan/scene-refs/t.pack_b.manifest.json" in line
        assert "scan/scene-refs/t.pack_a.exports.json" in line

    def test_each_collate_statement_binds_its_own_manifest(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        for scope in ("t.pack_a", "t.pack_b"):
            block = statement(
                content,
                f"build scan/scene-refs/{scope}.dyndep "
                f"| scan/scene-refs/{scope}.exports.json:",
            )
            assert f"SCAN_MANIFEST = scan/scene-refs/{scope}.manifest.json" in block, (
                scope
            )


class TestEdgeArgsInNinja:
    """The args file's path is static; only its content waits for collate."""

    ARGS = EdgeArgsSpec(suffix=".refs", var="SCENE_REFS", token="$SCENE_REFS")

    def test_governed_statement_binds_the_args_var(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS))

        block = statement(content, "build packs/b.pack:")

        assert "SCENE_REFS = packs/b.pack.refs" in block

    def test_governed_rule_references_the_args_var(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS))

        block = statement(content, "build packs/b.pack:")
        rule_name = block[0].split(": ", 1)[1].split()[0]

        assert any("$SCENE_REFS" in line for line in rule_block(content, rule_name))

    def test_args_file_is_an_implicit_collate_output(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS))

        line = statement(
            content,
            "build scan/scene-refs/t.pack_b.dyndep "
            "| scan/scene-refs/t.pack_b.exports.json packs/b.pack.refs:",
        )[0]

        assert "packs/b.pack.refs:" in line

    def test_no_args_file_without_edge_args(self, tmp_path, monkeypatch):
        content = build_ninja(tmp_path, monkeypatch)

        assert ".refs" not in content


class TestAGovernedEdgeStillRebuildsOnWhatItDependsOn:
    """A dyndep is written before the edge runs, from the declared sources,
    so it cannot stand in for a depfile: a file the command depends() on
    stays an implicit dep, and editing it re-runs the command."""

    def test_a_file_dependency_of_a_scanned_command_is_implicit(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "scan.py").write_text("# scan tool stub\n")
        (tmp_path / "tools" / "pack.py").write_text("# packer stub\n")
        (tmp_path / "a.scene").write_text("contents\n")
        monkeypatch.chdir(tmp_path)

        project = Project("t", root_dir=tmp_path, build_dir="build")
        env = project.Environment()
        a = env.Command(
            target="packs/a.pack",
            source=["a.scene"],
            command=[sys.executable, "$SRCDIR/tools/pack.py", "$SOURCE", "$TARGET"],
            depends=["tools/pack.py"],
            name="pack_a",
        )
        make_scanner().attach(a)
        project.resolve()

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        content = normalize_path((tmp_path / "build" / "build.ninja").read_text())
        edge = statement(content, "build packs/a.pack:")[0]

        implicit, _, order_only = edge.partition("||")
        assert "$topdir/tools/pack.py" in implicit
        assert "$topdir/tools/pack.py" not in order_only
        assert ".dyndep" in order_only

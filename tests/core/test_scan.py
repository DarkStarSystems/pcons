# SPDX-License-Identifier: MIT
"""Tests for the Scanner (discovered dependencies) core primitive.

The primitive is exercised through plain ``env.Command`` edges: a scanner is
tool-agnostic, so no compiler is needed to check that the resolver wires the
scan edges, the per-target collate edge, and the governed edges' dyndep.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from pcons import ArgsFormat, EdgeArgsSpec, Project, Scanner
from pcons.core.errors import PconsError
from pcons.core.node import FileNode
from pcons.core.subst import NodeVar, PathToken
from pcons.core.target import Target

SCAN_SOURCES = ("a.scene", "b.scene", "c.scene", "notes.txt")


def make_tree(tmp_path: Path) -> None:
    """Write the source files the reference project names.

    ``env.Command`` fails fast on a source that does not exist, so every
    path a test mentions has to be on disk before the project is built.
    """
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "scan.py").write_text("# scan tool stub\n")
    for name in SCAN_SOURCES:
        (tmp_path / name).write_text(f"contents of {name}\n")


def make_scanner(name: str = "scene-refs", **kwargs: Any) -> Scanner:
    """A scanner over ``.scene`` sources, with test-friendly defaults."""
    kwargs.setdefault("source_suffixes", [".scene"])
    kwargs.setdefault(
        "scan_command",
        [sys.executable, "$SRCDIR/tools/scan.py", "$SOURCE", "$TARGET"],
    )
    kwargs.setdefault("scan_deps", ["tools/scan.py"])
    kwargs.setdefault("provide_template", "packs/{name}.pack")
    kwargs.setdefault("on_unresolved", "error")
    return Scanner(name, **kwargs)


def make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """A project rooted at *tmp_path*, with the process cwd there too.

    The manifest is written at configure time relative to ``build_dir``,
    which is itself relative to the project root, so the cwd has to be the
    project root — as it is whenever ``pcons`` runs a build script.
    """
    make_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    return Project("t", root_dir=tmp_path, build_dir="build")


def pack(env: Any, letter: str, sources: list[str] | None = None) -> Target:
    """One governed edge: ``packs/<letter>.pack`` from ``<letter>.scene``."""
    return env.Command(
        target=f"packs/{letter}.pack",
        source=sources if sources is not None else [f"{letter}.scene"],
        command=["cp", "${SOURCES[0]}", "$TARGET"],
        name=f"pack_{letter}",
    )


def reference_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scanner: Scanner | None = None,
) -> tuple[Project, Scanner, Target, Target]:
    """Two packs, ``b`` depending on ``a``, both scanned; resolved."""
    project = make_project(tmp_path, monkeypatch)
    env = project.Environment()
    scanner = scanner if scanner is not None else make_scanner()
    a = pack(env, "a")
    b = pack(env, "b")
    b.add_dependency(a)
    scanner.attach(a, b)
    project.resolve()
    return project, scanner, a, b


def read_manifest(tmp_path: Path, scope: str, scanner: str = "scene-refs") -> Any:
    """The configure-written manifest for one scope."""
    path = tmp_path / "build" / "scan" / scanner / f"{scope}.manifest.json"
    return json.loads(path.read_text())


def node_paths(nodes: list[Any]) -> list[str]:
    return [str(n.path).replace("\\", "/") for n in nodes]


class TestScannerValidation:
    """Scanner rejects malformed declarations up front."""

    def test_rejects_uppercase_name(self):
        with pytest.raises(PconsError, match="must be lowercase"):
            make_scanner("SceneRefs")

    def test_rejects_leading_hyphen_in_name(self):
        with pytest.raises(PconsError, match="must be lowercase"):
            make_scanner("-scene-refs")

    def test_rejects_source_suffix_without_dot(self):
        with pytest.raises(PconsError, match="must start with"):
            make_scanner(source_suffixes=["scene"])

    def test_rejects_empty_source_suffixes(self):
        with pytest.raises(PconsError, match="needs source_suffixes"):
            make_scanner(source_suffixes=[])

    def test_rejects_empty_scan_command(self):
        with pytest.raises(PconsError, match="needs a scan_command"):
            make_scanner(scan_command=[])

    def test_rejects_info_suffix_without_dot(self):
        with pytest.raises(PconsError, match="info_suffix"):
            make_scanner(info_suffix="scaninfo")

    def test_rejects_scan_depfile_without_dot(self):
        with pytest.raises(PconsError, match="scan_depfile"):
            make_scanner(scan_depfile="d")

    def test_rejects_scan_depfile_equal_to_info_suffix(self):
        with pytest.raises(PconsError, match="must differ"):
            make_scanner(info_suffix=".info", scan_depfile=".info")

    def test_rejects_unknown_on_unresolved(self):
        with pytest.raises(PconsError, match="on_unresolved"):
            make_scanner(on_unresolved="explode")

    def test_sequences_become_tuples(self):
        """Lists in, tuples out, so the frozen scanner stays hashable."""
        scanner = make_scanner(
            source_suffixes=[".scene", ".prefab"],
            scan_command=["python", "scan.py"],
            scan_deps=["tools/scan.py"],
        )
        assert scanner.source_suffixes == (".scene", ".prefab")
        assert scanner.scan_command == ("python", "scan.py")
        assert scanner.scan_deps == ("tools/scan.py",)

    def test_collate_command_becomes_tuple(self):
        scanner = make_scanner(collate_command=["my-collate", "--manifest"])
        assert scanner.collate_command == ("my-collate", "--manifest")

    def test_collate_command_stays_none(self):
        assert make_scanner().collate_command is None

    def test_attach_twice_is_a_noop(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        scanner = make_scanner()
        a = pack(env, "a")

        scanner.attach(a)
        scanner.attach(a)
        scanner.attach(a, a)

        assert a._scanners == [scanner]


class TestScanEdge:
    """One scan edge per governed build edge."""

    def test_scan_node_is_named_after_the_governed_output(self, tmp_path, monkeypatch):
        project, _, a, _ = reference_project(tmp_path, monkeypatch)

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))

        assert scan_node._build_info is not None
        assert scan_node.path == Path("build/packs/a.pack.scaninfo.json")
        assert a.output_nodes[0].path == Path("build/packs/a.pack")

    def test_scan_build_info(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)
        env = project.targets[0]._env

        info = project.node(Path("build/packs/a.pack.scaninfo.json"))._build_info

        assert info["tool"] == "scan_scene_refs"
        assert info["command_var"] == "scancmd"
        assert info["restat"] is True
        assert info["description"] == "SCAN[scene-refs] $out"
        assert info["env"] is env

    def test_scanned_sources_are_the_explicit_inputs(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))

        assert node_paths(scan_node.explicit_deps) == ["a.scene"]
        assert node_paths(scan_node._build_info["sources"]) == ["a.scene"]

    def test_scan_deps_become_implicit_deps(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))

        assert "tools/scan.py" in node_paths(scan_node.implicit_deps)

    def test_scan_depfile_becomes_a_path_token(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(
            tmp_path, monkeypatch, make_scanner(scan_depfile=".d")
        )

        info = project.node(Path("build/packs/a.pack.scaninfo.json"))._build_info

        assert isinstance(info["depfile"], PathToken)
        assert info["depfile"].suffix == ".d"
        assert info["deps_style"] == "gcc"

    def test_no_scan_depfile_leaves_no_depfile(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        info = project.node(Path("build/packs/a.pack.scaninfo.json"))._build_info

        assert info.get("depfile") is None
        assert info.get("deps_style") is None

    def test_scan_vars_are_recorded(self, tmp_path, monkeypatch):
        """A per-edge var keeps one rule serving edges with differing values."""

        def scan_vars(env, scanned, governed):
            return {"PACK_NAME": governed.path.name}

        project, _, _, _ = reference_project(
            tmp_path, monkeypatch, make_scanner(scan_vars=scan_vars)
        )

        info = project.node(Path("build/packs/a.pack.scaninfo.json"))._build_info

        assert info["vars"] == {"PACK_NAME": "a.pack"}

    def test_one_scan_node_for_a_multi_source_edge(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        target = pack(env, "a", sources=["a.scene", "b.scene"])
        make_scanner().attach(target)
        project.resolve()

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))

        assert node_paths(scan_node.explicit_deps) == ["a.scene", "b.scene"]
        assert not (project.build_dir / "packs/b.pack.scaninfo.json").exists()

    def test_non_matching_sources_are_not_scanned(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        target = pack(env, "a", sources=["notes.txt", "a.scene"])
        make_scanner().attach(target)
        project.resolve()

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))

        assert node_paths(scan_node.explicit_deps) == ["a.scene"]
        assert node_paths(scan_node._build_info["sources"]) == ["a.scene"]

    def test_scan_inherits_the_governed_edges_ordering_deps(
        self, tmp_path, monkeypatch
    ):
        """A scan reads what the governed command reads, so a generated
        header the compile waits for must exist before the scan too --
        review finding: the scan raced the generator on clean builds."""
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        (tmp_path / "gen.py").write_text("# generator stub\n")
        gen = env.Command(
            target="gen.h",
            source="gen.py",
            command="python $SOURCE $TARGET",
            name="gen_h",
        )
        a = pack(env, "a")
        a.depends(gen)
        make_scanner().attach(a)
        project.resolve()

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))
        # This scanner declares no dep tracking, so the inherited dep is
        # implicit: a regenerated header must dirty the scan itself. With a
        # scan depfile it would be order-only instead (see the next test).
        assert "build/gen.h" in node_paths(scan_node.implicit_deps)

    def test_inherited_deps_are_order_only_with_a_scan_depfile(
        self, tmp_path, monkeypatch
    ):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        (tmp_path / "gen.py").write_text("# generator stub\n")
        gen = env.Command(
            target="gen.h",
            source="gen.py",
            command="python $SOURCE $TARGET",
            name="gen_h",
        )
        a = pack(env, "a")
        a.depends(gen)
        make_scanner(scan_depfile=".d").attach(a)
        project.resolve()

        scan_node = project.node(Path("build/packs/a.pack.scaninfo.json"))
        assert "build/gen.h" in node_paths(scan_node.order_only_deps)
        assert "build/gen.h" not in node_paths(scan_node.implicit_deps)


class TestCollateEdge:
    """One collate edge per scanned target."""

    def test_collate_node_path_and_inputs(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        collate = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))

        assert node_paths(collate.explicit_deps) == [
            "build/packs/a.pack.scaninfo.json",
            "build/scan/scene-refs/t.pack_a.manifest.json",
        ]

    def test_collate_imports_a_dependency_scope_exports(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        collate = project.node(Path("build/scan/scene-refs/t.pack_b.dyndep"))

        assert node_paths(collate.explicit_deps) == [
            "build/packs/b.pack.scaninfo.json",
            "build/scan/scene-refs/t.pack_b.manifest.json",
            "build/scan/scene-refs/t.pack_a.exports.json",
        ]

    def test_collate_build_info(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        info = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))._build_info

        assert info["tool"] == "collate_scene_refs"
        assert info["command_var"] == "collatecmd"
        assert info["restat"] is True
        assert info["description"] == "COLLATE[scene-refs] $out"

    def test_collate_outputs(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        outputs = project.node(
            Path("build/scan/scene-refs/t.pack_a.dyndep")
        )._build_info["outputs"]

        assert outputs["dyndep"] == {
            "path": Path("build/scan/scene-refs/t.pack_a.dyndep"),
            "implicit": False,
        }
        assert outputs["exports"] == {
            "path": Path("build/scan/scene-refs/t.pack_a.exports.json"),
            "implicit": True,
        }

    def test_collate_command_carries_the_manifest_in_a_var(self, tmp_path, monkeypatch):
        """The manifest path rides a per-edge var so all scopes share one rule."""
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        info = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))._build_info

        assert info["command"][:4] == [
            sys.executable,
            "-m",
            "pcons.core.collate",
            "--manifest",
        ]
        manifest_token = info["command"][4]
        assert isinstance(manifest_token, NodeVar)
        assert manifest_token.name == "SCAN_MANIFEST"
        assert info["vars"]["SCAN_MANIFEST"] == "scan/scene-refs/t.pack_a.manifest.json"

    def test_custom_collate_command_is_used_verbatim(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(
            tmp_path, monkeypatch, make_scanner(collate_command=["my-collate", "-q"])
        )

        info = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))._build_info

        assert info["command"] == ["my-collate", "-q"]
        # A custom command may reference NodeVar("SCAN_MANIFEST"); the
        # variable is supplied either way.
        assert info["vars"]["SCAN_MANIFEST"] == "scan/scene-refs/t.pack_a.manifest.json"

    def test_exports_node_is_a_secondary_output_of_collate(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        collate = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))
        exports = project.node(Path("build/scan/scene-refs/t.pack_a.exports.json"))

        assert exports._build_info == {"primary_node": collate}

    def test_scan_scope_is_recorded_on_the_project(self, tmp_path, monkeypatch):
        project, scanner, a, _ = reference_project(tmp_path, monkeypatch)

        scope = project._scan_scopes[("scene-refs", "t::pack_a")]

        assert scope.scanner is scanner
        assert scope.target is a
        assert scope.manifest_rel == "scan/scene-refs/t.pack_a.manifest.json"
        assert scope.dyndep_rel == "scan/scene-refs/t.pack_a.dyndep"
        assert scope.exports_rel == "scan/scene-refs/t.pack_a.exports.json"
        assert scope.collate_node is project.node(
            Path("build/scan/scene-refs/t.pack_a.dyndep")
        )
        assert scope.exports_node is project.node(
            Path("build/scan/scene-refs/t.pack_a.exports.json")
        )
        assert scope.governed == [a.output_nodes[0]]

    def test_both_targets_get_their_own_scope(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(tmp_path, monkeypatch)

        assert set(project._scan_scopes) == {
            ("scene-refs", "t::pack_a"),
            ("scene-refs", "t::pack_b"),
        }


class TestGovernedEdge:
    """Each governed edge is stamped with its target's dyndep file."""

    def test_dyndep_is_stamped_on_the_edge(self, tmp_path, monkeypatch):
        _, _, a, b = reference_project(tmp_path, monkeypatch)

        assert (
            a.output_nodes[0]._build_info["dyndep"] == "scan/scene-refs/t.pack_a.dyndep"
        )
        assert (
            b.output_nodes[0]._build_info["dyndep"] == "scan/scene-refs/t.pack_b.dyndep"
        )

    def test_collate_node_is_an_order_only_dep(self, tmp_path, monkeypatch):
        """Order-only: a rewritten dyndep must not by itself dirty every
        governed edge — the loaded dyndep's real deps carry propagation."""
        project, _, a, _ = reference_project(tmp_path, monkeypatch)

        collate = project.node(Path("build/scan/scene-refs/t.pack_a.dyndep"))

        assert collate in a.output_nodes[0].order_only_deps
        assert collate not in a.output_nodes[0].implicit_deps

    def test_unscanned_edge_keeps_no_dyndep(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        scanned = pack(env, "a")
        plain = env.Command(
            target="notes.copy",
            source=["notes.txt"],
            command=["cp", "$SOURCE", "$TARGET"],
            name="copy_notes",
        )
        make_scanner().attach(scanned)
        project.resolve()

        assert plain.output_nodes[0]._build_info.get("dyndep") is None


class TestManifest:
    """The configure-written manifest holds only static facts."""

    def test_manifest_header_fields(self, tmp_path, monkeypatch):
        reference_project(tmp_path, monkeypatch)

        manifest = read_manifest(tmp_path, "t.pack_a")

        assert manifest["version"] == 1
        assert manifest["scanner"] == "scene-refs"
        assert manifest["scope"] == "t.pack_a"
        assert manifest["dyndep"] == "scan/scene-refs/t.pack_a.dyndep"
        assert manifest["exports_out"] == "scan/scene-refs/t.pack_a.exports.json"
        assert manifest["provide_template"] == "packs/{name}.pack"
        assert manifest["on_unresolved"] == "error"
        assert manifest["edge_args"] is None

    def test_manifest_edge(self, tmp_path, monkeypatch):
        reference_project(tmp_path, monkeypatch)

        (edge,) = read_manifest(tmp_path, "t.pack_a")["edges"]

        assert edge["out"] == "packs/a.pack"
        assert edge["info"] == "packs/a.pack.scaninfo.json"
        assert edge["declared_outputs"] == ["packs/a.pack"]
        assert edge["provide_template"] is None
        assert "args_file" not in edge

    def test_imports_follow_the_target_dag(self, tmp_path, monkeypatch):
        reference_project(tmp_path, monkeypatch)

        assert read_manifest(tmp_path, "t.pack_a")["imports"] == []
        assert read_manifest(tmp_path, "t.pack_b")["imports"] == [
            "scan/scene-refs/t.pack_a.exports.json"
        ]

    def test_imports_are_transitive(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        scanner = make_scanner()
        a, b, c = pack(env, "a"), pack(env, "b"), pack(env, "c")
        b.add_dependency(a)
        c.add_dependency(b)
        scanner.attach(a, b, c)
        project.resolve()

        imports = read_manifest(tmp_path, "t.pack_c")["imports"]

        assert set(imports) == {
            "scan/scene-refs/t.pack_a.exports.json",
            "scan/scene-refs/t.pack_b.exports.json",
        }

    def test_unattached_dependency_contributes_no_import(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        a, b = pack(env, "a"), pack(env, "b")
        b.add_dependency(a)
        make_scanner().attach(b)
        project.resolve()

        assert read_manifest(tmp_path, "t.pack_b")["imports"] == []

    def test_manifest_is_written_with_a_digest_sidecar(self, tmp_path, monkeypatch):
        """The sidecar is what makes a re-configure a no-op for collate."""
        reference_project(tmp_path, monkeypatch)

        base = tmp_path / "build/scan/scene-refs"

        assert (base / "t.pack_a.manifest.json").exists()
        assert (base / "t.pack_a.manifest.json.sha256").exists()


class TestEdgeArgs:
    """A collate-written args file reaches the command line through a var."""

    ARGS = EdgeArgsSpec(suffix=".refs", var="SCENE_REFS", token="$SCENE_REFS")

    def test_governed_edge_gets_the_var_and_token(self, tmp_path, monkeypatch):
        _, _, a, _ = reference_project(
            tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS)
        )

        info = a.output_nodes[0]._build_info

        assert info["vars"]["SCENE_REFS"] == "packs/a.pack.refs"
        assert "$SCENE_REFS" in info["extra_command_flags"]

    def test_args_file_is_an_implicit_collate_output(self, tmp_path, monkeypatch):
        project, _, _, _ = reference_project(
            tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS)
        )

        outputs = project.node(
            Path("build/scan/scene-refs/t.pack_a.dyndep")
        )._build_info["outputs"]

        assert outputs["args_0"] == {
            "path": Path("build/packs/a.pack.refs"),
            "implicit": True,
        }

    def test_manifest_edge_names_the_args_file(self, tmp_path, monkeypatch):
        reference_project(tmp_path, monkeypatch, make_scanner(edge_args=self.ARGS))

        (edge,) = read_manifest(tmp_path, "t.pack_a")["edges"]

        assert edge["args_file"] == "packs/a.pack.refs"

    def test_manifest_records_the_args_format(self, tmp_path, monkeypatch):
        spec = EdgeArgsSpec(
            suffix=".modmap",
            var="SCAN_ARGS",
            token="@$SCAN_ARGS",
            format=ArgsFormat(header=("$root .",), line="{name} {path}"),
            include="requires",
        )
        reference_project(tmp_path, monkeypatch, make_scanner(edge_args=spec))

        edge_args = read_manifest(tmp_path, "t.pack_a")["edge_args"]

        assert edge_args == {
            "suffix": ".modmap",
            "var": "SCAN_ARGS",
            "format": {"header": ["$root ."], "line": "{name} {path}"},
            "include": "requires",
        }


class TestSharedEdgeOwnership:
    """One build node in two targets (the resolver's object cache does this
    for identical compiles): the first scope owns the edge, the second
    imports the owner's exports instead of governing it again."""

    def _two_targets_sharing_a_node(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from pcons.core.scan import ScannerResolver

        project = make_project(tmp_path, monkeypatch)
        env = SimpleNamespace(register_node=lambda _n: None)

        shared_src = FileNode(tmp_path / "shared.scene")
        shared = FileNode("build/obj.shared/shared.pack")
        shared._build_info = {"env": env, "sources": [shared_src]}

        def make_target(name, extra_src):
            from pcons.core.target import Target

            src = FileNode(tmp_path / extra_src)
            own = FileNode(f"build/obj.{name}/{extra_src}.pack")
            own._build_info = {"env": env, "sources": [src]}
            target = Target(name, target_type="program", project=project)
            target._env = env  # type: ignore[assignment]
            target.intermediate_nodes.extend([shared, own])
            project._targets.append(target)
            return target

        a = make_target("a", "a.scene")
        b = make_target("b", "b.scene")
        scanner = make_scanner()
        scanner.attach(a, b)
        ScannerResolver(project).run(project.targets)
        return project, a, b, shared

    def test_first_scope_owns_the_shared_edge(self, tmp_path, monkeypatch):
        project, a, b, shared = self._two_targets_sharing_a_node(tmp_path, monkeypatch)

        scope_a = project._scan_scopes[("scene-refs", "t::a")]
        scope_b = project._scan_scopes[("scene-refs", "t::b")]
        assert shared in scope_a.governed
        assert shared not in scope_b.governed
        # One dyndep file per edge: the shared node keeps the owner's.
        assert shared._build_info["dyndep"] == scope_a.dyndep_rel

    def test_second_scope_imports_the_owner(self, tmp_path, monkeypatch):
        import json

        project, a, b, shared = self._two_targets_sharing_a_node(tmp_path, monkeypatch)

        manifest = json.loads(
            (tmp_path / "build/scan/scene-refs/t.b.manifest.json").read_text()
        )
        assert "scan/scene-refs/t.a.exports.json" in manifest["imports"]

    def test_a_target_with_only_shared_edges_becomes_a_pass_through(
        self, tmp_path, monkeypatch
    ):
        """The scope must still exist — a dependent that declares only this
        target reaches the owner's exports through it (review finding: a
        second library listing a shared interface broke its consumers)."""
        from types import SimpleNamespace

        from pcons.core.scan import ScannerResolver
        from pcons.core.target import Target

        project = make_project(tmp_path, monkeypatch)
        env = SimpleNamespace(register_node=lambda _n: None)
        shared_src = FileNode(tmp_path / "shared.scene")
        shared = FileNode("build/obj.shared/shared.pack")
        shared._build_info = {"env": env, "sources": [shared_src]}
        targets = []
        for name in ("a", "b"):
            t = Target(name, target_type="program", project=project)
            t._env = env  # type: ignore[assignment]
            t.intermediate_nodes.append(shared)
            project._targets.append(t)
            targets.append(t)
        scanner = make_scanner()
        scanner.attach(*targets)

        ScannerResolver(project).run(project.targets)

        owner = project._scan_scopes[("scene-refs", "t::a")]
        passthrough = project._scan_scopes[("scene-refs", "t::b")]
        assert passthrough.governed == []
        assert passthrough.exports_node is None
        assert passthrough.forwards == [owner]

    def test_a_dependent_of_a_pass_through_imports_the_owner(
        self, tmp_path, monkeypatch
    ):
        """consumer -> two -> (interface shared with one): consumer's
        manifest imports one's exports though it never declares one — the
        library it links physically contains one's artifact."""
        import json
        from types import SimpleNamespace

        from pcons.core.scan import ScannerResolver
        from pcons.core.target import Target

        project = make_project(tmp_path, monkeypatch)
        env = SimpleNamespace(register_node=lambda _n: None)
        shared_src = FileNode(tmp_path / "shared.scene")
        shared = FileNode("build/obj.shared/shared.pack")
        shared._build_info = {"env": env, "sources": [shared_src]}

        def make_target(name, nodes):
            t = Target(name, target_type="static_library", project=project)
            t._env = env  # type: ignore[assignment]
            t.intermediate_nodes.extend(nodes)
            project._targets.append(t)
            return t

        one = make_target("one", [shared])
        two = make_target("two", [shared])
        consumer_src = FileNode(tmp_path / "c.scene")
        consumer_edge = FileNode("build/obj.consumer/c.pack")
        consumer_edge._build_info = {"env": env, "sources": [consumer_src]}
        consumer = make_target("consumer", [consumer_edge])
        consumer.add_dependency(two)

        scanner = make_scanner()
        scanner.attach(one, two, consumer)
        ScannerResolver(project).run(project.targets)

        manifest = json.loads(
            (tmp_path / "build/scan/scene-refs/t.consumer.manifest.json").read_text()
        )
        assert manifest["imports"] == ["scan/scene-refs/t.one.exports.json"]


class TestScannerErrors:
    """Misuse is caught at configure time, with an actionable message."""

    def test_attach_without_matching_sources(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        target = env.Command(
            target="notes.copy",
            source=["notes.txt"],
            command=["cp", "$SOURCE", "$TARGET"],
            name="copy_notes",
        )
        make_scanner().attach(target)

        with pytest.raises(PconsError, match="no build edge"):
            project.resolve()

    def test_two_different_scanners_share_a_name(self, tmp_path, monkeypatch):
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        target = pack(env, "a")
        make_scanner(on_unresolved="ignore").attach(target)
        make_scanner(on_unresolved="error").attach(target)

        with pytest.raises(PconsError, match="Two conflicting scanners"):
            project.resolve()

    def test_two_scanners_claim_one_scan_output(self, tmp_path, monkeypatch):
        """Distinct names, but the same default info_suffix on one edge."""
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        target = pack(env, "a")
        make_scanner("refs-one").attach(target)
        make_scanner("refs-two").attach(target)

        with pytest.raises(PconsError, match="already has a producer"):
            project.resolve()

    def test_target_objects_as_sources_are_rejected(self, tmp_path, monkeypatch):
        """Target sources resolve after the wiring pass, so they cannot be scanned."""
        project = make_project(tmp_path, monkeypatch)
        env = project.Environment()
        a = pack(env, "a")
        b = env.Command(
            target="packs/b.pack",
            source=[a, "b.scene"],
            command=["cat", "$SOURCES", ">", "$TARGET"],
            name="pack_b",
        )
        make_scanner().attach(b)

        with pytest.raises(PconsError, match="resolve too late"):
            project.resolve()


def test_scan_and_collate_nodes_are_registered_with_the_environment(
    tmp_path, monkeypatch
):
    """Generators walk the environment's nodes, so the new ones must be there."""
    project, _, _, _ = reference_project(tmp_path, monkeypatch)
    env = project.targets[0]._env

    registered = {
        str(n.path).replace("\\", "/")
        for n in env.created_nodes
        if isinstance(n, FileNode)
    }

    assert "build/packs/a.pack.scaninfo.json" in registered
    assert "build/scan/scene-refs/t.pack_a.dyndep" in registered
    assert "build/scan/scene-refs/t.pack_a.exports.json" in registered

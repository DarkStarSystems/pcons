# SPDX-License-Identifier: MIT
"""Two targets that moc the same header and link together (no Qt needed).

The moc set is decided while the build runs, so the report is a build edge:
each automoc edge exports the headers it moc'ed with the include chain that
reached them, and one edge per link closure reads those back and warns.

Nothing here asserts a value the build script passed in. The edge is read out
of the emitted ``build.ninja``, and the message out of the report tool run on
exports files the real scanner produced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.toolchains.qt._automoc import _write_exports
from pcons.toolchains.qt._moc_report import EXPORTS_VERSION
from pcons.toolchains.qt._moc_report import main as moc_report
from pcons.toolchains.qt.scan import QtScanner

from ._qt_test_utils import cxx_env_with_qt, generate_ninja


def _header(name: str, includes: str = "") -> str:
    return (
        f"#pragma once\n#include <QObject>\n{includes}"
        f"class {name} : public QObject {{ Q_OBJECT }};\n"
    )


def _report_edges(ninja: str) -> list[str]:
    """Every ``build`` line driven by the duplicate-moc report rule."""
    rules = {
        line.split()[1]
        for line in ninja.splitlines()
        if line.startswith("rule qt_mocreportcmd")
    }
    return [
        line
        for line in ninja.splitlines()
        if line.startswith("build ") and any(rule in line for rule in rules)
    ]


def _exports(
    root: Path,
    name: str,
    sources: list[str],
    no_moc: list[str] | None = None,
    include_dirs: list[Path] | None = None,
) -> Path:
    """Run the real scan for one target and write its exports file."""
    gen_dir = root / "build" / f"qt.{name}"
    scanner = QtScanner(root, cache_dir=gen_dir)
    scan = scanner.scan_target_sources(
        [root / source for source in sources],
        include_dirs=include_dirs or [],
        no_moc=[root / path for path in no_moc or []],
    )
    path = gen_dir / "automoc.exports.json"
    _write_exports(path, name, root, scan)
    return path


def _warnings(tmp_path: Path, capsys, *exports: Path) -> str:
    stamp = tmp_path / "build" / "report.stamp"
    assert moc_report(["--stamp", str(stamp), *(str(p) for p in exports)]) == 0
    assert stamp.is_file()
    return capsys.readouterr().err


@pytest.fixture
def shared_dir_tree(tmp_path, monkeypatch):
    """A program and a QML module whose sources share one directory.

    ``main.cpp`` includes ``Controller.hpp`` by quoted name with no include
    directory configured, so it resolves through the same-directory fallback,
    and the walk goes on into ``src/sub``, a second directory the program
    names nowhere.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    sub = src / "sub"
    sub.mkdir(parents=True)
    (sub / "Helper.hpp").write_text(_header("Helper"))
    (src / "Controller.hpp").write_text(
        _header("Controller", '#include "sub/Helper.hpp"\n')
    )
    (src / "Controller.cpp").write_text('#include "Controller.hpp"\n')
    (src / "main.cpp").write_text(
        '#include "Controller.hpp"\nint main() { return 0; }\n'
    )
    return tmp_path


class TestTheReportEdge:
    """What the build files say, which is where the report now lives."""

    def test_the_link_closure_gets_one_edge_over_both_exports(self, shared_dir_tree):
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        ninja = generate_ninja(project)

        edges = _report_edges(ninja)
        assert len(edges) == 1
        assert "qt.app/automoc.exports.json" in edges[0]
        assert "qt.mod/automoc.exports.json" in edges[0]

    def test_a_shared_librarys_private_link_stays_behind_its_own_link(
        self, shared_dir_tree
    ):
        """What the linker pulls in is what can collide.

        A shared library resolves its private dependencies itself, so the
        program that links it never sees their meta-object symbols.
        """
        (shared_dir_tree / "src" / "Widget.cpp").write_text(
            '#include "Controller.hpp"\n'
        )
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        shared = project.QtSharedLibrary("shared", env, sources=["src/Widget.cpp"])
        shared.private.link_libs.append(module)
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(shared)

        ninja = generate_ninja(project)

        edges = _report_edges(ninja)
        assert len(edges) == 1
        assert "qt.app/automoc.exports.json" in edges[0]
        assert "qt.shared/automoc.exports.json" in edges[0]
        assert "qt.mod/automoc.exports.json" not in edges[0]

    def test_the_root_link_waits_for_the_report(self, shared_dir_tree):
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        ninja = generate_ninja(project)

        stamp = _report_edges(ninja)[0].split(":")[0].removeprefix("build ").strip()
        link = next(
            line for line in ninja.splitlines() if line.startswith("build app: ")
        )
        assert f"|| {stamp}" in link

    def test_the_automoc_edge_declares_its_exports_file(self, shared_dir_tree):
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"])

        ninja = generate_ninja(project)

        edge = next(
            line
            for line in ninja.splitlines()
            if line.startswith("build qt.app/mocs_compilation.cpp")
        )
        assert "| qt.app/automoc.exports.json:" in edge

    def test_the_report_paths_carry_the_build_prefix(self, shared_dir_tree):
        """Every generated path keeps the environment's build prefix."""
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        env.build_prefix = "host"
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        ninja = generate_ninja(project)

        edge = _report_edges(ninja)[0]
        assert edge.startswith("build host/qt.mocreport.")
        assert "host/qt.app/automoc.exports.json" in edge
        assert "host/qt.mod/automoc.exports.json" in edge
        spec = json.loads(
            (shared_dir_tree / "build" / "host" / "qt.app" / "automoc.json").read_text()
        )
        assert spec["exports"] == str(
            shared_dir_tree / "build" / "host" / "qt.app" / "automoc.exports.json"
        )

    def test_two_targets_that_never_link_get_no_edge(self, tmp_path, monkeypatch):
        """The same header moc'ed by two programs is two separate links."""
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "Shared.hpp").write_text(_header("Shared"))
        (src / "one.cpp").write_text(
            '#include "Shared.hpp"\nint main() { return 0; }\n'
        )
        (src / "two.cpp").write_text(
            '#include "Shared.hpp"\nint main() { return 0; }\n'
        )

        project = Project("two", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = cxx_env_with_qt(project)
        project.QtProgram("one", env, sources=["src/one.cpp"])
        project.QtProgram("two", env, sources=["src/two.cpp"])

        assert _report_edges(generate_ninja(project)) == []


class TestTheExportsFile:
    def test_it_names_each_header_with_the_chain_that_reached_it(self, shared_dir_tree):
        path = _exports(shared_dir_tree, "app", ["src/main.cpp"])

        document = json.loads(path.read_text())

        assert document["target"] == "app"
        assert document["headers"] == {
            str(Path("src/Controller.hpp")): [
                str(Path("src/main.cpp")),
                str(Path("src/Controller.hpp")),
            ],
            str(Path("src/sub/Helper.hpp")): [
                str(Path("src/main.cpp")),
                str(Path("src/Controller.hpp")),
                str(Path("src/sub/Helper.hpp")),
            ],
        }

    def test_a_header_outside_the_project_root_keeps_its_absolute_path(self, tmp_path):
        root = tmp_path / "proj"
        vendor = tmp_path / "vendor"
        (root / "src").mkdir(parents=True)
        vendor.mkdir()
        (vendor / "Far.hpp").write_text(_header("Far"))
        (root / "src" / "main.cpp").write_text(
            '#include "Far.hpp"\nint main() { return 0; }\n'
        )

        path = _exports(root, "app", ["src/main.cpp"], include_dirs=[vendor])

        far = str((vendor / "Far.hpp").resolve())
        assert json.loads(path.read_text())["headers"] == {
            far: [str(Path("src/main.cpp")), far]
        }


class TestTheMessage:
    def test_the_shared_directory_split_is_reported(self, shared_dir_tree, capsys):
        text = _warnings(
            shared_dir_tree,
            capsys,
            _exports(shared_dir_tree, "app", ["src/main.cpp"]),
            _exports(shared_dir_tree, "mod", ["src/Controller.cpp"]),
        )

        assert "Controller.hpp" in text
        assert "'app'" in text and "'mod'" in text

    def test_the_include_chain_is_printed_source_first(self, shared_dir_tree, capsys):
        text = _warnings(
            shared_dir_tree,
            capsys,
            _exports(shared_dir_tree, "app", ["src/main.cpp"]),
            _exports(shared_dir_tree, "mod", ["src/Controller.cpp"]),
        )

        main_cpp = Path("src/main.cpp")
        controller_cpp = Path("src/Controller.cpp")
        controller_hpp = Path("src/Controller.hpp")
        helper_hpp = Path("src/sub/Helper.hpp")
        assert f"  'app' reaches it through {main_cpp} -> {controller_hpp}\n" in text
        assert (
            f"  'mod' reaches it through {controller_cpp} -> {controller_hpp}\n" in text
        )
        assert (
            f"  'app' reaches it through {main_cpp} -> {controller_hpp} "
            f"-> {helper_hpp}\n" in text
        )

    def test_the_transitive_hop_is_reported(self, shared_dir_tree, capsys):
        text = _warnings(
            shared_dir_tree,
            capsys,
            _exports(shared_dir_tree, "app", ["src/main.cpp"]),
            _exports(shared_dir_tree, "mod", ["src/Controller.cpp"]),
        )

        assert "Helper.hpp" in text

    def test_no_moc_on_the_reached_header_does_not_stop_the_walk(
        self, shared_dir_tree, capsys
    ):
        """Excluding Controller.hpp leaves the deeper duplicate in place."""
        text = _warnings(
            shared_dir_tree,
            capsys,
            _exports(
                shared_dir_tree,
                "app",
                ["src/main.cpp"],
                no_moc=["src/Controller.hpp"],
            ),
            _exports(shared_dir_tree, "mod", ["src/Controller.cpp"]),
        )

        assert "Helper.hpp" in text

    def test_a_split_that_mocs_each_header_once_is_quiet(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "Controller.hpp").write_text(_header("Controller"))
        (src / "Controller.cpp").write_text('#include "Controller.hpp"\n')
        (src / "Window.hpp").write_text(_header("Window"))
        (src / "main.cpp").write_text(
            '#include "Window.hpp"\nint main() { return 0; }\n'
        )

        text = _warnings(
            tmp_path,
            capsys,
            _exports(tmp_path, "app", ["src/main.cpp"]),
            _exports(tmp_path, "mod", ["src/Controller.cpp"]),
        )

        assert text == ""

    def test_an_unreadable_exports_file_fails_the_edge(self, tmp_path, capsys):
        broken = tmp_path / "broken.json"
        broken.write_text("{")

        assert moc_report(["--stamp", str(tmp_path / "s.stamp"), str(broken)]) == 1
        assert "cannot read" in capsys.readouterr().err

    def test_an_exports_file_from_another_version_fails_the_edge(
        self, tmp_path, capsys
    ):
        stale = tmp_path / "stale.json"
        stale.write_text(
            json.dumps({"version": EXPORTS_VERSION + 1, "target": "app", "headers": {}})
        )
        stamp = tmp_path / "s.stamp"

        assert moc_report(["--stamp", str(stamp), str(stale)]) == 1
        assert "unrecognized automoc exports version" in capsys.readouterr().err
        assert not stamp.exists()

    def test_a_document_whose_headers_are_not_a_map_is_ignored(
        self, shared_dir_tree, capsys
    ):
        malformed = shared_dir_tree / "build" / "malformed.exports.json"
        malformed.parent.mkdir(parents=True, exist_ok=True)
        malformed.write_text(
            json.dumps(
                {
                    "version": EXPORTS_VERSION,
                    "target": "bad",
                    "headers": ["src/Controller.hpp"],
                }
            )
        )

        text = _warnings(
            shared_dir_tree,
            capsys,
            _exports(shared_dir_tree, "app", ["src/main.cpp"]),
            malformed,
            _exports(shared_dir_tree, "mod", ["src/Controller.cpp"]),
        )

        assert "Controller.hpp" in text
        assert "'app'" in text and "'mod'" in text
        assert "'bad'" not in text

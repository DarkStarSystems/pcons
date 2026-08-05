# SPDX-License-Identifier: MIT
"""Tests for the Qt code-generation builders (Moc/Uic/Rcc).

No Qt installation required: the qt toolchain is constructed with fake
tool paths and the generated build.ninja is inspected directly.
"""

from __future__ import annotations

import pytest

from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt, generate_ninja, qt_only_env


def _make_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    for name in ("thing.h", "widget.cpp", "form.ui"):
        (tmp_path / "src" / name).write_text("// test\n")
    (tmp_path / "res.qrc").write_text("<RCC/>\n")
    return Project("qtb", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestDefaultLayout:
    def test_output_paths(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        moc_cpp = env.qt.Moc(sources="src/thing.h")
        dot_moc = env.qt.Moc(sources="src/widget.cpp")
        ui_h = env.qt.Uic(sources="src/form.ui")
        qrc_cpp = env.qt.Rcc(sources="res.qrc")

        rel = lambda n: str(n.path).replace("\\", "/")  # noqa: E731
        assert rel(moc_cpp[0]).endswith("build/qt.gen/src/moc_thing.cpp")
        assert rel(dot_moc[0]).endswith("build/qt.gen/src/widget.moc")
        assert rel(ui_h[0]).endswith("build/qt.gen/src/ui_form.h")
        assert rel(qrc_cpp[0]).endswith("build/qt.gen/qrc_res.cpp")

    def test_explicit_target(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        nodes = env.qt.Uic("build/custom/my_ui.h", "src/form.ui")
        assert str(nodes[0].path).replace("\\", "/").endswith("build/custom/my_ui.h")

    def test_empty_sources_is_an_error(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        with pytest.raises(ValueError, match="sources"):
            env.qt.Moc(source="src/thing.h")  # classic slip: source=


class TestNinjaOutput:
    def test_moc_rule_has_depfile(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.Moc(sources="src/thing.h")
        content = generate_ninja(project)

        assert "/fake/bin/moc" in content
        assert "--output-dep-file" in content
        assert "--dep-file-path $out.d" in content
        assert "depfile = $out.d" in content
        assert "deps = gcc" in content
        assert "build qt.gen/src/moc_thing.cpp:" in content

    def test_moc_includes_defines_and_flags(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.mocincludes = ["/qt/include", "/qt/include/QtCore"]
        env.qt.mocdefines = ["QT_CORE_LIB", "MY_DEF=1"]
        env.qt.mocflags = ["--no-notes"]
        env.qt.Moc(sources="src/thing.h")
        content = generate_ninja(project)

        assert "-I/qt/include" in content
        assert "-I/qt/include/QtCore" in content
        assert "-DQT_CORE_LIB" in content
        assert "-DMY_DEF=1" in content
        assert "--no-notes" in content

    def test_moc_predefs_var(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.mocpredefs = ["--include", "qt.gen/moc_predefs.h"]
        env.qt.Moc(sources="src/thing.h")
        content = generate_ninja(project)
        assert "--include qt.gen/moc_predefs.h" in content

    def test_uic_rule_is_plain(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.Uic(sources="src/form.ui")
        content = generate_ninja(project)
        assert "/fake/bin/uic" in content
        assert "-o $out $in" in content
        assert "build qt.gen/src/ui_form.h:" in content

    def test_rcc_rule_name_and_depfile(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.Rcc(sources="res.qrc")
        content = generate_ninja(project)
        assert "/fake/bin/rcc" in content
        # The name is a per-edge variable, so one rule serves every .qrc.
        assert "--name $RCCNAME" in content
        assert "  RCCNAME = res\n" in content
        assert "--depfile $out.d" in content
        assert "build qt.gen/qrc_res.cpp:" in content

    def test_rcc_name_override(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        env.qt.Rcc(sources="res.qrc", name="assets")
        content = generate_ninja(project)
        assert "  RCCNAME = assets\n" in content


def _widgets_tree(tmp_path):
    src = tmp_path / "src"
    (src / "window.h").write_text(
        "#pragma once\n#include <QObject>\n"
        "class Window : public QObject { Q_OBJECT };\n"
    )
    (src / "window.cpp").write_text('#include "window.h"\n')
    (src / "main.cpp").write_text('#include "window.h"\nint main() { return 0; }\n')


class TestQtProgram:
    def test_full_wiring(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        import pcons.toolchains.qt  # noqa: F401  (registers QtProgram)

        project.QtProgram(
            "app",
            env,
            sources=["src/main.cpp", "src/window.cpp", "src/form.ui", "res.qrc"],
        )
        content = generate_ninja(project).replace("\\", "/")

        # automoc: window.h found via #include scan -> moc edge.
        assert "build qt.app/src/moc_window.cpp: qt_moccmd" in content
        # autouic / autorcc edges.
        assert "build qt.app/src/ui_form.h: qt_uiccmd" in content
        assert "build qt.app/qrc_res.cpp: qt_rcccmd" in content
        # Generated moc/qrc TUs compile into the target.
        assert "moc_window.cpp.o" in content
        assert "qrc_res.cpp.o" in content
        # Scan staleness guard with depfile.
        assert "build qt.app/scan.ok: qt_scancheckcmd" in content
        assert "scan-manifest.json" in content
        # Generated-header dir is on the compile include path.
        assert "qt.app" in content
        # ui_form.h is an implicit dep of compiles (waits before compiling).
        assert "ui_form.h" in content

    def test_scan_manifest_written(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"])
        manifest = tmp_path / "build" / "qt.app" / "scan-manifest.json"
        assert manifest.exists()
        import json

        data = json.loads(manifest.read_text())
        assert data["target"] == "app"
        assert any(p.endswith("window.h") for p in data["moc_headers"])

    def test_automoc_off(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"], automoc=False)
        content = generate_ninja(project)
        assert "moc_window" not in content
        assert "scan.ok" not in content

    def test_no_moc_exclusion(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"], no_moc=["src/window.h"])
        content = generate_ninja(project)
        assert "moc_window" not in content

    def test_missing_moc_include_is_hard_error(self, tmp_path, monkeypatch):
        from pcons.toolchains.qt import MocIncludeError

        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "src" / "bad.cpp").write_text(
            "#include <QObject>\nclass B : public QObject { Q_OBJECT };\n"
        )
        env = cxx_env_with_qt(project)
        with pytest.raises(MocIncludeError, match=r'#include "bad\.moc"'):
            project.QtProgram("app", env, sources=["src/bad.cpp"])

    def test_requires_qt_toolchain(self, tmp_path, monkeypatch):
        from pcons.toolchains import find_c_toolchain

        try:
            cxx_toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("no C++ toolchain available")
        project = _make_project(tmp_path, monkeypatch)
        env = project.Environment(toolchain=cxx_toolchain)
        with pytest.raises(RuntimeError, match="find_qt"):
            project.QtProgram("app", env, sources=["src/widget.cpp"])


class TestMocSeesTargetFlags:
    """moc must see the env's defines/includes, not only link= targets'."""

    def test_env_defines_and_includes_reach_moc(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        env.cxx.defines.append("MYFEATURE=1")
        (tmp_path / "inc").mkdir()
        env.cxx.includes.append("inc")
        project.QtProgram("app", env, sources=["src/main.cpp"])
        content = generate_ninja(project).replace("\\", "/")
        moc_line = next(
            line for line in content.splitlines() if "/fake/bin/moc" in line
        )
        assert "-DMYFEATURE=1" in moc_line
        assert "inc" in moc_line

    def test_scanner_sees_link_targets_public_include_dirs(self, tmp_path, monkeypatch):
        # The modern multi-library layout: Q_OBJECT header lives in a
        # library's public include dir, reached via #include "sub/hdr.h".
        project = _make_project(tmp_path, monkeypatch)
        libinc = tmp_path / "libs" / "mylib" / "inc"
        libinc.mkdir(parents=True)
        (libinc / "engine.h").write_text(
            "#pragma once\n#include <QObject>\n"
            "class Engine : public QObject { Q_OBJECT };\n"
        )
        (tmp_path / "src" / "main.cpp").write_text(
            '#include "engine.h"\nint main() { return 0; }\n'
        )
        env = cxx_env_with_qt(project)
        mylib = project.StaticLibrary("mylib", env, sources=["src/widget.cpp"])
        mylib.public.include_dirs.append(libinc)
        project.QtProgram("app", env, sources=["src/main.cpp"], link=[mylib])
        content = generate_ninja(project).replace("\\", "/")
        assert "moc_engine.cpp" in content

    def test_header_in_sources_scanned_not_linked(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp", "src/window.h"])
        content = generate_ninja(project).replace("\\", "/")
        assert "moc_window.cpp" in content
        # The header itself must not appear as a link input.
        link_line = next(
            line
            for line in content.splitlines()
            if line.startswith("build app:") or "build app.exe:" in line
        )
        assert "window.h" not in link_line

    def test_filenode_qrc_source(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = cxx_env_with_qt(project)
        qrc_node = project.node("res.qrc")
        project.QtProgram("app", env, sources=["src/main.cpp", qrc_node])
        content = generate_ninja(project).replace("\\", "/")
        # The FileNode's real path (not its repr) names the rcc edge.
        assert "build qt.app/qrc_res.cpp: qt_rcccmd" in content


class TestQtResources:
    def test_synthesized_qrc(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "a.txt").write_text("a\n")
        (tmp_path / "assets" / "b.txt").write_text("b\n")
        env = cxx_env_with_qt(project)
        project.QtResources("assets", env, files=["assets/*.txt"], prefix="/data")
        qrc = tmp_path / "build" / "qt.res" / "assets.qrc"
        assert qrc.exists()
        text = qrc.read_text()
        assert '<qresource prefix="/data">' in text
        assert 'alias="assets/a.txt"' in text
        assert 'alias="assets/b.txt"' in text
        content = generate_ninja(project)
        assert "  RCCNAME = assets\n" in content

    def test_missing_glob_is_an_error(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = cxx_env_with_qt(project)
        with pytest.raises(FileNotFoundError, match="matched no files"):
            project.QtResources("assets", env, files=["nope/*.png"])


class TestAlongsideCxxToolchain:
    """The qt toolchain composes with a C++ toolchain via add_toolchain."""

    def test_moc_output_feeds_program(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
        env = cxx_env_with_qt(project)

        moc_cpp = env.qt.Moc(sources="src/thing.h")
        project.Program("app", env, sources=["src/main.cpp", moc_cpp[0]])
        content = generate_ninja(project)

        # The generated moc_thing.cpp is compiled like any other TU...
        assert (
            "build obj.app/build/qt.gen/src/moc_thing.cpp.o:"
            in content.replace("\\", "/")
            or "moc_thing.cpp.o" in content
        )
        # ...and moc itself runs as a visible edge.
        assert "build qt.gen/src/moc_thing.cpp:" in content


class TestResourceRulesAreShared:
    """Many .qrc files, one rcc rule.

    The resource name is a per-edge variable rather than command text, so the
    rule is identical for every resource. Writing the name into the command
    would give each one a rule of its own, since that text is the rule's
    identity.
    """

    def test_many_resources_share_one_rule(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        for i in range(6):
            (tmp_path / f"r{i}.qrc").write_text("<RCC/>\n")
            env.qt.Rcc(sources=f"r{i}.qrc")

        content = generate_ninja(project)

        rules = [ln for ln in content.splitlines() if ln.startswith("rule qt_rcccmd")]
        names = sorted(
            ln.split("=", 1)[1].strip()
            for ln in content.splitlines()
            if ln.strip().startswith("RCCNAME =")
        )
        assert len(rules) == 1
        assert names == [f"r{i}" for i in range(6)]


class TestNodeVarNameGuard:
    def test_shadowing_a_generator_variable_raises(self, tmp_path, monkeypatch):
        """A per-node "out" would be used where the edge's output belongs."""
        import pytest

        project = _make_project(tmp_path, monkeypatch)
        env = qt_only_env(project)
        (tmp_path / "r.qrc").write_text("<RCC/>\n")
        nodes = env.qt.Rcc(sources="r.qrc")
        nodes[0]._build_info["vars"]["out"] = "nope"

        with pytest.raises(ValueError, match="shadows one the generator defines"):
            generate_ninja(project)

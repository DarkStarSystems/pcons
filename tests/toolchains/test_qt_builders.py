# SPDX-License-Identifier: MIT
"""Tests for the Qt code-generation builders (Moc/Uic/Rcc).

No Qt installation required: the qt toolchain is constructed with fake
tool paths and the generated build.ninja is inspected directly.
"""

from __future__ import annotations

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt.toolchain import QtTool, QtToolchain


def _make_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    for name in ("thing.h", "widget.cpp", "form.ui"):
        (tmp_path / "src" / name).write_text("// test\n")
    (tmp_path / "res.qrc").write_text("<RCC/>\n")
    return Project("qtb", root_dir=tmp_path, build_dir=tmp_path / "build")


def _qt_env(project):
    """Environment with only the (fake) qt toolchain."""
    toolchain = QtToolchain()
    toolchain._tools = {
        "qt": QtTool(moc="/fake/bin/moc", uic="/fake/bin/uic", rcc="/fake/bin/rcc")
    }
    toolchain._configured = True
    env = project.Environment(toolchain=toolchain)
    return env


def _generate(project) -> str:
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (project.build_dir / "build.ninja").read_text()


class TestDefaultLayout:
    def test_output_paths(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
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
        env = _qt_env(project)
        nodes = env.qt.Uic("build/custom/my_ui.h", "src/form.ui")
        assert str(nodes[0].path).replace("\\", "/").endswith("build/custom/my_ui.h")

    def test_empty_sources_is_an_error(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        with pytest.raises(ValueError, match="sources"):
            env.qt.Moc(source="src/thing.h")  # classic slip: source=


class TestNinjaOutput:
    def test_moc_rule_has_depfile(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.Moc(sources="src/thing.h")
        content = _generate(project)

        assert "/fake/bin/moc" in content
        assert "--output-dep-file" in content
        assert "--dep-file-path $out.d" in content
        assert "depfile = $out.d" in content
        assert "deps = gcc" in content
        assert "build qt.gen/src/moc_thing.cpp:" in content

    def test_moc_includes_defines_and_flags(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.mocincludes = ["/qt/include", "/qt/include/QtCore"]
        env.qt.mocdefines = ["QT_CORE_LIB", "MY_DEF=1"]
        env.qt.mocflags = ["--no-notes"]
        env.qt.Moc(sources="src/thing.h")
        content = _generate(project)

        assert "-I/qt/include" in content
        assert "-I/qt/include/QtCore" in content
        assert "-DQT_CORE_LIB" in content
        assert "-DMY_DEF=1" in content
        assert "--no-notes" in content

    def test_moc_predefs_var(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.mocpredefs = ["--include", "qt.gen/moc_predefs.h"]
        env.qt.Moc(sources="src/thing.h")
        content = _generate(project)
        assert "--include qt.gen/moc_predefs.h" in content

    def test_uic_rule_is_plain(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.Uic(sources="src/form.ui")
        content = _generate(project)
        assert "/fake/bin/uic" in content
        assert "-o $out $in" in content
        assert "build qt.gen/src/ui_form.h:" in content

    def test_rcc_rule_name_and_depfile(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.Rcc(sources="res.qrc")
        content = _generate(project)
        assert "/fake/bin/rcc" in content
        assert "--name res" in content
        assert "--depfile $out.d" in content
        assert "build qt.gen/qrc_res.cpp:" in content

    def test_rcc_name_override(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _qt_env(project)
        env.qt.Rcc(sources="res.qrc", name="assets")
        content = _generate(project)
        assert "--name assets" in content


def _cxx_env_with_qt(project):
    """A real C++ toolchain env with a fake qt toolchain added; skips
    the test when no compiler is available."""
    from pcons.toolchains import find_c_toolchain

    try:
        cxx_toolchain = find_c_toolchain()
    except RuntimeError:
        pytest.skip("no C++ toolchain available")
    env = project.Environment(toolchain=cxx_toolchain)
    qt_toolchain = QtToolchain()
    qt_toolchain._tools = {
        "qt": QtTool(moc="/fake/bin/moc", uic="/fake/bin/uic", rcc="/fake/bin/rcc")
    }
    qt_toolchain._configured = True
    env.add_toolchain(qt_toolchain)
    return env


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
        env = _cxx_env_with_qt(project)
        import pcons.toolchains.qt  # noqa: F401  (registers QtProgram)

        project.QtProgram(
            "app",
            env,
            sources=["src/main.cpp", "src/window.cpp", "src/form.ui", "res.qrc"],
        )
        content = _generate(project).replace("\\", "/")

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
        env = _cxx_env_with_qt(project)
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
        env = _cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"], automoc=False)
        content = _generate(project)
        assert "moc_window" not in content
        assert "scan.ok" not in content

    def test_no_moc_exclusion(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        _widgets_tree(tmp_path)
        env = _cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"], no_moc=["src/window.h"])
        content = _generate(project)
        assert "moc_window" not in content

    def test_missing_moc_include_is_hard_error(self, tmp_path, monkeypatch):
        from pcons.toolchains.qt import MocIncludeError

        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "src" / "bad.cpp").write_text(
            "#include <QObject>\nclass B : public QObject { Q_OBJECT };\n"
        )
        env = _cxx_env_with_qt(project)
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


class TestQtResources:
    def test_synthesized_qrc(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "a.txt").write_text("a\n")
        (tmp_path / "assets" / "b.txt").write_text("b\n")
        env = _cxx_env_with_qt(project)
        project.QtResources("assets", env, files=["assets/*.txt"], prefix="/data")
        qrc = tmp_path / "build" / "qt.res" / "assets.qrc"
        assert qrc.exists()
        text = qrc.read_text()
        assert '<qresource prefix="/data">' in text
        assert 'alias="assets/a.txt"' in text
        assert 'alias="assets/b.txt"' in text
        content = _generate(project)
        assert "--name assets" in content

    def test_missing_glob_is_an_error(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, monkeypatch)
        env = _cxx_env_with_qt(project)
        with pytest.raises(FileNotFoundError, match="matched no files"):
            project.QtResources("assets", env, files=["nope/*.png"])


class TestAlongsideCxxToolchain:
    """The qt toolchain composes with a C++ toolchain via add_toolchain."""

    def test_moc_output_feeds_program(self, tmp_path, monkeypatch):
        from pcons.toolchains import find_c_toolchain

        try:
            cxx_toolchain = find_c_toolchain()
        except RuntimeError:
            pytest.skip("no C++ toolchain available")

        project = _make_project(tmp_path, monkeypatch)
        (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
        env = project.Environment(toolchain=cxx_toolchain)
        qt_toolchain = QtToolchain()
        qt_toolchain._tools = {"qt": QtTool(moc="/fake/bin/moc")}
        qt_toolchain._configured = True
        env.add_toolchain(qt_toolchain)

        moc_cpp = env.qt.Moc(sources="src/thing.h")
        project.Program("app", env, sources=["src/main.cpp", moc_cpp[0]])
        content = _generate(project)

        # The generated moc_thing.cpp is compiled like any other TU...
        assert (
            "build obj.app/build/qt.gen/src/moc_thing.cpp.o:"
            in content.replace("\\", "/")
            or "moc_thing.cpp.o" in content
        )
        # ...and moc itself runs as a visible edge.
        assert "build qt.gen/src/moc_thing.cpp:" in content

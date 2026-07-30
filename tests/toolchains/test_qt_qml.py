# SPDX-License-Identifier: MIT
"""Tests for QtQmlModule (no Qt installation required)."""

from __future__ import annotations

import pytest

from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt, generate_ninja


@pytest.fixture
def qml_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "backend.h").write_text(
        "#pragma once\n#include <QObject>\n"
        "class Backend : public QObject {\n    Q_OBJECT\n    QML_ELEMENT\n};\n"
    )
    (src / "backend.cpp").write_text('#include "backend.h"\n')
    qml = tmp_path / "qml"
    qml.mkdir()
    (qml / "Main.qml").write_text("import QtQml\nQtObject {}\n")
    return Project("qmltest", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestQtQmlModule:
    def test_full_pipeline(self, qml_project, tmp_path):
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "ui",
            env,
            uri="com.example.demo",
            version="2.1",
            qml_files=["qml/Main.qml"],
            sources=["src/backend.cpp"],
        )
        content = generate_ninja(qml_project)

        # moc runs with JSON sidecar output.
        assert "--output-json" in content
        # JSON sidecars merge into metatypes...
        assert "build qt.ui/ui_metatypes.json: qt_collectjsoncmd" in content
        assert "moc_backend.cpp.json" in content
        # ...which feed qmltyperegistrar with URI and version.
        assert "build qt.ui/ui_qmltyperegistrations.cpp: qt_typeregcmd" in content
        assert "--import-name com.example.demo" in content
        assert "--major-version 2" in content
        assert "--minor-version 1" in content
        assert "--generate-qmltypes qt.ui/ui.qmltypes" in content
        # The registration TU compiles into the module.
        assert "ui_qmltyperegistrations.cpp.o" in content
        # Resources (qml files + qmldir) compile in via rcc.
        assert "build qt.ui/qrc_ui.cpp: qt_rcccmd" in content

        # qmldir content.
        qmldir = (tmp_path / "build" / "qt.ui" / "qmldir").read_text()
        assert "module com.example.demo" in qmldir
        assert "typeinfo ui.qmltypes" in qmldir
        assert "prefer :/qt/qml/com/example/demo/" in qmldir
        assert "Main 2.1 Main.qml" in qmldir

        # The synthesized qrc embeds under the engine's default import path.
        qrc = (tmp_path / "build" / "qt.ui" / "ui.qrc").read_text()
        assert '<qresource prefix="/qt/qml/com/example/demo">' in qrc
        assert 'alias="Main.qml"' in qrc
        assert 'alias="qmldir"' in qrc

    def test_pure_qml_module_skips_registrar(self, qml_project, tmp_path):
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "puremod", env, uri="Pure.Ui", qml_files=["qml/Main.qml"]
        )
        content = generate_ninja(qml_project)
        assert "qt_typeregcmd" not in content
        assert "qt_collectjsoncmd" not in content
        qmldir = (tmp_path / "build" / "qt.puremod" / "qmldir").read_text()
        assert "module Pure.Ui" in qmldir
        assert "typeinfo" not in qmldir
        assert "Main 1.0 Main.qml" in qmldir

    def test_object_target_kind(self, qml_project):
        # Object target: registration + resources can't be dead-stripped
        # by static-library linking in the consuming app.
        env = cxx_env_with_qt(qml_project)
        target = qml_project.QtQmlModule(
            "ui", env, uri="X.Y", sources=["src/backend.cpp"]
        )
        assert target.target_type == "object"

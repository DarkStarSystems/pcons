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


class TestQmlSingletons:
    """`pragma Singleton` has to reach the qmldir.

    Without the keyword the engine resolves the name as a type rather than an
    instance, so every property access through it is undefined at runtime while
    the build stays green.
    """

    def _qmldir(self, project, tmp_path, body: str) -> str:
        (tmp_path / "qml" / "Theme.qml").write_text(body)
        env = cxx_env_with_qt(project)
        project.QtQmlModule(
            "ui",
            env,
            uri="com.example.demo",
            qml_files=["qml/Main.qml", "qml/Theme.qml"],
        )
        generate_ninja(project)
        return (tmp_path / "build" / "qt.ui" / "qmldir").read_text()

    @pytest.mark.parametrize(
        "body",
        [
            "pragma Singleton\nimport QtQml\nQtObject {}\n",
            "\n\npragma Singleton\nimport QtQml\nQtObject {}\n",
            "// the app's palette\npragma Singleton\nimport QtQml\nQtObject {}\n",
            "/* the app's\n   palette */\npragma Singleton\nimport QtQml\n"
            "QtObject {}\n",
            "pragma ComponentBehavior: Bound\npragma Singleton\nimport QtQml\n"
            "QtObject {}\n",
            "pragma Singleton;\nimport QtQml\nQtObject {}\n",
        ],
    )
    def test_the_pragma_is_found(self, qml_project, tmp_path, body):
        qmldir = self._qmldir(qml_project, tmp_path, body)

        assert "singleton Theme 1.0 Theme.qml" in qmldir

    @pytest.mark.parametrize(
        "body",
        [
            "import QtQml\nQtObject {}\n",
            "// pragma Singleton\nimport QtQml\nQtObject {}\n",
            "import QtQml\npragma Singleton\nQtObject {}\n",
        ],
    )
    def test_a_plain_file_stays_a_type(self, qml_project, tmp_path, body):
        qmldir = self._qmldir(qml_project, tmp_path, body)

        assert "Theme 1.0 Theme.qml" in qmldir
        assert "singleton" not in qmldir

    @pytest.mark.parametrize(
        ("name", "body"),
        [("empty", ""), ("pragmas", "pragma ComponentBehavior: Bound\n")],
    )
    def test_a_file_with_no_type_body_is_not_a_singleton(
        self, qml_project, tmp_path, name, body
    ):
        qmldir = self._qmldir(qml_project, tmp_path, body)

        assert "singleton" not in qmldir

    def test_a_file_that_cannot_be_read_is_not_a_singleton(self, qml_project, tmp_path):
        """A qml_files entry the build generates does not exist yet."""
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "ui",
            env,
            uri="com.example.demo",
            qml_files=["qml/Main.qml", "qml/Generated.qml"],
        )
        generate_ninja(qml_project)

        qmldir = (tmp_path / "build" / "qt.ui" / "qmldir").read_text()
        assert "Generated 1.0 Generated.qml" in qmldir
        assert "singleton" not in qmldir

    def test_the_qml_files_are_configure_dependencies(self, qml_project, tmp_path):
        self._qmldir(qml_project, tmp_path, "import QtQml\nQtObject {}\n")

        deps = {p.name for p in qml_project.configure_dependencies}
        assert {"Main.qml", "Theme.qml"} <= deps


class TestQtQmlModuleUnderABuildPrefix:
    """An environment's `build_prefix` reaches the moc sidecar paths.

    The build tool runs in the top-level build directory, so the paths the
    commands name carry the prefix. Relativizing against the environment's own
    build directory subtracts it and moc cannot open the sidecars.
    """

    def test_the_sidecar_paths_carry_the_prefix(self, qml_project):
        env = cxx_env_with_qt(qml_project)
        env.build_prefix = "host"
        qml_project.QtQmlModule(
            "ui",
            env,
            uri="com.example.demo",
            qml_files=["qml/Main.qml"],
            sources=["src/backend.cpp"],
        )

        content = generate_ninja(qml_project)

        assert "  JSONFILES = host/qt.ui/src/moc_backend.cpp.json\n" in content
        assert "  QMLTYPES = host/qt.ui/ui.qmltypes\n" in content
        assert "build host/qt.ui/src/moc_backend.cpp:" in content


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
        # The URI, version and qmltypes path are per-edge variables, so one
        # typeregistrar rule serves every QML module.
        assert "--import-name $QMLURI" in content
        assert "  QMLURI = com.example.demo\n" in content
        assert "  QMLMAJOR = 2\n" in content
        assert "  QMLMINOR = 1\n" in content
        assert "  QMLTYPES = qt.ui/ui.qmltypes\n" in content
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


class TestQmlModuleEnvironment:
    """A QML module keeps the environment it was declared in."""

    def test_one_name_in_two_environments(self, qml_project):
        host = cxx_env_with_qt(qml_project, name="host")
        cross = cxx_env_with_qt(qml_project, name="cross")

        on_host = qml_project.QtQmlModule(
            "ui", host, uri="X.Y", sources=["src/backend.cpp"]
        )
        on_cross = qml_project.QtQmlModule(
            "ui", cross, uri="X.Y", sources=["src/backend.cpp"]
        )

        assert on_host.qualified_name == "qmltest::ui@host"
        assert on_cross.qualified_name == "qmltest::ui@cross"

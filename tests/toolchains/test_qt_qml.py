# SPDX-License-Identifier: MIT
"""Tests for QtQmlModule (no Qt installation required)."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt, generate_ninja


def _qrc_files(qrc: Path) -> dict[str, str]:
    """The generated .qrc read back as {alias: embedded file path}."""
    root = ElementTree.parse(qrc).getroot()
    return {entry.attrib["alias"]: (entry.text or "") for entry in root.iter("file")}


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

        assert "singleton Theme 1.0 qml/Theme.qml" in qmldir

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

        assert "Theme 1.0 qml/Theme.qml" in qmldir
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
        assert "Generated 1.0 qml/Generated.qml" in qmldir
        assert "singleton" not in qmldir

    def test_the_qml_files_are_configure_dependencies(self, qml_project, tmp_path):
        self._qmldir(qml_project, tmp_path, "import QtQml\nQtObject {}\n")

        deps = {p.name for p in qml_project.configure_dependencies}
        assert {"Main.qml", "Theme.qml"} <= deps


class TestQtQmlModuleUnderABuildPrefix:
    """An environment's `build_prefix` reaches the generated Qt paths.

    The build tool runs in the top-level build directory, so the paths the
    commands name carry the prefix. Relativizing against the environment's own
    build directory subtracts it, and then the automoc reader cannot open its
    spec and qmltyperegistrar cannot open the metatypes.
    """

    def test_the_sidecar_paths_carry_the_prefix(self, qml_project, tmp_path):
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

        assert "  AUTOMOCSPEC = host/qt.ui/automoc.json\n" in content
        assert (
            "build host/qt.ui/mocs_compilation.cpp | "
            "host/qt.ui/automoc.exports.json host/qt.ui/ui_metatypes.json: "
            "qt_automoccmd" in content
        )
        registrar = next(
            line
            for line in content.splitlines()
            if line.startswith("build host/qt.ui/ui_qmltyperegistrations.cpp:")
        )
        assert registrar.endswith(" host/qt.ui/ui_metatypes.json")
        assert "  QMLTYPES = host/qt.ui/ui.qmltypes\n" in content

        spec = json.loads(
            (tmp_path / "build" / "host" / "qt.ui" / "automoc.json").read_text()
        )
        assert spec["gen_dir"] == str(tmp_path / "build" / "host" / "qt.ui")
        assert spec["exports"] == str(
            tmp_path / "build" / "host" / "qt.ui" / "automoc.exports.json"
        )
        assert spec["metatypes"] == str(
            tmp_path / "build" / "host" / "qt.ui" / "ui_metatypes.json"
        )


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
        spec = json.loads((tmp_path / "build" / "qt.ui" / "automoc.json").read_text())
        assert "--output-json" in spec["moc_args"]
        # JSON sidecars merge into metatypes...
        assert (
            "build qt.ui/mocs_compilation.cpp | qt.ui/automoc.exports.json "
            "qt.ui/ui_metatypes.json: qt_automoccmd" in content
        )
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
        assert "Main 2.1 qml/Main.qml" in qmldir

        # The synthesized qrc embeds under the engine's default import path.
        qrc = (tmp_path / "build" / "qt.ui" / "ui.qrc").read_text()
        assert '<qresource prefix="/qt/qml/com/example/demo">' in qrc
        assert 'alias="qml/Main.qml"' in qrc
        assert 'alias="qmldir"' in qrc

    def test_pure_qml_module_skips_registrar(self, qml_project, tmp_path):
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "puremod", env, uri="Pure.Ui", qml_files=["qml/Main.qml"]
        )
        content = generate_ninja(qml_project)
        assert "qt_typeregcmd" not in content
        assert "qt_automoccmd" not in content
        qmldir = (tmp_path / "build" / "qt.puremod" / "qmldir").read_text()
        assert "module Pure.Ui" in qmldir
        assert "typeinfo" not in qmldir
        assert "Main 1.0 qml/Main.qml" in qmldir

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


class TestQmlSourceDirs:
    """Where a module's QML came from, for tools that read the filesystem.

    ``qmlimportscanner`` is the one that matters: a deployment step has to
    hand it root paths, and the module's own QML is embedded in a resource
    by then. Nothing else knows the directory, so the module records it.
    """

    def test_a_module_records_where_its_qml_is(self, qml_project, tmp_path):
        from pcons.toolchains.qt.qml import qml_source_dirs

        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule("ui", env, uri="X.Y", qml_files=["qml/Main.qml"])

        assert qml_source_dirs(qml_project) == [tmp_path / "qml"]

    def test_a_project_with_no_qml_module_has_none(self, qml_project):
        from pcons.toolchains.qt.qml import qml_source_dirs

        assert qml_source_dirs(qml_project) == []

    def test_a_module_with_no_qml_files_contributes_nothing(self, qml_project):
        """C++ QML_ELEMENT types and no .qml file. There is nothing for a
        scanner to read."""
        from pcons.toolchains.qt.qml import qml_source_dirs

        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule("ui", env, uri="X.Y", sources=["src/backend.cpp"])

        assert qml_source_dirs(qml_project) == []

    def test_two_modules_sharing_a_directory_name_it_once(self, qml_project, tmp_path):
        from pcons.toolchains.qt.qml import qml_source_dirs

        (tmp_path / "qml" / "Other.qml").write_text("import QtQml\nQtObject {}\n")
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule("ui", env, uri="X.Y", qml_files=["qml/Main.qml"])
        qml_project.QtQmlModule("more", env, uri="X.Z", qml_files=["qml/Other.qml"])

        assert qml_source_dirs(qml_project) == [tmp_path / "qml"]

    def test_the_environment_narrows_it(self, qml_project, tmp_path):
        from pcons.toolchains.qt.qml import qml_source_dirs

        host = cxx_env_with_qt(qml_project, name="host")
        cross = cxx_env_with_qt(qml_project, name="cross")
        qml_project.QtQmlModule("ui", host, uri="X.Y", qml_files=["qml/Main.qml"])

        assert qml_source_dirs(qml_project, host) == [tmp_path / "qml"]
        assert qml_source_dirs(qml_project, cross) == []

    def test_a_subdirectory_project_is_included(self, qml_project, tmp_path):
        """The package describes one build, and add_subdirectory does not make
        a second one."""
        from pcons.toolchains.qt.qml import qml_source_dirs
        from pcons.util.add_subdirectory import add_subdirectory

        child_qml = tmp_path / "child" / "qml"
        child_qml.mkdir(parents=True)
        (child_qml / "Main.qml").write_text("import QtQml\nQtObject {}\n")
        (tmp_path / "child" / "pcons-build.py").write_text(
            "from pcons import get_var\n"
            "from pcons.core.project import Project\n"
            "child = Project('child', root_dir=get_var('ROOT'))\n"
            "child.QtQmlModule('ui', child.default_environment, uri='X.Y',\n"
            "                  qml_files=['child/qml/Main.qml'])\n"
        )
        env = cxx_env_with_qt(qml_project)

        add_subdirectory("child", env=env, vars={"ROOT": str(tmp_path)})

        assert qml_source_dirs(qml_project) == [child_qml]

    def test_a_subdirectory_entry_is_anchored_at_the_declaring_script(
        self, qml_project, tmp_path
    ):
        """A scanner root has to be a directory that exists, so the entry is
        resolved against the same root the resource path came from."""
        from pcons.toolchains.qt.qml import qml_source_dirs
        from pcons.util.add_subdirectory import add_subdirectory

        sub = tmp_path / "tools" / "widget"
        (sub / "qml").mkdir(parents=True)
        (sub / "qml" / "Chip.qml").write_text("import QtQml\nQtObject {}\n")
        (sub / "pcons-build.py").write_text(
            "from pcons import context\n"
            "project = context.current_project\n"
            "project.QtQmlModule('subui', project.default_environment,\n"
            "                    uri='My.Sub', qml_files=['qml/Chip.qml'])\n"
        )
        env = cxx_env_with_qt(qml_project)

        add_subdirectory("tools/widget", env=env)

        assert qml_source_dirs(qml_project) == [sub / "qml"]


class TestQmlFilesKeepTheirPath:
    """An entry is also its path inside the module resource.

    Every expected string here was read out of what Qt 6.11.1's
    ``qt_add_qml_module`` generated for the same file list: the qmldir under
    ``<build>/My/Module/qmldir`` and the qrc under ``<build>/.qt/rcc/``.
    """

    def _generate(self, project, tmp_path, files, uri="My.Module"):
        for name in files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("import QtQuick\nItem {}\n")
        env = cxx_env_with_qt(project)
        project.QtQmlModule("ui", env, uri=uri, qml_files=list(files))
        generate_ninja(project)
        module_dir = tmp_path / "build" / "qt.ui"
        return (
            (module_dir / "qmldir").read_text(),
            (module_dir / "ui.qrc").read_text(),
        )

    def test_the_qmldir_names_the_nested_path(self, qml_project, tmp_path):
        qmldir, _ = self._generate(
            qml_project,
            tmp_path,
            ["Main.qml", "sub/Thing.qml", "deep/nested/Page.qml"],
        )

        lines = qmldir.splitlines()
        assert "Main 1.0 Main.qml" in lines
        assert "Thing 1.0 sub/Thing.qml" in lines
        assert "Page 1.0 deep/nested/Page.qml" in lines

    def test_the_qrc_alias_is_the_nested_path(self, qml_project, tmp_path):
        _, qrc = self._generate(
            qml_project,
            tmp_path,
            ["Main.qml", "sub/Thing.qml", "deep/nested/Page.qml"],
        )

        assert '<qresource prefix="/qt/qml/My/Module">' in qrc
        assert 'alias="Main.qml"' in qrc
        assert 'alias="sub/Thing.qml"' in qrc
        assert 'alias="deep/nested/Page.qml"' in qrc

    def test_a_path_object_entry_becomes_a_slashed_resource_path(
        self, qml_project, tmp_path
    ):
        """qml_files takes str or Path; a resource path is always slashed."""
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "ui", env, uri="My.Module", qml_files=[Path("qml") / "Main.qml"]
        )
        generate_ninja(qml_project)

        module_dir = tmp_path / "build" / "qt.ui"
        assert "Main 1.0 qml/Main.qml" in (module_dir / "qmldir").read_text()
        assert 'alias="qml/Main.qml"' in (module_dir / "ui.qrc").read_text()

    def test_a_module_declared_in_a_subdirectory_drops_the_subdirectory(
        self, qml_project, tmp_path
    ):
        """The root is the declaring script's directory, as in CMake.

        ``qt_add_qml_module`` resolves QML_FILES against
        CMAKE_CURRENT_SOURCE_DIR, so a module declared two directories down
        that lists ``qml/Chip.qml`` puts it at
        ``qrc:/qt/qml/<uri>/qml/Chip.qml``. The path of the build script that
        declared the module is not part of the resource layout.
        """
        from pcons.util.add_subdirectory import add_subdirectory

        sub = tmp_path / "tools" / "widget"
        (sub / "qml").mkdir(parents=True)
        (sub / "qml" / "Chip.qml").write_text("import QtQml\nQtObject {}\n")
        (sub / "pcons-build.py").write_text(
            "from pcons import context\n"
            "project = context.current_project\n"
            "project.QtQmlModule('subui', project.default_environment,\n"
            "                    uri='My.Sub', qml_files=['qml/Chip.qml'])\n"
        )
        env = cxx_env_with_qt(qml_project)

        add_subdirectory("tools/widget", env=env)
        generate_ninja(qml_project)

        module_dir = tmp_path / "build" / "tools" / "widget" / "qt.subui"
        assert (
            "Chip 1.0 qml/Chip.qml" in (module_dir / "qmldir").read_text().splitlines()
        )
        embedded = _qrc_files(module_dir / "subui.qrc")
        assert embedded["qml/Chip.qml"] == str(sub / "qml" / "Chip.qml")


class TestQmldirInEverySubdirectory:
    """Every resource directory below the root needs a qmldir of its own.

    The engine resolves an unqualified type name through the implicit import
    of the loaded file's own resource directory. A file below the module root
    therefore sees nothing of the module unless a qmldir sits beside it
    redirecting to the root, which is what ``qt_add_qml_module`` writes under
    ``QTP0004`` NEW. Everything here was read back out of Qt 6.11.1's own
    output for the same file list.

    ``examples/81_qml_nested_layout`` is the end-to-end half of this: it
    builds and runs an application whose ``qml/pages/Detail.qml`` names a type
    living in ``qml/widgets/``. Without these files it dies with
    ``Badge is not a type``.
    """

    def _generate(self, project, tmp_path, files, uri="My.Module"):
        for name in files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("import QtQuick\nItem {}\n")
        env = cxx_env_with_qt(project)
        project.QtQmlModule("ui", env, uri=uri, qml_files=list(files))
        generate_ninja(project)
        return tmp_path / "build" / "qt.ui"

    def test_each_directory_holding_qml_gets_one(self, qml_project, tmp_path):
        module_dir = self._generate(
            qml_project,
            tmp_path,
            ["Main.qml", "sub/Thing.qml", "deep/nested/Page.qml"],
        )

        written = sorted(
            path.parent.relative_to(module_dir).as_posix()
            for path in module_dir.rglob("qmldir")
        )
        assert written == [".", "deep/nested", "sub"]

    def test_the_stub_prefers_the_module_root(self, qml_project, tmp_path):
        module_dir = self._generate(qml_project, tmp_path, ["sub/Thing.qml"])

        assert (module_dir / "sub" / "qmldir").read_text() == (
            "prefer :/qt/qml/My/Module/\n"
        )

    def test_the_module_root_keeps_the_real_qmldir(self, qml_project, tmp_path):
        module_dir = self._generate(qml_project, tmp_path, ["sub/Thing.qml"])

        lines = (module_dir / "qmldir").read_text().splitlines()
        assert lines[0] == "module My.Module"
        assert "Thing 1.0 sub/Thing.qml" in lines

    def test_a_directory_holding_no_qml_of_its_own_gets_none(
        self, qml_project, tmp_path
    ):
        """``deep/`` only holds a directory, so nothing ever loads from it."""
        module_dir = self._generate(qml_project, tmp_path, ["deep/nested/Page.qml"])

        assert not (module_dir / "deep" / "qmldir").exists()
        assert (module_dir / "deep" / "nested" / "qmldir").exists()

    def test_a_flat_module_gets_only_the_root_qmldir(self, qml_project, tmp_path):
        module_dir = self._generate(qml_project, tmp_path, ["Main.qml", "Other.qml"])

        assert [path.parent for path in module_dir.rglob("qmldir")] == [module_dir]

    def test_the_stubs_are_embedded_under_their_own_directory(
        self, qml_project, tmp_path
    ):
        module_dir = self._generate(
            qml_project,
            tmp_path,
            ["Main.qml", "sub/Thing.qml", "deep/nested/Page.qml"],
        )

        embedded = _qrc_files(module_dir / "ui.qrc")
        assert embedded["qmldir"] == str(module_dir / "qmldir")
        assert embedded["sub/qmldir"] == str(module_dir / "sub" / "qmldir")
        assert embedded["deep/nested/qmldir"] == str(
            module_dir / "deep" / "nested" / "qmldir"
        )

    def test_a_directory_holding_a_singleton_gets_the_same_stub(
        self, qml_project, tmp_path
    ):
        """The redirect is what makes the singleton resolve as an instance.

        A subdirectory left without a qmldir resolves ``Theme`` by scanning
        that directory, which yields the type. Redirecting to the root qmldir
        yields its ``singleton`` line instead, so no separate handling is
        needed here.
        """
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "Theme.qml").write_text(
            "pragma Singleton\nimport QtQml\nQtObject {}\n"
        )
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "ui", env, uri="My.Module", qml_files=["state/Theme.qml"]
        )
        generate_ninja(qml_project)
        module_dir = tmp_path / "build" / "qt.ui"

        assert (module_dir / "state" / "qmldir").read_text() == (
            "prefer :/qt/qml/My/Module/\n"
        )
        assert "singleton Theme 1.0 state/Theme.qml" in (
            (module_dir / "qmldir").read_text().splitlines()
        )


class TestQmlEntriesOutsideTheRoot:
    """The root is ``project.current_dir``, and an entry has to sit under it.

    An entry is also the file's path inside the module resource, so an entry
    with no place under the root has no resource path either.
    """

    def test_an_absolute_entry_under_the_root_is_accepted(self, qml_project, tmp_path):
        """pcons can compute a resource path for it, so it takes it.

        ``qt_add_qml_module`` refuses every absolute entry; that is a CMake
        limitation, not a rule worth copying.
        """
        env = cxx_env_with_qt(qml_project)
        qml_project.QtQmlModule(
            "spelled_out", env, uri="My.Module", qml_files=["qml/Main.qml"]
        )
        qml_project.QtQmlModule(
            "absolute",
            env,
            uri="My.Other",
            qml_files=[str(tmp_path / "qml" / "Main.qml")],
        )
        generate_ninja(qml_project)

        build = tmp_path / "build"
        relative_line = (build / "qt.spelled_out" / "qmldir").read_text().splitlines()
        absolute_line = (build / "qt.absolute" / "qmldir").read_text().splitlines()
        assert "Main 1.0 qml/Main.qml" in relative_line
        assert "Main 1.0 qml/Main.qml" in absolute_line
        assert (
            _qrc_files(build / "qt.absolute" / "absolute.qrc")["qml/Main.qml"]
            == _qrc_files(build / "qt.spelled_out" / "spelled_out.qrc")["qml/Main.qml"]
        )

    def test_an_absolute_entry_outside_the_root_is_refused(self, qml_project, tmp_path):
        outside = tmp_path.parent / "elsewhere" / "Outside.qml"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("import QtQml\nQtObject {}\n")
        env = cxx_env_with_qt(qml_project)

        with pytest.raises(ValueError) as excinfo:
            qml_project.QtQmlModule(
                "ui", env, uri="My.Module", qml_files=[str(outside)]
            )

        message = str(excinfo.value)
        assert str(outside) in message
        assert str(tmp_path) in message

    def test_an_entry_reaching_above_the_root_is_refused(self, qml_project, tmp_path):
        """``qt_add_qml_module`` keeps the dot-dots and pcons deliberately does
        not: an entry above the root has no place under the module's resource
        prefix, which is the same reason an outside absolute entry is refused.
        One rule, one message."""
        env = cxx_env_with_qt(qml_project)

        with pytest.raises(ValueError) as excinfo:
            qml_project.QtQmlModule(
                "ui",
                env,
                uri="My.Module",
                qml_files=["qml/Main.qml", "../outside/Outside.qml"],
            )

        message = str(excinfo.value)
        assert "../outside/Outside.qml" in message
        assert str(tmp_path) in message


class TestQmlFileCollisions:
    """Two entries that land on one resource path must not be silent.

    Measured against Qt 6.11.1: with ``sub/Thing.qml`` and ``other/Thing.qml``
    in one module, ``qt_add_qml_module`` writes two ``Thing 1.0`` qmldir lines
    and the engine resolves ``Thing`` to the last one. The first file is in the
    resource and unreachable by name, with no warning from any tool.
    """

    def _module(self, project, tmp_path, files):
        for name in files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("import QtQml\nQtObject {}\n")
        env = cxx_env_with_qt(project)
        return lambda: project.QtQmlModule(
            "ui", env, uri="com.example.demo", qml_files=list(files)
        )

    def test_two_files_sharing_a_type_name_raise(self, qml_project, tmp_path):
        build = self._module(
            qml_project, tmp_path, ["sub/Thing.qml", "other/Thing.qml"]
        )

        with pytest.raises(ValueError) as excinfo:
            build()

        message = str(excinfo.value)
        assert "sub/Thing.qml" in message
        assert "other/Thing.qml" in message
        assert "Thing" in message

    def test_the_same_entry_twice_raises(self, qml_project, tmp_path):
        build = self._module(qml_project, tmp_path, ["qml/Main.qml", "qml/Main.qml"])

        with pytest.raises(ValueError) as excinfo:
            build()

        assert "qml/Main.qml" in str(excinfo.value)
        assert "twice" in str(excinfo.value)

    def test_the_same_name_in_one_directory_is_not_a_collision(
        self, qml_project, tmp_path
    ):
        build = self._module(qml_project, tmp_path, ["sub/Thing.qml", "sub/Other.qml"])

        build()
        generate_ninja(qml_project)

        qmldir = (tmp_path / "build" / "qt.ui" / "qmldir").read_text().splitlines()
        assert "Thing 1.0 sub/Thing.qml" in qmldir
        assert "Other 1.0 sub/Other.qml" in qmldir

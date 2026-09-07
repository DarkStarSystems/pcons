# SPDX-License-Identifier: MIT
"""Qt consumers must find the install a parent project located.

find_qt() caches discovery on the project it is called with. A
subdirectory that creates its own Project inherits nothing by name, so
every consumer of the located install has to look up the project tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import pcons.toolchains.qt.deploy as qt_deploy
import pcons.toolchains.qt.finder as qt_finder
from pcons.core.project import Project
from pcons.packages.description import PackageDescription
from pcons.toolchains.qt import _automoc, find_qt
from pcons.util.add_subdirectory import add_subdirectory

from ._qt_test_utils import cxx_env_with_qt, generate_ninja

_MODULES = ("Core", "Qml")
_TOOLS = ("macdeployqt", "moc", "qmltyperegistrar")


class _FakePkgConfig:
    """A pkg-config universe describing the Qt tree under ``prefix``."""

    def __init__(self, prefix: Path) -> None:
        self._prefix = prefix
        self._pcs = {
            f"Qt6{name}": PackageDescription(
                name=f"Qt6{name}",
                version="6.7.2",
                include_dirs=[
                    str(prefix / "include"),
                    str(prefix / "include" / f"Qt{name}"),
                ],
                libraries=[f"Qt6{name}"],
                defines=[f"QT_{name.upper()}_LIB"],
                prefix=str(prefix),
            )
            for name in _MODULES
        }

    def is_available(self) -> bool:
        return True

    def find(self, name, version=None, components=None):
        return self._pcs.get(name)

    def get_variable(self, name, var):
        return {
            "prefix": str(self._prefix),
            "bindir": str(self._prefix / "bin"),
        }.get(var)


def _make_qt_tree(prefix: Path) -> Path:
    (prefix / "include" / "QtCore").mkdir(parents=True)
    (prefix / "include" / "QtQml").mkdir(parents=True)
    (prefix / "include" / "QtCore" / "qobject.h").write_text(
        "#pragma once\nclass QObject { Q_OBJECT };\n"
    )
    (prefix / "bin").mkdir()
    for tool in _TOOLS:
        for name in (tool, f"{tool}.exe"):
            (prefix / "bin" / name).write_text("")
    metatypes = prefix / "lib" / "metatypes"
    metatypes.mkdir(parents=True)
    for module in _MODULES:
        (metatypes / f"qt6{module.lower()}_metatypes.json").write_text("[]")
    return prefix


@pytest.fixture
def qt_prefix(tmp_path: Path) -> Path:
    return _make_qt_tree(tmp_path / "qt6")


def _find_fake_qt(project: Project, prefix: Path, modules=("Core",)):
    finder = _FakePkgConfig(prefix)
    with patch.object(qt_finder, "_pkgconfig_finder", lambda qt_root: finder):
        return find_qt(project, modules=list(modules))


def _child(tmp_path: Path, script: str) -> Path:
    child = tmp_path / "child"
    (child / "src").mkdir(parents=True)
    (child / "src" / "main.cpp").write_text(
        "#include <QtCore/qobject.h>\nint main() { return 0; }\n"
    )
    (child / "pcons-build.py").write_text(script)
    return child


def _patch_platform(**flags):
    defaults = {"is_macos": False, "is_windows": False, "is_linux": False}
    defaults.update(flags)
    return patch.object(qt_deploy, "get_platform", lambda: SimpleNamespace(**defaults))


@pytest.fixture
def top(tmp_path, monkeypatch) -> Project:
    monkeypatch.chdir(tmp_path)
    return Project("top", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestQtInstallAcrossSubdirectories:
    def test_child_automoc_leaves_qt_headers_alone(self, top, tmp_path, qt_prefix):
        env = cxx_env_with_qt(top)
        qt = _find_fake_qt(top, qt_prefix)
        env.use(qt.Core)
        _child(
            tmp_path,
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "app = project.QtProgram(\n"
            "    'app', project.default_environment, sources=['src/main.cpp']\n"
            ")\n",
        )

        add_subdirectory("child")
        content = generate_ninja(top)

        assert "child/qt.app/automoc.json" in content
        spec_path = tmp_path / "build" / "child" / "qt.app" / "automoc.json"
        spec = json.loads(spec_path.read_text())
        assert spec["include_dirs"] == []
        assert [Path(p).name for p in spec["sources"]] == ["main.cpp"]

        gen = spec_path.parent
        assert (
            _automoc.main(
                [
                    "--spec",
                    str(spec_path),
                    "-o",
                    str(gen / "mocs_compilation.cpp"),
                    "--depfile",
                    str(gen / "mocs_compilation.cpp.d"),
                ]
            )
            == 0
        )
        assert "#include" not in (gen / "mocs_compilation.cpp").read_text()
        depfile = (gen / "mocs_compilation.cpp.d").read_text().replace("\\", "/")
        assert str(qt_prefix).replace("\\", "/") not in depfile

    def test_child_deploy_uses_the_located_tool(self, top, tmp_path, qt_prefix):
        cxx_env_with_qt(top)
        _find_fake_qt(top, qt_prefix)
        _child(
            tmp_path,
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "env = project.default_environment\n"
            "app = project.Program('app', env, sources=['src/main.cpp'])\n"
            "project.QtDeploy('deploy', env, app=app, bundle='App.app')\n",
        )

        with _patch_platform(is_macos=True):
            add_subdirectory("child")
        content = generate_ninja(top)

        macdeployqt = str(qt_prefix / "bin" / "macdeployqt").replace("\\", "/")
        assert macdeployqt in content

    def test_child_find_qt_beats_the_parents(self, top, tmp_path, qt_prefix):
        cxx_env_with_qt(top)
        _find_fake_qt(top, qt_prefix)
        own = _make_qt_tree(tmp_path / "qt6-own")
        _child(
            tmp_path,
            "from pcons.core.project import Project\n"
            "from pcons.toolchains.qt import find_qt\n"
            "project = Project('child')\n"
            "env = project.default_environment\n"
            "find_qt(project, modules=['Core'])\n"
            "app = project.Program('app', env, sources=['src/main.cpp'])\n"
            "project.QtDeploy('deploy', env, app=app, bundle='App.app')\n",
        )

        finder = _FakePkgConfig(own)
        with (
            patch.object(qt_finder, "_pkgconfig_finder", lambda qt_root: finder),
            _patch_platform(is_macos=True),
        ):
            add_subdirectory("child")
        content = generate_ninja(top)

        assert str(own / "bin" / "macdeployqt").replace("\\", "/") in content
        assert str(qt_prefix / "bin" / "macdeployqt").replace("\\", "/") not in content

    def test_child_qml_module_gets_the_foreign_types(self, top, tmp_path, qt_prefix):
        cxx_env_with_qt(top)
        _find_fake_qt(top, qt_prefix, modules=("Core", "Qml"))
        child = _child(
            tmp_path,
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "qml = Project.top_level().get_target('Qt6Qml')\n"
            "project.QtQmlModule(\n"
            "    'ui', project.default_environment, uri='com.example.demo',\n"
            "    qml_files=['qml/Main.qml'], sources=['src/backend.cpp'],\n"
            "    link=[qml],\n"
            ")\n",
        )
        (child / "src" / "backend.h").write_text(
            "#pragma once\n#include <QObject>\n"
            "class Backend : public QObject {\n    Q_OBJECT\n    QML_ELEMENT\n};\n"
        )
        (child / "src" / "backend.cpp").write_text('#include "backend.h"\n')
        (child / "qml").mkdir()
        (child / "qml" / "Main.qml").write_text("import QtQml\nQtObject {}\n")

        add_subdirectory("child")
        content = generate_ninja(top)

        metatypes = str(qt_prefix / "lib" / "metatypes").replace("\\", "/")
        assert f"--foreign-types {metatypes}" in content

    def test_child_reusing_the_top_project_scans_its_own_sources(
        self, top, tmp_path, qt_prefix
    ):
        cxx_env_with_qt(top)
        _find_fake_qt(top, qt_prefix)
        child = _child(
            tmp_path,
            "import sys\n"
            "from pcons import context\n"
            "project = context.current_project\n"
            "env = project.default_environment\n"
            "gen = env.Command(\n"
            "    target='gen/extra.cpp', source='mk.py',\n"
            "    command=[sys.executable, '$SOURCE', '$TARGET'],\n"
            ")\n"
            "app = project.QtProgram(\n"
            "    'app', env, sources=['src/main.cpp', gen.output_nodes[0]],\n"
            "    no_moc=['src/window.h'],\n"
            ")\n",
        )
        (child / "mk.py").write_text("import sys\n")
        (child / "src" / "window.h").write_text(
            "#pragma once\n#include <QObject>\n"
            "class Window : public QObject {\n    Q_OBJECT\n};\n"
        )
        (child / "src" / "main.cpp").write_text(
            '#include "window.h"\nint main() { return 0; }\n'
        )

        add_subdirectory("child")
        content = generate_ninja(top)

        assert "child/qt.app/automoc.json $topdir/child/src/main.cpp" in content
        spec_path = tmp_path / "build" / "child" / "qt.app" / "automoc.json"
        spec = json.loads(spec_path.read_text())
        assert spec["sources"] == [str(child / "src" / "main.cpp")]
        assert spec["no_moc"] == [str(child / "src" / "window.h")]
        assert "child/obj.app/gen/extra.cpp.o: cxx" in content

        gen = spec_path.parent
        assert (
            _automoc.main(
                [
                    "--spec",
                    str(spec_path),
                    "-o",
                    str(gen / "mocs_compilation.cpp"),
                    "--depfile",
                    str(gen / "mocs_compilation.cpp.d"),
                ]
            )
            == 0
        )
        depfile = (gen / "mocs_compilation.cpp.d").read_text().replace("\\", "/")
        for name in ("main.cpp", "window.h"):
            assert str(child / "src" / name).replace("\\", "/") in depfile

# SPDX-License-Identifier: MIT
"""automoc decides the moc set at build time, not at configure time.

A generator that writes a Q_OBJECT header contributes to the moc set on the
first build. Nothing here asserts a value the build script passed in: every
check reads the emitted ``build.ninja``, the generated ``mocs_compilation.cpp``
or the built program back out.

The end-to-end generator sleeps. A generator that returns at once is scheduled
first often enough that a configure-time scan looks correct by luck.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt.toolchain import _qt_available

from ._qt_test_utils import cxx_env_with_qt, generate_ninja

SLOW_GENERATOR = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import sys
    import time
    from pathlib import Path

    out = Path(sys.argv[1])
    time.sleep(1.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        '#pragma once\\n'
        '#include <QObject>\\n'
        'class Generated : public QObject {\\n'
        '    Q_OBJECT\\n'
        'public:\\n'
        '    int answer() const { return 42; }\\n'
        '};\\n'
    )
    Path(sys.argv[2]).write_text("done\\n")
    """
)

MAIN_CPP = """\
#include "generated.hpp"
#include <cstdio>

int main() {
    Generated g;
    std::printf("meta=%s answer=%d\\n", g.metaObject()->className(), g.answer());
    return 0;
}
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)
needs_qt = pytest.mark.skipif(not _qt_available(), reason="no Qt installation")


def run_ninja(build_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Build with no pcons run in between: the freshness question."""
    return subprocess.run(
        ["ninja", *args], cwd=build_dir, capture_output=True, text=True, check=False
    )


def build_files(project: Project) -> Path:
    """Write the build files; return the build directory as an absolute path."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return project.root_dir / project.build_dir


def aggregator(root: Path, target: str = "app") -> Path:
    return root / "build" / f"qt.{target}" / "mocs_compilation.cpp"


@needs_qt
@needs_ninja
class TestGeneratedHeaderReachesTheMocSet:
    """Shape A: a generator writes a Q_OBJECT header the scan must see."""

    @staticmethod
    def _project(root: Path, monkeypatch) -> Project:
        from pcons.toolchains import find_c_toolchain
        from pcons.toolchains.qt import find_qt

        monkeypatch.chdir(root)

        write(root / "gen.py", SLOW_GENERATOR)
        write(root / "src" / "main.cpp", MAIN_CPP)

        project = Project("automoc", root_dir=root, build_dir=root / "build")
        env = project.Environment(toolchain=find_c_toolchain())
        env.cxx.set_standard(17)
        env.cxx.includes.append("gen")
        qt = find_qt(project, env, modules=["Core"])
        gen = env.Command(
            target="gen.stamp",
            source="gen.py",
            command=[
                sys.executable,
                "$SOURCE",
                (root / "gen" / "generated.hpp").as_posix(),
                "$TARGET",
            ],
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"], link=[qt.Core])
        app.depends(gen)
        project.resolve()
        return project

    def test_first_build_mocs_the_generated_header(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, monkeypatch)
        build_dir = build_files(project)

        result = run_ninja(build_dir)
        assert result.returncode == 0, result.stderr or result.stdout

        assert "moc_generated.cpp" in aggregator(tmp_path).read_text()
        program = subprocess.run(
            [str(build_dir / "app")], capture_output=True, text=True, check=True
        )
        assert "meta=Generated" in program.stdout
        assert "answer=42" in program.stdout

    def test_second_build_does_nothing(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, monkeypatch)
        build_dir = build_files(project)
        assert run_ninja(build_dir).returncode == 0
        assert "no work to do" in run_ninja(build_dir).stdout


@needs_qt
@needs_ninja
class TestTheMocSetTracksTheSources:
    """A header gaining or losing Q_OBJECT, under ninja alone."""

    @staticmethod
    def _project(root: Path, monkeypatch) -> Project:
        from pcons.toolchains import find_c_toolchain
        from pcons.toolchains.qt import find_qt

        monkeypatch.chdir(root)

        write(
            root / "src" / "thing.h",
            "#pragma once\n#include <QObject>\nclass Thing : public QObject {\n"
            "    Q_OBJECT\n};\n",
        )
        write(root / "src" / "later.h", "#pragma once\nint later();\n")
        write(
            root / "src" / "main.cpp",
            '#include "thing.h"\n#include "later.h"\nint main() { return 0; }\n',
        )
        project = Project("automoc", root_dir=root, build_dir=root / "build")
        env = project.Environment(toolchain=find_c_toolchain())
        env.cxx.set_standard(17)
        qt = find_qt(project, env, modules=["Core"])
        project.QtProgram("app", env, sources=["src/main.cpp"], link=[qt.Core])
        project.resolve()
        return project

    def test_a_header_that_gains_q_object_is_mocd(self, tmp_path, monkeypatch):
        build_dir = build_files(self._project(tmp_path, monkeypatch))
        assert run_ninja(build_dir).returncode == 0
        assert "moc_later.cpp" not in aggregator(tmp_path).read_text()

        write(
            tmp_path / "src" / "later.h",
            "#pragma once\n#include <QObject>\nclass Later : public QObject {\n"
            "    Q_OBJECT\n};\n",
        )
        result = run_ninja(build_dir)
        assert result.returncode == 0, result.stderr or result.stdout
        assert "moc_later.cpp" in aggregator(tmp_path).read_text()

    def test_a_header_that_loses_q_object_drops_its_moc_output(
        self, tmp_path, monkeypatch
    ):
        build_dir = build_files(self._project(tmp_path, monkeypatch))
        assert run_ninja(build_dir).returncode == 0
        stale = build_dir / "qt.app" / "src" / "moc_thing.cpp"
        assert stale.exists()

        write(tmp_path / "src" / "thing.h", "#pragma once\nint thing();\n")
        result = run_ninja(build_dir)
        assert result.returncode == 0, result.stderr or result.stdout
        assert not stale.exists()
        assert "moc_thing.cpp" not in aggregator(tmp_path).read_text()

    def test_an_edit_that_keeps_the_set_leaves_the_aggregator_alone(
        self, tmp_path, monkeypatch
    ):
        build_dir = build_files(self._project(tmp_path, monkeypatch))
        assert run_ninja(build_dir).returncode == 0
        before = aggregator(tmp_path).stat().st_mtime_ns

        write(
            tmp_path / "src" / "thing.h",
            "#pragma once\n#include <QObject>\nclass Thing : public QObject {\n"
            "    Q_OBJECT\npublic:\n    int extra() const { return 1; }\n};\n",
        )
        result = run_ninja(build_dir)
        assert result.returncode == 0, result.stderr or result.stdout
        assert aggregator(tmp_path).stat().st_mtime_ns == before


CHILD_BUILD_SCRIPT = """\
# SPDX-License-Identifier: MIT
import sys

from pcons.core.project import Project

project = Project("child")
env = project.default_environment
gen = env.Command(
    target="gen/extra.h",
    source="mk.py",
    command=[sys.executable, "$SOURCE", "$TARGET"],
)
app = project.QtProgram("app", env, sources=["src/main.cpp"])
app.depends(gen)
"""


class TestSubdirectoryTargets:
    """A Qt target declared by an add_subdirectory child script.

    The child script makes its own Project, so the automoc edge belongs to
    a sub-project while the toolchain's after_resolve hook is only ever
    handed the top-level one.
    """

    def test_the_automoc_edge_of_a_child_waits_for_a_generator(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        child = tmp_path / "child"
        write(child / "pcons-build.py", CHILD_BUILD_SCRIPT)
        write(child / "mk.py", "pass\n")
        write(
            child / "src" / "window.h",
            "#pragma once\n#include <QObject>\n"
            "class Window : public QObject { Q_OBJECT };\n",
        )
        write(child / "src" / "main.cpp", '#include "window.h"\nint main(){}\n')

        project = Project("top", root_dir=tmp_path, build_dir=tmp_path / "build")
        cxx_env_with_qt(project)
        project.add_subdirectory("child")
        content = generate_ninja(project)

        automoc = next(
            line
            for line in content.splitlines()
            if line.startswith("build child/qt.app/mocs_compilation.cpp")
        )
        assert "|| " in automoc
        assert "gen/extra.h" in automoc.split("|| ", 1)[1]


class TestNinjaShape:
    """What the emitted build.ninja says, with no Qt installation."""

    @staticmethod
    def _project(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write(
            tmp_path / "src" / "window.h",
            "#pragma once\n#include <QObject>\n"
            "class Window : public QObject { Q_OBJECT };\n",
        )
        write(tmp_path / "src" / "main.cpp", '#include "window.h"\nint main(){}\n')
        write(tmp_path / "mk.py", "pass\n")
        return Project("qtb", root_dir=tmp_path, build_dir=tmp_path / "build")

    def test_one_automoc_edge_replaces_the_stamp_and_the_per_header_edges(
        self, tmp_path, monkeypatch
    ):
        project = self._project(tmp_path, monkeypatch)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"])
        content = generate_ninja(project)

        assert "scan.ok" not in content
        assert "scan-manifest.json" not in content
        assert "qt_moccmd" not in content
        automoc = next(
            line
            for line in content.splitlines()
            if line.startswith("build qt.app/mocs_compilation.cpp:")
        )
        assert "$topdir/src/main.cpp" in automoc
        assert "mocs_compilation.cpp.o" in content

    def test_the_automoc_edge_has_a_depfile(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, monkeypatch)
        env = cxx_env_with_qt(project)
        project.QtProgram("app", env, sources=["src/main.cpp"])
        content = generate_ninja(project)
        rule = content.split("rule qt_automoccmd", 1)[1].split("\nrule ", 1)[0]
        assert "depfile = $out.d" in rule
        assert "deps = gcc" in rule
        assert "restat = 1" in rule

    def test_the_automoc_edge_waits_for_a_generator(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, monkeypatch)
        env = cxx_env_with_qt(project)
        gen = env.Command(
            target="gen/extra.h",
            source="mk.py",
            command=[sys.executable, "$SOURCE", "$TARGET"],
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.depends(gen)
        content = generate_ninja(project)

        automoc = next(
            line
            for line in content.splitlines()
            if line.startswith("build qt.app/mocs_compilation.cpp:")
        )
        assert "|| " in automoc
        assert "gen/extra.h" in automoc.split("|| ", 1)[1]

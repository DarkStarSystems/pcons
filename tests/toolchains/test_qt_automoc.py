# SPDX-License-Identifier: MIT
"""automoc decides the moc set at build time, not at configure time.

A generator that writes a Q_OBJECT header contributes to the moc set on the
first build. Nothing here asserts a value the build script passed in: every
check reads the emitted ``build.ninja``, the generated ``mocs_compilation.cpp``
or the built program back out.

The end-to-end generator sleeps. A generator that returns at once is scheduled
first often enough that a configure-time scan looks correct by luck.

The last classes drive the build-time tool itself, with a stub moc, for the
answers no end-to-end build produces on demand: a depfile it cannot read, a
moc that fails, a moc output whose source moved ahead of it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt import _automoc
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


STUB_MOC = '''\
# SPDX-License-Identifier: MIT
"""A stand-in for moc: writes what moc writes, or fails where asked to."""

import json
import sys
from pathlib import Path

argv = sys.argv[1:]
failures = {a.split("=", 1)[1] for a in argv if a.startswith("--fail=")}
out = Path(argv[argv.index("-o") + 1])

if "--collect-json" in argv:
    if "collect-json" in failures:
        out.write_text("[ truncated")
        sys.exit(3)
    sidecars = argv[argv.index("-o") + 2 :]
    out.write_text(json.dumps([json.loads(Path(p).read_text()) for p in sidecars]))
    sys.exit(0)

if "moc" in failures:
    sys.exit(2)

source = Path(argv[-1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("// moc " + source.name + "\\n")
Path(argv[argv.index("--dep-file-path") + 1]).write_text(
    out.as_posix() + ": " + source.as_posix() + "\\n"
)
out.with_name(out.name + ".json").write_text(json.dumps({"inputFile": source.name}))
'''


def stub_moc(root: Path, *failures: str) -> list[str]:
    """A moc command line whose named phases exit non-zero."""
    stub = write(root / "stub_moc.py", STUB_MOC)
    return [sys.executable, str(stub), *(f"--fail={phase}" for phase in failures)]


def q_object_tree(root: Path) -> Path:
    """One header worth moc'ing; returns where its moc output belongs."""
    write(
        root / "src" / "thing.h",
        "#pragma once\n#include <QObject>\n"
        "class Thing : public QObject { Q_OBJECT };\n",
    )
    write(root / "src" / "main.cpp", '#include "thing.h"\nint main() { return 0; }\n')
    return root / "build" / "qt.app" / "src" / "moc_thing.cpp"


def write_spec(root: Path, moc: list[str], **extra: object) -> Path:
    gen_dir = root / "build" / "qt.app"
    spec = {
        "version": _automoc.SPEC_VERSION,
        "target": "app",
        "project_root": str(root),
        "gen_dir": str(gen_dir),
        "sources": [str(root / "src" / "main.cpp")],
        "include_dirs": [],
        "no_moc": [],
        "moc": moc,
        "moc_args": [],
        "moc_deps": [],
        "has_includes": True,
        **extra,
    }
    return write(gen_dir / "automoc.json", json.dumps(spec))


def run_automoc(root: Path, spec: Path) -> int:
    gen_dir = root / "build" / "qt.app"
    return _automoc.main(
        [
            "--spec",
            str(spec),
            "-o",
            str(gen_dir / "mocs_compilation.cpp"),
            "--depfile",
            str(gen_dir / "mocs_compilation.cpp.d"),
        ]
    )


class TestDepfileReading:
    """moc's depfile is what the tool re-reads to answer its own freshness."""

    def test_a_depfile_it_cannot_read_lists_no_prerequisites(self, tmp_path):
        assert _automoc._read_depfile(tmp_path) == []

    def test_an_escaped_space_keeps_one_prerequisite_whole(self, tmp_path):
        depfile = write(
            tmp_path / "moc_thing.cpp.d",
            "moc_thing.cpp: \\\n  /src/my\\ dir/a.h \\\n  /src/b.h\n",
        )
        assert _automoc._read_depfile(depfile) == [
            Path("/src/my dir/a.h"),
            Path("/src/b.h"),
        ]

    def test_the_last_prerequisite_needs_no_trailing_newline(self, tmp_path):
        depfile = write(tmp_path / "moc_thing.cpp.d", "moc_thing.cpp: /src/a.h")
        assert _automoc._read_depfile(depfile) == [Path("/src/a.h")]


class TestBuildTimeFreshness:
    """ninja never sees the moc outputs, so the tool decides when they age."""

    def test_moc_re_runs_only_once_its_source_moves_ahead_of_it(self, tmp_path):
        moc_output = q_object_tree(tmp_path)
        spec = write_spec(tmp_path, stub_moc(tmp_path))
        assert run_automoc(tmp_path, spec) == 0
        assert moc_output.is_file()

        header = tmp_path / "src" / "thing.h"
        old = 1_000_000_000
        for path in (spec, header, tmp_path / "src" / "main.cpp"):
            os.utime(path, (old, old))
        os.utime(moc_output.with_name(moc_output.name + ".d"), (old, old))
        os.utime(moc_output, (old + 10, old + 10))
        stamp = moc_output.stat().st_mtime_ns

        assert run_automoc(tmp_path, spec) == 0
        assert moc_output.stat().st_mtime_ns == stamp

        os.utime(header, (old + 20, old + 20))
        assert run_automoc(tmp_path, spec) == 0
        assert moc_output.stat().st_mtime_ns != stamp


class TestMocFailures:
    """What a non-zero moc leaves behind, and what the tool returns."""

    def test_a_failing_moc_names_the_source_and_stops(self, tmp_path, capsys):
        q_object_tree(tmp_path)
        spec = write_spec(tmp_path, stub_moc(tmp_path, "moc"))

        assert run_automoc(tmp_path, spec) == 2
        assert "moc failed on" in capsys.readouterr().err
        assert not (tmp_path / "build" / "qt.app" / "mocs_compilation.cpp").exists()

    def test_a_failing_moc_says_which_source_it_was(self, tmp_path, capsys):
        q_object_tree(tmp_path)
        spec = write_spec(tmp_path, stub_moc(tmp_path, "moc"))

        run_automoc(tmp_path, spec)
        assert "thing.h" in capsys.readouterr().err

    def test_a_failing_collect_json_drops_the_truncated_file(self, tmp_path, capsys):
        q_object_tree(tmp_path)
        metatypes = tmp_path / "build" / "qt.app" / "app_metatypes.json"
        spec = write_spec(
            tmp_path, stub_moc(tmp_path, "collect-json"), metatypes=str(metatypes)
        )

        assert run_automoc(tmp_path, spec) == 3
        assert "moc --collect-json failed" in capsys.readouterr().err
        assert not metatypes.with_name(metatypes.name + ".tmp").exists()
        assert not metatypes.exists()

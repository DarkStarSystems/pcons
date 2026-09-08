# SPDX-License-Identifier: MIT
"""A dependency's generator has to reach its consumers' compiles.

``lib.public.include_dirs`` reaches every target that links ``lib``, so a
generator that fills one of those directories has to be waited for by every
one of those targets too, not only by ``lib``'s own compiles. The API
reference calls ``depends()`` the fluent form of ``add_dependency()``, and
the two must agree about that.

Every assertion here reads the emitted ``build.ninja`` or the build's own
output back out.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pcons.core.errors import DependencyCycleError
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.tools.compile_link import CompileLinkFactory

SLOW_GENERATOR = textwrap.dedent(
    """\
    # SPDX-License-Identifier: MIT
    import os
    import sys
    import time
    from pathlib import Path

    time.sleep(1.5)
    header = Path(sys.argv[1])
    header.parent.mkdir(parents=True, exist_ok=True)
    tmp = header.with_suffix(".tmp")
    tmp.write_text("#pragma once\\n#define GENERATED_ANSWER 42\\n")
    os.replace(tmp, header)
    Path(sys.argv[2]).write_text("done\\n")
    """
)

needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def consumer_project(root: Path, spelling: str) -> Project:
    """A library with a generated public header, and a program linking it.

    *spelling* picks between ``depends`` and ``add_dependency`` for the edge
    that says the library's public headers come from a generator.
    """
    write(root / "mk.py", SLOW_GENERATOR)
    write(
        root / "lib" / "lib.c",
        '#include "generated.h"\nint lib_value(void) { return GENERATED_ANSWER; }\n',
    )
    write(
        root / "app" / "main.c",
        '#include "generated.h"\n#include <stdio.h>\n'
        "int lib_value(void);\n"
        'int main(void) { printf("%d %d\\n", GENERATED_ANSWER, lib_value()); return 0; }\n',
    )

    project = Project("consumer", root_dir=root, build_dir=root / "build")
    env = project.Environment(toolchain="c")
    gen_dir = Path(project.root_dir) / project.build_dir / "gen"
    gen = env.Command(
        target="gen.stamp",
        source="mk.py",
        command=[sys.executable, "$SOURCE", str(gen_dir / "generated.h"), "$TARGET"],
    )
    lib = project.StaticLibrary("shared_bits", env, sources=["lib/lib.c"])
    getattr(lib, spelling)(gen)
    lib.public.include_dirs.append(gen_dir)
    app = project.Program("app", env, sources=["app/main.c"])
    app.link(lib)
    project.resolve()
    return project


def build_files(project: Project) -> Path:
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return Path(project.root_dir) / project.build_dir


def object_of(project: Project, target: str, source: str) -> str:
    """The object file *target* compiles *source* to, named as the toolchain names it.

    The suffix is ``.o`` or ``.obj`` depending on the detected toolchain, so no
    test may spell it out.
    """
    nodes = [
        node
        for node in project.get_target(target).intermediate_nodes
        if node.path.name.startswith(source)
    ]
    assert len(nodes) == 1, [node.path.name for node in nodes]
    return nodes[0].path.name


def edge_for(build_ninja: str, output: str) -> str:
    """The one ``build`` statement in *build_ninja* that writes *output*."""
    for block in build_ninja.split("\nbuild ")[1:]:
        head = block.split("\n", 1)[0]
        if head.split(":")[0].strip().endswith(output):
            return "build " + block.split("\nbuild ")[0]
    raise AssertionError(f"no edge writes {output}")


class TestOrderReachesTheConsumer:
    @pytest.mark.parametrize("spelling", ["depends", "add_dependency"])
    def test_the_consumer_compile_waits_for_the_generator(self, tmp_path, spelling):
        project = consumer_project(tmp_path, spelling)
        build_dir = build_files(project)
        text = (build_dir / "build.ninja").read_text()

        edge = edge_for(text, object_of(project, "app", "main.c"))
        assert "||" in edge, edge
        assert "gen.stamp" in edge.split("||", 1)[1], edge


@needs_ninja
class TestTheBuildConverges:
    @pytest.mark.parametrize("spelling", ["depends", "add_dependency"])
    def test_a_clean_build_succeeds_and_settles_in_one_pass(self, tmp_path, spelling):
        project = consumer_project(tmp_path, spelling)
        build_dir = build_files(project)

        first = subprocess.run(
            ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
        )
        assert first.returncode == 0, first.stderr or first.stdout

        second = subprocess.run(
            ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
        )
        assert second.returncode == 0, second.stderr or second.stdout
        assert "no work to do" in second.stdout


class TestABackEdgeStopsAtTheTarget:
    """A dependency that depends back on its consumer never orders it.

    ``lib.depends(app)`` with ``app.link(lib)`` closes a loop, and the
    resolver says so. The walk that carries a dependency's generators to its
    consumers still has to answer for the shape, because it reads the same
    ``depends()`` list: whatever it collects for ``app``, ``app``'s own link
    output is not part of it. A compile that waited for the program it is
    linked into would be an edge ninja cannot schedule.
    """

    def test_the_resolver_rejects_the_back_edge(self, tmp_path):
        write(tmp_path / "lib" / "lib.c", "int lib_value(void) { return 1; }\n")
        write(
            tmp_path / "app" / "main.c",
            "int lib_value(void);\nint main(void) { return lib_value(); }\n",
        )
        project = Project("backedge", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain="c")
        lib = project.StaticLibrary("bits", env, sources=["lib/lib.c"])
        app = project.Program("app", env, sources=["app/main.c"])
        app.link(lib)
        lib.depends(app)

        with pytest.raises(DependencyCycleError):
            project.resolve()

    def test_a_targets_own_output_stays_out_of_its_compile_ordering(self, tmp_path):
        project = consumer_project(tmp_path, "depends")
        lib = project.get_target("shared_bits")
        app = project.get_target("app")
        lib.depends(app)

        ordering = CompileLinkFactory(project)._ordering_dependency_outputs(app)
        paths = {node.path for node in ordering}

        assert app.output_nodes
        assert paths.isdisjoint({node.path for node in app.output_nodes})
        assert any(path.name == "gen.stamp" for path in paths)

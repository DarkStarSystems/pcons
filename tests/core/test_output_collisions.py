# SPDX-License-Identifier: MIT
"""Two targets resolving to one output file is an error, not a merge (#96).

Node deduplication maps a path to one node, so before this check the
second target's inputs piled onto the first's build edge: an archive
quietly holding both environments' objects.
"""

from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "common.c").write_text("int f(void) { return 1; }\n")
    return src


def _lib(project, env, name, **naming):
    lib = project.StaticLibrary(name, env, sources=["src/common.c"])
    for attr, value in naming.items():
        setattr(lib, attr, value)
    return lib


class TestOutputCollisions:
    def test_two_envs_one_output_name_raises(self, tmp_path, source, gcc_toolchain):
        """The #96 shape: same source, two environments, forced same name."""
        project = Project("p", root_dir=tmp_path)
        env_a = project.Environment(toolchain=gcc_toolchain, name="a")
        env_b = project.Environment(toolchain=gcc_toolchain, name="b")
        _lib(project, env_a, "common-a", output_name="common")
        _lib(project, env_b, "common-b", output_name="common")

        with pytest.raises(PconsError, match="both build") as exc_info:
            project.resolve()

        message = str(exc_info.value)
        assert "common-a" in message
        assert "common-b" in message
        assert "environment-keyed" in message  # both envs are named

    def test_distinct_prefixes_resolve_fine(self, tmp_path, source, gcc_toolchain):
        """The documented recipe: an environment-keyed prefix."""
        project = Project("p", root_dir=tmp_path)
        env_a = project.Environment(toolchain=gcc_toolchain, name="a")
        env_b = project.Environment(toolchain=gcc_toolchain, name="b")
        _lib(project, env_a, "common-a", output_name="common", output_prefix="a/lib")
        _lib(project, env_b, "common-b", output_name="common", output_prefix="b/lib")

        project.resolve()

        paths = {
            node.path for target in project.targets for node in target.output_nodes
        }
        assert len(paths) == 2

    def test_two_commands_one_target_file_raises(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        (tmp_path / "in.txt").write_text("x")
        env.Command(
            target="out.txt",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="one",
        )
        env.Command(
            target="out.txt",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="two",
        )

        with pytest.raises(PconsError, match="both build"):
            project.resolve()

    def test_a_multi_output_command_is_one_producer(self, tmp_path, gcc_toolchain):
        """Several outputs of one edge share a target; that's not a collision."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        (tmp_path / "in.txt").write_text("x")
        env.Command(
            target=["a.txt", "b.txt"],
            source="in.txt",
            command=["tee", "$TARGETS"],
            name="fanout",
        )

        project.resolve()

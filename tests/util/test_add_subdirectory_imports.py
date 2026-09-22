# SPDX-License-Identifier: MIT
"""Tests for ``add_subdirectory(imports=...)``, the way objects travel down."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.util.add_subdirectory import add_subdirectory


def _make_subdir(parent: Path | Project, name: str, content: str) -> Path:
    """Create a subdirectory with a pcons-build.py script."""
    parent = parent.root_dir if isinstance(parent, Project) else parent
    subdir = parent / name
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "pcons-build.py").write_text(content)
    return subdir


READS_IMPORT = (
    "from pcons.core.project import Project\n"
    "project = Project.current()\n"
    "value = project.imports['thing']\n"
)


class TestWhatTravelsDown:
    """Any object the including script hands over arrives intact."""

    def test_a_sibling_target_becomes_a_dependency(self, test_project: Project) -> None:
        """The case string lookups were reached for: b's target, used in c."""
        env = test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "b",
            "from pcons.core.project import Project\n"
            "env = Project.current().default_environment\n"
            "icons = env.Command(\n"
            "    target='icons.h', source='mk.py', command='python3 $SOURCE $TARGET'\n"
            ")\n",
        )
        _make_subdir(
            test_project,
            "c",
            "from pcons.core.project import Project\n"
            "project = Project.current()\n"
            "env = project.default_environment\n"
            "app = env.Command(\n"
            "    target='app.txt', source='app.py', command='python3 $SOURCE $TARGET'\n"
            ")\n"
            "app.depends(project.imports['icons'])\n",
        )

        b = add_subdirectory("b")
        c = add_subdirectory("c", imports={"icons": b.icons})
        test_project.resolve()

        icons_node = b.icons.output_nodes[0]
        assert icons_node in c.app.output_nodes[0].implicit_deps
        assert env is test_project.environments[0]

    def test_a_plain_value_arrives(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", READS_IMPORT)

        ns = add_subdirectory("child", imports={"thing": 42})

        assert ns.value == 42

    def test_an_environment_arrives(self, test_project: Project) -> None:
        mcu = test_project.Environment(toolchain="c", name="mcu")
        _make_subdir(test_project, "child", READS_IMPORT)

        ns = add_subdirectory("child", imports={"thing": mcu})

        assert ns.value is mcu

    def test_the_project_method_forwards_them(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", READS_IMPORT)

        ns = test_project.add_subdirectory("child", imports={"thing": 42})

        assert ns.value == 42

    def test_a_script_with_its_own_project_reads_them(
        self, test_project: Project
    ) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "value = project.imports['thing']\n",
        )

        ns = add_subdirectory("child", imports={"thing": 42})

        assert ns.value == 42


class TestScope:
    """Each inclusion has its own mapping, and nothing else does."""

    def test_a_top_level_project_has_none(self, test_project: Project) -> None:
        assert dict(test_project.imports) == {}

    def test_an_inclusion_without_imports_has_none(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "given = dict(Project.current().imports)\n",
        )

        ns = add_subdirectory("child")

        assert ns.given == {}

    def test_a_grandchild_does_not_inherit(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child/grandchild",
            "from pcons.core.project import Project\n"
            "given = dict(Project.current().imports)\n",
        )
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "given = dict(Project.current().imports)\n"
            "inner = add_subdirectory('grandchild')\n",
        )

        ns = add_subdirectory("child", imports={"thing": 42})

        assert ns.given == {"thing": 42}
        assert ns.inner.given == {}

    def test_a_grandchild_gets_what_its_own_parent_passed(
        self, test_project: Project
    ) -> None:
        _make_subdir(test_project, "child/grandchild", READS_IMPORT)
        _make_subdir(
            test_project,
            "child",
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('grandchild', imports={'thing': 'inner'})\n",
        )

        ns = add_subdirectory("child", imports={"thing": "outer"})

        assert ns.inner.value == "inner"

    def test_two_inclusions_see_their_own(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", READS_IMPORT)

        host = add_subdirectory("child", imports={"thing": "host"})
        mcu = add_subdirectory("child", imports={"thing": "mcu"})

        assert (host.value, mcu.value) == ("host", "mcu")

    def test_the_parent_is_itself_again_afterwards(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", READS_IMPORT)

        add_subdirectory("child", imports={"thing": 42})

        assert dict(test_project.imports) == {}


class TestMapping:
    """``project.imports`` is a read-only mapping that explains itself."""

    def test_a_missing_key_names_what_was_given(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "value = Project.current().imports['icons']\n",
        )

        with pytest.raises(KeyError) as excinfo:
            add_subdirectory("child", imports={"thing": 42, "other": 1})

        message = str(excinfo.value)
        assert "'thing'" in message and "'other'" in message
        assert "imports=" in message

    def test_a_missing_key_with_nothing_given(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "value = Project.current().imports['icons']\n",
        )

        with pytest.raises(KeyError, match="nothing"):
            add_subdirectory("child")

    def test_get_answers_for_a_standalone_script(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "value = Project.current().imports.get('icons', 'standalone')\n",
        )

        ns = add_subdirectory("child")

        assert ns.value == "standalone"

    def test_it_reads_like_a_mapping(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "imports = Project.current().imports\n"
            "seen = (sorted(imports), len(imports), 'thing' in imports)\n",
        )

        ns = add_subdirectory("child", imports={"thing": 42, "other": 1})

        assert ns.seen == (["other", "thing"], 2, True)

    def test_an_entry_cannot_be_assigned(self, test_project: Project) -> None:
        with pytest.raises(TypeError):
            test_project.imports["thing"] = 42  # ty: ignore[unsupported-operator]

    def test_the_mapping_cannot_be_replaced(self, test_project: Project) -> None:
        with pytest.raises(AttributeError):
            test_project.imports = {"thing": 42}  # ty: ignore[invalid-assignment]

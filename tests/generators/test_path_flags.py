# SPDX-License-Identifier: MIT
"""Relativizing paths carried in compiler flags, in both spellings."""

import logging

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def _generate(project: Project, tmp_path):
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (tmp_path / "build" / "build.ninja").read_text()


def _project_with_flags(tmp_path, gcc_toolchain, flags: list[str]) -> Project:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.c").write_text("int main(void){return 0;}\n")
    (tmp_path / "vendor").mkdir(exist_ok=True)

    project = Project("p", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    env.cc.flags.extend(flags)
    project.Program("app", env, sources=["src/main.c"])
    return project


class TestPathFlagRelativization:
    def test_joined_form(self, tmp_path, gcc_toolchain):
        project = _project_with_flags(
            tmp_path, gcc_toolchain, [f"-isystem{tmp_path / 'vendor'}"]
        )

        content = _generate(project, tmp_path)

        assert "-isystem$topdir/vendor" in content
        assert str(tmp_path / "vendor") not in content

    def test_separate_token_form(self, tmp_path, gcc_toolchain):
        """The spelling clang documents, and the one people reach for."""
        project = _project_with_flags(
            tmp_path, gcc_toolchain, ["-isystem", str(tmp_path / "vendor")]
        )

        content = _generate(project, tmp_path)

        assert "$topdir/vendor" in content
        assert str(tmp_path / "vendor") not in content

    def test_relative_argument_is_left_alone(self, tmp_path, gcc_toolchain):
        """-include takes a header *name* resolved through the include path
        (Qt's mkspecs pass "-include arm_acle.h"); rewriting it as a path
        breaks the build."""
        project = _project_with_flags(
            tmp_path, gcc_toolchain, ["-include", "arm_acle.h"]
        )

        content = _generate(project, tmp_path)

        assert "-include arm_acle.h" in content

    def test_path_outside_the_tree_stays_absolute(self, tmp_path, gcc_toolchain):
        outside = tmp_path.parent / "elsewhere-sdk"
        project = _project_with_flags(
            tmp_path, gcc_toolchain, ["-isystem", str(outside)]
        )

        content = _generate(project, tmp_path)

        assert str(outside) in content

    def test_unknown_flag_with_in_tree_path_warns(
        self, tmp_path, gcc_toolchain, caplog
    ):
        project = _project_with_flags(
            tmp_path, gcc_toolchain, [f"-fprofile-dir={tmp_path / 'prof'}"]
        )

        with caplog.at_level(logging.WARNING):
            _generate(project, tmp_path)

        assert "not relocatable" in caplog.text

    def test_recognized_flags_do_not_warn(self, tmp_path, gcc_toolchain, caplog):
        project = _project_with_flags(
            tmp_path, gcc_toolchain, [f"-I{tmp_path / 'vendor'}"]
        )

        with caplog.at_level(logging.WARNING):
            _generate(project, tmp_path)

        assert "not relocatable" not in caplog.text

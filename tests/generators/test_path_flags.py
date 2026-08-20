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

    def test_equals_form(self, tmp_path, gcc_toolchain):
        """`--sysroot=<path>` joins flag to argument with an "=".

        Split naively on the flag name, the argument reads as "=/opt/sdk" --
        not absolute, because of the leading "=" -- and gets relativized into
        `--sysroot../=/opt/sdk`, which the compiler rejects outright. Only the
        WASI job caught this, and only because it links against a real SDK.
        """
        sdk = tmp_path.parent / "wasi-sysroot"
        project = _project_with_flags(tmp_path, gcc_toolchain, [f"--sysroot={sdk}"])

        content = _generate(project, tmp_path)

        assert f"--sysroot={sdk}" in content
        assert "--sysroot../" not in content

    def test_equals_form_with_in_tree_path(self, tmp_path, gcc_toolchain):
        """The "=" survives when the path *is* rewritten."""
        project = _project_with_flags(
            tmp_path, gcc_toolchain, [f"--sysroot={tmp_path / 'sysroot'}"]
        )

        content = _generate(project, tmp_path)

        assert "--sysroot=$topdir/sysroot" in content

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


class TestAuxiliaryInputRelativization:
    """A source-tree .manifest reaches the linker from the build directory."""

    def test_source_tree_manifest_renders_execution_relative(self, tmp_path):
        from pcons.toolchains.msvc import (
            MsvcCompiler,
            MsvcCxxCompiler,
            MsvcLinker,
            MsvcToolchain,
        )

        toolchain = MsvcToolchain()
        toolchain._tools = {
            "cc": MsvcCompiler(),
            "cxx": MsvcCxxCompiler(),
            "link": MsvcLinker(),
        }

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.c").write_text("int main(void){return 0;}\n")
        (tmp_path / "windows").mkdir()
        (tmp_path / "windows" / "app.manifest").write_text("<assembly/>\n")

        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=toolchain)
        app = project.Program("app", env, sources=["src/main.c"])
        app.add_sources(["windows/app.manifest"])

        content = _generate(project, tmp_path)

        assert "/MANIFESTINPUT:$topdir/windows/app.manifest" in content
        assert "/MANIFESTINPUT:windows" not in content

    def test_generated_manifest_stays_build_dir_local(self, tmp_path):
        """An env.Command-generated manifest lives in the build dir; its
        flag must say so, not $topdir (see 20_windows_manifest)."""
        from pcons.toolchains.msvc import (
            MsvcCompiler,
            MsvcCxxCompiler,
            MsvcLinker,
            MsvcToolchain,
        )

        toolchain = MsvcToolchain()
        toolchain._tools = {
            "cc": MsvcCompiler(),
            "cxx": MsvcCxxCompiler(),
            "link": MsvcLinker(),
        }
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.c").write_text("int main(void){return 0;}\n")

        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=toolchain)
        manifest = env.Command(
            target="app.manifest",
            source=None,
            command=["python", "-c", "pass"],
            name="gen_manifest",
        )
        app = project.Program("app", env, sources=["src/main.c"])
        app.add_sources([manifest])

        content = _generate(project, tmp_path)

        assert "/MANIFESTINPUT:app.manifest" in content
        assert "/MANIFESTINPUT:$topdir" not in content

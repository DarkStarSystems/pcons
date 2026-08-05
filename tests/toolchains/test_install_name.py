# SPDX-License-Identifier: MIT
"""Tests for automatic install_name / SONAME on shared libraries."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pcons.core.subst import TargetPath
from pcons.core.target import Target
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain

# ── target.set_option() / target.get_option() ──────────────────────────────────────────────


class TestTargetSetGet:
    def test_default_is_none(self, test_project):  # noqa: F811
        t = Target("lib", target_type="shared_library")
        assert t.get_option("install_name") is None

    def test_set_and_get(self, test_project):  # noqa: F811
        t = Target("lib", target_type="shared_library")
        t.set_option("install_name", "@rpath/libcustom.dylib")
        assert t.get_option("install_name") == "@rpath/libcustom.dylib"

    def test_set_returns_self(self, test_project):  # noqa: F811
        t = Target("lib", target_type="shared_library")
        assert t.set_option("install_name", "foo") is t

    def test_get_with_default(self, test_project):  # noqa: F811
        t = Target("lib", target_type="shared_library")
        assert t.get_option("install_name", "fallback") == "fallback"

    def test_undeclared_option_raises(self, test_project):  # noqa: F811
        """A typo used to be stored and read by nothing."""
        t = Target("lib", target_type="shared_library")
        with pytest.raises(ValueError, match="Unknown target option 'install_names'"):
            t.set_option("install_names", "@rpath/libcustom.dylib")


# ── Toolchain.get_link_flags_for_target ───────────────────────────────────────


def _make_shared_target(name: str = "foo") -> Target:
    return Target(name, target_type="shared_library")


class TestGccInstallName:
    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_default_install_name(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        # A marker, not a formatted name: the filename reaches the command
        # through a per-edge variable, so every shared library shares one rule.
        assert flags == [TargetPath(basename=True, prefix="-Wl,-install_name,@rpath/")]

    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_explicit_install_name(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "/usr/local/lib/libfoo.2.dylib")
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == ["-Wl,-install_name,/usr/local/lib/libfoo.2.dylib"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_disabled_install_name(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "")
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == []

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_default_soname(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == [TargetPath(basename=True, prefix="-Wl,-soname,")]

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_explicit_soname(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "libfoo.so.2")
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == ["-Wl,-soname,libfoo.so.2"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_disabled_soname(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "")
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == []

    def test_program_gets_no_flags(self, test_project):  # noqa: F811
        tc = GccToolchain()
        target = Target("app", target_type="program")
        flags = tc.get_link_flags_for_target(target, "app", [])
        assert flags == []

    def test_static_library_gets_no_flags(self, test_project):  # noqa: F811
        tc = GccToolchain()
        target = Target("lib", target_type="static_library")
        flags = tc.get_link_flags_for_target(target, "libfoo.a", [])
        assert flags == []


class TestLlvmInstallName:
    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_default(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = LlvmToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        # A marker, not a formatted name: the filename reaches the command
        # through a per-edge variable, so every shared library shares one rule.
        assert flags == [TargetPath(basename=True, prefix="-Wl,-install_name,@rpath/")]


class TestSharedLibrariesShareOneRule:
    """The reason the flag is a marker: N shared libraries, one link rule.

    Formatting the filename into the flag gives every library a private copy
    of the whole link rule, since a ninja rule is identified by its command
    text. On Linux that is unavoidable for the user -- a shipped .so cannot
    drop its SONAME the way a macOS bundle can drop an install name.
    """

    def _ninja(self, tmp_path, gcc_toolchain, count=5, configure=None):
        from pcons.core.project import Project
        from pcons.generators.generator import BaseGenerator
        from pcons.generators.ninja import NinjaGenerator

        (tmp_path / "a.c").write_text("int f(void){return 1;}\n")
        project = Project("libs", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        for i in range(count):
            lib = project.SharedLibrary(f"L{i}", env, sources=["a.c"])
            if configure:
                configure(i, lib)
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    @staticmethod
    def _link_rules(content):
        return [ln for ln in content.splitlines() if ln.startswith("rule link_shared")]

    def test_many_libraries_share_one_rule(self, tmp_path, gcc_toolchain):
        content = self._ninja(tmp_path, gcc_toolchain, count=5)

        assert len(self._link_rules(content)) == 1

    def test_each_library_names_itself(self, tmp_path, gcc_toolchain):
        content = self._ninja(tmp_path, gcc_toolchain, count=3)

        names = {
            ln.split("=", 1)[1].strip()
            for ln in content.splitlines()
            if ln.strip().startswith("out_basename =")
        }
        assert names == {"libL0.dylib", "libL1.dylib", "libL2.dylib"} or names == {
            "libL0.so",
            "libL1.so",
            "libL2.so",
        }

    def test_the_output_flag_is_untouched(self, tmp_path, gcc_toolchain):
        """The basename marker must not carry an index: one indexed target
        marker puts every marker in the command into $target_N mode, and a
        shared-library edge defines no target_N, so `-o` would go empty."""
        content = self._ninja(tmp_path, gcc_toolchain, count=1)

        rule = self._link_rules(content)[0]
        command = next(
            ln
            for ln in content.splitlines()[content.splitlines().index(rule) :]
            if "command =" in ln
        )
        assert "-o $out " in command
        assert "$target_" not in command

    def test_out_basename_is_on_the_build_statement(self, tmp_path, gcc_toolchain):
        """Not inside the rule block, where it would be one shared value."""
        content = self._ninja(tmp_path, gcc_toolchain, count=1)
        lines = content.splitlines()
        var = next(i for i, ln in enumerate(lines) if "out_basename =" in ln)
        owner = next(
            lines[i]
            for i in range(var, -1, -1)
            if lines[i] and not lines[i].startswith((" ", "\t"))
        )

        assert owner.startswith("build ")

    def test_a_disabled_library_gets_its_own_rule(self, tmp_path, gcc_toolchain):
        """Its command genuinely differs -- it carries no install name."""

        def configure(i, lib):
            if i == 0:
                lib.set_option("install_name", "")

        content = self._ninja(tmp_path, gcc_toolchain, count=3, configure=configure)

        assert len(self._link_rules(content)) == 2

    def test_a_hand_written_flag_wins(self, test_project):  # noqa: F811
        """`existing_flags` is how the automatic one steps aside; a marker
        can't be compared against the caller's strings."""
        from pcons.toolchains.gcc import GccToolchain

        target = _make_shared_target()
        flags = GccToolchain().get_link_flags_for_target(
            target, "libfoo.dylib", ["-Wl,-install_name,@rpath/mine.dylib"]
        )

        assert flags == []

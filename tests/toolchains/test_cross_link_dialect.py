# SPDX-License-Identifier: MIT
"""A cross build links the way its target's linker wants, not the host's.

An Android shared library linked on a Mac takes a link group and a version
script; one built for macOS from Linux takes no group and a symbol list. The host is pinned by patching where env.target reads it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pcons.core.environment import Environment
from pcons.core.subst import PathToken
from pcons.core.target import Target
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain
from pcons.toolchains.presets import android, linux_cross, target_platform_for_triple

MAC_HOST = target_platform_for_triple("arm64-apple-darwin")
LINUX_HOST = target_platform_for_triple("x86_64-linux-gnu")


def _env(host, preset, toolchain) -> Environment:
    """An environment with the tools a cross preset repoints, retargeted by
    *preset* while the host reads as *host*."""
    with patch("pcons.configure.platform.get_platform", return_value=host):
        env = Environment()
        for tool, cmd in (("cc", "cc"), ("cxx", "c++"), ("link", "cc"), ("ar", "ar")):
            config = env.add_tool(tool)
            config.set("cmd", cmd)
            config.set("flags", [])
            config.set("defines", [])
        env._toolchain = toolchain
        env.apply_cross_preset(preset)
    return env


class TestAndroidFromAMac:
    @pytest.fixture
    def env(self, test_project):  # noqa: F811
        return _env(MAC_HOST, android(ndk="/fake/ndk", api=35), GccToolchain())

    def test_archives_in_a_cycle_are_grouped(self, env):
        archive = PathToken(path="liba.a", path_type="build")
        group = GccToolchain().link_group_tokens([archive], env=env)
        assert group == ["-Wl,--start-group", archive, "-Wl,--end-group"]

    def test_shared_library_compiles_pic(self, env):
        flags = GccToolchain().get_compile_flags_for_target_type(
            "shared_library", env=env
        )
        assert flags == ["-fPIC"]

    def test_exported_symbols_take_a_version_script(self, env):
        target = Target("plug", env=env, target_type="shared_library")
        target.set_option("exported_symbols", ["OfxGetPlugin"])
        flags = GccToolchain().get_link_flags_for_target(target, "libplug.so", [])
        prefixes = [f.prefix for f in flags if isinstance(f, PathToken)]
        assert "-Wl,--version-script=" in prefixes
        assert not any(p.startswith("-Wl,-exported_symbols_list") for p in prefixes)


class TestMacosFromLinux:
    @pytest.fixture
    def env(self, test_project):  # noqa: F811
        # clang retargets by flag; GCC would need a cross binary.
        return _env(
            LINUX_HOST, linux_cross(triple="arm64-apple-darwin"), LlvmToolchain()
        )

    def test_archives_in_a_cycle_need_no_group(self, env):
        archive = PathToken(path="liba.a", path_type="build")
        assert LlvmToolchain().link_group_tokens([archive], env=env) is None

    def test_no_pic_flag(self, env):
        assert (
            LlvmToolchain().get_compile_flags_for_target_type("shared_library", env=env)
            == []
        )

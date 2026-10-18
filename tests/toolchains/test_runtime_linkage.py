# SPDX-License-Identifier: MIT
"""env.set_runtime_linkage(): each toolchain links its own runtimes that way.

Every toolchain and combination is in the mixed-toolchain snapshot; these
check the setting itself."""

from __future__ import annotations

import pytest

from pcons.core.project import Project
from pcons.toolchains.unix import UnixToolchain


@pytest.fixture
def env(tmp_path, gcc_toolchain, monkeypatch):
    # On macOS, "g++" is usually Apple clang.
    monkeypatch.setattr(UnixToolchain, "_compiler_is_apple_clang", lambda self: False)
    return Project("rt", root_dir=tmp_path).Environment(toolchain=gcc_toolchain)


STATIC = ["-static-libgcc", "-static-libstdc++"]


def test_the_shared_runtimes_are_the_default(env) -> None:
    assert env.runtime_linkage == "shared"
    assert env.link.flags == []


def test_static_and_back(env) -> None:
    env.set_runtime_linkage("static")
    assert env.runtime_linkage == "static"
    assert env.link.flags == STATIC
    env.set_variant("debug")
    assert env.link.flags == STATIC
    env.set_runtime_linkage("shared")
    assert env.link.flags == []


def test_a_clone_keeps_it(env) -> None:
    env.set_runtime_linkage("static")
    clone = env.clone()
    assert clone.runtime_linkage == "static"
    assert clone.link.flags == STATIC


def test_explain_attributes_the_flags(env) -> None:
    env.set_runtime_linkage("static")
    assert [p.name for p in env.applied_presets if p.category == "runtime"][-1] == (
        "static"
    )


def test_an_unknown_linkage_is_an_error(env) -> None:
    with pytest.raises(ValueError, match="'shared' or 'static'"):
        env.set_runtime_linkage("dynamic")


def _host(monkeypatch, os_name: str):
    """LLVM's toolchain on a pinned host, its compiler probes pinned too."""
    from pcons.configure import platform as platform_mod
    from pcons.toolchains import unix as unix_mod
    from pcons.tools.toolchain import toolchain_registry

    windows = os_name == "windows"
    monkeypatch.setattr(
        platform_mod,
        "_platform",
        platform_mod.Platform(
            os=os_name,
            arch="x86_64",
            is_64bit=True,
            exe_suffix=".exe" if windows else "",
            shared_lib_suffix=".dll" if windows else ".so",
            shared_lib_prefix="" if windows else "lib",
            static_lib_suffix=".lib" if windows else ".a",
            static_lib_prefix="" if windows else "lib",
            object_suffix=".obj" if windows else ".o",
        ),
    )
    monkeypatch.setattr(unix_mod, "target_triple", lambda compiler: None)
    monkeypatch.setattr(UnixToolchain, "_compiler_is_apple_clang", lambda self: False)
    entry = toolchain_registry.get("llvm")
    assert entry is not None
    return entry.create_toolchain()


def test_a_cross_target_decides_the_runtime(monkeypatch, tmp_path) -> None:
    """Realized again for the platform the cross preset builds for, not the
    one before it: clang can't link its runtime static for macOS."""
    from pcons.toolchains.presets import CrossPreset

    toolchain = _host(monkeypatch, "linux")
    env = Project("rt", root_dir=tmp_path).Environment(toolchain=toolchain)
    env.set_runtime_linkage("static")
    assert env.link.flags == STATIC
    mac = CrossPreset(name="mac", arch="arm64", triple="arm64-apple-macos")
    with pytest.raises(ValueError, match="Apple"):
        env.apply_cross_preset(mac)


def test_the_msvc_runtime_is_for_an_msvc_target(monkeypatch, tmp_path) -> None:
    """On Windows, clang for the MSVC ABI cross-building for Linux gets no
    MSVC C runtime flags, which GNU ld would reject."""
    from pcons.toolchains.presets import linux_cross

    toolchain = _host(monkeypatch, "windows")
    env = Project("rt", root_dir=tmp_path).Environment(toolchain=toolchain)
    assert "-fms-runtime-lib=dll" in env.cc.flags
    env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))
    assert not any("fms-runtime-lib" in str(f) for f in env.cc.flags)
    assert not any("NODEFAULTLIB" in str(f) for f in env.link.flags)

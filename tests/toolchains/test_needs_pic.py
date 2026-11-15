# SPDX-License-Identifier: MIT
"""Which targets compile as position-independent code on which platforms."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pcons.configure.platform import Platform
from pcons.toolchains.gnu_common import needs_pic


def _platform(os_name: str) -> Platform:
    return Platform(
        os=os_name,
        arch="x86_64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".so",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )


@pytest.mark.parametrize("os_name", ["linux", "android", "freebsd"])
def test_libraries_are_pic_where_shared_libraries_need_it(os_name) -> None:
    """A static or object library may go into a shared library, so it is
    PIC by default, as Meson builds them; a program isn't."""
    platform = _platform(os_name)
    for target_type in ("shared_library", "static_library", "object"):
        assert needs_pic(target_type, platform), target_type
    assert not needs_pic("program", platform)


def test_pic_false_opts_a_static_library_out_but_not_a_shared_one() -> None:
    linux = _platform("linux")
    assert not needs_pic("static_library", linux, pic=False)
    assert not needs_pic("object", linux, pic=False)
    assert needs_pic("shared_library", linux, pic=False)


@pytest.mark.parametrize("os_name", ["darwin", "windows", "none", "wasi"])
def test_nothing_is_pic_elsewhere(os_name) -> None:
    """macOS code is PIC already, Windows doesn't use it, and a bare-metal
    or wasm target has no loader to need it."""
    platform = _platform(os_name)
    for target_type in ("shared_library", "static_library", "object", "program"):
        assert not needs_pic(target_type, platform), target_type


def test_swift_libraries_get_no_pic_flag() -> None:
    """swiftc's code is always position-independent, and it rejects
    ``-fPIC``."""
    from pcons.toolchains.swift import SwiftToolchain

    linux = SimpleNamespace(target=_platform("linux"))
    for target_type in ("shared_library", "static_library", "object"):
        flags = SwiftToolchain().get_compile_flags_for_target_type(target_type, linux)  # type: ignore[arg-type]
        assert flags == [], target_type

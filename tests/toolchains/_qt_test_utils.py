# SPDX-License-Identifier: MIT
"""Shared helpers for the Qt toolchain tests.

All Qt unit tests run without a Qt installation: the qt toolchain is
constructed with fake tool paths and the generated build.ninja is
inspected as text.

The fake Android SDK is the exception that has to exist on disk: the
deployment settings default ``sdkBuildToolsRevision`` to the highest
revision installed, which is a directory listing. It is one fixed tree of
empty directories, shared by every worker and every run.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcons.configure.platform import get_platform
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt.finder import QtPackage
from pcons.toolchains.qt.toolchain import QtTool, QtToolchain

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project

FAKE_TOOLS = {
    "moc": "/fake/bin/moc",
    "uic": "/fake/bin/uic",
    "rcc": "/fake/bin/rcc",
    "qmltyperegistrar": "/fake/bin/qmltyperegistrar",
    "lrelease": "/fake/bin/lrelease",
    "lupdate": "/fake/bin/lupdate",
}


def fake_qt_toolchain(**tool_paths: str) -> QtToolchain:
    """A configured qt toolchain with fake (or given) tool paths."""
    toolchain = QtToolchain()
    toolchain._tools = {"qt": QtTool(**{**FAKE_TOOLS, **tool_paths})}
    toolchain._configured = True
    return toolchain


def qt_only_env(project: Project) -> Environment:
    """Environment whose primary toolchain is the fake qt toolchain."""
    return project.Environment(toolchain=fake_qt_toolchain())


def cxx_env_with_qt(project: Project, name: str | None = None) -> Environment:
    """A real C++ toolchain env with the fake qt toolchain added.

    Skips the calling test when no C++ compiler is available.
    """
    from pcons.toolchains import find_c_toolchain

    try:
        cxx_toolchain = find_c_toolchain()
    except RuntimeError:
        pytest.skip("no C++ toolchain available")
    env = project.Environment(toolchain=cxx_toolchain, name=name)
    env.add_toolchain(fake_qt_toolchain())
    return env


ANDROID_NDK = "/fake/ndk"

#: The one build-tools revision the fake SDK has installed.
ANDROID_BUILD_TOOLS = "37.0.0"


def _fake_android_sdk() -> str:
    """An SDK root that exists, holding one build-tools revision."""
    root = Path(tempfile.gettempdir()) / "pcons-fake-android-sdk"
    (root / "build-tools" / ANDROID_BUILD_TOOLS).mkdir(parents=True, exist_ok=True)
    return str(root)


ANDROID_SDK = _fake_android_sdk()

QT_HOST_TOOLS = ("rcc", "qmlimportscanner", "qmldom")


def android_env(
    arch: str = "arm64-v8a", *, sdk: str | None = ANDROID_SDK
) -> Environment:
    """An Android cross environment, with no real NDK behind it.

    Built by hand rather than through ``find_c_toolchain``, so it runs
    everywhere: nothing here compiles anything.
    """
    from pcons.core.environment import Environment as Env
    from pcons.toolchains.llvm import LlvmToolchain
    from pcons.toolchains.presets import android

    env = Env()
    for name, cmd in (
        ("cc", "clang"),
        ("cxx", "clang++"),
        ("link", "clang"),
        ("ar", "ar"),
    ):
        tool = env.add_tool(name)
        tool.set("cmd", cmd)
        tool.set("flags", [])
    env._toolchain = LlvmToolchain()
    env.apply_cross_preset(android(ndk=ANDROID_NDK, arch=arch, api=35, sdk=sdk))
    return env


def fake_qt_for_android(root: Path, tools: Sequence[str] = QT_HOST_TOOLS) -> QtPackage:
    """A Qt for Android whose tools sit in the host Qt beside it.

    That split is what ``qtpaths --query`` reports for a Qt for Android: the
    prefix is the Android install, QT_HOST_BINS and QT_HOST_LIBEXECS are the
    host one, and the Android install ships no runnable rcc at all.
    """
    host = root / "Qt" / "6.11.1" / "gcc_64"
    (host / "bin").mkdir(parents=True, exist_ok=True)
    (host / "libexec").mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if get_platform().is_windows else ""
    for tool in tools:
        (host / "libexec" / f"{tool}{suffix}").write_text("")
    prefix = root / "Qt" / "6.11.1" / "android_arm64_v8a"
    prefix.mkdir(parents=True, exist_ok=True)
    return QtPackage(
        version="6.11.1",
        prefix=prefix,
        bin_dir=host / "bin",
        libexec_dir=host / "libexec",
        is_framework=False,
        found_via="qtpaths",
        modules={},
        module_factory=lambda name: None,
    )


def generate_ninja(project: Project) -> str:
    """Generate build.ninja and return its content (slashes normalized).

    Read where the generator wrote it rather than through the working
    directory, which a test is free to move.
    """
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    build_dir = project.build_dir
    if not build_dir.is_absolute():
        build_dir = Path(project.root_dir) / build_dir
    return (build_dir / "build.ninja").read_text().replace("\\", "/")


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

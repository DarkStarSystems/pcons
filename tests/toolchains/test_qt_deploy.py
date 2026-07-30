# SPDX-License-Identifier: MIT
"""Tests for QtDeploy (no Qt installation required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import pcons.toolchains.qt.deploy as qt_deploy
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt.toolchain import QtTool, QtToolchain


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    return Project("deploytest", root_dir=tmp_path, build_dir=tmp_path / "build")


def _env_with_qt(project):
    from pcons.toolchains import find_c_toolchain

    try:
        cxx = find_c_toolchain()
    except RuntimeError:
        pytest.skip("no C++ toolchain available")
    env = project.Environment(toolchain=cxx)
    qt_toolchain = QtToolchain()
    qt_toolchain._tools = {"qt": QtTool(moc="/fake/bin/moc")}
    qt_toolchain._configured = True
    env.add_toolchain(qt_toolchain)
    return env


def _generate(project) -> str:
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (project.build_dir / "build.ninja").read_text().replace("\\", "/")


def _patch_platform(**flags):
    defaults = {"is_macos": False, "is_windows": False, "is_linux": False}
    defaults.update(flags)
    return patch.object(qt_deploy, "get_platform", lambda: SimpleNamespace(**defaults))


class TestQtDeploy:
    def test_macos_needs_bundle(self, project):
        env = _env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_macos=True):
            with pytest.raises(ValueError, match="bundle="):
                project.QtDeploy("deploy", env, app=app)

    def test_macos_command_shape(self, project):
        env = _env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_macos=True):
            project.QtDeploy("deploy", env, app=app, bundle="App.app")
        content = _generate(project)
        assert "macdeployqt" in content
        assert "App.app" in content
        assert "build deploy: phony" in content
        # Utility target: excluded from 'all' and the default line.
        for line in content.splitlines():
            if line.startswith("build all: phony") or line.startswith("default "):
                assert "deploy" not in line

    def test_windows_command_shape(self, project):
        env = _env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_windows=True):
            project.QtDeploy("deploy", env, app=app, deploy_dir="dist/deploy")
        content = _generate(project)
        assert "windeployqt" in content
        assert "--dir dist/deploy" in content

    def test_linux_unsupported(self, project):
        env = _env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_linux=True):
            with pytest.raises(RuntimeError, match="linuxdeploy"):
                project.QtDeploy("deploy", env, app=app)

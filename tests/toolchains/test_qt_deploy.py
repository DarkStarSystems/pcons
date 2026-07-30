# SPDX-License-Identifier: MIT
"""Tests for QtDeploy (no Qt installation required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import pcons.toolchains.qt.deploy as qt_deploy
from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt, generate_ninja


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    return Project("deploytest", root_dir=tmp_path, build_dir=tmp_path / "build")


def _patch_platform(**flags):
    defaults = {"is_macos": False, "is_windows": False, "is_linux": False}
    defaults.update(flags)
    return patch.object(qt_deploy, "get_platform", lambda: SimpleNamespace(**defaults))


class TestQtDeploy:
    def test_macos_needs_bundle(self, project):
        env = cxx_env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_macos=True):
            with pytest.raises(ValueError, match="bundle="):
                project.QtDeploy("deploy", env, app=app)

    def test_macos_command_shape(self, project):
        env = cxx_env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_macos=True):
            project.QtDeploy("deploy", env, app=app, bundle="App.app")
        content = generate_ninja(project)
        assert "macdeployqt" in content
        assert "App.app" in content
        assert "build deploy: phony" in content
        # Utility target: excluded from 'all' and the default line.
        for line in content.splitlines():
            if line.startswith("build all: phony") or line.startswith("default "):
                assert "deploy" not in line

    def test_windows_command_shape(self, project):
        env = cxx_env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_windows=True):
            project.QtDeploy("deploy", env, app=app, deploy_dir="dist/deploy")
        content = generate_ninja(project)
        assert "windeployqt" in content
        assert "--dir dist/deploy" in content

    def test_linux_unsupported(self, project):
        env = cxx_env_with_qt(project)
        app = project.Program("app", env, sources=["src/main.cpp"])
        with _patch_platform(is_linux=True):
            with pytest.raises(RuntimeError, match="linuxdeploy"):
                project.QtDeploy("deploy", env, app=app)

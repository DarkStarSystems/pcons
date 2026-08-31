# SPDX-License-Identifier: MIT
"""Staging what androiddeployqt reads.

androiddeployqt reads the application out of ``<output>/libs/<abi>/`` and
does not put it there. Measured against Qt 6.11.1: with nothing staged it
exits 0 and reports success, having packaged no application at all. So the
tests here are about a failure that says nothing when it happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.qt.android import deployment_settings
from pcons.toolchains.qt.apk import stage_application_library

from ._qt_test_utils import android_env, fake_qt_for_android


@pytest.fixture
def app_project(test_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (Path(test_project.root_dir) / "app.c").write_text("int f(void){return 0;}\n")
    return test_project


@pytest.fixture
def found_qt(monkeypatch, tmp_path):
    qt = fake_qt_for_android(tmp_path / "qt")
    monkeypatch.setattr(
        "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: qt
    )
    return qt


def _app(project, env, name: str = "myapp"):
    return project.SharedLibrary(name, env, sources=["app.c"])


def _staged_path(target) -> Path:
    return Path(target.output_nodes[0].path)


class TestWhereTheLibraryGoes:
    def test_it_lands_under_libs_slash_abi(self, app_project) -> None:
        env = android_env()
        staged = stage_application_library(app_project, env, app=_app(app_project, env))
        app_project.resolve()

        assert _staged_path(staged) == Path(
            "myapp/libs/arm64-v8a/libmyapp_arm64-v8a.so"
        )

    def test_the_abi_decides_the_directory_and_the_suffix(self, app_project) -> None:
        env = android_env("x86_64")
        staged = stage_application_library(app_project, env, app=_app(app_project, env))
        app_project.resolve()

        assert _staged_path(staged) == Path("myapp/libs/x86_64/libmyapp_x86_64.so")

    def test_an_explicit_output_directory_is_honoured(self, app_project) -> None:
        env = android_env()
        staged = stage_application_library(
            app_project, env, app=_app(app_project, env), output="package"
        )
        app_project.resolve()

        assert _staged_path(staged) == Path(
            "package/libs/arm64-v8a/libmyapp_arm64-v8a.so"
        )

    def test_two_applications_in_one_environment_do_not_collide(
        self, app_project
    ) -> None:
        """androiddeployqt owns its whole output directory and names the
        package after it, so one per application."""
        env = android_env()
        first = stage_application_library(
            app_project, env, app=_app(app_project, env, "one")
        )
        second = stage_application_library(
            app_project, env, app=_app(app_project, env, "two")
        )
        app_project.resolve()

        assert _staged_path(first) == Path("one/libs/arm64-v8a/libone_arm64-v8a.so")
        assert _staged_path(second) == Path("two/libs/arm64-v8a/libtwo_arm64-v8a.so")


class TestItAgreesWithTheSettingsFile:
    """The name is written once. Two spellings of it is the bug this guards:
    androiddeployqt looks for exactly ``lib<application-binary>_<abi>.so``,
    logs an ``llvm-readobj`` error when it is not there, and exits 0."""

    def test_the_staged_name_is_the_one_the_settings_ask_for(
        self, app_project, found_qt
    ) -> None:
        env = android_env()
        app = _app(app_project, env)

        settings = deployment_settings(app_project, env, app=app)
        staged = stage_application_library(app_project, env, app=app)
        app_project.resolve()

        wanted = f"lib{settings['application-binary']}_{settings['abi']}.so"
        assert _staged_path(staged).name == wanted

    def test_the_directory_is_the_abi_the_settings_name(
        self, app_project, found_qt
    ) -> None:
        env = android_env("armeabi-v7a")
        app = _app(app_project, env)

        settings = deployment_settings(app_project, env, app=app)
        staged = stage_application_library(app_project, env, app=app)
        app_project.resolve()

        assert _staged_path(staged).parent.name == settings["abi"]


class TestItIsARealBuildEdge:
    STAGED = "myapp/libs/arm64-v8a/libmyapp_arm64-v8a.so"

    def _ninja(self, project, env) -> str:
        from ._qt_test_utils import generate_ninja

        stage_application_library(project, env, app=_app(project, env))
        return generate_ninja(project)

    def test_the_copy_has_the_application_as_its_input(self, app_project) -> None:
        content = self._ninja(app_project, android_env())

        edge = next(
            line
            for line in content.splitlines()
            if line.startswith(f"build {self.STAGED}:")
        )
        assert "install_copycmd" in edge
        assert edge.endswith("libmyapp.so")

    def test_a_plain_ninja_run_makes_it(self, app_project) -> None:
        """Not an install-only step: nobody should have to ask for the install
        target to get a package that has the application in it."""
        content = self._ninja(app_project, android_env())

        default = next(
            line for line in content.splitlines() if line.startswith("build all: phony")
        )
        assert self.STAGED in default

    def test_it_is_a_target(self, app_project) -> None:
        env = android_env()

        staged = stage_application_library(app_project, env, app=_app(app_project, env))

        assert staged.name == "myapp-apk-lib"


class TestWhatItRefuses:
    def test_an_environment_that_was_never_retargeted(self, app_project) -> None:
        env = Environment()

        with pytest.raises(ValueError, match="retargeted with"):
            stage_application_library(app_project, env, app=_app(app_project, env))

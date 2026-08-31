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
from pcons.toolchains.qt.android import android_deployment_settings, deployment_settings
from pcons.toolchains.qt.apk import android_apk, stage_application_library

from ._qt_test_utils import QT_HOST_TOOLS, android_env, fake_qt_for_android


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


@pytest.fixture
def deployable(monkeypatch, tmp_path):
    """A Qt install that has androiddeployqt, in front of the settings writer."""
    qt = fake_qt_for_android(tmp_path / "qt", (*QT_HOST_TOOLS, "androiddeployqt"))
    monkeypatch.setattr(
        "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: qt
    )
    return qt


def _apk(project, env, app, **kwargs):
    settings = android_deployment_settings(project, env, app=app)
    return android_apk(project, env, app=app, settings=settings, **kwargs)


DEBUG_APK = "myapp/build/outputs/apk/debug/myapp-debug.apk"


def _ninja(project, env, app, **kwargs) -> str:
    from ._qt_test_utils import generate_ninja

    _apk(project, env, app, **kwargs)
    return generate_ninja(project)


def _edge(content: str, output: str) -> str:
    return next(
        line for line in content.splitlines() if line.startswith(f"build {output}:")
    )


class TestWhereThePackageGoes:
    """Gradle names the package after the output directory, not after the
    application, and puts it under a directory named after the variant. Both
    measured against Qt 6.11.1 by listing what a real run wrote, not by
    reading androiddeployqt's own closing message, which prints a path with
    no variant directory that nothing ever writes."""

    def test_the_debug_package(self, app_project, deployable) -> None:
        env = android_env()

        content = _ninja(app_project, env, _app(app_project, env))

        assert _edge(content, DEBUG_APK)

    def test_the_release_package_says_it_is_unsigned(
        self, app_project, deployable
    ) -> None:
        env = android_env()

        content = _ninja(app_project, env, _app(app_project, env), release=True)

        assert _edge(
            content, "myapp/build/outputs/apk/release/myapp-release-unsigned.apk"
        )

    def test_the_output_directory_names_the_package(
        self, app_project, deployable
    ) -> None:
        env = android_env()

        content = _ninja(app_project, env, _app(app_project, env), output="package")

        assert _edge(content, "package/build/outputs/apk/debug/package-debug.apk")


class TestTheCommand:
    def test_it_runs_androiddeployqt_on_the_settings_file(
        self, app_project, deployable
    ) -> None:
        env = android_env()

        content = _ninja(app_project, env, _app(app_project, env))

        assert str(deployable.tool_path("androiddeployqt")) in content
        assert "android-deployment-settings.json" in content
        assert "--output myapp" in content

    def test_the_staged_library_is_a_dependency(self, app_project, deployable) -> None:
        """Nothing else makes androiddeployqt wait for the application, and
        without it the package is built successfully and is empty."""
        env = android_env()

        content = _ninja(app_project, env, _app(app_project, env))

        assert "myapp/libs/arm64-v8a/libmyapp_arm64-v8a.so" in _edge(content, DEBUG_APK)

    def test_release_is_asked_for_only_when_asked_for(
        self, app_project, deployable
    ) -> None:
        env = android_env()

        assert "--release" not in _ninja(app_project, env, _app(app_project, env))

    def test_release_is_passed_through(self, app_project, deployable) -> None:
        env = android_env()

        assert "--release" in _ninja(
            app_project, env, _app(app_project, env), release=True
        )


class TestStaging:
    def test_it_is_made_when_it_is_not_given(self, app_project, deployable) -> None:
        env = android_env()
        _apk(app_project, env, _app(app_project, env))

        assert app_project.get_target("myapp-apk-lib", False) is not None

    def test_one_made_by_hand_is_used_as_it_is(self, app_project, deployable) -> None:
        env = android_env()
        app = _app(app_project, env)
        staged = stage_application_library(app_project, env, app=app)

        _apk(app_project, env, app, staged=staged)

        assert app_project.get_target("myapp-apk-lib_1", False) is None


class TestNoBuild:
    """--no-build is androiddeployqt's "install a package built earlier"
    mode, not a way to stop before Gradle: without --install it writes
    nothing at all. So the target is a stamp, and nothing depends on it."""

    def _env(self):
        from ._qt_test_utils import fake_qt_toolchain

        env = android_env()
        env.add_toolchain(fake_qt_toolchain())
        return env

    def test_the_target_is_a_stamp(self, app_project, deployable) -> None:
        env = self._env()
        apk = _apk(app_project, env, _app(app_project, env), no_build=True)
        app_project.resolve()

        assert Path(apk.output_nodes[0].path).name == "androiddeployqt.stamp"

    def test_it_is_not_built_by_default(self, app_project, deployable) -> None:
        env = self._env()

        apk = _apk(app_project, env, _app(app_project, env), no_build=True)

        assert apk.build_by_default is False

    def test_the_flag_is_passed(self, app_project, deployable) -> None:
        env = self._env()

        assert "--no-build" in _ninja(
            app_project, env, _app(app_project, env), no_build=True
        )

    def test_it_says_what_it_needs(self, app_project, deployable) -> None:
        """The stamp is written by a helper that runs under the qt
        toolchain's interpreter, so that toolchain has to be there."""
        env = android_env()

        with pytest.raises(RuntimeError, match="qt toolchain"):
            _apk(app_project, env, _app(app_project, env), no_build=True)


class TestWhatTheRunnerRefuses:
    def test_no_qt_located_for_this_environment(self, app_project, monkeypatch) -> None:
        from pcons.toolchains.qt.finder import QtNotFoundError

        monkeypatch.setattr(
            "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: None
        )
        env = android_env()

        with pytest.raises(QtNotFoundError, match="find_qt"):
            android_apk(app_project, env, app=_app(app_project, env), settings="s.json")

    def test_a_qt_with_no_androiddeployqt(self, app_project, found_qt) -> None:
        from pcons.toolchains.qt.finder import QtNotFoundError

        env = android_env()

        with pytest.raises(QtNotFoundError, match="androiddeployqt"):
            android_apk(app_project, env, app=_app(app_project, env), settings="s.json")

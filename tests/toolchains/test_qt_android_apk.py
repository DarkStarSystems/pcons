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


PASSWORD = "s3cr3t-store-pw"
UNSIGNED = "myapp/build/outputs/apk/release/myapp-release-unsigned.apk"
SIGNED = "myapp/build/outputs/apk/release/myapp-release-signed.apk"


@pytest.fixture
def sdk(tmp_path) -> Path:
    """An SDK with two build-tools revisions, both holding an apksigner."""
    from pcons.configure.platform import get_platform

    suffix = ".bat" if get_platform().is_windows else ""
    root = tmp_path / "sdk"
    for revision in ("9.0.0", "37.0.0"):
        directory = root / "build-tools" / revision
        directory.mkdir(parents=True)
        (directory / f"apksigner{suffix}").write_text("")
    return root


def _release(project, env, app, **kwargs):
    from pcons.toolchains.qt.apk import sign_apk

    apk = _apk(project, env, app, release=True)
    return sign_apk(project, env, app=app, apk=apk, **kwargs)


def _signed_ninja(project, env, app, **kwargs) -> str:
    from ._qt_test_utils import generate_ninja

    _release(project, env, app, **kwargs)
    return generate_ninja(project)


class TestThePasswordNeverReachesTheBuildFile:
    """The one property this whole surface exists for. A password written
    into build.ninja is a password in a generated file people commit."""

    def test_an_environment_variable_is_named_and_not_read(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        content = _signed_ninja(
            app_project,
            env,
            _app(app_project, env),
            keystore="release.jks",
            store_password="env:MYAPP_KEYSTORE_PASS",
        )

        assert PASSWORD not in content
        assert "env:MYAPP_KEYSTORE_PASS" in content

    def test_a_password_file_is_named_by_path_and_not_read(
        self, app_project, deployable, sdk, tmp_path
    ) -> None:
        secret = tmp_path / "keystore-pass"
        secret.write_text(f"{PASSWORD}\n")
        env = android_env(sdk=str(sdk))

        content = _signed_ninja(
            app_project,
            env,
            _app(app_project, env),
            keystore="release.jks",
            store_password=f"file:{secret}",
        )

        assert PASSWORD not in content
        assert secret.name in content

    def test_a_literal_password_is_refused(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))
        app = _app(app_project, env)

        with pytest.raises(ValueError, match="build.ninja"):
            _release(
                app_project,
                env,
                app,
                keystore="release.jks",
                store_password=f"pass:{PASSWORD}",
            )

    def test_a_literal_key_password_is_refused(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))
        app = _app(app_project, env)

        with pytest.raises(ValueError, match="key_password"):
            _release(
                app_project,
                env,
                app,
                keystore="release.jks",
                store_password="env:KS",
                key_password=f"pass:{PASSWORD}",
            )


class TestTheSigningEdge:
    def _content(self, project, env, **kwargs):
        return _signed_ninja(
            project,
            env,
            _app(project, env),
            keystore="release.jks",
            store_password="env:KS",
            **kwargs,
        )

    def test_it_signs_the_unsigned_release_package(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        edge = _edge(self._content(app_project, env), SIGNED)

        assert UNSIGNED in edge

    def test_it_is_a_second_edge_and_gradle_does_not_re_run(
        self, app_project, deployable, sdk
    ) -> None:
        """Signing is not folded into androiddeployqt's own --sign, so a new
        keystore re-signs the package rather than rebuilding it."""
        env = android_env(sdk=str(sdk))

        content = self._content(app_project, env)

        assert _edge(content, UNSIGNED)
        assert _edge(content, SIGNED)

    def test_the_keystore_is_a_dependency(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))

        edge = _edge(self._content(app_project, env), SIGNED)

        assert "release.jks" in edge

    def test_the_alias_is_passed_when_it_is_given(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        content = self._content(app_project, env, alias="upload")

        assert "--ks-key-alias upload" in content

    def test_no_alias_is_passed_when_none_is_given(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        assert "--ks-key-alias" not in self._content(app_project, env)

    def test_the_key_password_is_left_to_the_keystore_password(
        self, app_project, deployable, sdk
    ) -> None:
        """apksigner opens the key with the keystore password when
        --key-pass is absent, so absent is one fewer secret to place."""
        env = android_env(sdk=str(sdk))

        assert "--key-pass" not in self._content(app_project, env)

    def test_the_highest_build_tools_revision_wins(
        self, app_project, deployable, sdk
    ) -> None:
        """Ordered as versions, not as strings: "9.0.0" sorts above
        "37.0.0" alphabetically and is the older release."""
        env = android_env(sdk=str(sdk))

        content = self._content(app_project, env)

        assert "37.0.0" in content
        assert "9.0.0" not in content

    def test_it_is_a_target(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))
        app = _app(app_project, env)

        signed = _release(
            app_project, env, app, keystore="release.jks", store_password="env:KS"
        )

        assert signed.name == "myapp-apk-signed"


class TestWhatSigningRefuses:
    def _sign(self, project, env, **kwargs):
        return _release(project, env, _app(project, env), **kwargs)

    def test_no_keystore_at_all(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="keystore="):
            self._sign(app_project, env, store_password="env:KS")

    def test_no_keystore_never_falls_back_to_a_debug_key(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="never signed with a debug key"):
            self._sign(app_project, env, store_password="env:KS")

    def test_no_password_source(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="store_password="):
            self._sign(app_project, env, keystore="release.jks")

    def test_a_prompt_a_build_edge_cannot_answer(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="no console"):
            self._sign(app_project, env, keystore="release.jks", store_password="stdin")

    def test_a_source_apksigner_does_not_understand(
        self, app_project, deployable, sdk
    ) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="not an apksigner password source"):
            self._sign(
                app_project, env, keystore="release.jks", store_password="MYAPP_PASS"
            )

    def test_a_source_that_names_nothing(self, app_project, deployable, sdk) -> None:
        env = android_env(sdk=str(sdk))

        with pytest.raises(ValueError, match="names nothing"):
            self._sign(app_project, env, keystore="release.jks", store_password="env:")

    def test_one_password_file_for_both_passwords(
        self, app_project, deployable, sdk, tmp_path
    ) -> None:
        """Measured: apksigner reads one line per password from a file and
        dies with "end of file reached" on the second."""
        env = android_env(sdk=str(sdk))
        secret = f"file:{tmp_path / 'pass'}"

        with pytest.raises(ValueError, match="one password per line"):
            self._sign(
                app_project,
                env,
                keystore="release.jks",
                store_password=secret,
                key_password=secret,
            )

    def test_an_sdk_with_no_build_tools(
        self, app_project, deployable, tmp_path
    ) -> None:
        env = android_env(sdk=str(tmp_path / "empty-sdk"))

        with pytest.raises(ValueError, match="apksigner="):
            self._sign(
                app_project, env, keystore="release.jks", store_password="env:KS"
            )

    def test_an_environment_that_was_never_retargeted(self, app_project) -> None:
        from pcons.toolchains.qt.apk import sign_apk

        env = Environment()

        with pytest.raises(ValueError, match="retargeted with"):
            sign_apk(
                app_project,
                env,
                app="myapp",
                apk=None,  # ty: ignore[invalid-argument-type]
                keystore="release.jks",
                store_password="env:KS",
            )


class TestWhereTheSignedPackageGoes:
    def test_it_is_the_name_qt_gives_it(self, app_project) -> None:
        from pcons.toolchains.qt.apk import signed_apk_path

        env = android_env()

        assert signed_apk_path(env, "myapp") == Path("build") / SIGNED

    def test_an_explicit_output_directory_is_honoured(self, app_project) -> None:
        from pcons.toolchains.qt.apk import signed_apk_path

        env = android_env()

        assert signed_apk_path(env, "myapp", output="package") == Path(
            "package/build/outputs/apk/release/package-release-signed.apk"
        )

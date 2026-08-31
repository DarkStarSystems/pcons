# SPDX-License-Identifier: MIT
"""The deployment settings androiddeployqt reads.

Every expected value here was read off a real file written by Qt's own CMake
for an Android build, Qt 6.11.1, arm64-v8a. One version is one data point:
these tests pin what that version wants, not a universal schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.presets import android
from pcons.toolchains.qt.android import android_deployment_settings, deployment_settings

NDK = "/fake/ndk"
SDK = "/fake/sdk"
QT_PREFIX = "/fake/Qt/6.11.1/android_arm64_v8a"


class _FakeQt:
    def __init__(self, prefix: str = QT_PREFIX) -> None:
        self.prefix = Path(prefix)


def _android_env(arch: str = "arm64-v8a", *, sdk: str | None = SDK) -> Environment:
    from pcons.toolchains.llvm import LlvmToolchain

    env = Environment()
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
    env.apply_cross_preset(android(ndk=NDK, arch=arch, api=35, sdk=sdk))
    return env


@pytest.fixture(autouse=True)
def _project(test_project):
    """Environment() reads the active project, so one has to exist."""
    return test_project


@pytest.fixture
def found_qt(monkeypatch):
    qt = _FakeQt()
    monkeypatch.setattr(
        "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: qt
    )
    return qt


def _settings(env: Environment, app: str = "myapp") -> dict:
    return deployment_settings(None, env, app=app)


class TestTheRequiredKeys:
    """androiddeployqt refuses to start without these eight, each with its own
    message. Established by removing them one at a time from a file the real
    tool accepted, Qt 6.11.1."""

    REQUIRED = (
        "qt",
        "sdk",
        "ndk",
        "ndk-host",
        "architectures",
        "application-binary",
        "toolchain-prefix",
        "stdcpp-path",
    )

    def test_every_required_key_is_written(self, found_qt) -> None:
        settings = _settings(_android_env())
        assert set(self.REQUIRED) <= set(settings)

    def test_the_qt_directory_is_the_one_found_for_this_environment(
        self, found_qt
    ) -> None:
        """The Android Qt, not the host one that runs androiddeployqt."""
        assert _settings(_android_env())["qt"] == {"arm64-v8a": QT_PREFIX}

    def test_the_install_facts_come_from_the_preset(self, found_qt) -> None:
        settings = _settings(_android_env())

        assert settings["sdk"] == SDK
        assert settings["ndk"] == NDK
        assert settings["ndk-host"] == "linux-x86_64"

    def test_the_application_binary_is_a_name_not_a_path(self, found_qt) -> None:
        """androiddeployqt builds lib<name>_<abi>.so out of it and looks for
        that under the output directory."""
        settings = _settings(_android_env(), app="myapp")

        assert settings["application-binary"] == "myapp"

    def test_the_stdcpp_path_is_the_sysroot_library_directory(self, found_qt) -> None:
        settings = _settings(_android_env())

        assert (
            settings["stdcpp-path"]
            == f"{NDK}/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/"
        )


class TestTheArchitectureMapping:
    """`architectures` wants the NDK sysroot directory name, which is not the
    compiler triple: the triple carries the API level, and for armeabi-v7a the
    two disagree on the CPU as well."""

    def test_arm64_drops_the_api_level(self, found_qt) -> None:
        env = _android_env("arm64-v8a")

        assert env.cross.triple == "aarch64-linux-android35"
        assert _settings(env)["architectures"] == {"arm64-v8a": "aarch64-linux-android"}

    def test_armeabi_v7a_is_not_its_triple_at_all(self, found_qt) -> None:
        env = _android_env("armeabi-v7a")

        assert env.cross.triple == "armv7a-linux-androideabi35"
        assert _settings(env)["architectures"] == {
            "armeabi-v7a": "arm-linux-androideabi"
        }

    @pytest.mark.parametrize(
        ("arch", "directory"),
        [("x86_64", "x86_64-linux-android"), ("x86", "i686-linux-android")],
    )
    def test_the_intel_abis(self, arch: str, directory: str, found_qt) -> None:
        assert _settings(_android_env(arch))["architectures"] == {arch: directory}


class TestWhatItRefuses:
    def test_an_environment_that_was_never_retargeted(self, found_qt) -> None:
        env = Environment()

        with pytest.raises(ValueError, match="retargeted with"):
            _settings(env)

    def test_a_preset_with_no_sdk(self, found_qt) -> None:
        """Nothing in compiling needs it, so the preset may omit it and only
        this refuses."""
        env = _android_env(sdk=None)

        with pytest.raises(ValueError, match="Android SDK"):
            _settings(env)

    def test_no_qt_located_for_this_environment(self, monkeypatch) -> None:
        from pcons.toolchains.qt.finder import QtNotFoundError

        monkeypatch.setattr(
            "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: None
        )

        with pytest.raises(QtNotFoundError, match="find_qt"):
            _settings(_android_env())


class TestWhatItLeavesToAndroiddeployqt:
    """Keys the tool answers for itself. Writing them would be a second
    opinion, and the tool's own is the one that has the app in front of it."""

    @pytest.mark.parametrize(
        "key",
        [
            "android-deploy-plugins",
            "qml-import-paths",
            "qml-root-path",
            "qml-importscanner-binary",
        ],
    )
    def test_absent(self, key: str, found_qt) -> None:
        assert key not in _settings(_android_env())

    def test_the_scanner_is_skipped_for_an_app_with_no_qml(self, found_qt) -> None:
        assert _settings(_android_env())["qml-skip-import-scanning"] is True


class TestTheOptionalKeys:
    def test_they_are_absent_until_asked_for(self, found_qt, tmp_path) -> None:
        path = android_deployment_settings(
            None, _android_env(), app="myapp", output=tmp_path / "s.json"
        )
        settings = json.loads(path.read_text())

        assert "android-package-name" not in settings
        assert "sdkBuildToolsRevision" not in settings

    def test_they_are_written_when_asked_for(self, found_qt, tmp_path) -> None:
        path = android_deployment_settings(
            None,
            _android_env(),
            app="myapp",
            output=tmp_path / "s.json",
            package_name="org.example.myapp",
            build_tools="37.0.0",
        )
        settings = json.loads(path.read_text())

        assert settings["android-package-name"] == "org.example.myapp"
        assert settings["sdkBuildToolsRevision"] == "37.0.0"


class TestTheFileOnDisk:
    def test_it_is_json_and_the_path_is_returned(self, found_qt, tmp_path) -> None:
        path = android_deployment_settings(
            None, _android_env(), app="myapp", output=tmp_path / "sub" / "s.json"
        )

        assert path == tmp_path / "sub" / "s.json"
        assert json.loads(path.read_text())["abi"] == "arm64-v8a"


class TestAnAbiNobodyMapped:
    def test_a_preset_whose_abi_has_no_sysroot_directory(self, found_qt) -> None:
        """`android()` rejects an unknown ABI itself, so this only reaches a
        preset written by hand -- riscv64, which the NDK ships a sysroot for
        and pcons has no ABI name for."""
        from pcons.toolchains.presets import CrossPreset

        env = _android_env()
        env._cross_preset = CrossPreset(
            name="android-riscv64",
            arch="riscv64",
            triple="riscv64-linux-android35",
            ndk=NDK,
            ndk_host="linux-x86_64",
            sdk=SDK,
        )

        with pytest.raises(ValueError, match="riscv64"):
            _settings(env)


class TestWhereTheFileGoes:
    def test_the_default_is_the_build_directory(self, found_qt, test_project) -> None:
        env = _android_env()
        env.build_dir = "build"

        path = android_deployment_settings(test_project, env, app="myapp")

        assert path == Path(test_project.root_dir) / "build" / (
            "android-deployment-settings.json"
        )
        assert json.loads(path.read_text())["abi"] == "arm64-v8a"

    def test_a_relative_path_is_from_the_project_root(
        self, found_qt, test_project
    ) -> None:
        path = android_deployment_settings(
            test_project, _android_env(), app="myapp", output="out/s.json"
        )

        assert path == Path(test_project.root_dir) / "out" / "s.json"

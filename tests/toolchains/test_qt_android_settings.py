# SPDX-License-Identifier: MIT
"""The deployment settings androiddeployqt reads.

Every expected value here was read off a real file written by Qt's own CMake
for an Android build, Qt 6.11.1, arm64-v8a. One version is one data point:
these tests pin what that version wants, not a universal schema.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.qt.android import android_deployment_settings, deployment_settings
from pcons.toolchains.qt.finder import QtPackage

from ._qt_test_utils import (
    ANDROID_NDK,
    ANDROID_SDK,
    QT_HOST_TOOLS,
    android_env,
    fake_qt_for_android,
    fake_qt_toolchain,
)


def _qml_module(project, env, name: str, directory: str) -> None:
    """A QtQmlModule whose one QML file sits in *directory*."""
    qml = Path(project.root_dir) / directory
    qml.mkdir(parents=True, exist_ok=True)
    (qml / "Main.qml").write_text("import QtQml\nQtObject {}\n")
    env.add_toolchain(fake_qt_toolchain())
    project.QtQmlModule(
        name, env, uri=f"com.example.{name}", qml_files=[f"{directory}/Main.qml"]
    )


@pytest.fixture(autouse=True)
def _project(test_project):
    """Environment() reads the active project, so one has to exist."""
    return test_project


@pytest.fixture
def install_qt(monkeypatch, tmp_path):
    """Put a Qt install in front of the settings writer, tools of my choosing."""

    def install(tools: Sequence[str] = QT_HOST_TOOLS) -> QtPackage:
        qt = fake_qt_for_android(tmp_path, tools)
        monkeypatch.setattr(
            "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: qt
        )
        return qt

    return install


@pytest.fixture
def found_qt(install_qt) -> QtPackage:
    return install_qt()


@pytest.fixture
def settings_for(test_project, found_qt):
    def write(env: Environment, app: str = "myapp") -> dict:
        return deployment_settings(test_project, env, app=app)

    return write


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

    def test_every_required_key_is_written(self, settings_for) -> None:
        settings = settings_for(android_env())
        assert set(self.REQUIRED) <= set(settings)

    def test_the_qt_directory_is_the_one_found_for_this_environment(
        self, settings_for, found_qt
    ) -> None:
        """The Android Qt, not the host one that runs androiddeployqt."""
        v = settings_for(android_env())["qt"]
        assert "arm64-v8a" in v
        assert Path(v["arm64-v8a"]).as_posix() == found_qt.prefix.as_posix()

    def test_the_install_facts_come_from_the_preset(self, settings_for) -> None:
        """The host tag names the machine running the build, so a literal one
        here would be red everywhere but on a Linux x86_64 runner."""
        env = android_env()
        settings = settings_for(env)

        assert settings["sdk"] == ANDROID_SDK
        assert settings["ndk"] == env.cross.ndk
        assert settings["ndk-host"] == env.cross.ndk_host

    def test_the_application_binary_is_a_name_not_a_path(self, settings_for) -> None:
        """androiddeployqt builds lib<name>_<abi>.so out of it and looks for
        that under the output directory."""
        settings = settings_for(android_env(), app="myapp")

        assert settings["application-binary"] == "myapp"

    def test_the_stdcpp_path_is_the_sysroot_library_directory(
        self, settings_for
    ) -> None:
        env = android_env()
        settings = settings_for(env)

        assert (
            settings["stdcpp-path"] == f"{ANDROID_NDK}/toolchains/llvm/prebuilt/"
            f"{env.cross.ndk_host}/sysroot/usr/lib/"
        )


class TestTheStaticKeys:
    """Same in every CMake-written file measured, Qt 6.11.1, so they are
    constants here too rather than anything the caller decides."""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("tool-prefix", "llvm"),
            ("toolchain-prefix", "llvm"),
            ("toolchain-version", "clang"),
            ("useLLVM", True),
            ("android-legacy-packaging", False),
            ("zstdCompression", False),
            ("generate-java-qtquickview-contents", False),
        ],
    )
    def test_the_value(self, key: str, value: object, settings_for) -> None:
        assert settings_for(android_env())[key] == value

    def test_the_description_names_pcons(self, settings_for) -> None:
        """CMake's own text says cmake wrote it. This one did not."""
        assert "pcons" in settings_for(android_env())["description"]


class TestTheQtRelativeDirectories:
    """Where each part of a Qt for Android sits under its prefix, read off
    6.11.1. Keyed by ABI like `qt` itself, because a multi-ABI package names
    a prefix per ABI and these follow it."""

    @pytest.mark.parametrize(
        ("key", "subdirectory"),
        [
            ("qtDataDirectory", "."),
            ("qtLibExecsDirectory", "libexec"),
            ("qtLibsDirectory", "lib"),
            ("qtPluginsDirectory", "plugins"),
            ("qtQmlDirectory", "qml"),
        ],
    )
    def test_the_subdirectory(self, key: str, subdirectory: str, settings_for) -> None:
        assert settings_for(android_env())[key] == {"arm64-v8a": subdirectory}

    def test_they_are_keyed_by_the_same_abi_as_qt(self, settings_for) -> None:
        settings = settings_for(android_env("x86_64"))

        for key in (
            "qtDataDirectory",
            "qtLibExecsDirectory",
            "qtLibsDirectory",
            "qtPluginsDirectory",
            "qtQmlDirectory",
        ):
            assert settings[key].keys() == settings["qt"].keys()


class TestWhereItLooksForLibraries:
    def test_the_extra_prefix_is_the_android_qt(self, settings_for, found_qt) -> None:
        assert settings_for(android_env())["extraPrefixDirs"] == [str(found_qt.prefix)]

    def test_the_extra_library_dir_is_where_a_shared_library_lands(
        self, settings_for, test_project
    ) -> None:
        """The app's own dependency .so files, which androiddeployqt copies in
        beside it. Asserted against a real target's output rather than against
        a second derivation of the path."""
        (Path(test_project.root_dir) / "a.c").write_text("int f(void){return 0;}\n")
        env = android_env()
        env.library_directory = "lib"
        lib = test_project.SharedLibrary("dep", env, sources=["a.c"])
        test_project.resolve()

        settings = settings_for(env)

        landed = Path(test_project.root_dir) / lib.output_nodes[0].path
        assert settings["extraLibraryDirs"] == [str(landed.parent)]

    def test_a_library_in_every_directory_one_landed_in(
        self, test_project, settings_for
    ) -> None:
        """A target places itself with its own build directory, which carries
        the subdirectory it was declared in. Naming the environment's root
        once names a directory holding no library at all, and androiddeployqt
        then builds an APK that dies on its first dlopen."""
        from pcons.util.add_subdirectory import add_subdirectory

        root = Path(test_project.root_dir)
        env = android_env()
        for name in ("one", "two"):
            (root / name).mkdir()
            (root / name / "a.c").write_text("int f(void){return 0;}\n")
            (root / name / "pcons-build.py").write_text(
                "from pcons import context\n"
                "project = context.current_project\n"
                "env = project.default_environment\n"
                f'lib = project.SharedLibrary("{name}", env, sources=["a.c"])\n'
            )
            add_subdirectory(name, project=test_project, env=env)
        test_project.resolve()

        landed = {
            str((root / target.output_nodes[0].path).parent)
            for target in test_project.targets
            if target.target_type == "shared_library"
        }

        assert set(settings_for(env)["extraLibraryDirs"]) == landed
        assert len(landed) == 2

    def test_the_paths_are_absolute(self, settings_for) -> None:
        """androiddeployqt runs from wherever it is invoked and reads them
        directly, not through a build directory."""
        settings = settings_for(android_env())

        assert Path(settings["extraLibraryDirs"][0]).is_absolute()
        assert Path(settings["extraPrefixDirs"][0]).is_absolute()


class TestTheToolsItRunsItself:
    """androiddeployqt runs rcc and qmlimportscanner. A Qt for Android ships
    neither, so these have to be the host Qt's."""

    @pytest.mark.parametrize(
        ("key", "tool"),
        [
            ("rcc-binary", "rcc"),
            ("qml-importscanner-binary", "qmlimportscanner"),
            ("qml-dom-binary", "qmldom"),
        ],
    )
    def test_it_is_the_host_tool(
        self, key: str, tool: str, settings_for, found_qt
    ) -> None:
        path = Path(settings_for(android_env())[key])

        assert path == found_qt.tool_path(tool)
        assert path.is_relative_to(found_qt.libexec_dir)
        assert not path.is_relative_to(found_qt.prefix)

    def test_an_optional_tool_is_omitted_rather_than_demanded(
        self, install_qt, test_project
    ) -> None:
        """qmldom is not in every Qt build, and one missing tool must not make
        the whole file unwritable."""
        install_qt(tools=("rcc", "qmlimportscanner"))

        settings = deployment_settings(test_project, android_env(), app="myapp")

        assert "qml-dom-binary" not in settings
        assert "rcc-binary" in settings


class TestTheArchitectureMapping:
    """`architectures` wants the NDK sysroot directory name, which is not the
    compiler triple: the triple carries the API level, and for armeabi-v7a the
    two disagree on the CPU as well."""

    def test_arm64_drops_the_api_level(self, settings_for) -> None:
        env = android_env("arm64-v8a")

        assert env.cross.triple == "aarch64-linux-android35"
        assert settings_for(env)["architectures"] == {
            "arm64-v8a": "aarch64-linux-android"
        }

    def test_armeabi_v7a_is_not_its_triple_at_all(self, settings_for) -> None:
        env = android_env("armeabi-v7a")

        assert env.cross.triple == "armv7a-linux-androideabi35"
        assert settings_for(env)["architectures"] == {
            "armeabi-v7a": "arm-linux-androideabi"
        }

    @pytest.mark.parametrize(
        ("arch", "directory"),
        [("x86_64", "x86_64-linux-android"), ("x86", "i686-linux-android")],
    )
    def test_the_intel_abis(self, arch: str, directory: str, settings_for) -> None:
        assert settings_for(android_env(arch))["architectures"] == {arch: directory}


class TestWhatItRefuses:
    def test_an_environment_that_was_never_retargeted(self, settings_for) -> None:
        env = Environment()

        with pytest.raises(ValueError, match="retargeted with"):
            settings_for(env)

    def test_a_preset_with_no_sdk(self, settings_for) -> None:
        """Nothing in compiling needs it, so the preset may omit it and only
        this refuses."""
        env = android_env(sdk=None)

        with pytest.raises(ValueError, match="Android SDK"):
            settings_for(env)

    def test_no_qt_located_for_this_environment(
        self, monkeypatch, test_project
    ) -> None:
        from pcons.toolchains.qt.finder import QtNotFoundError

        monkeypatch.setattr(
            "pcons.toolchains.qt.finder.qt_install", lambda project, env=None: None
        )

        with pytest.raises(QtNotFoundError, match="find_qt"):
            deployment_settings(test_project, android_env(), app="myapp")


class TestWhatItLeavesToAndroiddeployqt:
    """Keys the tool answers for itself. Writing them would be a second
    opinion, and the tool's own is the one that has the app in front of it."""

    def test_the_plugin_list_is_absent(self, settings_for) -> None:
        assert "android-deploy-plugins" not in settings_for(android_env())

    def test_the_scanner_is_skipped_for_an_app_with_no_qml(self, settings_for) -> None:
        settings = settings_for(android_env())

        assert settings["qml-skip-import-scanning"] is True
        assert "qml-root-path" not in settings
        assert "qml-import-paths" not in settings


class TestTheQmlKeys:
    """androiddeployqt runs qmlimportscanner itself, and the scanner reads the
    filesystem. So it is told where the QML source is, and nothing else: which
    Qt QML modules to bundle stays its own answer."""

    def test_the_root_path_is_every_qml_module_source_directory(
        self, settings_for, test_project
    ) -> None:
        env = android_env()
        _qml_module(test_project, env, "ui", "qml")
        _qml_module(test_project, env, "widgets", "extra/qml")

        settings = settings_for(env)

        assert settings["qml-root-path"] == [
            str(Path(test_project.root_dir) / "qml"),
            str(Path(test_project.root_dir) / "extra" / "qml"),
        ]

    def test_a_directory_two_modules_share_is_named_once(
        self, settings_for, test_project
    ) -> None:
        env = android_env()
        _qml_module(test_project, env, "ui", "qml")
        _qml_module(test_project, env, "more", "qml")

        assert settings_for(env)["qml-root-path"] == [
            str(Path(test_project.root_dir) / "qml")
        ]

    def test_a_module_built_elsewhere_is_not_this_package_s(
        self, settings_for, test_project
    ) -> None:
        """One project may build the same module for the host too. The Android
        package describes the Android environment."""
        host = android_env()
        host.name = "host"
        _qml_module(test_project, host, "ui", "qml")

        settings = settings_for(android_env())

        assert settings["qml-skip-import-scanning"] is True

    def test_the_scanner_is_not_skipped_when_there_is_qml(
        self, settings_for, test_project
    ) -> None:
        env = android_env()
        _qml_module(test_project, env, "ui", "qml")

        assert "qml-skip-import-scanning" not in settings_for(env)

    def test_the_two_keys_do_not_have_the_same_shape(
        self, settings_for, test_project
    ) -> None:
        """Measured off a CMake-written file: qml-root-path is a JSON list and
        qml-import-paths is one comma-separated string. Symmetrical names, and
        androiddeployqt reads them with different code."""
        env = android_env()
        _qml_module(test_project, env, "ui", "qml")

        settings = settings_for(env)

        assert isinstance(settings["qml-root-path"], list)
        assert isinstance(settings["qml-import-paths"], str)

    def test_the_paths_are_absolute(self, settings_for, test_project) -> None:
        env = android_env()
        _qml_module(test_project, env, "ui", "qml")

        settings = settings_for(env)

        assert Path(settings["qml-root-path"][0]).is_absolute()
        assert Path(settings["qml-import-paths"]).is_absolute()


class TestTheOptionalKeys:
    def test_they_are_absent_until_asked_for(
        self, found_qt, test_project, tmp_path
    ) -> None:
        path = android_deployment_settings(
            test_project, android_env(), app="myapp", output=tmp_path / "s.json"
        )
        settings = json.loads(path.read_text())

        assert "android-package-name" not in settings
        assert "android-package-source-directory" not in settings
        assert "permissions" not in settings
        assert "sdkBuildToolsRevision" not in settings

    def test_they_are_written_when_asked_for(
        self, found_qt, test_project, tmp_path
    ) -> None:
        path = android_deployment_settings(
            test_project,
            android_env(),
            app="myapp",
            output=tmp_path / "s.json",
            package_name="org.example.myapp",
            build_tools="37.0.0",
        )
        settings = json.loads(path.read_text())

        assert settings["android-package-name"] == "org.example.myapp"
        assert settings["sdkBuildToolsRevision"] == "37.0.0"


class TestThePackageSourceDirectory:
    """The manifest, the Java and the resources the caller overlays on Qt's
    templates. pcons takes a path; assembling the directory stays the
    caller's, project.Install() being one way to do it."""

    def _written(self, project, tmp_path, source_dir) -> dict:
        path = android_deployment_settings(
            project,
            android_env(),
            app="myapp",
            output=tmp_path / "s.json",
            package_source_dir=source_dir,
        )
        return json.loads(path.read_text())

    def test_a_relative_path_is_made_absolute_from_the_project_root(
        self, found_qt, test_project, tmp_path
    ) -> None:
        """androiddeployqt reads it directly, not through a build directory."""
        settings = self._written(test_project, tmp_path, "android")

        assert settings["android-package-source-directory"] == str(
            Path(test_project.root_dir) / "android"
        )

    def test_an_absolute_path_is_left_alone(
        self, found_qt, test_project, tmp_path
    ) -> None:
        settings = self._written(test_project, tmp_path, tmp_path / "android")

        assert settings["android-package-source-directory"] == str(tmp_path / "android")


class TestThePermissions:
    def test_bare_names_are_wrapped(self, found_qt, test_project, tmp_path) -> None:
        """The [{"name": ...}] shape is the file's business, not the caller's."""
        path = android_deployment_settings(
            test_project,
            android_env(),
            app="myapp",
            output=tmp_path / "s.json",
            permissions=["android.permission.INTERNET", "android.permission.CAMERA"],
        )

        assert json.loads(path.read_text())["permissions"] == [
            {"name": "android.permission.INTERNET"},
            {"name": "android.permission.CAMERA"},
        ]


class TestTheFileOnDisk:
    def test_it_is_json_and_the_path_is_returned(
        self, found_qt, test_project, tmp_path
    ) -> None:
        path = android_deployment_settings(
            test_project,
            android_env(),
            app="myapp",
            output=tmp_path / "sub" / "s.json",
        )

        assert path == tmp_path / "sub" / "s.json"
        assert json.loads(path.read_text())["abi"] == "arm64-v8a"


class TestAnAbiNobodyMapped:
    def test_a_preset_whose_abi_has_no_sysroot_directory(self, settings_for) -> None:
        """`android()` rejects an unknown ABI itself, so this only reaches a
        preset written by hand -- riscv64, which the NDK ships a sysroot for
        and pcons has no ABI name for."""
        from pcons.toolchains.presets import CrossPreset

        env = android_env()
        env._cross_preset = CrossPreset(
            name="android-riscv64",
            arch="riscv64",
            triple="riscv64-linux-android35",
            ndk=ANDROID_NDK,
            ndk_host="linux-x86_64",
            sdk=ANDROID_SDK,
        )

        with pytest.raises(ValueError, match="riscv64"):
            settings_for(env)


class TestWhereTheFileGoes:
    def test_the_default_is_the_projects_build_directory(
        self, found_qt, test_project
    ) -> None:
        path = android_deployment_settings(test_project, android_env(), app="myapp")

        assert path == Path(test_project.root_dir) / test_project.build_dir / (
            "android-deployment-settings.json"
        )
        assert json.loads(path.read_text())["abi"] == "arm64-v8a"

    def test_an_environments_own_build_dir_does_not_move_it(
        self, found_qt, test_project
    ) -> None:
        """Where the build writes is the project's answer. An environment
        that carries a build_dir of its own is not a second one."""
        env = android_env()
        env.build_dir = Path("elsewhere")

        path = android_deployment_settings(test_project, env, app="myapp")

        assert path.parent == Path(test_project.root_dir) / test_project.build_dir

    def test_a_relative_path_is_from_the_project_root(
        self, found_qt, test_project
    ) -> None:
        path = android_deployment_settings(
            test_project, android_env(), app="myapp", output="out/s.json"
        )

        assert path == Path(test_project.root_dir) / "out" / "s.json"


class TestRewritingIt:
    def test_the_same_settings_leave_the_file_alone(
        self, found_qt, test_project
    ) -> None:
        """A build edge reads this file, so a configure that decided the same
        thing again must not make it look newer."""
        first = android_deployment_settings(test_project, android_env(), app="myapp")
        stamp = first.stat().st_mtime_ns
        os.utime(first, ns=(stamp - 2_000_000_000, stamp - 2_000_000_000))
        before = first.stat().st_mtime_ns

        again = android_deployment_settings(test_project, android_env(), app="myapp")

        assert again == first
        assert again.stat().st_mtime_ns == before

    def test_changed_settings_are_written(self, found_qt, test_project) -> None:
        path = android_deployment_settings(test_project, android_env(), app="myapp")

        android_deployment_settings(
            test_project, android_env(), app="myapp", build_tools="37.0.0"
        )

        assert json.loads(path.read_text())["sdkBuildToolsRevision"] == "37.0.0"

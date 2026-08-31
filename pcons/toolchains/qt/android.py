# SPDX-License-Identifier: MIT
"""The deployment settings file androiddeployqt reads.

``androiddeployqt`` is driven by a JSON file that Qt's CMake writes. Without
it no Android package can be built, however well the compile and link went.

    from pcons.toolchains.qt.android import android_deployment_settings

    env.apply_cross_preset(android(ndk=NDK, api=35, sdk=SDK))
    app = project.QtSharedLibrary("myapp", env, sources=[...])
    settings = android_deployment_settings(project, env, app=app)

This writes the smallest useful slice: one ABI, one application, no QML.
Two things it deliberately does not answer, because androiddeployqt answers
them itself and a pcons key would only be a second opinion:

- the transitive Qt library set, which it reads out of the staged ``.so``
  with ``llvm-readobj --needed-libs`` and out of Qt's own
  ``Qt6X_<abi>-android-dependencies.xml``
- the QML imports, which it finds by running ``qmlimportscanner``

``android-deploy-plugins`` is left out for the same reason. Absent, Qt's
XML decides which plugin directories are bundled: a larger package, never a
broken one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.toolchains.presets import CrossPreset

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target

#: The NDK sysroot directory each Android ABI's libraries live in. Not the
#: compiler triple: that carries the API level ("aarch64-linux-android35"),
#: and for armeabi-v7a the two disagree on the CPU as well ("armv7a-" against
#: "arm-"). Read off <ndk>/toolchains/llvm/prebuilt/<host>/sysroot/usr/lib.
_SYSROOT_DIR: dict[str, str] = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "arm-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
}


def _android_preset(env: Environment) -> CrossPreset:
    cross = env.cross
    if cross is None or getattr(cross, "ndk", None) is None:
        raise ValueError(
            "androiddeployqt settings need an environment retargeted with "
            "an Android preset: env.apply_cross_preset(android(ndk=..., "
            "api=...))."
        )
    if cross.sdk is None:
        raise ValueError(
            f"androiddeployqt needs the Android SDK and preset "
            f"'{cross.name}' has none. Pass it where the NDK is passed: "
            f"android(ndk=..., api=..., sdk=...)."
        )
    if cross.arch not in _SYSROOT_DIR:
        raise ValueError(
            f"No NDK sysroot directory known for Android ABI "
            f"'{cross.arch}'. Supported: {', '.join(_SYSROOT_DIR)}."
        )
    return cross


def deployment_settings(
    project: Project, env: Environment, *, app: Target | str
) -> dict:
    """The settings androiddeployqt reads, as a dict.

    Every value comes from what the environment was retargeted with or from
    the Qt installation found for it; nothing here searches for anything.
    Use :func:`android_deployment_settings` to write it, unless you mean to
    add keys of your own first.

    Args:
        project: The project.
        env: The environment the application is built in, retargeted with
             an Android preset carrying an ``sdk``.
        app: The application target, or its name. androiddeployqt looks for
             ``lib<name>_<abi>.so`` under the output directory, so this is
             the name and not a path.

    Returns:
        The settings, ready for :func:`json.dump`.

    Raises:
        ValueError: If the environment is not an Android cross environment,
            its preset carries no SDK, or its ABI has no known NDK sysroot
            directory.
        QtNotFoundError: If no Qt installation was found for *env*.
    """
    from pcons.toolchains.qt.finder import QtNotFoundError, qt_install

    cross = _android_preset(env)
    abi = cross.arch

    qt = qt_install(project, env)
    if qt is None:
        raise QtNotFoundError(
            "androiddeployqt settings name the Qt built for the target. "
            "Call find_qt() on this environment first."
        )

    assert cross.ndk is not None and cross.ndk_host is not None
    ndk = Path(cross.ndk)
    host = cross.ndk_host
    stdcpp = ndk / "toolchains" / "llvm" / "prebuilt" / host / "sysroot" / "usr" / "lib"

    return {
        "qt": {abi: str(qt.prefix)},
        "sdk": str(cross.sdk),
        "ndk": str(ndk),
        "ndk-host": host,
        "toolchain-prefix": "llvm",
        "stdcpp-path": f"{stdcpp.as_posix()}/",
        "abi": abi,
        "architectures": {abi: _SYSROOT_DIR[abi]},
        "application-binary": app if isinstance(app, str) else app.name,
        "qml-skip-import-scanning": True,
    }


def android_deployment_settings(
    project: Project,
    env: Environment,
    *,
    app: Target | str,
    output: str | Path | None = None,
    package_name: str | None = None,
    build_tools: str | None = None,
) -> Path:
    """Write the deployment settings file and return where it was written.

    Written at configure time, like every other file whose content is
    decided by the build script rather than by running something: see
    :func:`pcons.configure.config_file.configure_file`. Editing the build
    script re-runs pcons, which rewrites this.

    Args:
        project: The project.
        env: The environment the application is built in.
        app: The application target, or its name.
        output: Where to write it. Default:
                ``<build_dir>/android-deployment-settings.json``.
        package_name: The Android package name ("org.example.myapp"). Left
                      out, androiddeployqt takes it from the manifest.
        build_tools: SDK build-tools revision ("37.0.0"). Left out,
                     androiddeployqt picks the highest installed.

    Returns:
        The path written.
    """
    settings = deployment_settings(project, env, app=app)
    if package_name is not None:
        settings["android-package-name"] = package_name
    if build_tools is not None:
        settings["sdkBuildToolsRevision"] = build_tools

    if output is None:
        output = (
            Path(env.get("build_dir", "build")) / "android-deployment-settings.json"
        )
    output = Path(output)
    if not output.is_absolute():
        output = Path(project.root_dir) / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(settings, indent=3) + "\n", encoding="utf-8")
    return output

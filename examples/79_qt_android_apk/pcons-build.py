#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A Qt Quick application packaged as an Android APK by androiddeployqt.

Where examples/77_android_apk drives the SDK tools by hand and ships no
Java at all, this one hands the packaging to androiddeployqt, which runs
Gradle underneath it. That is the trade: pcons stops owning the manifest
merge, the Java compile and the resource pipeline, and pays for a build
system inside a build edge.

What it shows:

1. ``find_qt`` on an Android cross environment. ``probe="qtpaths"`` is
   required: a Qt for Android ships no runnable moc or rcc, and only that
   probe reads the QT_HOST_BINS / QT_HOST_LIBEXECS the install reports,
   so the host Qt beside it supplies the tools.
2. A ``QtQmlModule`` cross-compiled: moc, qmltyperegistrar and rcc run on
   the build machine, the objects are aarch64.
3. ``android_deployment_settings()``, the JSON androiddeployqt reads.
   ``build_tools=`` is not optional in practice: without
   ``sdkBuildToolsRevision`` androiddeployqt writes an empty
   ``androidBuildToolsVersion`` and Gradle stops with "Invalid revision".
4. Staging the application where androiddeployqt looks for it, which
   ``android_apk()`` does on its own.
5. A package source directory with one Java class, so Gradle really
   compiles Java into classes.dex and QJniObject really calls it.

The manifest names ``QtActivity`` and keeps Qt's own placeholders --
androiddeployqt substitutes the application library name into it.

    PCONS_ANDROID_GRADLE=1 pcons     # the real package, Gradle and all

Without that variable the androiddeployqt edge runs with ``--no-build``:
it parses the settings, reads the staged library and stops, which proves
everything pcons is responsible for without paying for Gradle. That is
what CI runs.

Needs an Android NDK and SDK, named by ANDROID_NDK_HOME (or
ANDROID_NDK_ROOT) and ANDROID_HOME (or ANDROID_SDK_ROOT), and a Qt built
for Android named by PCONS_QT_ANDROID_ROOT. A real Gradle run also needs
network for the first build and a JDK the Android Gradle plugin supports:
JAVA_HOME=/usr/lib/jvm/java-21-openjdk on a machine whose default is
newer.
"""

import os
from pathlib import Path

from pcons import Project
from pcons.toolchains.presets import android
from pcons.toolchains.qt import find_qt
from pcons.toolchains.qt.android import android_deployment_settings
from pcons.toolchains.qt.apk import android_apk

ABI = "arm64-v8a"
API = 28
PACKAGE = "org.pcons.qtapkexample"


def _from_env(*names: str) -> Path:
    for name in names:
        value = os.environ.get(name)
        if value:
            return Path(value)
    raise SystemExit(f"Set one of {', '.join(names)} to build this example.")


def _newest(directory: Path) -> str:
    revisions = sorted(
        (p for p in directory.iterdir() if p.is_dir()),
        key=lambda p: tuple(
            int(part) if part.isdigit() else 0 for part in p.name.split(".")
        ),
    )
    if not revisions:
        raise SystemExit(f"Nothing installed under {directory}.")
    return revisions[-1].name


ndk = _from_env("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT")
sdk = _from_env("ANDROID_HOME", "ANDROID_SDK_ROOT")
qt_root = _from_env("PCONS_QT_ANDROID_ROOT")

project = Project("qt_android_apk")

env = project.Environment(toolchain="llvm", name="android")
env.apply_cross_preset(android(ndk=str(ndk), arch=ABI, api=API, sdk=str(sdk)))
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Quick"], qt_root=qt_root, probe="qtpaths")

ui = project.QtQmlModule(
    "demo_ui",
    env,
    uri="PconsAndroidDemo",
    qml_files=["qml/Main.qml"],
    link=[qt.Quick],
)

app = project.QtSharedLibrary("qtapp", env, sources=["src/main.cpp"], link=[qt.Quick])
app.link(ui)

settings = android_deployment_settings(
    project,
    env,
    app=app,
    package_name=PACKAGE,
    package_source_dir="android",
    build_tools=_newest(sdk / "build-tools"),
)

apk = android_apk(
    project,
    env,
    app=app,
    settings=settings,
    no_build=os.environ.get("PCONS_ANDROID_GRADLE") != "1",
)

project.Default(app, apk)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Qt deployment: bundle the Qt runtime with the application.

Builds a Qt app, lays out a macOS .app bundle, and wires a `ninja
deploy` utility target that runs macdeployqt to copy the Qt frameworks
and plugins into the bundle, making it relocatable. On Windows the same
QtDeploy() call runs windeployqt against the executable instead.
Deployment never runs in the default build.
"""

import sys

from pcons import Project, find_c_toolchain, get_platform
from pcons.toolchains.qt import find_qt

project = Project("qt_deploy")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Widgets"])

app = project.QtProgram("demo", env, sources=["src/main.cpp"], link=[qt.Widgets])

if get_platform().is_macos:
    # Minimal .app layout in the build dir: binary plus Info.plist.
    bundle_bin = project.Install("Demo.app/Contents/MacOS", [app], no_prefix=True)
    python = sys.executable.replace("\\", "/")
    plist = env.Command(
        target="Demo.app/Contents/Info.plist",
        source="Info.plist.in",
        command=[
            python,
            "-c",
            "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])",
            "$SOURCE",
            "$TARGET",
        ],
        name="demo_plist",
    )
    deploy = project.QtDeploy("deploy", env, app=app, bundle="Demo.app")
    # Depend on the installed binary by path: Install targets resolve
    # late, so depending on bundle_bin itself wouldn't add the edge yet.
    _ = bundle_bin
    deploy.depends("Demo.app/Contents/MacOS/demo", plist)
elif get_platform().is_windows:
    deploy = project.QtDeploy("deploy", env, app=app, deploy_dir="deploy")

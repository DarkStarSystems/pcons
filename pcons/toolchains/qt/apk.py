# SPDX-License-Identifier: MIT
"""Building an Android package with androiddeployqt.

    from pcons.toolchains.qt.android import android_deployment_settings
    from pcons.toolchains.qt.apk import stage_application_library

    app = project.QtSharedLibrary("myapp", env, sources=[...])
    settings = android_deployment_settings(project, env, app=app)
    staged = stage_application_library(project, env, app=app)

androiddeployqt reads the application out of ``<output>/libs/<abi>/`` and
does not put it there. Its own dependency libraries it does copy, out of
the ``extraLibraryDirs`` the settings file names, but not the application
itself. Measured against Qt 6.11.1: with nothing staged it exits 0, reports
"Android package built successfully", and produces a package with no
application in it. Staging is what stands between a build and that.

A copy, rather than building the application straight into the staging
directory the way Qt's CMake does. One environment then builds any number
of applications, each with its own staging directory, instead of needing
one environment each because of a packaging detail.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.toolchains.qt.android import (
    _android_preset,
    android_output_dir,
    application_library_name,
)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


def stage_application_library(
    project: Project,
    env: Environment,
    *,
    app: Target,
    output: str | Path | None = None,
) -> Target:
    """Copy the application library where androiddeployqt reads it.

    Args:
        project: The project.
        env: The environment the application is built in, retargeted with
             an Android preset.
        app: The application target, a shared library.
        output: The androiddeployqt output directory. Default:
                :func:`~pcons.toolchains.qt.android.android_output_dir`.

    Returns:
        The staging target, one copy, named ``<app>-apk-lib``.

    Raises:
        ValueError: If the environment is not an Android cross environment.
    """
    abi = _android_preset(env).arch
    directory = Path(output) if output is not None else android_output_dir(env, app)
    staged = directory / "libs" / abi / application_library_name(app, abi)
    return project.InstallAs(staged, app, name=f"{app.name}-apk-lib", no_prefix=True)

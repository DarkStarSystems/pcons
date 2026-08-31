# SPDX-License-Identifier: MIT
"""Building an Android package with androiddeployqt.

    from pcons.toolchains.qt.android import android_deployment_settings
    from pcons.toolchains.qt.apk import android_apk

    app = project.QtSharedLibrary("myapp", env, sources=[...])
    settings = android_deployment_settings(project, env, app=app)
    apk = android_apk(project, env, app=app, settings=settings)

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

**androiddeployqt drives Gradle**, which is a build system inside a build
edge, and pcons's first rule is configuration rather than execution. It is
still the right call: the alternative is owning Qt's Java, its manifest
merging and its resource pipeline. The cost is real and worth knowing. The
first run downloads the Gradle distribution and the Android Gradle plugin
from the network, which takes minutes; later runs use the cache. Measured
with a warm cache, Qt 6.11.1: 19 s for a debug package, 35 Gradle tasks.
No system Gradle is needed, androiddeployqt brings its own wrapper.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.toolchains.qt.android import (
    _android_preset,
    android_output_dir,
    application_binary,
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


def apk_path(
    env: Environment,
    app: Target | str,
    *,
    output: str | Path | None = None,
    release: bool = False,
) -> Path:
    """Where androiddeployqt writes the package.

    Gradle names the package after the **output directory**, not after
    ``application-binary``, and puts it under a directory named after the
    variant. Measured against Qt 6.11.1: ``--output out`` writes
    ``out/build/outputs/apk/debug/out-debug.apk``, and with ``--release``
    ``out/build/outputs/apk/release/out-release-unsigned.apk``.

    Do not take this from androiddeployqt's own closing message. Under
    ``--no-build`` it prints ``build/outputs/apk//out-debug.apk``, without
    the variant directory, and no such file is ever written.

    Args:
        env: The environment the application is built in.
        app: The application target, or its name.
        output: The androiddeployqt output directory. Default:
                :func:`~pcons.toolchains.qt.android.android_output_dir`.
        release: The release package rather than the debug one.

    Returns:
        The path, relative to the project root unless the environment's
        build directory is absolute.
    """
    directory = Path(output) if output is not None else android_output_dir(env, app)
    variant = "release" if release else "debug"
    stem = (
        f"{directory.name}-release-unsigned" if release else f"{directory.name}-debug"
    )
    return directory / "build" / "outputs" / "apk" / variant / f"{stem}.apk"


def android_apk(
    project: Project,
    env: Environment,
    *,
    app: Target,
    settings: str | Path,
    staged: Target | None = None,
    output: str | Path | None = None,
    release: bool = False,
    no_build: bool = False,
    name: str | None = None,
) -> Target:
    """Run androiddeployqt, and Gradle under it, to build the package.

    androiddeployqt is a **host** tool, and the Qt located for an Android
    environment already answers with the host directories, so nothing here
    searches for it.

    Args:
        project: The project.
        env: The environment the application is built in, retargeted with
             an Android preset.
        app: The application target, a shared library.
        settings: The deployment settings file, from
                  :func:`~pcons.toolchains.qt.android.android_deployment_settings`.
        staged: The staging target from :func:`stage_application_library`.
                Made here when left out, so a package cannot be built
                without the application in it.
        output: The androiddeployqt output directory. Default:
                :func:`~pcons.toolchains.qt.android.android_output_dir`.
        release: Build the release package. It is unsigned, so it installs
                 on nothing until it is signed; the debug default is signed
                 with Gradle's own debug key and installs.
        no_build: Pass ``--no-build``, which skips the packaging entirely --
                  it is androiddeployqt's "install a package built earlier"
                  mode, not a way to stop before Gradle. It writes no
                  package, so the target is a stamp and is not built by
                  default.
        name: Target name. Default ``<app>-apk``.

    Returns:
        The command target: the package, or the stamp under ``no_build``.

    Raises:
        ValueError: If the environment is not an Android cross environment.
        QtNotFoundError: If no Qt installation was found for *env*, or it
            has no androiddeployqt.
    """
    from pcons.toolchains.qt.builders import _require_qt_tool, _stamped_command
    from pcons.toolchains.qt.finder import QtNotFoundError, qt_install

    _android_preset(env)
    qt = qt_install(project, env)
    if qt is None:
        raise QtNotFoundError(
            "androiddeployqt is a host tool of the Qt built for the target. "
            "Call find_qt() on this environment first."
        )
    tool = qt.tool_path("androiddeployqt", required=True)

    directory = Path(output) if output is not None else android_output_dir(env, app)
    if staged is None:
        staged = stage_application_library(project, env, app=app, output=directory)

    arguments = [
        "--input",
        "${SOURCES[0]}",
        "--output",
        _execution_relative(project, directory),
    ]
    if release:
        arguments.append("--release")
    if no_build:
        arguments.append("--no-build")

    command: list[str] = ["$TOOL", *arguments]
    if no_build:
        _require_qt_tool(env, "android_apk(no_build=True)")
        command = _stamped_command(env, *command)
        produced: Path = directory / "androiddeployqt.stamp"
    else:
        produced = apk_path(env, app, output=directory, release=release)

    result = env.Command(
        name=name or f"{application_binary(app)}-apk",
        target=produced,
        tool=tool,
        source=[Path(settings), staged],
        command=command,
    )
    if no_build:
        result.build_by_default = False
    return result


def _execution_relative(project: Project, directory: Path) -> str:
    """*directory* as the command will see it: ninja runs in the build dir."""
    return project._path_resolver.normalize_target_path(directory).as_posix()

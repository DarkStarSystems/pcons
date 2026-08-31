# SPDX-License-Identifier: MIT
"""Building an Android package with androiddeployqt.

    from pcons.toolchains.qt.android import android_deployment_settings
    from pcons.toolchains.qt.apk import android_apk

    app = project.QtSharedLibrary("myapp", env, sources=[...])
    settings = android_deployment_settings(project, env, app=app)
    apk = android_apk(project, env, app=app, settings=settings)

A release package comes out unsigned and installs on nothing until
:func:`sign_apk` runs apksigner over it. **No keystore password is ever a
pcons argument**: the caller names an environment variable or a file and
apksigner reads the value when the edge runs, so nothing secret is written
into build.ninja.

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

from pcons.configure.platform import get_platform
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
    from pcons.toolchains.presets import CrossPreset


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


#: apksigner password sources that keep the value out of the build file. Its
#: ``pass:<password>`` form is refused: pcons would write the password into
#: build.ninja, which is the one thing signing here must never do.
_PASSWORD_SOURCES = ("env:", "file:")


def signed_apk_path(
    env: Environment,
    app: Target | str,
    *,
    output: str | Path | None = None,
) -> Path:
    """Where :func:`sign_apk` writes the signed package.

    Beside the unsigned one, under the name Qt's own ``--sign`` gives it.
    Measured against Qt 6.11.1: ``androiddeployqt --release --sign`` leaves
    ``<out>-release-signed.apk`` and no unsigned file at all.

    Args:
        env: The environment the application is built in.
        app: The application target, or its name.
        output: The androiddeployqt output directory. Default:
                :func:`~pcons.toolchains.qt.android.android_output_dir`.

    Returns:
        The path, relative to the project root unless the environment's
        build directory is absolute.
    """
    unsigned = apk_path(env, app, output=output, release=True)
    stem = unsigned.name[: -len(".apk")]
    if stem.endswith("-unsigned"):
        stem = stem[: -len("-unsigned")]
    return unsigned.with_name(f"{stem}-signed.apk")


def sign_apk(
    project: Project,
    env: Environment,
    *,
    app: Target | str,
    apk: Target,
    keystore: str | Path | None = None,
    store_password: str | None = None,
    alias: str | None = None,
    key_password: str | None = None,
    output: str | Path | None = None,
    apksigner: str | Path | None = None,
    name: str | None = None,
) -> Target:
    """Sign a release package with apksigner, on an edge of its own.

    The password is named, never given. *store_password* is an apksigner
    password source -- ``"env:MYAPP_KEYSTORE_PASS"`` or
    ``"file:/run/secrets/keystore-pass"`` -- so the build file holds the
    name of a variable or the path of a file and apksigner reads the value
    itself when the edge runs. ``pass:<password>`` is refused: pcons would
    write it into build.ninja.

    A separate edge rather than androiddeployqt's own ``--sign``, which
    reads ``QT_ANDROID_KEYSTORE_STORE_PASS`` and would keep the password out
    of the build file just as well. Signing here does not re-run Gradle when
    the keystore changes, states in the build file where the password comes
    from instead of depending on four ambient variables, and leaves
    :func:`apk_path` telling the truth -- ``--sign`` renames the package
    androiddeployqt writes.

    Args:
        project: The project.
        env: The environment the application is built in, retargeted with
             an Android preset.
        app: The application target, or its name.
        apk: The unsigned release package, from
             :func:`android_apk` with ``release=True``.
        keystore: The release keystore. Made absolute against the project
                  root, and a dependency of the edge, so a new keystore
                  re-signs.
        store_password: Where apksigner reads the keystore password:
                        ``"env:NAME"`` or ``"file:PATH"``.
        alias: The key alias in the keystore. Needed only when the keystore
               holds more than one key.
        key_password: Where apksigner reads the private key password, in the
                      same two forms. Left out, apksigner opens the key with
                      the keystore password. A ``file:`` source may not be
                      the same file as *store_password*: apksigner reads one
                      line per password from a file and fails on the second.
        output: The androiddeployqt output directory. Default:
                :func:`~pcons.toolchains.qt.android.android_output_dir`.
        apksigner: The apksigner program. Default: the highest build-tools
                   revision installed under the SDK the Android preset names.
        name: Target name. Default ``<app>-apk-signed``.

    Returns:
        The signing target, one package.

    Raises:
        ValueError: If the environment is not an Android cross environment,
            if *keystore* or *store_password* is missing, if a password
            source would put the password in the build file, or if no
            apksigner was found.
    """
    cross = _android_preset(env)
    if keystore is None:
        raise ValueError(
            "sign_apk() needs the release keystore: pass keystore=<path>. "
            "A release package is never signed with a debug key, and pcons "
            "never makes a release keystore."
        )
    store = _password_source(store_password, "store_password")
    key = (
        None if key_password is None else _password_source(key_password, "key_password")
    )
    if key is not None and key == store and key.startswith("file:"):
        raise ValueError(
            "store_password and key_password name the same file. apksigner "
            "reads one password per line from a file and fails on the "
            "second. Leave key_password out to open the key with the "
            "keystore password, or name a second file."
        )

    tool = _apksigner(cross, apksigner)
    keystore_path = Path(keystore)
    if not keystore_path.is_absolute():
        keystore_path = Path(project.root_dir) / keystore_path

    command: list[str] = ["$TOOL", "sign", "--ks", str(keystore_path)]
    if alias is not None:
        command += ["--ks-key-alias", alias]
    command += ["--ks-pass", store]
    if key is not None:
        command += ["--key-pass", key]
    command += ["--out", "$TARGET", "${SOURCES[0]}"]

    return env.Command(
        name=name or f"{application_binary(app)}-apk-signed",
        target=signed_apk_path(env, app, output=output),
        tool=tool,
        source=[apk],
        command=command,
        depends=[keystore_path],
    )


def _password_source(spec: str | None, argument: str) -> str:
    """An apksigner password source that holds no password."""
    if spec is None:
        raise ValueError(
            f"sign_apk() needs to know where apksigner reads the password: "
            f"pass {argument}='env:NAME' or {argument}='file:PATH'."
        )
    if spec.startswith("pass:"):
        raise ValueError(
            f"{argument}='pass:...' puts the password in build.ninja, which "
            f"is a generated file people commit. Use 'env:NAME' or "
            f"'file:PATH'; apksigner reads the value itself when the edge "
            f"runs."
        )
    if spec == "stdin":
        raise ValueError(
            f"{argument}='stdin' makes apksigner prompt, and a build edge "
            f"has no console to answer it. Use 'env:NAME' or 'file:PATH'."
        )
    if not spec.startswith(_PASSWORD_SOURCES):
        raise ValueError(
            f"{argument}='{spec}' is not an apksigner password source. "
            f"Use 'env:NAME' or 'file:PATH'."
        )
    scheme, _, value = spec.partition(":")
    if not value:
        raise ValueError(f"{argument}='{spec}' names nothing after '{scheme}:'.")
    if scheme == "file":
        path = Path(value)
        if not path.is_absolute():
            path = path.resolve()
        return f"file:{path}"
    return spec


def _apksigner(cross: CrossPreset, override: str | Path | None) -> str | Path:
    """apksigner, from the SDK the Android preset names."""
    if override is not None:
        return override
    assert cross.sdk is not None
    suffix = ".bat" if get_platform().is_windows else ""
    revisions = Path(cross.sdk) / "build-tools"
    installed = sorted(
        (p for p in revisions.glob("*") if (p / f"apksigner{suffix}").is_file()),
        key=lambda p: _revision(p.name),
    )
    if not installed:
        raise ValueError(
            f"No apksigner under {revisions}. Install the SDK build tools, "
            f"or name the program with apksigner=<path>."
        )
    return installed[-1] / f"apksigner{suffix}"


def _revision(name: str) -> tuple[int, ...]:
    """A build-tools directory name, ordered as the version it is."""
    return tuple(int(part) if part.isdigit() else 0 for part in name.split("."))

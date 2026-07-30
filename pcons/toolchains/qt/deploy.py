# SPDX-License-Identifier: MIT
"""QtDeploy: bundle Qt runtime libraries with an application.

    app = project.QtProgram("myapp", env, sources=[...], link=[qt.Widgets])
    project.QtDeploy("deploy", env, app=app)          # Windows
    project.QtDeploy("deploy", env, app=app,
                     bundle="MyApp.app")              # macOS (a .app bundle)

Deployment is a utility target — run explicitly with ``ninja deploy``,
never part of the default build.

Per platform:

- **Windows**: ``windeployqt`` copies the needed Qt DLLs, plugins, and
  QML imports next to the executable (or into ``deploy_dir``).
- **macOS**: ``macdeployqt`` fixes up a ``.app`` bundle in place, copying
  Qt frameworks and plugins into it. Build the bundle first (e.g. with
  ``pcons.contrib.bundle.create_macos_bundle`` or an Install layout) and
  pass its path as ``bundle=``.
- **Linux**: not provided by Qt; use ``linuxdeploy``/AppImage tooling.
  QtDeploy raises with that pointer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder_registry import builder
from pcons.toolchains.qt.builders import _require_qt_tool
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation


@builder(
    "QtDeploy",
    target_type="command",
    requires_env=True,
    description="Bundle Qt runtime with an app (macdeployqt/windeployqt)",
)
class QtDeployBuilder:
    """`ninja deploy` utility target (see module docstring)."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        app: Target,
        bundle: str | Path | None = None,
        deploy_dir: str | Path | None = None,
        flags: Sequence[str] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a deployment utility target.

        Args:
            project: The project.
            name: Target name (`ninja <name>`; also aliased as `deploy`).
            env: Environment with the qt toolchain (via find_qt).
            app: The program target to deploy.
            bundle: macOS only — path of the .app bundle to fix up
                (relative to the build dir), required there.
            deploy_dir: Windows only — directory to deploy into
                (default: next to the executable).
            flags: Extra flags for macdeployqt/windeployqt.

        Returns:
            The command target (build_by_default=False).
        """
        _require_qt_tool(env, "QtDeploy()")
        defined_at = defined_at or get_caller_location()
        platform = get_platform()
        qt_env = env.clone()
        build_dir = Path(env.get("build_dir", "build"))
        stamp = build_dir / f"qt.{name}" / "deploy.stamp"

        from pcons.toolchains.qt.finder import _qt_installs

        qt = _qt_installs.get(project)

        def qt_tool(tool_name: str) -> str:
            if qt is not None:
                path = qt.tool_path(tool_name)
                if path is not None:
                    return str(path)
            return tool_name

        prefix = [
            str(qt_env.qt.python),
            "-m",
            "pcons.toolchains.qt._stamped",
            "--stamp",
            "$TARGET",
            "--",
        ]
        if platform.is_macos:
            if bundle is None:
                raise ValueError(
                    "QtDeploy on macOS needs bundle=<path to the .app> "
                    "(macdeployqt operates on app bundles; see "
                    "pcons.contrib.bundle for building one)."
                )
            command = [*prefix, qt_tool("macdeployqt"), str(bundle), *flags]
        elif platform.is_windows:
            command = [*prefix, qt_tool("windeployqt")]
            if deploy_dir is not None:
                command += ["--dir", str(deploy_dir)]
            command += [*flags, "$SOURCE"]
        else:
            raise RuntimeError(
                "QtDeploy supports macOS (macdeployqt) and Windows "
                "(windeployqt). On Linux use linuxdeploy or "
                "appimagetool on the installed tree."
            )

        target = qt_env.Command(
            target=stamp,
            source=[app],
            command=command,
            name=f"{name}-cmd",
        )
        target.build_by_default = False
        project.Alias(name, target)
        if name != "deploy":
            project.Alias("deploy", target)
        return target

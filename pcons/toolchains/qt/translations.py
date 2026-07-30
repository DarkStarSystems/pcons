# SPDX-License-Identifier: MIT
"""QtTranslations: compile and embed translation catalogs.

    qt = find_qt(project, env, modules=["Widgets"])
    tr = project.QtTranslations("i18n", env, ts_files=["i18n/app_de.ts"])
    app.link(tr)

Each ``.ts`` catalog is compiled with lrelease and the resulting ``.qm``
files embed as resources under ``:/i18n/`` — load them with:

    QTranslator translator;
    translator.load(QLocale(), "app", "_", ":/i18n");

Maintaining the catalogs (extracting new tr() strings from sources into
the .ts files) is a *source-tree edit*, so it is deliberately not part
of the build: ``QtTranslations(..., lupdate_sources=[...])`` adds a
``lupdate`` utility alias, run explicitly with ``ninja lupdate``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.toolchains.qt.builders import (
    _qrc_xml,
    _require_qt_tool,
    _stamped_command,
    _write_if_changed,
)
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation


@builder(
    "QtTranslations",
    target_type="object",
    requires_env=True,
    description="Compile .ts catalogs with lrelease and embed the .qm files",
)
class QtTranslationsBuilder:
    """Compile and embed Qt translation catalogs (see module docstring)."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        ts_files: Sequence[str | Path],
        prefix: str = "/i18n",
        lupdate_sources: Sequence[str | Path] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a translations target.

        Args:
            project: The project.
            name: Target name (also the Q_INIT_RESOURCE name).
            env: Environment with the qt toolchain (via find_qt).
            ts_files: Translation source catalogs (.ts, in the source
                tree, maintained by lupdate/Linguist).
            prefix: Resource prefix for the embedded .qm files.
            lupdate_sources: When given, adds the `ninja lupdate` utility
                alias that refreshes the .ts catalogs from these sources.
                Never part of the default build (it edits the source tree).

        Returns:
            An object target; app.link(tr) embeds the catalogs.
        """
        _require_qt_tool(env, "QtTranslations()")
        defined_at = defined_at or get_caller_location()
        if not ts_files:
            raise ValueError(f"QtTranslations '{name}': ts_files is empty")
        root = project.root_dir
        build_dir = Path(env.get("build_dir", "build"))
        qt_dir = build_dir / f"qt.{name}"
        qt_env = env.clone()

        # lrelease each catalog: .ts -> .qm
        qm_nodes: list[Node] = []
        for ts in ts_files:
            ts_path = Path(ts)
            qm_nodes.append(
                qt_env.qt.Lrelease(qt_dir / f"{ts_path.stem}.qm", str(ts_path))[0]
            )

        # Embed the .qm files under <prefix>/.
        entries = [
            (f"{Path(ts).stem}.qm", root / qt_dir / f"{Path(ts).stem}.qm")
            for ts in ts_files
        ]
        qrc_rel = qt_dir / f"{name}.qrc"
        _write_if_changed(root / qrc_rel, _qrc_xml(prefix, entries))

        rcc_node = qt_env.qt.Rcc(qt_dir / f"qrc_{name}.cpp", qrc_rel, name=name)[0]
        rcc_node.implicit_deps.extend(qm_nodes)

        factory = getattr(project, "ObjectLibrary")  # noqa: B009
        target: Target = factory(
            name, qt_env, sources=[rcc_node], defined_at=defined_at
        )

        if lupdate_sources:
            _add_lupdate_target(
                project, qt_env, name, qt_dir, ts_files, lupdate_sources
            )
        return target


def _add_lupdate_target(
    project: Project,
    qt_env: Environment,
    name: str,
    qt_dir: Path,
    ts_files: Sequence[str | Path],
    sources: Sequence[str | Path],
) -> None:
    """A `ninja lupdate` utility target refreshing the .ts catalogs.

    Excluded from the default build and from 'all' (build_by_default is
    False): it writes into the source tree, which only a translator
    updating catalogs should trigger.
    """
    stamp = qt_dir / f"{name}-lupdate.stamp"
    # env.Command has no tool-namespace expansion; use the concrete paths.
    command = _stamped_command(
        qt_env,
        str(qt_env.qt.lupdate),
        "$SOURCES",
        "-ts",
        *[f"$SRCDIR/{Path(ts).as_posix()}" for ts in ts_files],
    )
    lupdate_target = qt_env.Command(
        target=stamp,
        source=list(sources),
        command=command,
        name=f"{name}-lupdate",
    )
    lupdate_target.build_by_default = False
    project.Alias("lupdate", lupdate_target)

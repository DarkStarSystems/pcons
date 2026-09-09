# SPDX-License-Identifier: MIT
"""QtQmlModule: a QML module (QML files + QML_ELEMENT C++ types) in one call.

    qt = find_qt(project, env, modules=["Quick"])
    ui = project.QtQmlModule("AppUi", env,
        uri="com.example.app",
        qml_files=["Main.qml", "SettingsPage.qml"],
        sources=["backend.cpp"],           # classes marked QML_ELEMENT
        link=[qt.Quick])
    app = project.QtProgram("app", env, sources=["main.cpp"], link=[qt.Quick])
    app.link(ui)

The application then loads the module with
``engine.loadFromModule("com.example.app", "Main")`` — no import-path or
registration code needed.

What one QtQmlModule call replaces (CMake's qt_add_qml_module plumbing):

- moc runs with ``--output-json``; the JSON sidecars merge into a
  metatypes file (``moc --collect-json``)
- ``qmltyperegistrar`` turns that into ``<name>_qmltyperegistrations.cpp``
  (+ a ``.qmltypes`` file for tooling), registering every QML_ELEMENT
  class under the module URI
- a ``qmldir`` is synthesized (module line, typeinfo, type entries)
- QML files, qmldir, and the qmltypes embed as resources under
  ``:/qt/qml/<uri-as-path>/`` — the engine's default import path

The module is built as an *object* target: its objects (including the
type registration and resources) link directly into the consuming
application, so nothing is lost to static-library dead-stripping and no
Q_INIT_RESOURCE or plugin import boilerplate is needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.subst import PathToken
from pcons.toolchains.qt.builders import (
    _qrc_xml,
    _qt_make_target,
    _require_qt_tool,
    _write_if_changed,
)
from pcons.toolchains.qt.toolchain import _source_path
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation


def _set_node_vars(node: Node, node_vars: dict[str, object]) -> None:
    """Attach per-node command variables (the swift.py precedent)."""
    info = getattr(node, "_build_info", None)
    if info is not None:
        info["vars"] = node_vars


def _parse_version(version: str) -> tuple[str, str]:
    parts = version.split(".")
    major = parts[0] or "1"
    minor = parts[1] if len(parts) > 1 else "0"
    return major, minor


def _linked_qt_modules(link: Sequence[Target]) -> set[str]:
    """Lower-case Qt module names ("qml", "core") in the link closure."""
    names: set[str] = set()
    seen: set[int] = set()

    def visit(target: Target) -> None:
        if id(target) in seen:
            return
        seen.add(id(target))
        if target.name.startswith("Qt6"):
            names.add(target.name[3:].lower())
        for dep in target.public.link_libs:
            if not isinstance(dep, str):
                visit(dep)

    for target in link:
        visit(target)
    return names


def _qt_metatypes(
    project: Project, env: Environment, link: Sequence[Target]
) -> list[Path]:
    """Qt's own metatypes files for the linked modules.

    Restricted to the link closure (plus core/qml, always needed as
    base-class providers): passing every installed module's metatypes
    makes qmltyperegistrar trip over unrelated modules that define
    same-named types (e.g. Charts vs Graphs QAbstractAxis).
    """
    from pcons.toolchains.qt.finder import qt_install

    qt = qt_install(project, env)
    if qt is None:
        return []
    wanted = _linked_qt_modules(link) | {"core", "qml"}
    return [
        path
        for path in qt.metatypes_files()
        if any(path.name.startswith(f"qt6{module}_") for module in wanted)
    ]


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _declares_singleton(path: Path) -> bool:
    """Whether a QML file declares itself a singleton.

    ``pragma Singleton`` lives in the leading pragma block, above the imports,
    so only that block is read: the first line that is not a comment and not a
    pragma ends it. A file pcons cannot read yet, a generated one, reads as not
    a singleton.

    The engine resolves a type declared without ``singleton`` in the qmldir as a
    type rather than an instance, so every property access through it is
    undefined at runtime while the build stays green. That is why this is read
    from the source rather than left to the author to declare twice.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in _BLOCK_COMMENT.sub(" ", text).splitlines():
        statement = " ".join(line.split("//", 1)[0].split()).rstrip(";")
        if not statement:
            continue
        if not statement.startswith("pragma"):
            return False
        if statement == "pragma Singleton":
            return True
    return False


@builder(
    "QtQmlModule",
    target_type="object",
    requires_env=True,
    description="QML module: QML files + QML_ELEMENT types, one call",
)
class QtQmlModuleBuilder:
    """Build a QML module as an object target (see module docstring)."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        uri: str,
        version: str = "1.0",
        qml_files: Sequence[str | Path] = (),
        sources: Sequence[str | Path] = (),
        link: Sequence[Target] = (),
        no_moc: Sequence[str | Path] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a QML module target.

        Args:
            project: The project.
            name: Target name (also names the .qmltypes file).
            env: Environment with the qt toolchain (via find_qt).
            uri: Module URI, e.g. "com.example.app". QML imports it and
                the resources live under :/qt/qml/<uri-as-path>/.
            version: Module version "major.minor".
            qml_files: QML files to embed (type name = file stem).
            sources: C++ sources; QML_ELEMENT classes register
                automatically (via the same automoc scan as QtProgram).
            link: Targets to link — pass Qt modules (link=[qt.Quick]).
            no_moc: Files to exclude from the moc scan.

        Returns:
            An object target; app.link(module) pulls everything in.
        """
        _require_qt_tool(env, "QtQmlModule()")
        defined_at = defined_at or get_caller_location()
        major, minor = _parse_version(version)
        uri_path = uri.replace(".", "/")

        # The backing target: compiles the C++ sources with automoc,
        # moc emitting JSON sidecars for the type registrar.
        target, info = _qt_make_target(
            "ObjectLibrary",
            project,
            name,
            env,
            list(sources),
            link=link,
            no_moc=no_moc,
            moc_json=True,
            defined_at=defined_at,
        )
        qt_env = info.qt_env
        qt_dir = info.qt_dir
        root = project.root_dir

        # ---- C++ type registration (only when there are moc'ed types) ----
        registrar_node: Node | None = None
        qmltypes_name = f"{name}.qmltypes"
        resolver = project._path_resolver
        # Both moc modes emit JSON sidecars: QML_ELEMENT in a header
        # (moc_X.cpp.json) or in a self-mocing .cpp (X.moc.json).
        moc_output_nodes = [*info.moc_header_nodes, *info.dot_moc_nodes]
        if moc_output_nodes:
            # Sidecars are named as the build tool sees them (PathToken
            # path_type="build" renders verbatim).
            json_tokens = [
                PathToken(
                    path=f"{resolver.make_execution_relative(_source_path(node))}.json",
                    path_type="build",
                )
                for node in moc_output_nodes
            ]
            metatypes = qt_env.qt.CollectJson(
                qt_dir / f"{name}_metatypes.json", moc_output_nodes
            )[0]
            _set_node_vars(metatypes, {"JSONFILES": json_tokens})

            foreign = _qt_metatypes(project, env, link)
            registrar_node = qt_env.qt.TypeRegistrar(
                qt_dir / f"{name}_qmltyperegistrations.cpp", [metatypes]
            )[0]
            _set_node_vars(
                registrar_node,
                {
                    "QMLURI": uri,
                    "QMLMAJOR": major,
                    "QMLMINOR": minor,
                    "QMLTYPES": PathToken(
                        path=resolver.make_execution_relative(qt_dir / qmltypes_name),
                        path_type="build",
                    ),
                    "QMLFOREIGN": (
                        ["--foreign-types", ",".join(str(p) for p in foreign)]
                        if foreign
                        else []
                    ),
                },
            )
            # The generated registration code includes the user's
            # headers via #include <header.h>.
            for directory in info.moc_header_dirs:
                target.private.include_dirs.append(directory)
            target.add_sources([registrar_node])

        # ---- qmldir ----------------------------------------------------
        qmldir_lines = [f"module {uri}"]
        if registrar_node is not None:
            qmldir_lines.append(f"typeinfo {qmltypes_name}")
        qmldir_lines.append(f"prefer :/qt/qml/{uri_path}/")
        for qml in qml_files:
            qml_path = Path(qml)
            # The qmldir is written now, from the file's own content, so a
            # pragma added later has to re-run pcons and not only rcc.
            project.add_configure_dependency(root / qml_path)
            kind = "singleton " if _declares_singleton(root / qml_path) else ""
            qmldir_lines.append(
                f"{kind}{qml_path.stem} {major}.{minor} {qml_path.name}"
            )
        _write_if_changed(root / qt_dir / "qmldir", "\n".join(qmldir_lines) + "\n")

        # ---- resources under :/qt/qml/<uri>/ ---------------------------
        entries = [(Path(qml).name, root / qml) for qml in qml_files]
        entries.append(("qmldir", root / qt_dir / "qmldir"))
        if registrar_node is not None:
            entries.append((qmltypes_name, root / qt_dir / qmltypes_name))
        qrc_rel = qt_dir / f"{name}.qrc"
        _write_if_changed(root / qrc_rel, _qrc_xml(f"/qt/qml/{uri_path}", entries))

        rcc_node = qt_env.qt.Rcc(
            qt_dir / f"qrc_{name}.cpp", qrc_rel, name=f"qml_{name}"
        )[0]
        if registrar_node is not None:
            # rcc embeds the .qmltypes the registrar writes.
            rcc_node.implicit_deps.append(registrar_node)
        target.add_sources([rcc_node])

        return target

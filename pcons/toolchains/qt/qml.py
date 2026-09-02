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

import os
import re
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.subst import PathToken
from pcons.toolchains.qt.builders import (
    _qrc_xml,
    _qt_make_target,
    _require_qt_tool,
    _set_node_vars,
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


#: Every QtQmlModule declared in a project tree, with the directories its
#: QML came from. A deployment tool that runs qmlimportscanner has to be told
#: where the QML *source* is: the scanner reads the filesystem, and a
#: module's own QML ends up in a resource. Keyed by the top-level project,
#: the way ``finder.py`` keys located installs, so it dies with the build.
_qml_module_sources: weakref.WeakKeyDictionary[
    Project, list[tuple[Target, list[Path]]]
] = weakref.WeakKeyDictionary()


def qml_source_dirs(project: Project, env: Environment | None = None) -> list[Path]:
    """Directories holding the QML of the QtQmlModule targets in this tree.

    In declaration order, without duplicates, sub-projects included. This is
    what ``qmlimportscanner`` wants as a root path: it walks the directories
    it is given and reads the ``import`` lines out of the files it finds.

    Args:
        project: Any project of the tree; the whole tree is searched.
        env: Only count modules built in this environment. All of them when
             None.

    Returns:
        The directories, as they were spelled to :class:`QtQmlModuleBuilder`.
    """
    dirs: list[Path] = []
    for target, sources in _qml_module_sources.get(project.top, ()):
        if env is not None and target.env is not env:
            continue
        for directory in sources:
            if directory not in dirs:
                dirs.append(directory)
    return dirs


def _qml_entries(
    name: str, qml_files: Sequence[str | Path], root: Path
) -> list[tuple[str, Path]]:
    """Each ``qml_files`` entry as (resource path, path under *root*).

    The resource path is the entry taken relative to *root*, which is what
    ``qt_add_qml_module`` writes into both the qmldir line and the qrc alias:
    ``sub/Thing.qml`` is reachable at ``qrc:/qt/qml/<uri>/sub/Thing.qml``.

    An absolute entry under *root* is relativised, unlike CMake, which refuses
    every absolute ``QML_FILES`` entry because it cannot derive a resource path
    from one. pcons can derive one whenever the entry sits under the root.

    An entry landing outside *root* is refused, ``../outside/Thing.qml``
    included, where ``qt_add_qml_module`` keeps the dot-dots in the alias. Such
    an entry has no place under the module's resource prefix, which is the same
    reason an outside absolute entry cannot work, so it is one rule here.

    Args:
        name: The module target name, for the error messages.
        qml_files: The entries as the caller spelled them.
        root: What entries are relative to, and the module's resource root.

    Returns:
        One pair per entry, in declaration order.

    Raises:
        ValueError: An entry lands outside *root*, is listed twice, or shares
            its QML type name with another entry.
    """
    entries: list[tuple[str, Path]] = []
    seen: set[str] = set()
    declared_by: dict[str, str] = {}
    for qml in qml_files:
        try:
            path = Path(os.path.normpath(root / qml)).relative_to(root)
        except ValueError:
            raise ValueError(
                f"QtQmlModule '{name}': qml_files entry '{qml}' is not under "
                f"'{root}', the directory qml_files is relative to. An entry "
                "is also the file's path inside the module resource, so one "
                "outside that directory has no resource path to be given. "
                "Move the file under the directory, or declare the module "
                "from a build script that has it."
            ) from None
        resource_path = path.as_posix()
        if resource_path in seen:
            raise ValueError(
                f"QtQmlModule '{name}': qml_files lists '{resource_path}' "
                "twice, and one entry would overwrite the other in the "
                "resource."
            )
        clash = declared_by.get(path.stem)
        if clash is not None:
            raise ValueError(
                f"QtQmlModule '{name}': '{clash}' and '{resource_path}' both "
                f"declare the QML type '{path.stem}'. The engine resolves the "
                "name to one of them and the other is unreachable. Rename one "
                "file, or split them into two modules."
            )
        seen.add(resource_path)
        declared_by[path.stem] = resource_path
        entries.append((resource_path, path))
    return entries


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
            qml_files: QML files to embed, relative to the directory of
                the build script declaring the module (or absolute, and
                under it). Each entry is also the file's path inside the
                module resource, and its stem is the QML type name. Two
                entries sharing a stem are an error, and so is an entry
                landing outside that directory.
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
        root = project._path_resolver.project_root
        qml_root = project.current_dir
        qml_entries = _qml_entries(name, qml_files, qml_root)

        # ---- C++ type registration (only when there are moc'ed types) ----
        registrar_node: Node | None = None
        qmltypes_name = f"{name}.qmltypes"
        resolver = project._path_resolver
        metatypes = info.metatypes_node
        if metatypes is not None:
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
        for resource_path, qml_path in qml_entries:
            # The qmldir is written now, from the file's own content, so a
            # pragma added later has to re-run pcons and not only rcc.
            project.add_configure_dependency(qml_root / qml_path)
            kind = "singleton " if _declares_singleton(qml_root / qml_path) else ""
            qmldir_lines.append(
                f"{kind}{qml_path.stem} {major}.{minor} {resource_path}"
            )
        _write_if_changed(root / qt_dir / "qmldir", "\n".join(qmldir_lines) + "\n")

        # ---- resources under :/qt/qml/<uri>/ ---------------------------
        entries = [(alias, qml_root / qml) for alias, qml in qml_entries]
        entries.append(("qmldir", root / qt_dir / "qmldir"))
        if registrar_node is not None:
            entries.append((qmltypes_name, root / qt_dir / qmltypes_name))
        qrc_rel = qt_dir / f"{name}.qrc"
        _write_if_changed(root / qrc_rel, _qrc_xml(f"/qt/qml/{uri_path}", entries))

        rcc_node = qt_env.qt.Rcc(
            qt_dir / f"qrc_{name}.cpp", project.node(qrc_rel), name=f"qml_{name}"
        )[0]
        if registrar_node is not None:
            # rcc embeds the .qmltypes the registrar writes.
            rcc_node.implicit_deps.append(registrar_node)
        target.add_sources([rcc_node])

        _qml_module_sources.setdefault(project.top, []).append(
            (target, _source_dirs(root, qml_files))
        )
        return target


def _source_dirs(root: Path, qml_files: Sequence[str | Path]) -> list[Path]:
    dirs: list[Path] = []
    for qml in qml_files:
        directory = (root / Path(qml)).parent
        if directory not in dirs:
            dirs.append(directory)
    return dirs

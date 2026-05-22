# SPDX-License-Identifier: MIT
"""JSON metadata generator for IDE integration.

Generates structured metadata about project targets (programs, libraries,
and other target kinds) so IDE plugins can query available targets and
their relationships.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.node import FileNode
from pcons.core.test import TestSpec
from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


class MetadataGenerator(BaseGenerator):
    """Generator that writes IDE-friendly target metadata as JSON."""

    def __init__(self, *, output_filename: str = "pcons_metadata.json") -> None:
        super().__init__("metadata")
        self._output_filename = output_filename

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate metadata JSON file.

        Args:
            project: Configured project to introspect.
            output_dir: Directory to write metadata file to.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / self._output_filename

        metadata: dict[str, Any] = {
            "schema_version": 2,
            "generator": self.name,
            "projects": [
                self._serialize_project(project),
                *[self._serialize_project(p) for p in project._children],
            ],
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            f.write("\n")

    def _serialize_project(self, project: Project) -> dict[str, Any]:
        """Serialize project-level metadata."""
        default_target_names = {target.name for target in project.default_targets}
        return {
            "name": project.name,
            "parent": project.parent.name if not project.is_top_level else None,
            "root_dir": project._path_resolver.make_project_relative(project.root_dir),
            "build_dir": project.build_dir.as_posix(),
            "targets": [
                self._serialize_target(target, project, default_target_names)
                for target in sorted(project.targets, key=lambda t: t.name)
            ],
            "aliases": [
                self._serialize_alias(name, project) for name in sorted(project.aliases)
            ],
        }

    def _serialize_target(
        self,
        target: Target,
        project: Project,
        default_target_names: set[str],
    ) -> dict[str, Any]:
        """Serialize one target to metadata."""
        outputs = [
            project._path_resolver.make_project_relative(node.path)
            for node in target.output_nodes
            if isinstance(node, FileNode)
        ]
        sources = [
            project._path_resolver.make_project_relative(node.path)
            for node in target.sources
            if isinstance(node, FileNode)
        ]
        dependencies = sorted({dep.name for dep in target.dependencies})

        location: dict[str, Any] = {
            "file": project._path_resolver.make_project_relative(
                Path(target.defined_at.filename)
            ),
            "line": target.defined_at.lineno,
        }
        if target.defined_at.function is not None:
            location["function"] = target.defined_at.function

        entry: dict[str, Any] = {
            "name": target.name,
            "qualified_name": target.qualified_name,
            "sub_directory": target._subdir,
            "type": target.target_type or "other",
            "is_default": target.name in default_target_names,
            "dependencies": dependencies,
            "sources": sources,
            "outputs": outputs,
            "defined_at": location,
        }

        # For Test() targets, embed the resolved TestSpec so IDE
        # integrations (e.g., the VSCode TestExplorer) have everything
        # they need to discover, group, and run tests from this one
        # metadata file — no need to also parse tests.json. The outer
        # entry's `defined_at` already gives CodeLens-style {file, line};
        # the embedded spec includes its own stringified defined_at for
        # diagnostic completeness.
        spec = target._builder_data.get("spec") if target._builder_data else None
        if isinstance(spec, TestSpec):
            entry["test"] = spec.to_jsonable()

        return entry

    def _serialize_alias(self, alias_name: str, project: Project) -> dict[str, Any]:
        """Serialize one alias to metadata."""

        alias = project.aliases[alias_name]
        entries: list[str] = []
        for node in alias.targets:
            if isinstance(node, FileNode):
                entries.append(project._path_resolver.make_project_relative(node.path))
            else:
                pass  # Ignore for now

        return {
            "name": alias_name,
            "entries": entries,
        }

# SPDX-License-Identifier: MIT
"""JSON metadata generator for IDE integration.

Generates structured metadata about project targets (programs, libraries,
and other target kinds) so IDE plugins can query available targets and
their relationships.

A target is addressed in this file by its ``id``, which is unique within the
file and the same on every run of an unchanged build. The id is the target's
qualified name, and for an anonymous one (see ``Target.anonymous``, whose
name is a label its builder derived) a ``#n`` suffix: its position, in
declaration order, among the anonymous targets of its project wearing that
qualified name. ``dependencies`` is a list of ids, in the order the target
depends on them. ``anonymous`` says which kind of target this is.

``name`` and ``qualified_name`` are for display. Neither is unique: two
installs into one directory wear one label, and so do two tests of one name.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.node import FileNode
from pcons.core.test import TestSpec
from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.core.tiers import BuildTiers


def _project_target_ids(project: Project) -> dict[int, str]:
    """Ids for one project's own targets, keyed by target identity.

    See the module docstring for the rule.
    """
    ids: dict[int, str] = {}
    ordinals: Counter[str] = Counter()
    for target in project._targets:
        qualified = target.qualified_name
        if target.anonymous:
            ordinals[qualified] += 1
            ids[id(target)] = f"{qualified}#{ordinals[qualified]}"
        else:
            ids[id(target)] = qualified
    return ids


class MetadataGenerator(BaseGenerator):
    """Generator that writes IDE-friendly target metadata as JSON."""

    def __init__(self, *, output_filename: str = "pcons_metadata.json") -> None:
        super().__init__("metadata")
        self._output_filename = output_filename
        self._ids: dict[int, str] = {}

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate the metadata JSON file in output_dir."""
        from pcons.core.tiers import decide_build_tiers

        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / self._output_filename

        # One decision for the whole tree, as the build generators use.
        tiers = decide_build_tiers(project)
        self._ids = {}
        metadata: dict[str, Any] = {
            "schema_version": 4,
            "generator": self.name,
            "projects": [
                self._serialize_project(p, tiers) for p in self._walk_projects(project)
            ],
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            f.write("\n")

    def _id_of(self, target: Target) -> str:
        """The id this file gives *target*.

        A dependency may reach a project the walk has not come to yet, or one
        outside this file altogether; either way its project settles its id.
        """
        known = self._ids.get(id(target))
        if known is None:
            self._ids.update(_project_target_ids(target.project))
            known = self._ids[id(target)]
        return known

    def _walk_projects(self, project: Project) -> list[Project]:
        """Flatten the project tree depth-first (root before descendants)."""
        result = [project]
        for child in project._children:
            result.extend(self._walk_projects(child))
        return result

    def _serialize_project(self, project: Project, tiers: BuildTiers) -> dict[str, Any]:
        """Serialize project-level metadata."""
        return {
            "name": project.name,
            "parent": project.parent.name if not project.is_top_level else None,
            "root_dir": project.top_path_resolver.make_project_relative(
                project.root_dir
            ),
            "build_dir": project.build_dir.as_posix(),
            "targets": [
                self._serialize_target(target, project, tiers)
                for target in sorted(project._targets, key=lambda t: t.name)
            ],
            "aliases": [
                self._serialize_alias(name, project) for name in sorted(project.aliases)
            ],
        }

    def _serialize_target(
        self,
        target: Target,
        project: Project,
        tiers: BuildTiers,
    ) -> dict[str, Any]:
        """Serialize one target to metadata."""
        outputs = [
            project.top_path_resolver.make_project_relative(node.path)
            for node in target.output_nodes
            if isinstance(node, FileNode)
        ]
        sources = [
            project.top_path_resolver.make_project_relative(node.path)
            for node in target.sources
            if isinstance(node, FileNode)
        ]
        dependencies = [self._id_of(dep) for dep in target.dependencies]
        decision = tiers.get(target)

        location: dict[str, Any] = {
            "file": project.top_path_resolver.make_project_relative(
                Path(target.defined_at.filename)
            ),
            "line": target.defined_at.lineno,
        }
        if target.defined_at.function is not None:
            location["function"] = target.defined_at.function

        entry: dict[str, Any] = {
            "id": self._id_of(target),
            "name": target.name,
            "qualified_name": target.qualified_name,
            "anonymous": target.anonymous,
            "sub_directory": str(target._subdir) if target._subdir.parts else None,
            "type": target.target_type or "other",
            "build_tier": decision.tier if decision else target.build_tier,
            "is_default": bool(decision and decision.tier == "default"),
            "dependencies": dependencies,
            "sources": sources,
            "outputs": outputs,
            "defined_at": location,
        }

        # Embed the resolved TestSpec so IDE integrations can discover and
        # run tests from this file alone, without also parsing tests.json.
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
                entries.append(
                    project.top_path_resolver.make_project_relative(node.path)
                )
            else:
                pass  # Ignore for now

        return {
            "name": alias_name,
            "entries": entries,
        }

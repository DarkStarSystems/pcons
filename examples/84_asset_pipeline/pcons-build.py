# SPDX-License-Identifier: MIT
"""A custom three-step builder, and what depends() means to each step.

``AssetBundle`` compiles every scene to a ``.abin``, packs them into one
``.pak``, and writes a ``.manifest`` for the pack: three steps, in one
target, with the step logic in one place (``AssetBundleFactory`` below).
The tool, ``tools/assetc.py``, exists only for this contrived example.

The build script says ``bundle.depends(palette)`` once, and pcons builds
the palette before any step of the bundle. Which step reads it is not the
script's business; in the builder, each step decides for itself. The
compile step *discovers* its dependencies (the tool reports what it read
in a depfile), so it holds the palette order-only and its depfile decides
whether a palette change recompiles a scene; ``pack`` and ``manifest``
discover nothing, so they hold the palette as a regular implicit
dependency and rerun whenever it changes.

The options file is the case a step cannot discover: ``assetc compile``
reads it but does not report it, the way a compiler reads a response file.
Only the builder knows which step reads it, so the builder declares it on
that step's node, and a change to the options recompiles every scene.

Usage:
  pcons
  cat build/level.manifest
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons import Project
from pcons.core.builder import GenericCommandBuilder
from pcons.core.builder_registry import builder
from pcons.core.node import FileNode
from pcons.core.target import Target
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment

ASSETC = [sys.executable, "$SRCDIR/tools/assetc.py"]


class AssetBundleTarget(Target):
    """An AssetBundle: the scenes it compiles, and the options file they read."""

    def __init__(self, name: str, *, options: str, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self.options = options


class AssetBundleFactory:
    """Turns an AssetBundle target into its three build steps."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def resolve(self, target: Target, env: Environment | None) -> None:
        assert env is not None and isinstance(target, AssetBundleTarget)
        options = target.options

        # Step 1: one compile per scene. The tool writes a depfile naming the
        # scene, its textures and the palette, so the step discovers its own
        # dependencies. The options file it reads but never reports cannot be
        # discovered, so the builder declares it, on exactly this node.
        compile_scene = GenericCommandBuilder(
            [
                *ASSETC,
                "compile",
                "$SOURCE",
                "-o",
                "$TARGET",
                "--depfile",
                "$TARGET.d",
                "--palette",
                "palette.txt",
                "--options",
                f"$SRCDIR/{options}",
            ],
            depfile=".d",
            deps_style="gcc",
        )
        compiled: list[FileNode] = []
        for scene in target.sources:
            (abin,) = compile_scene(env, f"abin/{Path(scene.name).stem}.abin", [scene])
            assert isinstance(abin, FileNode)
            abin.depends(self.project.node(options))
            compiled.append(abin)

        # Steps 2 and 3: pack everything, then write the manifest, the
        # target's output. Neither discovers anything, so each holds every
        # dependency of the bundle as a regular implicit dependency.
        pack = GenericCommandBuilder([*ASSETC, "pack", "$SOURCES", "-o", "$TARGET"])
        manifest = GenericCommandBuilder(
            [*ASSETC, "manifest", "$SOURCE", "-o", "$TARGET"]
        )
        (pak,) = pack(env, f"{target.name}.pak", list(compiled))
        (out,) = manifest(env, f"{target.name}.manifest", [pak])
        assert isinstance(pak, FileNode) and isinstance(out, FileNode)

        target.intermediate_nodes.extend([*compiled, pak])
        target.output_nodes.append(out)

    def resolve_pending(self, target: Target) -> None:
        pass


@builder(
    "AssetBundle",
    target_type="asset_bundle",
    requires_env=True,
    factory_class=AssetBundleFactory,
    description="Compile scenes, pack them, and write a manifest",
)
class AssetBundleBuilder:
    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: Sequence[str],
        *,
        options: str,
    ) -> Target:
        target = AssetBundleTarget(
            name,
            options=options,
            target_type="asset_bundle",
            defined_at=get_caller_location(),
            project=project,
            env=env,
        )
        target._builder_name = "AssetBundle"
        target.add_sources(sources)
        return target


project = Project("asset_pipeline")
env = project.Environment()

# The palette is generated. Every scene compile reads it (and says so in its
# depfile), so the bundle depends on the target that makes it.
palette = env.Command(
    target="palette.txt",
    source="palette.src",
    command=[
        sys.executable,
        "$SRCDIR/tools/assetc.py",
        "palette",
        "$SOURCE",
        "-o",
        "$TARGET",
    ],
)

bundle = project.AssetBundle(  # ty: ignore[unresolved-attribute]
    "level",
    env,
    sources=["scenes/forest.scene", "scenes/cave.scene"],
    options="assetc.opts",
)
bundle.depends(palette)

project.Default(bundle)

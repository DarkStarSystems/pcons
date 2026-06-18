# SPDX-License-Identifier: MIT
"""Cargo integration: build a Rust crate and consume it like a normal library.

Treats `cargo build` as a black-box sub-build. pcons drives cargo via
``env.Command(restat=True)`` so cargo's own incremental logic decides
whether anything needs recompiling, while Ninja decides whether to
relink downstream C/C++ consumers (only when the artifact actually
changes).

The returned target is an ``ImportedTarget`` shaped like any other
library: ``app.link(rust_core)`` propagates ``-I`` (if a header was
generated) and ``-L``/``-l`` flags through the normal usage-requirement
machinery.

Example:
    rust_core = project.CargoBuild(
        "rust_core",
        env,
        manifest="rust/Cargo.toml",
        crate_type="staticlib",
        generate_header="rust/cbindgen.toml",
    )
    app = project.Program("app", env, sources=["src/main.cpp"])
    app.link(rust_core)
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.packages.description import PackageDescription
from pcons.packages.imported import ImportedTarget

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


_CRATE_TYPES = frozenset({"staticlib", "cdylib", "bin"})


def _read_crate_name(manifest: Path) -> str:
    """Read the crate (library) name from a Cargo.toml.

    Uses the [lib] name if set, otherwise [package] name with hyphens
    replaced by underscores (cargo's own rule for library file names).
    """
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    lib_table = data.get("lib", {})
    if isinstance(lib_table, dict) and "name" in lib_table:
        return str(lib_table["name"])
    pkg_table = data.get("package", {})
    pkg_name = pkg_table.get("name")
    if not pkg_name:
        raise ValueError(f"Cargo.toml at {manifest} has no [package] name")
    return str(pkg_name).replace("-", "_")


def _artifact_filename(crate_name: str, crate_type: str) -> str:
    """Compute the on-disk filename cargo produces for a given crate type."""
    if crate_type == "staticlib":
        if sys.platform == "win32":
            return f"{crate_name}.lib"
        return f"lib{crate_name}.a"
    if crate_type == "cdylib":
        if sys.platform == "win32":
            return f"{crate_name}.dll"
        if sys.platform == "darwin":
            return f"lib{crate_name}.dylib"
        return f"lib{crate_name}.so"
    if crate_type == "bin":
        if sys.platform == "win32":
            return f"{crate_name}.exe"
        return crate_name
    raise ValueError(
        f"Unsupported crate_type {crate_type!r}; expected one of {sorted(_CRATE_TYPES)}"
    )


def _profile_subdir(profile: str) -> str:
    """Map a cargo profile name to its target/ subdirectory."""
    # "dev" is the source name; cargo writes it to target/debug/.
    return "debug" if profile == "dev" else profile


def _collect_rust_sources(manifest_dir: Path) -> list[Path]:
    """Best-effort glob of files that should trigger a cargo rerun.

    Picks up .rs files, Cargo.toml, and Cargo.lock under the manifest
    dir. Workspace members elsewhere on disk won't be tracked — that's
    cargo's job to detect when it runs.
    """
    deps: list[Path] = []
    for pattern in ("**/*.rs", "Cargo.toml", "Cargo.lock"):
        for p in manifest_dir.glob(pattern):
            if "target" in p.parts:
                continue
            deps.append(p)
    return deps


@builder(
    "CargoBuild",
    target_type="cargo",
    description="Build a Rust crate via cargo and expose it as a library",
)
class CargoBuildBuilder:
    """Build a Rust crate via cargo and return it as an ImportedTarget.

    The returned target carries the appropriate -L/-l/-I flags so that
    ``consumer.link(rust_target)`` Just Works.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        manifest: str | Path,
        crate_type: str = "staticlib",
        profile: str = "release",
        features: Sequence[str] = (),
        generate_header: str | Path | None = None,
        target_triple: str | None = None,
        extra_args: Sequence[str] = (),
        cargo: str = "cargo",
    ) -> Target:
        """Create a CargoBuild target.

        Args:
            project: The Project this target belongs to.
            name: Target name (and link library name unless overridden by
                  the crate's [lib] name in Cargo.toml).
            env: Environment used to register the underlying Command rule.
            manifest: Path to the crate's Cargo.toml (relative to project
                      root or absolute).
            crate_type: "staticlib", "cdylib", or "bin".
            profile: Cargo profile name. "release" → target/release/,
                     "dev" → target/debug/, anything else → target/<name>/.
            features: Cargo features to enable.
            generate_header: Path to a cbindgen.toml. If given, runs
                             cbindgen as a second command to produce a C
                             header in the build dir.
            target_triple: Optional target triple for cross-compilation
                           (e.g., "wasm32-wasi"). Adds a subdir under
                           target/.
            extra_args: Extra args appended to the cargo invocation.
            cargo: Cargo executable name or path.

        Returns:
            An ImportedTarget with the right usage requirements; depends
            on the underlying cargo (and optional cbindgen) Command
            targets so Ninja rebuilds the consumer when the artifact
            changes.
        """
        if crate_type not in _CRATE_TYPES:
            raise ValueError(
                f"crate_type {crate_type!r} not supported; expected one of {sorted(_CRATE_TYPES)}"
            )

        manifest_path = Path(manifest)
        if not manifest_path.is_absolute():
            manifest_path = project.root_dir / manifest_path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Cargo.toml not found: {manifest_path}")

        manifest_dir = manifest_path.parent
        crate_name = _read_crate_name(manifest_path)

        # Per-target output directory, kept inside the build dir so it's
        # easy to clean and doesn't collide with a user's own cargo runs.
        # Two views of the same directory: a project-relative one for the
        # pcons node graph, and an absolute one for the cargo command
        # (which runs from ninja's build dir, not project root).
        target_root = project.build_dir / "cargo" / name
        target_root_abs = (project.root_dir / target_root).resolve()
        profile_dir = _profile_subdir(profile)
        artifact_dir = target_root / profile_dir
        if target_triple:
            artifact_dir = target_root / target_triple / profile_dir

        artifact_path = artifact_dir / _artifact_filename(crate_name, crate_type)

        # Build the cargo command line.
        cargo_cmd: list[str] = [
            cargo,
            "build",
            f"--manifest-path={manifest_path}",
            f"--target-dir={target_root_abs}",
        ]
        if profile == "release":
            cargo_cmd.append("--release")
        elif profile != "dev":
            cargo_cmd.extend(["--profile", profile])
        if features:
            cargo_cmd.extend(["--features", ",".join(features)])
        if target_triple:
            cargo_cmd.extend(["--target", target_triple])
        cargo_cmd.extend(extra_args)

        rust_sources = _collect_rust_sources(manifest_dir)

        # Pass the command as a list so pcons treats each element as a
        # single token. Shell-quoting individual tokens would wrap pcons
        # specials like $TARGET in single quotes and prevent expansion.
        cargo_target = env.Command(
            name=f"{name}_cargo",
            target=artifact_path,
            source=None,
            depends=rust_sources,
            command=cargo_cmd,
            restat=True,
        )

        # Optional cbindgen header generation.
        cbindgen_target: Target | None = None
        include_dir: Path | None = None
        if generate_header is not None:
            cbindgen_config = Path(generate_header)
            if not cbindgen_config.is_absolute():
                cbindgen_config = project.root_dir / cbindgen_config
            include_dir = target_root / "include"
            header_path = include_dir / f"{crate_name}.h"

            cbindgen_cmd = [
                "cbindgen",
                "--config",
                str(cbindgen_config),
                "--crate",
                crate_name,
                "--output",
                "$TARGET",
                str(manifest_dir),
            ]

            cbindgen_target = env.Command(
                name=f"{name}_cbindgen",
                target=header_path,
                source=None,
                depends=[cbindgen_config, *rust_sources],
                command=cbindgen_cmd,
                restat=True,
            )

        # Wrap as an ImportedTarget so consumers' link() picks up flags.
        pkg = PackageDescription(
            name=name,
            libraries=[crate_name],
            library_dirs=[str(artifact_dir)],
            include_dirs=[str(include_dir)] if include_dir else [],
            found_by="cargo",
        )
        # from_package() creates the target, which auto-registers with the
        # current project via Target.__init__.
        imported = ImportedTarget.from_package(pkg)

        # The imported wrapper depends on the underlying cargo (and
        # cbindgen) Command targets. The compile_link machinery walks
        # transitive_dependencies() to collect output nodes for the
        # linker, so the .a/.lib gets wired in as an implicit dep of
        # the link step automatically.
        imported.add_dependency(cargo_target)
        if cbindgen_target is not None:
            imported.add_dependency(cbindgen_target)

        return imported

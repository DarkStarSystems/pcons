# SPDX-License-Identifier: MIT
"""Archive build context for Tarfile and Zipfile builders.

This module provides context classes that implement the ToolchainContext protocol
for archive creation. These classes compute effective archive settings from
environment defaults and target-level overrides.

The context approach decouples the core from archive-specific concepts:
- Core only knows about ToolchainContext.get_variables() -> dict[str, list[str]]
- ArchiveContext/InstallContext define what variables exist and their values
- Generators write variables without knowing their semantics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.target import Target


@dataclass
class ArchiveContext:
    """Context for archive creation (tar/zip).

    This class implements the ToolchainContext protocol for archive builders.
    It holds all the information needed for archive creation and provides
    variables suitable for command templates.

    The formatting is done here rather than in the generator, allowing
    different archive formats to use different flag formats.

    Attributes:
        compression: Compression type for tar (None, "gzip", "bz2", "xz").
        basedir: Base directory for computing relative paths in archive.
        archive_type: Type of archive ("tar" or "zip").
    """

    compression: str | None = None
    basedir: str = "."
    archive_type: str = "tar"

    def get_variables(self) -> dict[str, list[str]]:
        """Return variables for build statement.

        Keys match placeholders in command templates:
        - compression_flag: Compression flag (e.g., ["--compression", "gzip"])
        - basedir: Base directory flag (e.g., ["src"])

        Values are lists of individual tokens. The generator is responsible
        for joining them with appropriate quoting for the target format.

        Returns:
            Dictionary mapping variable names to lists of string tokens.
        """
        result: dict[str, list[str]] = {}

        # basedir is always set (defaults to ".")
        result["basedir"] = [self.basedir]

        # compression_flag only for tar archives with compression
        if self.archive_type == "tar" and self.compression:
            result["compression_flag"] = ["--compression", self.compression]
        else:
            # Empty list so the placeholder expands to nothing
            result["compression_flag"] = []

        return result

    @classmethod
    def from_target(
        cls, target: Target, env: Environment | None = None
    ) -> ArchiveContext:
        """Create an ArchiveContext from a target and optional environment.

        Merges environment defaults with target-level overrides.
        Target settings take precedence over environment settings.

        Priority (highest to lowest):
        1. Target properties (target.compression, target.basedir)
        2. Target builder_data (from create_target call)
        3. Environment defaults (env.archive.compression, env.archive.basedir)
        4. Built-in defaults

        Args:
            target: The archive target being built (should be ArchiveTarget).
            env: Optional environment with archive defaults.

        Returns:
            An ArchiveContext populated from target and environment.
        """
        # Start with defaults
        compression: str | None = None
        basedir = "."
        archive_type = "tar"

        # Get builder data from target
        builder_data = getattr(target, "_builder_data", None) or {}
        archive_type = builder_data.get("tool", "tarfile")
        if archive_type == "tarfile":
            archive_type = "tar"
        elif archive_type == "zipfile":
            archive_type = "zip"

        # Environment defaults (if available) - lowest priority
        if env is not None:
            archive_config = getattr(env, "archive", None)
            if archive_config is not None:
                env_compression = getattr(archive_config, "compression", None)
                if env_compression is not None:
                    compression = env_compression
                env_basedir = getattr(archive_config, "basedir", None)
                if env_basedir is not None:
                    basedir = str(env_basedir)

        # Target-level overrides from _builder_data (from create_target call)
        if builder_data.get("compression") is not None:
            compression = builder_data["compression"]
        if builder_data.get("base_dir") is not None:
            basedir = builder_data["base_dir"]

        # Check for ArchiveTarget properties (highest priority)
        # These are set via target.compression = "xz" after creation
        # Check for property overrides in __dict__ (ArchiveTarget stores them there)
        target_dict = getattr(target, "__dict__", {})
        if "_compression_override" in target_dict:
            compression = target_dict["_compression_override"]
        if "_basedir_override" in target_dict:
            basedir = target_dict["_basedir_override"]

        return cls(
            compression=compression,
            basedir=basedir,
            archive_type=archive_type,
        )


@dataclass
class InstallContext:
    """Context for install operations (copy, copytree).

    This class implements the ToolchainContext protocol for install builders.
    It holds all the information needed for file/directory installation and
    provides variables suitable for command templates.

    Attributes:
        destdir: Destination directory for InstallDir operations.
        install_type: Type of install ("copy" or "copytree").
    """

    destdir: str = ""
    install_type: str = "copy"

    def get_variables(self) -> dict[str, list[str]]:
        """Return variables for build statement.

        Keys match placeholders in command templates:
        - destdir: Destination directory (e.g., ["dist/bin"])

        Values are lists of individual tokens. The generator is responsible
        for joining them with appropriate quoting for the target format.

        Returns:
            Dictionary mapping variable names to lists of string tokens.
        """
        result: dict[str, list[str]] = {}

        if self.destdir:
            result["destdir"] = [self.destdir]

        return result

    @classmethod
    def from_target(
        cls, target: Target, env: Environment | None = None, destdir: str = ""
    ) -> InstallContext:
        """Create an InstallContext from a target and optional environment.

        Merges environment defaults with target-level overrides.
        Target settings take precedence over environment settings.

        Args:
            target: The install target being built.
            env: Optional environment with install defaults.
            destdir: Destination directory (for InstallDir).

        Returns:
            An InstallContext populated from target and environment.
        """
        # Start with provided destdir
        effective_destdir = destdir

        # Determine install type from builder name
        builder_name = getattr(target, "_builder_name", "Install")
        install_type = "copytree" if builder_name == "InstallDir" else "copy"

        # Environment defaults (if available)
        if env is not None:
            install_config = getattr(env, "install", None)
            if install_config is not None:
                env_destdir = getattr(install_config, "destdir", None)
                if env_destdir is not None and not effective_destdir:
                    effective_destdir = str(env_destdir)

        # Target-level overrides
        target_destdir = getattr(target, "_install_destdir", None)
        if target_destdir is not None:
            effective_destdir = target_destdir

        return cls(
            destdir=effective_destdir,
            install_type=install_type,
        )

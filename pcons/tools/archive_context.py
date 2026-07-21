# SPDX-License-Identifier: MIT
"""Archive build context for Tarfile and Zipfile builders.

ToolchainContext implementations that compute effective archive/install
settings from environment defaults and target-level overrides.
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

    Attributes:
        compression: Compression type for tar (None, "gzip", "bz2", "xz").
        basedir: Base directory for computing relative paths in archive.
        archive_type: Type of archive ("tar" or "zip").
    """

    compression: str | None = None
    basedir: str = "."
    archive_type: str = "tar"

    def get_env_overrides(self) -> dict[str, str | list[str]]:
        """Return values to set on env.archive.* before subst()."""
        result: dict[str, str | list[str]] = {}

        result["basedir"] = self.basedir

        # A list expands to multiple tokens (--compression, gzip);
        # empty list expands to nothing.
        if self.archive_type == "tar" and self.compression:
            result["compression_flag"] = ["--compression", self.compression]
        else:
            result["compression_flag"] = []

        return result

    @classmethod
    def from_target(
        cls, target: Target, env: Environment | None = None
    ) -> ArchiveContext:
        """Create an ArchiveContext from a target and optional environment.

        Priority (highest to lowest): target properties, target
        builder_data, environment defaults, built-in defaults.

        Args:
            target: The archive target being built (should be ArchiveTarget).
            env: Optional environment with archive defaults.
        """
        compression: str | None = None
        basedir = "."
        archive_type = "tar"

        builder_data = getattr(target, "_builder_data", None) or {}
        archive_type = builder_data.get("tool", "tarfile")
        if archive_type == "tarfile":
            archive_type = "tar"
        elif archive_type == "zipfile":
            archive_type = "zip"

        if env is not None:
            archive_config = getattr(env, "archive", None)
            if archive_config is not None:
                env_compression = getattr(archive_config, "compression", None)
                if env_compression is not None:
                    compression = env_compression
                env_basedir = getattr(archive_config, "basedir", None)
                if env_basedir is not None:
                    basedir = str(env_basedir)

        if builder_data.get("compression") is not None:
            compression = builder_data["compression"]
        if builder_data.get("base_dir") is not None:
            basedir = builder_data["base_dir"]

        # Property overrides (target.compression = "xz" after creation);
        # ArchiveTarget stores them in __dict__.
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

    Attributes:
        destdir: Destination directory for InstallDir operations.
        install_type: Type of install ("copy" or "copytree").
    """

    destdir: str = ""
    install_type: str = "copy"

    def get_env_overrides(self) -> dict[str, str]:
        """Return values to set on env.install.* before subst()."""
        result: dict[str, str] = {}

        if self.destdir:
            result["destdir"] = self.destdir

        return result

    @classmethod
    def from_target(
        cls, target: Target, env: Environment | None = None, destdir: str = ""
    ) -> InstallContext:
        """Create an InstallContext from a target and optional environment.

        Target settings take precedence over environment settings.

        Args:
            target: The install target being built.
            env: Optional environment with install defaults.
            destdir: Destination directory (for InstallDir).
        """
        effective_destdir = destdir

        builder_name = getattr(target, "_builder_name", "Install")
        install_type = "copytree" if builder_name == "InstallDir" else "copy"

        if env is not None:
            install_config = getattr(env, "install", None)
            if install_config is not None:
                env_destdir = getattr(install_config, "destdir", None)
                if env_destdir is not None and not effective_destdir:
                    effective_destdir = str(env_destdir)

        target_destdir = getattr(target, "_install_destdir", None)
        if target_destdir is not None:
            effective_destdir = target_destdir

        return cls(
            destdir=effective_destdir,
            install_type=install_type,
        )

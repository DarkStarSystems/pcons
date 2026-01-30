# SPDX-License-Identifier: MIT
"""Xcode project generator.

Generates .xcodeproj bundles from a configured pcons Project.
The generated project is fully buildable with xcodebuild.

Supported:
    - Program, StaticLibrary, SharedLibrary (PBXNativeTarget)
    - Install, InstallAs, InstallDir (PBXAggregateTarget with shell scripts)
    - Tarfile, Zipfile (PBXAggregateTarget with shell scripts)
    - Target dependencies, compile flags, defines, include paths
    - Debug/Release configurations

Limitations:
    - Source generators / custom commands with dependency tracking are not
      supported. Xcode's PBXShellScriptBuildPhase doesn't support depfiles,
      so commands that generate source files won't trigger proper rebuilds.
    - ObjectLibrary is not directly representable in Xcode's target model.
    - Aliases don't have a direct Xcode equivalent.

Path handling:
    - Xcode puts built products in Release/ or Debug/ subdirectories
    - Shell scripts run from the build directory (where .xcodeproj lives)
    - Source files use "../" paths to reach the project root
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pbxproj import XcodeProject
from pbxproj.pbxextensions.ProjectFiles import FileOptions
from pbxproj.PBXGenericObject import PBXGenericObject

from pcons.generators.generator import BaseGenerator

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target

# Map pcons target types to Xcode product types
PRODUCT_TYPE_MAP = {
    "program": "com.apple.product-type.tool",  # Command-line tool
    "static_library": "com.apple.product-type.library.static",
    "shared_library": "com.apple.product-type.library.dynamic",
}

# Target types that should be created as PBXAggregateTarget
AGGREGATE_TARGET_TYPES = {"interface", "archive"}

# Map product types to explicit file types
EXPLICIT_FILE_TYPE_MAP = {
    "com.apple.product-type.tool": "compiled.mach-o.executable",
    "com.apple.product-type.library.static": "archive.ar",
    "com.apple.product-type.library.dynamic": "compiled.mach-o.dylib",
}


def _generate_id() -> str:
    """Generate a 24-character hex ID like Xcode uses."""
    return uuid.uuid4().hex[:24].upper()


class XcodeGenerator(BaseGenerator):
    """Generator that produces Xcode project files.

    Generates a complete .xcodeproj bundle that can be built with xcodebuild
    or opened in Xcode for IDE features and building.

    Example:
        project = Project("myapp", build_dir="build")
        # ... configure project ...

        generator = XcodeGenerator()
        generator.generate(project)
        # Creates build/myapp.xcodeproj/

        # Build with: xcodebuild -project build/myapp.xcodeproj
    """

    def __init__(self) -> None:
        super().__init__("xcode")
        self._xcode_project: XcodeProject | None = None
        self._output_dir: Path | None = None
        self._project_root: Path | None = None
        self._pcons_project: Project | None = None  # Reference to pcons project
        self._target_ids: dict[str, str] = {}  # pcons target name -> Xcode target id
        self._objects: dict[str, dict[str, Any]] = {}
        self._main_group_id: str = ""
        self._products_group_id: str = ""
        self._sources_group_id: str = ""
        self._topdir: str = ".."  # Relative path from output_dir to project root

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate .xcodeproj bundle.

        Args:
            project: Configured project to generate for.
            output_dir: Directory to write .xcodeproj to.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        self._output_dir = output_dir.resolve()
        self._project_root = project.root_dir.resolve()
        self._pcons_project = project
        self._target_ids = {}
        self._objects = {}

        # Compute relative path from output_dir to project root
        # This is used for source file paths in the Xcode project
        import os

        try:
            self._topdir = os.path.relpath(self._project_root, self._output_dir)
        except ValueError:
            # On Windows, relpath fails for paths on different drives
            self._topdir = str(self._project_root)

        # Create xcodeproj bundle path
        xcodeproj_path = output_dir / f"{project.name}.xcodeproj"
        xcodeproj_path.mkdir(parents=True, exist_ok=True)
        pbxproj_path = xcodeproj_path / "project.pbxproj"

        # Build the project tree structure
        tree = self._create_project_tree(project)

        # If no buildable targets, don't create the project
        if not self._target_ids:
            return

        # Create XcodeProject and save
        self._xcode_project = XcodeProject(tree, str(pbxproj_path))

        # Add source files using pbxproj's add_file (handles build files)
        for target in project.targets:
            self._add_sources_to_target(target)

        # Configure build settings for each target
        for target in project.targets:
            self._configure_build_settings(target)

        # Set up dependencies
        for target in project.targets:
            self._setup_dependencies(target)

        # Save the project
        self._xcode_project.save()

    def _create_project_tree(self, project: Project) -> dict[str, Any]:
        """Create the base Xcode project tree structure.

        Args:
            project: The pcons project.

        Returns:
            Dictionary tree for XcodeProject.
        """
        # Generate IDs for project-level objects
        proj_id = _generate_id()
        self._main_group_id = _generate_id()
        self._products_group_id = _generate_id()
        self._sources_group_id = _generate_id()
        proj_config_list_id = _generate_id()
        proj_debug_config_id = _generate_id()
        proj_release_config_id = _generate_id()

        objects: dict[str, dict[str, Any]] = {}
        target_ids: list[str] = []

        # Create targets first
        for target in project.targets:
            target_id = self._create_target_objects(target, objects)
            if target_id:
                target_ids.append(target_id)
                self._target_ids[target.name] = target_id

        # Project-level build configurations
        # SYMROOT = "." ensures build products go directly in build dir,
        # not in build/build/ (xcodebuild defaults SYMROOT to "build")
        objects[proj_debug_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {
                "ALWAYS_SEARCH_USER_PATHS": "NO",
                "CLANG_CXX_LANGUAGE_STANDARD": "gnu++20",
                "CLANG_CXX_LIBRARY": "libc++",
                "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym",
                "GCC_OPTIMIZATION_LEVEL": "0",
                "MACOSX_DEPLOYMENT_TARGET": "13.0",
                "SDKROOT": "macosx",
                "SYMROOT": ".",
            },
            "name": "Debug",
        }

        objects[proj_release_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {
                "ALWAYS_SEARCH_USER_PATHS": "NO",
                "CLANG_CXX_LANGUAGE_STANDARD": "gnu++20",
                "CLANG_CXX_LIBRARY": "libc++",
                "GCC_OPTIMIZATION_LEVEL": "s",
                "MACOSX_DEPLOYMENT_TARGET": "13.0",
                "SDKROOT": "macosx",
                "SYMROOT": ".",
            },
            "name": "Release",
        }

        objects[proj_config_list_id] = {
            "isa": "XCConfigurationList",
            "buildConfigurations": [proj_debug_config_id, proj_release_config_id],
            "defaultConfigurationIsVisible": "0",
            "defaultConfigurationName": "Release",
        }

        # Collect product references for the products group
        product_refs = [
            objects[tid].get("productReference")
            for tid in target_ids
            if "productReference" in objects.get(tid, {})
        ]

        # Groups
        objects[self._products_group_id] = {
            "isa": "PBXGroup",
            "children": [ref for ref in product_refs if ref],
            "name": "Products",
            "sourceTree": "<group>",
        }

        objects[self._sources_group_id] = {
            "isa": "PBXGroup",
            "children": [],
            "name": "Sources",
            "sourceTree": "<group>",
        }

        objects[self._main_group_id] = {
            "isa": "PBXGroup",
            "children": [self._sources_group_id, self._products_group_id],
            "sourceTree": "<group>",
        }

        # Project
        objects[proj_id] = {
            "isa": "PBXProject",
            "buildConfigurationList": proj_config_list_id,
            "compatibilityVersion": "Xcode 14.0",
            "developmentRegion": "en",
            "hasScannedForEncodings": "0",
            "knownRegions": ["en", "Base"],
            "mainGroup": self._main_group_id,
            "productRefGroup": self._products_group_id,
            "projectDirPath": "",
            "projectRoot": "",
            "targets": target_ids,
        }

        self._objects = objects

        return {
            "archiveVersion": "1",
            "classes": {},
            "objectVersion": "56",
            "objects": objects,
            "rootObject": proj_id,
        }

    def _create_target_objects(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> str | None:
        """Create PBX objects for a pcons target.

        Args:
            target: The pcons target.
            objects: The objects dictionary to add to.

        Returns:
            The target ID, or None if target should be skipped.
        """
        target_type_str = str(target.target_type) if target.target_type else None

        # Handle aggregate targets (interface, archive) - Install, InstallDir, Tarfile, etc.
        if target_type_str in AGGREGATE_TARGET_TYPES:
            return self._create_aggregate_target(target, objects)

        # Skip object-only targets
        if target.target_type in ("object", None):
            return None

        product_type = PRODUCT_TYPE_MAP.get(str(target.target_type))
        if product_type is None:
            return None

        # Generate IDs
        target_id = _generate_id()
        target_config_list_id = _generate_id()
        target_debug_config_id = _generate_id()
        target_release_config_id = _generate_id()
        product_ref_id = _generate_id()
        sources_phase_id = _generate_id()
        frameworks_phase_id = _generate_id()

        # Determine output name
        output_name = target.output_name or target.name
        product_name = output_name

        # Add appropriate prefix/suffix for libraries
        if target.target_type == "static_library":
            if not output_name.startswith("lib"):
                output_name = f"lib{output_name}"
            if not output_name.endswith(".a"):
                output_name = f"{output_name}.a"
        elif target.target_type == "shared_library":
            if not output_name.startswith("lib"):
                output_name = f"lib{output_name}"
            if not output_name.endswith(".dylib"):
                output_name = f"{output_name}.dylib"

        explicit_file_type = EXPLICIT_FILE_TYPE_MAP.get(
            product_type, "compiled.mach-o.executable"
        )

        # Target-level build configurations
        objects[target_debug_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {
                "PRODUCT_NAME": product_name,
            },
            "name": "Debug",
        }

        objects[target_release_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {
                "PRODUCT_NAME": product_name,
            },
            "name": "Release",
        }

        objects[target_config_list_id] = {
            "isa": "XCConfigurationList",
            "buildConfigurations": [target_debug_config_id, target_release_config_id],
            "defaultConfigurationIsVisible": "0",
            "defaultConfigurationName": "Release",
        }

        # Product reference
        objects[product_ref_id] = {
            "isa": "PBXFileReference",
            "explicitFileType": explicit_file_type,
            "includeInIndex": "0",
            "name": output_name,
            "path": output_name,
            "sourceTree": "BUILT_PRODUCTS_DIR",
        }

        # Build phases
        objects[sources_phase_id] = {
            "isa": "PBXSourcesBuildPhase",
            "buildActionMask": "2147483647",
            "files": [],
            "runOnlyForDeploymentPostprocessing": "0",
        }

        objects[frameworks_phase_id] = {
            "isa": "PBXFrameworksBuildPhase",
            "buildActionMask": "2147483647",
            "files": [],
            "runOnlyForDeploymentPostprocessing": "0",
        }

        # Native target
        objects[target_id] = {
            "isa": "PBXNativeTarget",
            "buildConfigurationList": target_config_list_id,
            "buildPhases": [sources_phase_id, frameworks_phase_id],
            "buildRules": [],
            "dependencies": [],
            "name": target.name,
            "productName": product_name,
            "productReference": product_ref_id,
            "productType": product_type,
        }

        return target_id

    def _create_aggregate_target(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> str | None:
        """Create PBXAggregateTarget for interface/archive targets.

        Aggregate targets are used for Install, InstallDir, Tarfile, Zipfile
        builders. They run shell script phases instead of compilation.

        Args:
            target: The pcons target.
            objects: The objects dictionary to add to.

        Returns:
            The target ID, or None if target has no output nodes.
        """
        # Skip targets without output nodes (unresolved)
        if not target.output_nodes:
            return None

        # Generate IDs
        target_id = _generate_id()
        target_config_list_id = _generate_id()
        target_debug_config_id = _generate_id()
        target_release_config_id = _generate_id()

        # Target-level build configurations
        objects[target_debug_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {},
            "name": "Debug",
        }

        objects[target_release_config_id] = {
            "isa": "XCBuildConfiguration",
            "buildSettings": {},
            "name": "Release",
        }

        objects[target_config_list_id] = {
            "isa": "XCConfigurationList",
            "buildConfigurations": [target_debug_config_id, target_release_config_id],
            "defaultConfigurationIsVisible": "0",
            "defaultConfigurationName": "Release",
        }

        # Create run script build phase(s) based on builder type
        build_phases = self._create_script_phases_for_target(target, objects)

        # PBXAggregateTarget
        objects[target_id] = {
            "isa": "PBXAggregateTarget",
            "buildConfigurationList": target_config_list_id,
            "buildPhases": build_phases,
            "dependencies": [],
            "name": target.name,
            "productName": target.name,
        }

        return target_id

    def _create_script_phases_for_target(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Create run script build phases for an aggregate target.

        Args:
            target: The pcons target.
            objects: The objects dictionary to add to.

        Returns:
            List of phase IDs.
        """
        phases: list[str] = []
        builder_name = getattr(target, "_builder_name", None)

        if builder_name in ("Install", "InstallAs"):
            phases.extend(self._create_install_script_phases(target, objects))
        elif builder_name == "InstallDir":
            phases.extend(self._create_install_dir_script_phases(target, objects))
        elif builder_name in ("Tarfile", "Zipfile"):
            phases.extend(self._create_archive_script_phases(target, objects))

        return phases

    def _create_install_script_phases(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Create copy script phases for Install/InstallAs targets.

        Uses native `cp` command for simple file copying.

        Args:
            target: The install target.
            objects: The objects dictionary to add to.

        Returns:
            List of phase IDs.
        """
        phases: list[str] = []

        # Get the install nodes from the target
        install_nodes = getattr(target, "_install_nodes", [])
        if not install_nodes:
            install_nodes = target.output_nodes

        for node in install_nodes:
            if not hasattr(node, "_build_info"):
                continue

            build_info = node._build_info
            if build_info is None:
                continue
            sources = build_info.get("sources", [])
            if not sources:
                continue

            # Get source and destination paths
            # Both source (may be a built archive) and dest are in build dir
            source_node = sources[0]
            source_path = self._get_xcode_source_path(source_node.path)
            dest_path = self._make_build_output_path(node.path)

            # Create the script - ensure parent directory exists
            script = (
                f'mkdir -p "$(dirname "{dest_path}")"\ncp "{source_path}" "{dest_path}"'
            )

            phase_id = self._add_run_script_phase(
                objects,
                script=script,
                name=f"Install {node.path.name}",
                input_paths=[str(source_path)],
                output_paths=[str(dest_path)],
            )
            phases.append(phase_id)

        return phases

    def _create_install_dir_script_phases(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Create cp -R script phases for InstallDir targets.

        Uses native `cp -R` command for recursive directory copying.

        Args:
            target: The install dir target.
            objects: The objects dictionary to add to.

        Returns:
            List of phase IDs.
        """
        phases: list[str] = []
        builder_data = getattr(target, "_builder_data", None) or {}
        dest_dir = Path(builder_data.get("dest_dir", ""))

        # Get the source from pending_sources or resolved sources
        sources = getattr(target, "_pending_sources", None) or []
        if not sources and target.output_nodes:
            # Get source from first output node's build_info
            node = target.output_nodes[0]
            if hasattr(node, "_build_info") and node._build_info is not None:
                sources = node._build_info.get("sources", [])

        # Convert dest_dir to build output path (relative to build dir)
        dest_dir_str = self._make_build_output_path(dest_dir)

        for source in sources:
            if hasattr(source, "path"):
                source_path = str(self._make_relative_path(source.path))
                source_name = source.path.name
            elif isinstance(source, (str, Path)):
                source_path = str(self._make_relative_path(Path(source)))
                source_name = Path(source).name
            else:
                continue

            # Destination is dest_dir / source directory name
            dest_path = f"{dest_dir_str}/{source_name}"

            # Create the script
            script = f'mkdir -p "{dest_dir_str}"\ncp -R "{source_path}" "{dest_path}"'

            phase_id = self._add_run_script_phase(
                objects,
                script=script,
                name=f"Install directory {source_name}",
                input_paths=[source_path],
                output_paths=[dest_path],
            )
            phases.append(phase_id)

        return phases

    def _get_xcode_source_path(self, source_path: Path) -> str:
        """Get the correct path for a source in Xcode shell scripts.

        For regular source files: returns path relative to xcodeproj location.
        For native Xcode products (programs, libraries): returns Release/<name>
        since Xcode puts built products in the Release directory.
        For aggregate target outputs (archives, installed files): returns path
        relative to build dir (where xcodebuild runs scripts).

        Args:
            source_path: The source file path.

        Returns:
            Path string suitable for use in Xcode shell scripts.
        """
        if self._pcons_project is None:
            return str(self._make_relative_path(source_path))

        # Native Xcode target types that put outputs in Release/Debug directories
        native_target_types = {"program", "static_library", "shared_library"}
        # Aggregate target types whose outputs stay directly in build dir
        aggregate_target_types = {"interface", "archive"}

        # Check if this path is an output from another target
        for other_target in self._pcons_project.targets:
            target_type = (
                str(other_target.target_type) if other_target.target_type else None
            )

            for output in other_target.output_nodes:
                if hasattr(output, "path"):
                    # Compare paths (handle relative vs absolute)
                    is_match = False
                    try:
                        is_match = output.path.resolve() == source_path.resolve()
                    except (OSError, ValueError):
                        is_match = output.path == source_path

                    if is_match:
                        if target_type in native_target_types:
                            # Native built product - use Release/<filename>
                            return f"Release/{source_path.name}"
                        elif target_type in aggregate_target_types:
                            # Aggregate target output - use build output path
                            return self._make_build_output_path(source_path)

        # Not a target output - use standard relative path for source files
        return str(self._make_relative_path(source_path))

    def _create_archive_script_phases(
        self, target: Target, objects: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Create tar/zip script phases for Archive targets.

        Uses native `tar` and `zip` commands.

        Args:
            target: The archive target.
            objects: The objects dictionary to add to.

        Returns:
            List of phase IDs.
        """
        phases: list[str] = []
        builder_data = getattr(target, "_builder_data", None) or {}
        builder_name = getattr(target, "_builder_name", None)
        output_path = Path(builder_data.get("output", ""))
        compression = builder_data.get("compression")
        base_dir = builder_data.get("base_dir", ".")

        # Collect source paths from output nodes
        source_paths: list[str] = []
        if target.output_nodes:
            node = target.output_nodes[0]
            if hasattr(node, "_build_info") and node._build_info is not None:
                for source in node._build_info.get("sources", []):
                    if hasattr(source, "path"):
                        # Use helper to get correct path for Xcode
                        xcode_path = self._get_xcode_source_path(source.path)
                        source_paths.append(xcode_path)

        if not source_paths:
            return phases

        # Make output path relative to build dir (where xcodebuild runs scripts)
        output_rel = self._make_build_output_path(output_path)

        # Build the archive command
        if builder_name == "Tarfile":
            # Determine tar flags based on compression
            if compression == "gzip":
                tar_flags = "-czf"
            elif compression == "bz2":
                tar_flags = "-cjf"
            elif compression == "xz":
                tar_flags = "-cJf"
            else:
                tar_flags = "-cf"

            sources_str = " ".join(f'"{s}"' for s in source_paths)
            if base_dir and base_dir != ".":
                script = f'mkdir -p "$(dirname "{output_rel}")"\ncd "{base_dir}" && tar {tar_flags} "{output_rel}" {sources_str}'
            else:
                script = f'mkdir -p "$(dirname "{output_rel}")"\ntar {tar_flags} "{output_rel}" {sources_str}'
        else:  # Zipfile
            sources_str = " ".join(f'"{s}"' for s in source_paths)
            if base_dir and base_dir != ".":
                script = f'mkdir -p "$(dirname "{output_rel}")"\ncd "{base_dir}" && zip -r "{output_rel}" {sources_str}'
            else:
                script = f'mkdir -p "$(dirname "{output_rel}")"\nzip -r "{output_rel}" {sources_str}'

        phase_id = self._add_run_script_phase(
            objects,
            script=script,
            name=f"Create {output_path.name}",
            input_paths=source_paths,
            output_paths=[str(output_rel)],
        )
        phases.append(phase_id)

        return phases

    def _add_run_script_phase(
        self,
        objects: dict[str, dict[str, Any]],
        script: str,
        name: str = "Run Script",
        input_paths: list[str] | None = None,
        output_paths: list[str] | None = None,
    ) -> str:
        """Add a PBXShellScriptBuildPhase.

        Args:
            objects: The objects dictionary to add to.
            script: The shell script to run.
            name: Name of the build phase.
            input_paths: Input file paths for incremental builds.
            output_paths: Output file paths for incremental builds.

        Returns:
            The phase ID.
        """
        phase_id = _generate_id()

        objects[phase_id] = {
            "isa": "PBXShellScriptBuildPhase",
            "buildActionMask": "2147483647",
            "files": [],
            "inputPaths": input_paths or [],
            "outputPaths": output_paths or [],
            "runOnlyForDeploymentPostprocessing": "0",
            "shellPath": "/bin/sh",
            "shellScript": script,
            "name": name,
        }

        return phase_id

    # Source file extensions that Xcode can compile
    _XCODE_COMPILABLE_EXTENSIONS = frozenset(
        [
            ".c",
            ".cc",
            ".cpp",
            ".cxx",
            ".c++",  # C/C++
            ".m",
            ".mm",  # Objective-C/C++
            ".swift",  # Swift
            ".s",
            ".S",
            ".asm",  # Assembly
            ".metal",  # Metal shaders
        ]
    )

    def _add_sources_to_target(self, target: Target) -> None:
        """Add source files to an Xcode target using pbxproj's add_file.

        Args:
            target: The pcons target.
        """
        if self._xcode_project is None:
            return

        if target.name not in self._target_ids:
            return

        # Create a group for this target's sources
        group = self._xcode_project.get_or_create_group(target.name)

        for source in target.sources:
            if hasattr(source, "path"):
                # Cast to Path since we know it has path attribute (FileNode)
                src_path: Path = source.path  # type: ignore[attr-defined]

                # Skip pre-compiled objects and unrecognized files
                # These are passed through to the linker but aren't sources to compile
                if src_path.suffix.lower() not in self._XCODE_COMPILABLE_EXTENSIONS:
                    continue

                source_path = self._make_relative_path(src_path)
                file_options = FileOptions(create_build_files=True)
                self._xcode_project.add_file(
                    str(source_path),
                    parent=group,
                    force=False,
                    file_options=file_options,
                    target_name=target.name,
                )

    def _discover_headers(self, target: Target) -> list[Path]:
        """Find headers by scanning include directories.

        Args:
            target: The target to find headers for.

        Returns:
            List of header file paths.
        """
        headers: list[Path] = []
        include_dirs = list(target.public.include_dirs) + list(
            target.private.include_dirs
        )

        for inc_dir in include_dirs:
            if inc_dir.is_dir():
                for ext in [".h", ".hpp", ".hxx", ".H", ".hh"]:
                    headers.extend(inc_dir.rglob(f"*{ext}"))

        return sorted(set(headers))

    def _configure_build_settings(self, target: Target) -> None:
        """Configure Xcode build settings from pcons target.

        Args:
            target: The pcons target.
        """
        if self._xcode_project is None:
            return

        if target.name not in self._target_ids:
            return

        env = target._env

        # Collect include directories
        include_dirs: list[str] = []
        for inc_dir in target.public.include_dirs:
            include_dirs.append(str(self._make_relative_path(inc_dir)))
        for inc_dir in target.private.include_dirs:
            include_dirs.append(str(self._make_relative_path(inc_dir)))

        if include_dirs:
            self._xcode_project.set_flags(
                "HEADER_SEARCH_PATHS",
                include_dirs,
                target_name=target.name,
            )

        # Collect defines
        defines: list[str] = []
        defines.extend(target.public.defines)
        defines.extend(target.private.defines)
        if defines:
            self._xcode_project.set_flags(
                "GCC_PREPROCESSOR_DEFINITIONS",
                defines,
                target_name=target.name,
            )

        # Collect compiler flags
        cflags: list[str] = []
        cflags.extend(target.public.compile_flags)
        cflags.extend(target.private.compile_flags)

        # Get flags from environment if available
        if env is not None:
            if hasattr(env, "cc") and hasattr(env.cc, "flags"):
                env_flags = env.cc.flags
                if isinstance(env_flags, list):
                    cflags.extend(env_flags)
            if hasattr(env, "cxx") and hasattr(env.cxx, "flags"):
                env_flags = env.cxx.flags
                if isinstance(env_flags, list):
                    cflags.extend(env_flags)

        if cflags:
            self._xcode_project.set_flags(
                "OTHER_CFLAGS",
                cflags,
                target_name=target.name,
            )
            self._xcode_project.set_flags(
                "OTHER_CPLUSPLUSFLAGS",
                cflags,
                target_name=target.name,
            )

        # Collect link flags
        ldflags: list[str] = []
        ldflags.extend(target.public.link_flags)
        ldflags.extend(target.private.link_flags)

        # Add link libraries as -l flags
        for lib in target.public.link_libs:
            ldflags.append(f"-l{lib}")
        for lib in target.private.link_libs:
            ldflags.append(f"-l{lib}")

        if ldflags:
            self._xcode_project.set_flags(
                "OTHER_LDFLAGS",
                ldflags,
                target_name=target.name,
            )

        # Library search paths from environment
        if env is not None and hasattr(env, "link"):
            if hasattr(env.link, "libdirs"):
                libdirs = env.link.libdirs
                if isinstance(libdirs, list) and libdirs:
                    self._xcode_project.set_flags(
                        "LIBRARY_SEARCH_PATHS",
                        [str(d) for d in libdirs],
                        target_name=target.name,
                    )

    def _setup_dependencies(self, target: Target) -> None:
        """Set up target dependencies in Xcode project.

        For native targets, this sets up explicit dependencies.
        For aggregate targets (Install, Archive), this also adds dependencies
        on targets that produced the source files.

        Args:
            target: The pcons target.
        """
        if self._xcode_project is None:
            return

        if target.name not in self._target_ids:
            return

        # Get the Xcode target object
        xcode_target = self._xcode_project.get_target_by_name(target.name)
        if xcode_target is None:
            return

        # Collect dependencies from explicit target.dependencies
        dep_targets = list(target.dependencies)

        # For aggregate targets, also add implicit dependencies from source files
        target_type_str = str(target.target_type) if target.target_type else None
        if target_type_str in AGGREGATE_TARGET_TYPES:
            # Find targets that produced our source files
            dep_targets.extend(self._find_source_target_deps(target))

        for dep in dep_targets:
            if dep.name not in self._target_ids:
                continue

            dep_target = self._xcode_project.get_target_by_name(dep.name)
            if dep_target is None:
                continue

            # Create dependency objects using PBXGenericObject
            proxy_id = _generate_id()
            dep_id = _generate_id()

            # Get the root project object
            root_project = self._xcode_project.rootObject

            # PBXContainerItemProxy
            proxy_obj = PBXGenericObject()
            proxy_obj._id = proxy_id  # type: ignore[attr-defined]  # pbxproj internal
            proxy_obj["isa"] = "PBXContainerItemProxy"
            proxy_obj["containerPortal"] = root_project
            proxy_obj["proxyType"] = "1"
            proxy_obj["remoteGlobalIDString"] = self._target_ids[dep.name]
            proxy_obj["remoteInfo"] = dep.name
            self._xcode_project.objects[proxy_id] = proxy_obj

            # PBXTargetDependency
            dep_obj = PBXGenericObject()
            dep_obj._id = dep_id  # type: ignore[attr-defined]  # pbxproj internal
            dep_obj["isa"] = "PBXTargetDependency"
            dep_obj["target"] = self._target_ids[dep.name]
            dep_obj["targetProxy"] = proxy_id
            self._xcode_project.objects[dep_id] = dep_obj

            # Add to target's dependencies
            if "dependencies" not in xcode_target:
                xcode_target["dependencies"] = []
            xcode_target["dependencies"].append(dep_id)

    def _find_source_target_deps(self, target: Target) -> list[Target]:
        """Find targets that produced the source files for an aggregate target.

        For Install/Archive targets, source files may come from other targets
        (e.g., installing a built executable). This method finds those source
        targets so we can create proper Xcode target dependencies.

        Args:
            target: The aggregate target.

        Returns:
            List of targets that produced source files.
        """
        dep_targets: list[Target] = []
        seen_names: set[str] = set()

        # Get project for looking up other targets
        # Use generator's project reference since target._project may be None
        project = self._pcons_project
        if project is None:
            return dep_targets

        # Build a map of output paths to targets for efficient lookup
        # Resolve paths to handle relative vs absolute comparisons
        output_to_target: dict[Path, Target] = {}
        for other_target in project.targets:
            if other_target.name == target.name:
                continue
            for output in other_target.output_nodes:
                if hasattr(output, "path"):
                    # Try to resolve path, fallback to original if resolution fails
                    try:
                        resolved = output.path.resolve()
                    except (OSError, ValueError):
                        resolved = output.path
                    output_to_target[resolved] = other_target

        # Check output nodes for source files
        for node in target.output_nodes:
            if not hasattr(node, "_build_info"):
                continue

            build_info = node._build_info
            if build_info is None:
                continue

            sources = build_info.get("sources", [])
            for source in sources:
                if not hasattr(source, "path"):
                    continue

                # Resolve source path for comparison
                try:
                    source_resolved = source.path.resolve()
                except (OSError, ValueError):
                    source_resolved = source.path

                # Look up the target that produced this source
                if source_resolved in output_to_target:
                    dep = output_to_target[source_resolved]
                    if dep.name not in seen_names:
                        dep_targets.append(dep)
                        seen_names.add(dep.name)

        return dep_targets

    def _make_build_output_path(self, path: Path) -> str:
        """Make a path for a build output file in Xcode shell scripts.

        Build outputs are files in the build directory. Xcode runs shell scripts
        from the build directory, so these paths should be relative to build_dir.

        If the path doesn't include the build_dir prefix (e.g., just "file.tar.gz"),
        it's already relative to build_dir and can be used as-is.

        Args:
            path: The output file path (may or may not include build_dir prefix).

        Returns:
            Path string suitable for use in Xcode shell scripts.
        """
        if self._output_dir is None:
            return str(path)

        # If path is absolute under build_dir, make it relative to build_dir
        if path.is_absolute():
            try:
                return str(path.relative_to(self._output_dir))
            except ValueError:
                pass

        # If path already doesn't include build_dir prefix, use as-is
        # This is the common case for pcons target paths
        path_str = str(path)

        # Check if it starts with the build_dir name (and remove it if so)
        if self._pcons_project is not None:
            build_dir_name = str(self._pcons_project.build_dir)
            if path_str.startswith(build_dir_name + "/"):
                return path_str[len(build_dir_name) + 1 :]
            if path_str.startswith(build_dir_name + "\\"):
                return path_str[len(build_dir_name) + 1 :]

        return path_str

    def _make_relative_path(self, path: Path) -> Path:
        """Make a path relative to the xcodeproj location.

        Since the .xcodeproj is inside the build directory, source files
        need paths like "../src/file.c" to reference files in the project.

        For paths under project root: computes path via _topdir
        For paths under build dir: makes relative to build dir
        For external paths: returns as-is

        Args:
            path: The path to make relative.

        Returns:
            Path relative to the xcodeproj location (output_dir).
        """
        if self._project_root is None or self._output_dir is None:
            return path

        # Make path absolute first
        if not path.is_absolute():
            path = self._project_root / path

        path = path.resolve()

        # Check if path is under build dir (output_dir)
        try:
            return path.relative_to(self._output_dir)
        except ValueError:
            pass

        # Check if path is under project root
        try:
            rel_to_root = path.relative_to(self._project_root)
            # Combine with topdir: "../" + "src/file.c" = "../src/file.c"
            return Path(self._topdir) / rel_to_root
        except ValueError:
            pass

        # External path - return as-is (absolute)
        return path

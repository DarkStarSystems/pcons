# SPDX-License-Identifier: MIT
"""Xcode project generator.

Generates .xcodeproj bundles from a configured pcons Project.
The generated project is fully buildable with xcodebuild.
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
        project = Project("myapp")
        # ... configure project ...

        generator = XcodeGenerator()
        generator.generate(project, Path("build"))
        # Creates build/myapp.xcodeproj/

        # Build with: xcodebuild -project build/myapp.xcodeproj
    """

    def __init__(self) -> None:
        super().__init__("xcode")
        self._xcode_project: XcodeProject | None = None
        self._output_dir: Path | None = None
        self._project_root: Path | None = None
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
        # Skip interface and object targets
        if target.target_type in ("interface", "object", None):
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

        for dep in target.dependencies:
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

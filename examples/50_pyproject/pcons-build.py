#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a nanobind Python extension, packaged via the pcons.pyproject backend."""

import sys
import sysconfig
from pathlib import Path

from pcons import ImportedTarget, Project, get_var, get_variant, install_dir
from pcons.packages.description import PackageDescription
from pcons.packages.finders import ConanFinder
from pcons.toolchains import find_c_toolchain

project = Project("hello_python")

toolchain_override = get_var("TOOLCHAIN")
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

if toolchain.name == "msvc":
    env.cxx.flags.extend(["/std:c++latest", "/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.extend(["-std=c++23", "-stdlib=libc++"])
    env.link.libs.append("c++")
else:
    env.cxx.flags.append("-std=c++23")

# Discover Python's dev headers from the *running* interpreter via sysconfig,
# rather than pkg-config.
python = ImportedTarget.from_package(
    PackageDescription(
        name="python3",
        version=sysconfig.get_python_version(),
        include_dirs=[
            sysconfig.get_path("include"),
            sysconfig.get_path("platinclude"),
        ],
        found_by="sysconfig",
    )
)

conan = ConanFinder(
    conanfile=project.root_dir / "conanfile.txt",
    output_folder=project.build_dir / "conan",
    build_missing=True,
)

VARIANT = get_variant("release")

# Sync profile with toolchain - this generates the Conan profile file
conan.sync_profile(toolchain, env=env, build_type=VARIANT.capitalize())

# Install packages - cmake_layout subfolders are auto-searched
packages = {
    name: ImportedTarget.from_package(desc) for name, desc in conan.install().items()
}

nanobind = packages["nanobind"]

# nanobind is NOT header-only: its runtime must be compiled into each extension.
# nb_combined.cpp is the amalgamated source recommended for non-CMake builds.
assert nanobind.package and nanobind.package.prefix, (
    "Package prefix is required for nanobind"
)
nb_combined = Path(nanobind.package.prefix) / "nanobind" / "src" / "nb_combined.cpp"
assert nb_combined.is_file(), f"Amalgamated source not found at {nb_combined}"

pcons_hello_ext = project.SharedLibrary(
    "pcons_hello_ext",
    env,
    sources=["src/module.cpp", nb_combined],
)
if toolchain.name in ("gcc", "llvm"):
    pcons_hello_ext.private.compile_flags.extend(
        ["-fvisibility=hidden", "-fno-strict-aliasing"]
    )

hello_lib = project.SharedLibrary("hello_lib", env, sources=["src/hello.cpp"])
hello_lib.public.include_dirs.append("src")

if toolchain.name in ("gcc", "llvm"):
    # Add an rpath relative to the extension itself so it finds hello_lib
    # wherever the wheel is installed (e.g. site-packages), with no
    # LD_LIBRARY_PATH / DYLD_* patching in tests.
    # pcons already gives the dylib an @rpath install_name on macOS,
    # so we just need the matching rpath origin:
    # - ELF uses $ORIGIN ($$ so pcons passes it through literally)
    # - Mach-O uses @loader_path.
    rpath_origin = "@loader_path" if sys.platform == "darwin" else "$$ORIGIN"
    pcons_hello_ext.private.link_flags.append(f"-Wl,-rpath,{rpath_origin}")

# Python extensions must not have the "lib" prefix and must use the platform suffix
# e.g. pcons_hello_ext.cpython-314-x86_64-linux-gnu.so
pcons_hello_ext.output_prefix = ""
pcons_hello_ext.output_suffix = sysconfig.get_config_var("EXT_SUFFIX")
pcons_hello_ext.link(python, nanobind, hello_lib)

subgen = Path(nanobind.package.prefix) / "nanobind" / "stubgen.py"
assert subgen.is_file(), f"Stub generator not found at {subgen}"

cmd = project.Command(
    "generate_stubs",
    env,
    target=f"{pcons_hello_ext.name}.pyi",
    command=[sys.executable, str(subgen), "--module", pcons_hello_ext.name],
).depends(pcons_hello_ext)

# Stage the extension and its stubs for packaging. The pyproject build backend
# points PCONS_INSTALL_PREFIX at a staging directory and builds the "install"
# alias, then packages whatever lands there into the wheel.
# When building a wheel the backend sets PCONS_BUILD_WHEEL:
# the staging dir is then the site-packages image, so we install to its root (".")
# to place the extension and stubs at the top level where Python will import them.
# Otherwise we follow the usual bin/lib convention.
if get_var("PCONS_BUILD_WHEEL"):
    install_destination = "."
else:
    install_destination = install_dir(env, "shared_library")

install = project.Install(
    install_destination,
    [pcons_hello_ext, hello_lib, cmd],
    name="install",
)
project.Alias("install", install)

project.generate()

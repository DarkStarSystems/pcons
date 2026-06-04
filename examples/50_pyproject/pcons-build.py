#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducer for missing header-triggered rebuild on regular .cpp in modules mode."""

import sys
import sysconfig
from pathlib import Path

from pcons import ImportedTarget, Project, get_var, get_variant, install_dir
from pcons.packages.finders import ConanFinder, PkgConfigFinder
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

pkg_config = PkgConfigFinder()
python_desc = pkg_config.find("python3", version=">=3.11")
assert python_desc is not None, "Python development files not found via pkg-config"
python = ImportedTarget.from_package(python_desc)

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
    # use $ORIGIN rpath to avoid LD_LIBRARY_PATH patching in tests
    # and to allow the extension to find the library
    # when installed in a different location (e.g. site-packages)
    pcons_hello_ext.private.link_flags.append("-Wl,-rpath,$$ORIGIN")

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
install = project.Install(
    install_dir(env, "shared_library"),
    [pcons_hello_ext, hello_lib, cmd],
    name="install",
)
project.Alias("install", install)

project.generate()

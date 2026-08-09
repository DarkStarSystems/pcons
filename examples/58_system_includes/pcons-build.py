#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""System include directories: third-party headers, without their warnings.

Any project that vendors an SDK hits this. Your own code builds with
``-Wall -Wextra -Werror``; the SDK's headers were written to someone else's
standards and produce a flood of warnings you can neither fix nor ignore.

The answer everywhere is a second kind of include path — ``-isystem`` on
GCC/Clang, ``/external:I`` on MSVC, ``-imsvc`` on clang-cl — searched exactly
like ``-I`` but exempt from warnings. In pcons that's ``system_includes``,
alongside ``includes``, on any compile tool:

    env.cc.system_includes.append(sdk_dir)

and as a usage requirement, so a library can hand its consumers a vendored
SDK's headers without handing them its warnings:

    sdk.public.system_include_dirs.append(sdk_dir)

External packages take the same treatment through a ``system=`` argument,
which moves the package's include dirs to the system list without any list
surgery on the target::

    doctest = project.find_package("doctest", system=True)
    imported = ImportedTarget.from_package(description, system=True)
    env.use(description, system=True)

Both spellings are relativized in the generated build files, so build.ninja
stays relocatable.
"""

from pcons import ImportedTarget, Project
from pcons.packages.description import PackageDescription

project = Project("system_includes")
env = project.Environment(toolchain="c")

# Strict on our own code -- this is the whole point.
env.cc.flags.extend(["-Wall", "-Wextra", "-Werror"])

# The vendored SDK is exempt. Swap system_includes for includes below and the
# build fails on an unused parameter in a header nobody here can change.
sdk = project.HeaderOnlyLibrary("noisy_sdk")
sdk.public.system_include_dirs.append(project.root_dir / "vendor")

app = project.Program("sdk_demo", env, sources=["src/main.c"])
app.link(sdk)

# Same headers, arriving as an external package instead of a target. This is
# what a package finder or pcons-fetch hands you; system=True says its include
# dirs are -isystem, and leaves the description itself untouched.
sdk_package = PackageDescription(
    name="noisy_sdk_pkg",
    include_dirs=[str(project.root_dir / "vendor")],
)
imported = ImportedTarget.from_package(sdk_package, system=True)

pkg_app = project.Program("pkg_demo", env, sources=["src/main.c"])
pkg_app.link(imported)

project.Default(app, pkg_app)

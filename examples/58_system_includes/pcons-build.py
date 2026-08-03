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

Both spellings are relativized in the generated build files, so build.ninja
stays relocatable.
"""

from pcons import Project

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

project.Default(app)

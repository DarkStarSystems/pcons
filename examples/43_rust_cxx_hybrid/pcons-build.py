#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Rust + C++ hybrid: link a Rust staticlib into a C++ program.

Demonstrates pcons.tools.cargo.CargoBuild — pcons treats `cargo build`
as a black-box sub-build (with restat=True), then propagates the
resulting library through the normal usage-requirement mechanism so
the C++ link step picks up -L/-l/-I automatically.

Cargo handles intra-Rust incremental compilation; pcons handles the
cross-language dependency edges (relink C++ only when the Rust .a
actually changes).
"""

import sys

from pcons import Project
from pcons.toolchains import find_c_toolchain

project = Project("rust_cxx_hybrid")
env = project.Environment(toolchain=find_c_toolchain())

# Build the Rust crate as a staticlib. Cargo's target/ goes inside
# the build dir, so a clean wipes it.
rust_greet = project.CargoBuild(
    "rust_greet",
    env,
    manifest="rust/Cargo.toml",
    crate_type="staticlib",
    profile="release",
)

# Rust's std uses platform libraries that cargo would normally pull
# in when it drives the link. Since the C++ compiler drives the link
# here, we add them ourselves.
if sys.platform == "darwin":
    env.link.flags.extend(
        ["-framework", "Security", "-framework", "SystemConfiguration"]
    )
elif sys.platform.startswith("linux"):
    env.link.libs.extend(["dl", "pthread"])

env.cxx.includes.append(project.root_dir / "src")
app = project.Program("hello_rust", env, sources=["src/main.cpp"])
app.private.link_libs.append(rust_greet)

project.generate()

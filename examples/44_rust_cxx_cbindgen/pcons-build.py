#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Rust + C++ with cbindgen-generated FFI header.

Like 43_rust_cxx_hybrid, but the C header is produced from the Rust
sources by cbindgen at build time instead of being hand-written.
pcons wires the cbindgen invocation as a separate Command target;
the generated header's directory is propagated as an include
through the imported target's public usage requirements.
"""

import sys

from pcons import Project

project = Project("rust_cxx_cbindgen")
env = project.Environment(toolchain="c++")

rust_math = project.CargoBuild(
    "rust_math",
    env,
    manifest="rust/Cargo.toml",
    crate_type="staticlib",
    profile="release",
    generate_header="rust/cbindgen.toml",
)

# Rust's std needs a few system libraries at link time when the C++
# compiler drives the link instead of cargo.
if sys.platform == "darwin":
    env.link.flags.extend(
        ["-framework", "Security", "-framework", "SystemConfiguration"]
    )
elif sys.platform.startswith("linux"):
    env.link.libs.extend(["dl", "pthread"])
elif sys.platform == "win32":
    # Rust std on the MSVC target needs these Windows system libraries.
    # rustc prints the authoritative list for a crate with
    # `cargo rustc --release -- --print native-static-libs`.
    env.link.libs.extend(
        ["ws2_32", "userenv", "advapi32", "bcrypt", "ntdll", "kernel32"]
    )

app = project.Program("stats", env, sources=["src/main.cpp"])
app.private.link_libs.append(rust_math)

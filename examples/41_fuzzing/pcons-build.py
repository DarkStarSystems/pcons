#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating fuzz testing via libFuzzer + Test().

Pcons doesn't have a dedicated "fuzz target" builder — fuzzing is just a
fuzzer-instrumented Program plus one or two Tests. The pattern below is
the same shape you'd use for AFL++ or Honggfuzz (see the Fuzzing section
of docs/user-guide.md for those recipes); only the build flags and the
campaign invocation change.

The harness:
- `src/parser.c`: a tiny `parse_keyvalue()` function (the code under test).
- `src/fuzz_parser.cpp`: the libFuzzer entrypoint `LLVMFuzzerTestOneInput`.
  (.cpp so pcons picks the C++ linker driver — libFuzzer is itself a
  C++ library and needs the C++ runtime.)
- `corpus/`: a few seed inputs replayed by the regression test.

Two tests are declared:
- `parser.regression` — replays the corpus. Fast (<1s).
  commit.
- `parser.campaign` — fuzzes for a few seconds. Labelled "fuzz" so
  `pcons test -LE fuzz` excludes it from fast inner loops.

libFuzzer is clang-only: this script requires the LLVM toolchain and
raises if clang isn't found. The flags are written out inline rather
than hidden behind a helper, so it's obvious what the binary is being
built with.
"""

import os
import platform
import shutil
from pathlib import Path

from pcons import Project

project = Project("fuzzing", build_dir=os.environ.get("PCONS_BUILD_DIR", "build"))

# libFuzzer is clang-only, so require the LLVM toolchain by name
env = project.Environment(toolchain="llvm")

# libFuzzer build flags; adjust to suit.
#
#   -fsanitize=fuzzer   links libFuzzer's main() and the coverage runtime
#   -fsanitize=address  AddressSanitizer — catches memory bugs as crashes
#   -g                  symbols, so stack traces in crashes are useful
#   -O1                 libFuzzer's recommended optimization level
fuzz_flags = ["-fsanitize=fuzzer,address", "-g", "-O1"]
env.cxx.flags.extend(fuzz_flags)
env.cc.flags.extend(fuzz_flags)
env.link.flags.extend(fuzz_flags)

# macOS quirk: Homebrew LLVM ships libFuzzer (Apple's Xcode clang doesn't),
# but its libc++ ABI differs from the Apple SDK's libc++. The libFuzzer
# archive references internal libc++ symbols that resolve only against
# Homebrew's libc++ — so link against it explicitly.
if platform.system() == "Darwin":
    clang_path = shutil.which("clang")
    if clang_path is not None:
        libcxx_dir = Path(clang_path).parent.parent / "lib" / "c++"
        if libcxx_dir.exists():
            env.link.flags.extend([f"-L{libcxx_dir}", f"-Wl,-rpath,{libcxx_dir}"])

harness = project.Program(
    "fuzz_parser",
    env,
    sources=["src/parser.c", "src/fuzz_parser.cpp"],
)

# Pass absolute path to corpus so it resolves
# correctly from the build directory, where tests run.
corpus_dir = str((Path(__file__).parent / "corpus").resolve())

# Regression: replays every file in `corpus/` once and exits. Catches
# any regression that breaks an input we've already seen.
# `-runs=0` means "execute each corpus input once, then exit" (without
# it, libFuzzer treats the directory as a live corpus and keeps fuzzing).
project.Test(
    "parser.regression",
    harness,
    args=["-runs=0", corpus_dir],
    labels=["fuzz", "regression"],
    timeout=30,
)

# Campaign: actually fuzzes for a fixed wall-clock time. Short here so
# the example finishes quickly in CI; for real use you'd set this to 60,
# 300, or longer in a nightly job.
#
# libFuzzer treats the first corpus dir as read+write (newly-discovered
# inputs go there) and any additional dirs as read-only seeds. Pointing
# the writable dir at the build directory (created on the fly via
# `-create_missing_dirs=1`) keeps the version-controlled seed corpus
# from filling up on every run.
project.Test(
    "parser.campaign",
    harness,
    args=[
        "-create_missing_dirs=1",
        "campaign-corpus",
        corpus_dir,
        "-max_total_time=2",
    ],
    labels=["fuzz", "campaign"],
    timeout=20,
)

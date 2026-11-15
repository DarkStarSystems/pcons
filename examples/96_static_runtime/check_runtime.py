# SPDX-License-Identifier: MIT
"""Check which programs load a shared C++ runtime: app_shared does,
app_static doesn't."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

# The C++ runtime's shared library on each platform: libstdc++ (GCC),
# libc++ (clang), or MSVC's.
CXX_RUNTIME = re.compile(r"libstdc\+\+|libc\+\+|msvcp\d+\.dll|vcruntime\d+", re.I)


def shared_libraries(program: Path) -> str:
    """The listing of what *program* loads, from the platform's tool."""
    if sys.platform == "win32":
        tool = ["dumpbin", "/dependents"] if shutil.which("dumpbin") else None
        tool = tool or ["objdump", "-p"]
    elif sys.platform == "darwin":
        tool = ["otool", "-L"]
    else:
        tool = ["readelf", "-d"]
    return subprocess.run(
        [*tool, str(program)], capture_output=True, text=True, check=True
    ).stdout


suffix = ".exe" if sys.platform == "win32" else ""
for name, loads_runtime in (("app_shared", True), ("app_static", False)):
    listing = shared_libraries(Path("build") / f"{name}{suffix}")
    found = CXX_RUNTIME.findall(listing)
    if bool(found) != loads_runtime:
        sys.exit(
            f"{name}: expected the C++ runtime {'' if loads_runtime else 'not '}"
            f"to be shared, found {found or 'none'} in:\n{listing}"
        )
    print(f"{name}: {', '.join(sorted(set(found))) or 'no shared C++ runtime'}")

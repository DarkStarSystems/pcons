# SPDX-License-Identifier: MIT
"""Run the sanitized program cleanly, then with its planted overflow, which
AddressSanitizer must report.

On Windows the program loads its compiler's AddressSanitizer DLL, so that
compiler's directory goes first on PATH: MSVC's DLL and clang's have the
same name, but a program built with one can't use the other.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def asan_dll_dir() -> str:
    """Where the AddressSanitizer DLL of the program's compiler is."""
    entry = json.loads(Path("build/compile_commands.json").read_text())[0]
    compiler = shlex.split(entry["command"])[0]
    if Path(compiler).stem.lower() == "cl":
        return str(Path(compiler).parent)
    resource_dir = subprocess.run(
        [compiler, "--print-resource-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return str(Path(resource_dir) / "lib" / "windows")


env = dict(os.environ)
if sys.platform == "win32":
    env["PATH"] = asan_dll_dir() + os.pathsep + env["PATH"]

app = Path("build") / ("app.exe" if sys.platform == "win32" else "app")
clean = subprocess.run([str(app)], capture_output=True, text=True, env=env)
if clean.returncode != 0 or "sum 6" not in clean.stdout:
    sys.exit(
        f"app failed without the overflow (exit {clean.returncode}):\n"
        f"{clean.stdout}{clean.stderr}"
    )
bad = subprocess.run([str(app), "overflow"], capture_output=True, text=True, env=env)
if "AddressSanitizer: heap-buffer-overflow" not in bad.stdout + bad.stderr:
    sys.exit(f"AddressSanitizer didn't report the overflow:\n{bad.stdout}{bad.stderr}")
print("AddressSanitizer reported the overflow")

# SPDX-License-Identifier: MIT
"""C++20 module dependency scanner for Ninja dyndep.

Supports two scanner styles:
- "clang": uses clang-scan-deps (P1689R5 format); manifest uses "pcm" key
- "msvc":  uses cl.exe /scanDependencies <file> (P1689R5 format); manifest uses "ifc" key

Run as:
    python -m pcons.toolchains.cxx_module_scanner \\
        --manifest cxx.manifest.json \\
        --out cxx_modules.dyndep \\
        --mod-dir cxx_modules \\
        --scanner clang-scan-deps \\
        --scanner-style clang

    python -m pcons.toolchains.cxx_module_scanner \\
        --manifest cxx.manifest.json \\
        --out cxx_modules.dyndep \\
        --mod-dir cxx_modules \\
        --scanner cl.exe \\
        --scanner-style msvc

The manifest JSON format (clang):
    [
      {
        "src": "/abs/path/MyMod.cppm",
        "obj": "obj.hello/src/MyMod.cppm.obj",
        "is_module_interface": true,
        "pcm": "cxx_modules/MyMod.pcm",
        "compiler": "clang++",
        "compile_flags": ["-std=c++20"]
      },
      ...
    ]

The manifest JSON format (msvc):
    [
      {
        "src": "C:/abs/path/MyMod.cppm",
        "obj": "obj.hello/src/MyMod.cppm.obj",
        "is_module_interface": true,
        "ifc": "cxx_modules/MyMod.ifc",
        "compiler": "cl.exe",
        "compile_flags": ["/nologo", "/std:c++20"]
      },
      ...
    ]

All paths in the output are relative to the build directory (where Ninja runs).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run_scan_deps(
    scanner: str,
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
) -> dict[str, Any] | None:
    """Run clang-scan-deps on a single source file and return P1689R5 JSON.

    Args:
        scanner: Path/name of the clang-scan-deps executable.
        compiler: Compiler command (e.g., "clang++").
        compile_flags: List of compiler flags.
        src: Absolute path to the source file.
        obj: Object file path (relative to build dir).

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    cmd = [scanner, "-format=p1689", "--"]
    cmd += [compiler]
    cmd += compile_flags
    cmd += ["-c", src, "-o", obj]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(
            f"Warning: clang-scan-deps failed for {src}: {e.stderr}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        print(
            f"Error: scanner '{scanner}' not found. "
            "Install clang-scan-deps (part of LLVM/Clang tools).",
            file=sys.stderr,
        )
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(
            f"Warning: could not parse clang-scan-deps output for {src}: {e}",
            file=sys.stderr,
        )
        return None


def run_scan_deps_msvc(
    compiler: str,
    compile_flags: list[str],
    src: str,
) -> dict[str, Any] | None:
    """Run cl.exe /scanDependencies on a single source file and return P1689R5 JSON.

    Args:
        compiler: Path/name of cl.exe.
        compile_flags: List of compiler flags (e.g. ["/nologo", "/std:c++20"]).
        src: Absolute path to the source file.

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        cmd = [compiler, "/scanDependencies", tmp_path] + compile_flags + [src]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Warning: cl.exe /scanDependencies failed for {src}: {result.stderr}",
                file=sys.stderr,
            )
            return None
        try:
            return json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(
                f"Warning: could not parse cl.exe /scanDependencies output for {src}: {e}",
                file=sys.stderr,
            )
            return None
    except FileNotFoundError:
        print(
            f"Error: compiler '{compiler}' not found.",
            file=sys.stderr,
        )
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def write_dyndep(
    manifest: list[dict[str, Any]],
    mod_dir: str,
    out_path: str,
    scanner: str,
    scanner_style: str = "clang",
) -> None:
    """Scan manifest sources and write Ninja dyndep file.

    Args:
        manifest: List of manifest entry dicts (see module docstring).
        mod_dir: Module directory, relative to build dir (e.g., "cxx_modules").
        out_path: Output dyndep file path (relative to build dir).
        scanner: clang-scan-deps executable (clang style) or cl.exe path (msvc style).
        scanner_style: "clang" (default) or "msvc".
    """
    # Module file key in manifest entries ("pcm" for clang, "ifc" for msvc)
    mod_key = "ifc" if scanner_style == "msvc" else "pcm"

    # First pass: build module_name -> mod_path map from interface units.
    # The mod path comes from the manifest "pcm"/"ifc" field when present,
    # or is derived from the logical-name after scanning.
    module_to_pcm: dict[str, str] = {}

    # Pre-populate from manifest entries that are module interfaces and have
    # an explicit pcm field (we know this before scanning).
    for item in manifest:
        if item.get("is_module_interface") and "pcm" in item:
            # We don't know the logical name yet; defer until after scan.
            pass

    # Second pass: scan all files and build the full dependency picture.
    # entries: list of (obj, provides_pcms, requires_pcms)
    entries: list[tuple[str, list[str], list[str]]] = []

    # Intermediate: scan results keyed by obj path
    scan_results: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    for item in manifest:
        src = str(item["src"])
        obj = str(item["obj"])
        compiler = str(item.get("compiler", "clang++"))
        compile_flags = list(item.get("compile_flags", []))

        if scanner_style == "msvc":
            p1689 = run_scan_deps_msvc(compiler, compile_flags, src)
        else:
            p1689 = run_scan_deps(scanner, compiler, compile_flags, src, obj)
        scan_results.append((item, p1689))

    # Build module_name -> pcm_path map from scan results.
    # For module interfaces we prefer the manifest-provided pcm path.
    for item, p1689 in scan_results:
        if p1689 is None:
            continue
        rules = p1689.get("rules", [])
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            provides = rule.get("provides", [])
            if not isinstance(provides, list):
                continue
            for prov in provides:
                if not isinstance(prov, dict):
                    continue
                logical_name = str(prov.get("logical-name", ""))
                if not logical_name:
                    continue
                # Use manifest mod file field if available, else derive from logical name
                if item.get("is_module_interface") and mod_key in item:
                    pcm_path = str(item[mod_key])
                else:
                    # Derive: replace ':' with '-' for partition modules
                    safe_name = logical_name.replace(":", "-")
                    ext = ".ifc" if scanner_style == "msvc" else ".pcm"
                    pcm_path = f"{mod_dir}/{safe_name}{ext}"
                module_to_pcm[logical_name] = pcm_path

    # Third pass: build dyndep entries.
    for item, p1689 in scan_results:
        obj = str(item["obj"])
        is_interface = bool(item.get("is_module_interface", False))

        provides_pcms: list[str] = []
        requires_pcms: list[str] = []

        if p1689 is not None:
            rules = p1689.get("rules", [])
            if isinstance(rules, list):
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    # Provides (module interface outputs)
                    provides = rule.get("provides", [])
                    if isinstance(provides, list):
                        for prov in provides:
                            if not isinstance(prov, dict):
                                continue
                            logical_name = str(prov.get("logical-name", ""))
                            if logical_name and logical_name in module_to_pcm:
                                provides_pcms.append(module_to_pcm[logical_name])

                    # Requires (module dependencies)
                    requires = rule.get("requires", [])
                    if isinstance(requires, list):
                        for req in requires:
                            if not isinstance(req, dict):
                                continue
                            logical_name = str(req.get("logical-name", ""))
                            if logical_name and logical_name in module_to_pcm:
                                requires_pcms.append(module_to_pcm[logical_name])

        # If it's a module interface and we have a mod file from the manifest but no
        # provides from the scan (e.g., scanner failed), fall back to manifest mod file.
        if is_interface and not provides_pcms and mod_key in item:
            provides_pcms = [str(item[mod_key])]

        entries.append((obj, provides_pcms, requires_pcms))

    # Write dyndep file
    lines = ["ninja_dyndep_version = 1", ""]
    for obj, provides_pcms, requires_pcms in entries:
        # Implicit outputs: PCM files produced by this compilation
        if provides_pcms:
            implicit_out = " | " + " ".join(provides_pcms)
        else:
            implicit_out = ""

        # Implicit inputs: PCM files required by this compilation
        if requires_pcms:
            implicit_in = " | " + " ".join(requires_pcms)
        else:
            implicit_in = ""

        lines.append(f"build {obj}{implicit_out}: dyndep{implicit_in}")
        lines.append("")

    dyndep_text = "\n".join(lines)
    Path(out_path).write_text(dyndep_text, encoding="utf-8")


def main() -> int:
    """Entry point when run as python -m pcons.toolchains.cxx_module_scanner."""
    parser = argparse.ArgumentParser(
        description="Generate Ninja dyndep file for C++20 module dependencies"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to manifest JSON file (relative to build dir)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output dyndep file path (relative to build dir)",
    )
    parser.add_argument(
        "--mod-dir",
        default="cxx_modules",
        help="Module directory relative to build dir (default: cxx_modules)",
    )
    parser.add_argument(
        "--scanner",
        default="clang-scan-deps",
        help="Path/name of scanner executable (default: clang-scan-deps)",
    )
    parser.add_argument(
        "--scanner-style",
        default="clang",
        choices=["clang", "msvc"],
        help="Scanner style: 'clang' (clang-scan-deps) or 'msvc' (cl.exe) (default: clang)",
    )
    args = parser.parse_args()

    try:
        manifest_text = Path(args.manifest).read_text(encoding="utf-8")
        manifest: list[dict[str, Any]] = json.loads(manifest_text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading manifest {args.manifest}: {e}", file=sys.stderr)
        return 1

    write_dyndep(manifest, args.mod_dir, args.out, args.scanner, args.scanner_style)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: MIT
"""C++20 module dependency scanner for Ninja dyndep.

Supports three scanner styles:
- "clang": uses clang-scan-deps (P1689R5 format); manifest uses "pcm" key
- "msvc":  uses cl.exe /scanDependencies <file> (P1689R5 format); manifest uses "ifc" key
- "gcc":   uses g++ with -fdeps-format=p1689r5 and reads a deps JSON file

Run as:
    python -m pcons.toolchains.cxx_module_scanner \\
        --manifest cxx.manifest.json \\
        --out cxx_modules.dyndep \\
        --mod-dir cxx_modules \\
        --scanner clang-scan-deps \\
        --scanner-style clang

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
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CxxModuleScannerNotFound(RuntimeError):
    """Raised when the C++ module scanner executable is not on PATH.

    Let this propagate so configure fails loudly instead of producing
    empty/silent scans.
    """


def _write_text_if_changed(path: Path, text: str) -> None:
    """Write *text* to *path* only when content differs.

    Fast-path no-op uses a matching ``<path>.sha256`` digest file.
    If the digest file is missing or stale, use a size check first and
    only fall back to a byte-for-byte compare for equal-size candidates.
    """
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).digest()
    digest_file = path.with_suffix(path.suffix + ".sha256")

    if path.exists():
        if (
            path.stat().st_size == len(data)
            and digest_file.exists()
            and digest_file.read_bytes() == digest
        ):
            return

    path.write_bytes(data)
    digest_file.write_bytes(digest)


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
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"C++ module scanner '{scanner}' not found on PATH.\n"
            "  C++20 modules require clang-scan-deps (shipped with LLVM/Clang).\n"
            "  Install hints:\n"
            "    macOS:        brew install llvm  (then add the keg's bin to PATH)\n"
            "    Ubuntu/Deb:   apt install clang-tools  (or a recent LLVM via apt.llvm.org)\n"
            "    Fedora/RHEL:  dnf install clang-tools-extra\n"
            "    Windows:      winget install LLVM.LLVM  (or use the LLVM installer)\n"
            "  Or set env.cxx.scan_deps to the full path of your clang-scan-deps."
        ) from e

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
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"MSVC compiler '{compiler}' not found on PATH.\n"
            "  C++20 module scanning needs cl.exe to invoke /scanDependencies.\n"
            "  On Windows, run a Visual Studio Developer Command Prompt, or\n"
            '  source vcvars64.bat (e.g. "C:\\Program Files\\Microsoft Visual\n'
            '  Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvars64.bat") in\n'
            "  the shell that invokes pcons-build.py — that puts cl.exe and\n"
            "  the rest of the MSVC toolchain on PATH for the configure step."
        ) from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run_scan_deps_gcc(
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
) -> dict[str, Any] | None:
    """Run GCC p1689 scan and return parsed JSON.

    Args:
        compiler: Path/name of g++.
        compile_flags: List of compiler flags (e.g. ["-std=c++23"]).
        src: Absolute path to the source file.
        obj: Object file path relative to the build directory.

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_deps:
        deps_json = f_deps.name
    with tempfile.NamedTemporaryFile(suffix=".d", delete=False) as f_depfile:
        depfile = f_depfile.name
    with tempfile.NamedTemporaryFile(suffix=".ii", delete=False) as f_pp:
        preprocessed = f_pp.name

    try:
        cmd = [compiler]
        cmd += compile_flags
        cmd += [
            "-E",
            "-x",
            "c++",
            src,
            "-MT",
            obj,
            "-MD",
            "-MF",
            depfile,
            "-fmodules",
            f"-fdeps-file={deps_json}",
            f"-fdeps-target={obj}",
            "-fdeps-format=p1689r5",
            "-o",
            preprocessed,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Warning: GCC p1689 scan failed for {src}: {result.stderr}",
                file=sys.stderr,
            )
            return None

        try:
            return json.loads(Path(deps_json).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(
                f"Warning: could not parse GCC p1689 output for {src}: {e}",
                file=sys.stderr,
            )
            return None
        except OSError as e:
            print(
                f"Warning: could not read GCC p1689 output for {src}: {e}",
                file=sys.stderr,
            )
            return None
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"GCC compiler '{compiler}' not found on PATH.\n"
            "  C++20 module scanning needs g++ with p1689 support.\n"
            "  Install hints:\n"
            "    Ubuntu/Deb:   apt install g++\n"
            "    Fedora/RHEL:  dnf install gcc-c++\n"
            "    macOS:        brew install gcc"
        ) from e
    finally:
        Path(deps_json).unlink(missing_ok=True)
        Path(depfile).unlink(missing_ok=True)
        Path(preprocessed).unlink(missing_ok=True)


# =============================================================================
# Configure-time API: TU scan specs, results, and dyndep generation.
#
# Toolchains' after_resolve() invokes the scanner inline so its output can
# drive flag injection (e.g. /internalPartition) — Ninja dyndep can only
# modify deps/outputs, not flags. Build-time entry points (write_dyndep,
# main) remain for debugging and external callers.
# =============================================================================


@dataclass
class TuScanSpec:
    """Inputs to scan a single translation unit.

    Attributes:
        src: Absolute path to the source file.
        obj_rel: Object file path relative to the build directory.
        compiler: Compiler executable (e.g., "clang++", "cl.exe").
        compile_flags: Compiler flags including any module-related flags
            that the scanner needs to see (-x c++-module, /interface, etc.).
    """

    src: Path
    obj_rel: str
    compiler: str
    compile_flags: list[str] = field(default_factory=list)


@dataclass
class TuScanResult:
    """Parsed P1689R5 scan output for a single translation unit.

    Properties expose the bits of the scan output that drive flag injection
    and dyndep generation, hiding the JSON shape.
    """

    spec: TuScanSpec
    p1689: dict[str, Any] | None  # None if the scan failed

    @property
    def _primary_provides(self) -> dict[str, Any] | None:
        """First entry in rules[0].provides, or None if this isn't a module-providing TU."""
        if self.p1689 is None:
            return None
        rules = self.p1689.get("rules", [])
        if not isinstance(rules, list) or not rules:
            return None
        first_rule = rules[0]
        if not isinstance(first_rule, dict):
            return None
        provides = first_rule.get("provides", [])
        if not isinstance(provides, list) or not provides:
            return None
        first = provides[0]
        return first if isinstance(first, dict) else None

    @property
    def is_module_provider(self) -> bool:
        """True if this TU produces a module (interface or partition impl)."""
        return self._primary_provides is not None

    @property
    def is_interface(self) -> bool:
        """True for primary interfaces and partition interfaces.

        False for internal partition implementation units (which on MSVC
        require the /internalPartition flag). Per P1689R5 the field defaults
        to True if absent.
        """
        prov = self._primary_provides
        if prov is None:
            return False
        return bool(prov.get("is-interface", True))

    @property
    def logical_name(self) -> str:
        """Logical module name (e.g., "jt.Math" or "jt.Math:BigUInt.Impl")."""
        prov = self._primary_provides
        if prov is None:
            return ""
        name = prov.get("logical-name", "")
        return name if isinstance(name, str) else ""

    @property
    def required_logical_names(self) -> list[str]:
        """Logical names of all imported modules across all rules."""
        if self.p1689 is None:
            return []
        rules = self.p1689.get("rules", [])
        if not isinstance(rules, list):
            return []
        names: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            requires = rule.get("requires", [])
            if not isinstance(requires, list):
                continue
            for req in requires:
                if not isinstance(req, dict):
                    continue
                ln = req.get("logical-name", "")
                if isinstance(ln, str) and ln:
                    names.append(ln)
        return names


def module_file_for(logical_name: str, mod_dir: str, extension: str) -> str:
    """Compute the IFC/PCM path for a given logical module name.

    Replaces ':' with '-' so partition names produce valid filenames
    (e.g., "jt.Math:BigUInt.Impl" -> "{mod_dir}/jt.Math-BigUInt.Impl.ifc").
    """
    safe = logical_name.replace(":", "-")
    return f"{mod_dir}/{safe}{extension}"


def select_modules_scope(
    source_obj_by_language: dict[str, list[tuple[Path, Any]]],
) -> tuple[list[tuple[Path, Any]], list[tuple[Path, Any]]]:
    """Filter C++ TUs to those in envs that have module scanning enabled.

    A C++ environment opts in to module scanning either:
      - Implicitly: the env has at least one source whose suffix is in
        CXX_MODULE_INTERFACE_SUFFIXES (so the resolver tagged it as
        `cxx_module`).
      - Explicitly: `env.cxx.modules = True`, for module units in
        `.cpp`/`.cc` files (e.g. fmt's primary interface in `.cc`, or a
        target whose only module use is `import std;`).

    Returns:
        (cxx_module_pairs, cxx_pairs) restricted to qualifying envs. If
        no env qualifies, both lists are empty and the toolchain's
        after_resolve should early-return.
    """
    cxx_module_pairs = source_obj_by_language.get("cxx_module", []) or []
    cxx_pairs = source_obj_by_language.get("cxx", []) or []

    qualifying_env_ids: set[int] = set()

    # Implicit opt-in: any env with an extension-tagged module source.
    for _, obj_node in cxx_module_pairs:
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            continue
        env = bi.get("env")
        if env is not None:
            qualifying_env_ids.add(id(env))

    # Explicit opt-in: env.cxx.modules == True.
    for _, obj_node in list(cxx_module_pairs) + list(cxx_pairs):
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            continue
        env = bi.get("env")
        if env is None:
            continue
        cxx = getattr(env, "cxx", None)
        if cxx is not None and bool(getattr(cxx, "modules", False)):
            qualifying_env_ids.add(id(env))

    def _belongs(obj_node: Any) -> bool:
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            return False
        env = bi.get("env")
        return env is not None and id(env) in qualifying_env_ids

    return (
        [pair for pair in cxx_module_pairs if _belongs(pair[1])],
        [pair for pair in cxx_pairs if _belongs(pair[1])],
    )


# TODO(scan-cache): cache TuScanResults across configure runs
# (content-addressed by source content, scanner/compiler versions, and
# normalized flags). Scans are currently O(TUs) per configure, which
# dominates configure latency on large module-heavy projects.
def scan_translation_units(
    specs: list[TuScanSpec],
    scanner: str,
    scanner_style: str = "clang",
) -> list[TuScanResult]:
    """Run the scanner on each TU and return parsed results.

    Args:
        specs: Per-TU scan inputs.
        scanner: Path to clang-scan-deps (clang style) or cl.exe (msvc style).
        scanner_style: "clang" or "msvc".

    Returns:
        One TuScanResult per spec, in order. result.p1689 is None if scanning
        that TU failed (a warning is written to stderr by the runner).
    """
    results: list[TuScanResult] = []
    for spec in specs:
        if scanner_style == "msvc":
            p1689 = run_scan_deps_msvc(spec.compiler, spec.compile_flags, str(spec.src))
        elif scanner_style == "gcc":
            p1689 = run_scan_deps_gcc(
                spec.compiler,
                spec.compile_flags,
                str(spec.src),
                spec.obj_rel,
            )
        else:
            p1689 = run_scan_deps(
                scanner,
                spec.compiler,
                spec.compile_flags,
                str(spec.src),
                spec.obj_rel,
            )
        results.append(TuScanResult(spec=spec, p1689=p1689))
    return results


def build_module_map(
    results: list[TuScanResult],
    mod_dir: str,
    extension: str,
) -> dict[str, str]:
    """Build logical-name -> module-file-path map from scan results."""
    mapping: dict[str, str] = {}
    for r in results:
        if r.is_module_provider:
            mapping[r.logical_name] = module_file_for(
                r.logical_name, mod_dir, extension
            )
    return mapping


@dataclass
class StdModuleFlagSpec:
    """Categorizes which user flags to carry onto the `import std;` compile.

    The standard-library module's `.pcm` / `.ifc` is consumed by user TUs,
    so it must agree with them on every flag that affects ABI or what
    the standard library's headers expose. Build systems can't pass *all*
    user flags (some break the std-module compile — `-Werror`, user
    `-I`s, unrelated `-D`s) so we filter:

    Attributes:
        exact: full-flag matches that are pure passthrough
            (e.g. ``"-fno-rtti"``).
        prefixes: flag prefixes that carry a value as one token
            (e.g. ``("-std=", "-stdlib=", "-isysroot=")``).
        paired: flags that take the value as the *next* token
            (e.g. ``{"-target", "-isysroot"}`` — passed as
            ``-target X``).
        define_prefix: the toolchain's define flag prefix (``"-D"`` or
            ``"/D"``); used together with ``define_glob_prefixes`` to
            select user defines that must propagate.
        define_glob_prefixes: macro-name prefixes whose ``-Dfoo[=...]``
            invocations carry through. Used for stdlib feature-test
            macros — ``("_LIBCPP_",)`` for libc++, ``("_HAS_",
            "_ITERATOR_DEBUG_LEVEL", "_CONTAINER_DEBUG_LEVEL")`` for
            MSVC's STL.
    """

    exact: frozenset[str]
    prefixes: tuple[str, ...]
    paired: frozenset[str]
    define_prefix: str
    define_glob_prefixes: tuple[str, ...]


def select_std_module_flags(
    base_flags: list[str], spec: StdModuleFlagSpec
) -> list[str]:
    """Pick ABI-affecting flags from the user's compile flags.

    Walks ``base_flags`` once. Per the spec, copies exact-match flags,
    prefix-match flags (with their values), pair flags (with the
    following token), and stdlib-relevant ``-D`` defines. Order is
    preserved.
    """
    out: list[str] = []
    i = 0
    while i < len(base_flags):
        f = base_flags[i]
        if f in spec.exact:
            out.append(f)
            i += 1
            continue
        if spec.prefixes and f.startswith(spec.prefixes):
            out.append(f)
            i += 1
            continue
        if f in spec.paired and i + 1 < len(base_flags):
            out.extend([f, base_flags[i + 1]])
            i += 2
            continue
        if spec.define_prefix and f.startswith(spec.define_prefix):
            macro = f[len(spec.define_prefix) :]
            if spec.define_glob_prefixes and macro.startswith(
                spec.define_glob_prefixes
            ):
                out.append(f)
        i += 1
    return out


def bmi_key_for_flags(flags: list[str], spec: StdModuleFlagSpec) -> str:
    """Compute a short hash identifying a BMI-compatibility class.

    A Binary Module Interface (``.gcm`` / ``.pcm`` / ``.ifc``) can only be
    consumed by translation units compiled with matching BMI-sensitive flags
    (the C++ dialect, ABI knobs, stdlib feature macros, etc. — exactly the set
    ``spec`` selects). Two TUs that agree on every such flag may share one
    compiled module interface; TUs that differ on any of them must each get
    their own. Keying the BMI's on-disk directory by this hash lets the same
    module interface be reused across targets when compatible
    (``cxx_modules/<key>/provider.gcm``) and kept separate when not.

    The hash is order-independent: BMI compatibility does not depend on the
    order BMI-sensitive flags appear on the command line.
    """
    relevant = select_std_module_flags(list(flags), spec)
    canonical = "\0".join(sorted(relevant))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def merge_scan_compile_flags(
    base_flags: list[str],
    context: Any,
    extra_flags: tuple[str, ...] = (),
) -> list[str]:
    """Build a per-TU compile-flag list for module scanning.

    Starts from *base_flags*, injects *extra_flags* (deduped, e.g. GCC's
    ``-fmodules``), then appends the build context's flags (deduped), ``-I``
    includes, and ``-D`` defines, in that order.
    """
    seen = set(base_flags)
    compile_flags = list(base_flags)
    for flag in extra_flags:
        if flag not in seen:
            compile_flags.append(flag)
            seen.add(flag)
    if context:
        for flag in context.flags:
            if flag not in seen:
                compile_flags.append(flag)
                seen.add(flag)
        for inc in context.includes:
            compile_flags.append(f"-I{inc}")
        for define in context.defines:
            compile_flags.append(f"-D{define}")
    return compile_flags


def wire_std_into_targets(
    project: Any,
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    std_obj_nodes: dict[str, Any],
) -> None:
    """Add std/std.compat .obj files to the link inputs of importing targets.

    For every project target, looks at which `import std;` / `import std.compat;`
    requirements its TUs have (via the scan results) and appends the
    corresponding synthesized std-module .obj to the target's
    intermediate_nodes (so the link rule sees it) and to its output nodes'
    explicit_deps (so the build graph has the dependency).

    Toolchain-agnostic: works for both MSVC (.obj files) and clang (.o files)
    so long as the caller supplied a {logical_name: obj_node} map.
    """
    obj_id_to_required: dict[int, set[str]] = {}
    for r in results:
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        obj_id_to_required[id(obj_node)] = set(r.required_logical_names)

    for target in project.targets:
        target_required: set[str] = set()
        for obj_node in target.intermediate_nodes:
            target_required.update(obj_id_to_required.get(id(obj_node), set()))
        for logical, std_obj_node in std_obj_nodes.items():
            if logical in target_required:
                if std_obj_node not in target.intermediate_nodes:
                    target.intermediate_nodes.append(std_obj_node)
                for output_node in target.output_nodes:
                    if std_obj_node not in output_node.explicit_deps:
                        output_node.explicit_deps.append(std_obj_node)


def write_dyndep_from_results(
    results: list[TuScanResult],
    module_to_pcm: dict[str, str],
    out_path: str | Path,
) -> None:
    """Write a Ninja dyndep file from pre-computed scan results.

    For each TU:
      - implicit outputs are the IFC/PCM files this TU provides
      - implicit inputs are the IFC/PCM files this TU imports
    """
    entries: list[tuple[str, list[str], list[str]]] = []
    for r in results:
        provides_pcms: list[str] = []
        if r.is_module_provider and r.logical_name in module_to_pcm:
            provides_pcms.append(module_to_pcm[r.logical_name])

        requires_pcms: list[str] = []
        for ln in sorted(set(r.required_logical_names)):
            if ln in module_to_pcm:
                requires_pcms.append(module_to_pcm[ln])

        entries.append((r.spec.obj_rel, provides_pcms, requires_pcms))

    write_dyndep_entries(entries, out_path)


def keyed_bmi_path(logical_name: str, moddir: str, key: str, extension: str) -> str:
    """BMI path for a logical module in its compatibility class's directory.

    E.g. ``keyed_bmi_path("provider", "cxx_modules", "49eea...", ".pcm")`` ->
    ``cxx_modules/49eea.../provider.pcm``.
    """
    return module_file_for(logical_name, f"{moddir}/{key}", extension)


def map_module_providers(
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    obj_key: dict[int, str],
    moddir: str,
    bmi_ext: str,
) -> dict[tuple[str, str], str]:
    """Map ``(bmi_key, logical_name)`` -> providing object path.

    Walks the module-providing scan results and records which object compiles
    each logical module within each BMI-compatibility class. Raises
    RuntimeError if two *different* objects provide the same module with
    BMI-equivalent flags — both would write the same keyed BMI path.

    Results whose spec is not registered in ``spec_to_obj`` are skipped.
    """
    provider_obj: dict[tuple[str, str], str] = {}
    for r in results:
        if not r.is_module_provider:
            continue
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        key = obj_key[id(obj_node)]
        slot = (key, r.logical_name)
        if slot in provider_obj and provider_obj[slot] != r.spec.obj_rel:
            raise RuntimeError(
                f"Module '{r.logical_name}' is compiled into two different "
                f"objects ({provider_obj[slot]} and {r.spec.obj_rel}) with "
                f"BMI-equivalent flags, so both would write the same "
                f"{keyed_bmi_path(r.logical_name, moddir, key, bmi_ext)}. "
                f"Give them distinct BMI-sensitive flags or build the "
                f"interface in one place."
            )
        provider_obj[slot] = r.spec.obj_rel
    return provider_obj


def build_keyed_entries(
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    obj_key: dict[int, str],
    provider_obj: dict[tuple[str, str], str],
    moddir: str,
    bmi_ext: str,
) -> list[tuple[str, list[str], list[str]]]:
    """Build dyndep entries with provides/requires keyed per compatibility class.

    Each TU's provided and required modules resolve to BMI paths in its own
    class's ``cxx_modules/<key>/`` directory (a BMI is only consumable by TUs
    whose BMI-sensitive flags match).

    Raises RuntimeError if a TU imports a module whose compiled interface
    exists only in *other* compatibility classes — the import could never be
    satisfied, and the compile-time error would be far less clear. Imports of
    modules not provided anywhere in the project are passed through silently
    (they may be satisfied externally).
    """
    entries: list[tuple[str, list[str], list[str]]] = []
    provided_anywhere = {logical for _, logical in provider_obj}
    for r in results:
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        key = obj_key[id(obj_node)]
        provides: list[str] = []
        if r.is_module_provider:
            provides.append(keyed_bmi_path(r.logical_name, moddir, key, bmi_ext))
        requires: list[str] = []
        for ln in r.required_logical_names:
            if (key, ln) in provider_obj:
                requires.append(keyed_bmi_path(ln, moddir, key, bmi_ext))
            elif ln in provided_anywhere:
                others = sorted(
                    obj for (_, logical), obj in provider_obj.items() if logical == ln
                )
                raise RuntimeError(
                    f"Module '{ln}' is imported by {r.spec.obj_rel}, but its "
                    f"compiled interface is only built with different "
                    f"BMI-sensitive flags (by {', '.join(others)}). A module "
                    f"interface is only consumable by TUs whose BMI-sensitive "
                    f"flags (C++ dialect, ABI options) match. Compile the "
                    f"interface with this TU's flags too (e.g. add its source "
                    f"to the importing target), or align the targets' flags."
                )
        entries.append((r.spec.obj_rel, provides, requires))
    return entries


def write_dyndep_entries(
    entries: list[tuple[str, list[str], list[str]]],
    out_path: str | Path,
) -> None:
    """Write a Ninja dyndep file from pre-resolved (obj, provides, requires).

    Each entry is ``(obj_rel, provides_paths, requires_paths)`` where the
    provides/requires are build-dir-relative BMI paths. Toolchains that map
    one logical module name to different BMI paths per compatibility class
    (GCC's per-key ``cxx_modules/<key>/`` layout) build entries directly,
    since a single ``{logical: path}`` map cannot express that.
    """
    lines = ["ninja_dyndep_version = 1", ""]
    for obj_rel, provides_pcms, requires_pcms in sorted(entries, key=lambda e: e[0]):
        implicit_out = (
            " | " + " ".join(sorted(set(provides_pcms))) if provides_pcms else ""
        )
        implicit_in = (
            " | " + " ".join(sorted(set(requires_pcms))) if requires_pcms else ""
        )
        lines.append(f"build {obj_rel}{implicit_out}: dyndep{implicit_in}")
        lines.append("")

    _write_text_if_changed(Path(out_path), "\n".join(lines))


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

        entries.append(
            (
                obj,
                sorted(set(provides_pcms)),
                sorted(set(requires_pcms)),
            )
        )

    # Write dyndep file
    lines = ["ninja_dyndep_version = 1", ""]
    for obj, provides_pcms, requires_pcms in sorted(entries, key=lambda e: e[0]):
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
    _write_text_if_changed(Path(out_path), dyndep_text)


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
        choices=["clang", "msvc", "gcc"],
        help="Scanner style: 'clang' (clang-scan-deps), 'msvc' (cl.exe), or 'gcc' (default: clang)",
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

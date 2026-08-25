# SPDX-License-Identifier: MIT
"""Configure-side helpers for the C++ module passes.

Each toolchain's ``after_resolve`` builds a per-target module pass out of these:
which environments take part (:func:`select_modules_scope`,
:func:`collect_module_scopes`), which BMI-compatibility class a translation
unit belongs to (:class:`StdModuleFlagSpec`, :func:`select_std_module_flags`,
:func:`bmi_key_for_flags`), what flags the scan runs with
(:func:`merge_scan_compile_flags`), and where the scanner executable is
(:func:`find_scan_deps`).

The scanning itself is not here. It happens at build time, in the per-TU scan
edges the :class:`~pcons.core.scan.Scanner` primitive generates, and their
results are collated by ``pcons.toolchains.cxx_collate``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CxxModuleScannerNotFound(RuntimeError):
    """Raised when the C++ module scanner executable is not on PATH.

    Let this propagate so configure fails loudly instead of producing
    empty/silent scans.
    """


CLANG_SCAN_DEPS_HINTS = (
    "C++ module scanner 'clang-scan-deps' not found on PATH.\n"
    "  C++20 modules require clang-scan-deps (shipped with LLVM/Clang).\n"
    "  Install hints:\n"
    "    macOS:        brew install llvm  (then add the keg's bin to PATH)\n"
    "    Ubuntu/Deb:   apt install clang-tools  (or a recent LLVM via apt.llvm.org)\n"
    "    Fedora/RHEL:  dnf install clang-tools-extra\n"
    "    Windows:      winget install LLVM.LLVM  (or use the LLVM installer)\n"
    "  Or set env.cxx.scan_deps to the full path of your clang-scan-deps."
)


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

    `env.cxx.modules = False` disables scanning for the env outright — it
    beats the suffix opt-in (with a warning, since a module interface then
    compiles as plain C++). The default is None: auto.

    Returns:
        (cxx_module_pairs, cxx_pairs) restricted to qualifying envs. If
        no env qualifies, both lists are empty and the toolchain's
        after_resolve should early-return.
    """
    cxx_module_pairs = source_obj_by_language.get("cxx_module", []) or []
    cxx_pairs = source_obj_by_language.get("cxx", []) or []

    qualifying_env_ids: set[int] = set()
    opted_out_env_ids: set[int] = set()

    def _modules_setting(obj_node: Any) -> tuple[int | None, object]:
        bi = getattr(obj_node, "_build_info", None)
        env = bi.get("env") if bi else None
        if env is None:
            return None, None
        cxx = getattr(env, "cxx", None)
        return id(env), getattr(cxx, "modules", None) if cxx is not None else None

    # Explicit settings first: True opts .cpp module units in; False wins
    # over everything, including the suffix opt-in below.
    for _, obj_node in list(cxx_module_pairs) + list(cxx_pairs):
        env_id, setting = _modules_setting(obj_node)
        if env_id is None:
            continue
        if setting is False:
            opted_out_env_ids.add(env_id)
        elif setting:
            qualifying_env_ids.add(env_id)

    # Implicit opt-in: any env with an extension-tagged module source.
    for _, obj_node in cxx_module_pairs:
        env_id, setting = _modules_setting(obj_node)
        if env_id is None:
            continue
        if env_id in opted_out_env_ids:
            # An explicit False beats the suffix opt-in — but a module
            # interface that will consequently compile as plain C++ is
            # almost never what anyone wants, so say so.
            logger.warning(
                "env.cxx.modules is False, but %s is a module interface "
                "unit; it will compile without module semantics. Remove "
                "the setting to scan this environment.",
                getattr(obj_node, "path", obj_node),
            )
            continue
        qualifying_env_ids.add(env_id)

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


@dataclass
class ModuleScope:
    """One scanned target: the unit a per-target module pass works on.

    ``pairs`` are ``(source_path, obj_node, is_module_suffix)`` for every C++
    TU whose object this target *owns*. An object the resolver's cache shares
    between targets belongs to the first target that claims it (declaration
    order); the others reach its modules through that scope's exports.
    """

    target: Any
    env: Any
    pairs: list[tuple[Path, Any, bool]]


def collect_module_scopes(
    project: Any,
    source_obj_by_language: dict[str, list[tuple[Path, Any]]],
    toolchain: Any,
) -> list[ModuleScope]:
    """Group the scanned TUs by owning target, in declaration order.

    Applies :func:`select_modules_scope`'s env opt-in rules, then assigns
    each object node to exactly one target — the dyndep contract: one edge,
    one governing dyndep file.

    ``after_resolve`` hands every toolchain the project-wide map, so pass
    the calling *toolchain* to keep the scope to TUs whose environment
    actually uses it — otherwise a gcc toolchain would claim clang's
    objects and each would attach its own scanner to the other's targets.
    """

    def _uses_toolchain(obj_node: Any) -> bool:
        bi = getattr(obj_node, "_build_info", None)
        env = bi.get("env") if bi else None
        return env is not None and any(
            tc is toolchain for tc in getattr(env, "toolchains", ())
        )

    source_obj_by_language = {
        lang: [pair for pair in pairs if _uses_toolchain(pair[1])]
        for lang, pairs in source_obj_by_language.items()
    }
    cxx_module_pairs, cxx_pairs = select_modules_scope(source_obj_by_language)
    if not cxx_module_pairs and not cxx_pairs:
        return []

    tagged = [(src, obj, True) for src, obj in cxx_module_pairs]
    tagged += [(src, obj, False) for src, obj in cxx_pairs]
    by_obj = {id(obj): (src, obj, is_mod) for src, obj, is_mod in tagged}

    scopes: list[ModuleScope] = []
    claimed: set[int] = set()
    for target in project.targets:
        pairs: list[tuple[Path, Any, bool]] = []
        shared_only = None
        for node in target.intermediate_nodes:
            entry = by_obj.get(id(node))
            if entry is None:
                continue
            if id(node) not in claimed:
                claimed.add(id(node))
                pairs.append(entry)
            else:
                shared_only = entry
        if pairs:
            env = getattr(pairs[0][1], "_build_info", {}).get("env")
            scopes.append(ModuleScope(target=target, env=env, pairs=pairs))
        elif shared_only is not None:
            # Every module TU this target compiles is owned by an earlier
            # target (the object cache shares the node). Emit an empty
            # scope so the scanner still attaches: the core wiring records
            # a pass-through, and dependents of *this* target reach the
            # owner's exports through it.
            env = getattr(shared_only[1], "_build_info", {}).get("env")
            scopes.append(ModuleScope(target=target, env=env, pairs=[]))
    return scopes


def find_scan_deps(env: Any, tool_names: list[str], hints: str) -> str:
    """The module scanner executable for *env*, checked at configure time.

    ``env.cxx.scan_deps`` overrides the search — the escape hatch the
    not-found error has always promised. Raises
    :class:`CxxModuleScannerNotFound` with install *hints* when nothing is
    found, at configure time, where the message can still help.
    """
    cxx = getattr(env, "cxx", None)
    override = getattr(cxx, "scan_deps", None) if cxx is not None else None
    if override:
        return str(override)
    for name in tool_names:
        found = shutil.which(name)
        if found:
            return found
    raise CxxModuleScannerNotFound(hints)


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
    *,
    iprefix: str = "-I",
    isysprefix: str = "-isystem",
    dprefix: str = "-D",
    root: Path | None = None,
) -> list[str]:
    """Build a per-TU compile-flag list for module scanning.

    Starts from *base_flags*, injects *extra_flags* (deduped, e.g. GCC's
    ``-fmodules``), then appends the build context's flags (deduped),
    includes, and defines, in that order.

    With *root*, relative include paths are anchored there and made
    absolute. These flags are chosen at configure time, from the project
    root, and used by a scan edge that runs from the build directory, so
    they must work from either place.
    """

    def include_path(inc: Any) -> str:
        if root is not None and not os.path.isabs(str(inc)):
            return str(root / str(inc))
        return str(inc)

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
            compile_flags.append(f"{iprefix}{include_path(inc)}")
        # Vendored SDK headers live here; without them the scanner can't
        # preprocess the TU it is scanning.
        for inc in getattr(context, "system_includes", ()):
            compile_flags.append(f"{isysprefix}{include_path(inc)}")
        for define in context.defines:
            compile_flags.append(f"{dprefix}{define}")
    return compile_flags

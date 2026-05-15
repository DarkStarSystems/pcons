# SPDX-License-Identifier: MIT
"""Generate typed-stub mixin classes for pcons's dynamic-attribute classes.

Four pcons classes use `__getattr__` for dispatch and lose static type
checking as a result:

  - `Project` → `pcons/core/_project_builder_stubs.py`. Builders
    (Program, StaticLibrary, ...) come from `BuilderRegistry` and are
    fully introspected.

  - `Environment` → `pcons/core/_environment_stubs.py`. Tool namespaces
    (cc, cxx, link, install, archive, ...) populated per-toolchain.
    There is no single runtime registry, so the tool list is hardcoded
    here (see `_ENVIRONMENT_TOOL_NAMES`).

  - `ToolConfig` → `pcons/core/_toolconfig_stubs.py`. Common per-tool
    variables (cmd, flags, includes, ...). Hardcoded; toolchains can
    invent new names that fall through to __getattr__ at type-check time.

  - `UsageRequirements` → `pcons/core/_usage_requirements_stubs.py`.
    The C/C++ conventional set (include_dirs, compile_flags, ...).
    Hardcoded for the same reason as ToolConfig.

For Project, __getattr__ is hidden from type checkers so typos are
caught (the cost: user @builder methods need `type: ignore`). For the
other three, __getattr__ stays visible so user-defined names continue
to work without ceremony — known names are typed more specifically and
take precedence.

The mixin classes are inherited only under `TYPE_CHECKING`; at runtime
the base class is `object` and the original `__getattr__` dispatches as
before.

Scaling caveat: the freshness test catches drift between the generator
output and the committed files. It does NOT catch omissions in the
hardcoded lists (e.g. adding a new tool to a toolchain without updating
`_ENVIRONMENT_TOOL_NAMES`). A future refactor that adds
`TOOL_NAMES: ClassVar` to each Toolchain class would close that gap.

Usage:
    python -m pcons._gen_stubs              # rewrite all stub files
    python -m pcons._gen_stubs --check      # exit 1 if any file is stale
    python -m pcons._gen_stubs --print      # write all to stdout
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Callable, Sequence
from inspect import Parameter
from pathlib import Path

from pcons.builders import register_builtin_builders
from pcons.core.builder_registry import BuilderRegistry


def _stub_targets() -> dict[str, Callable[[], str]]:
    """Map of relative path under pcons/ → function that produces the file content."""
    return {
        "core/_project_builder_stubs.py": generate_project_builder_stubs,
        "core/_environment_stubs.py": generate_environment_stubs,
        "core/_toolconfig_stubs.py": generate_toolconfig_stubs,
        "core/_usage_requirements_stubs.py": generate_usage_requirements_stubs,
    }


# Annotation names that need rewriting because `Environment` is conventionally
# imported as `Env` in pcons code (and we want the stub to match).
_ANNOTATION_ALIASES = {"Environment": "Env"}

# Parameters dropped from generated stubs (auto-captured at the call site).
_DROPPED_PARAMS = {"defined_at"}


def _rewrite_annotation(anno: str) -> str:
    """Rewrite a string annotation to match the stub file's local names."""
    import re

    out = anno
    for src, dst in _ANNOTATION_ALIASES.items():
        out = re.sub(rf"\b{re.escape(src)}\b", dst, out)
    return out


def _format_param(p: Parameter) -> str:
    """Render a Parameter to its source-level spelling."""
    if p.kind is Parameter.VAR_POSITIONAL:
        return f"*{p.name}"
    if p.kind is Parameter.VAR_KEYWORD:
        return f"**{p.name}"

    parts = [p.name]
    if p.annotation is not Parameter.empty:
        parts.append(f": {_rewrite_annotation(str(p.annotation))}")
    if p.default is not Parameter.empty:
        parts.append(f" = {p.default!r}")
    return "".join(parts)


def _format_method(
    name: str, description: str, params: list[Parameter], ret: str
) -> str:
    """Format one typed method inside `class _ProjectBuilders` (8-space indent)."""
    lines: list[str] = []
    lines.append(f"        def {name}(")
    lines.append("            self,")

    saw_kw_only = False
    seen_positional = False
    for p in params:
        if p.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.POSITIONAL_ONLY):
            seen_positional = True
        if p.kind is Parameter.KEYWORD_ONLY and not saw_kw_only and seen_positional:
            lines.append("            *,")
            saw_kw_only = True
        if p.kind is Parameter.VAR_POSITIONAL:
            saw_kw_only = True
        lines.append(f"            {_format_param(p)},")

    lines.append(f"        ) -> {_rewrite_annotation(ret)}:")
    if description:
        lines.append(f'            """{description}"""')
    lines.append("            ...")
    return "\n".join(lines)


def _owner_of(create_target_fn: object) -> type | None:
    """Return the class that owns `create_target` if there is one.

    `@builder` is applied to a class with `create_target` as a staticmethod;
    in that case we recover the class for its docstring. Builders registered
    directly via `BuilderRegistry.register(create_target=<plain function>)`
    have no owning class, so we return None and let the caller fall back.
    """
    qualname = getattr(create_target_fn, "__qualname__", "")
    module = getattr(create_target_fn, "__module__", "")
    if "." in qualname and module:
        cls_name = qualname.rsplit(".", 1)[0]
        mod = sys.modules.get(module)
        if mod is not None:
            cls = getattr(mod, cls_name, None)
            if isinstance(cls, type):
                return cls
    return None


_PROJECT_FILE_HEADER = '''\
# SPDX-License-Identifier: MIT
# ruff: noqa
# fmt: off
"""Typed stub declarations for Project's built-in builder methods.

GENERATED by `python -m pcons._gen_stubs` from the BuilderRegistry. Do
not edit by hand — re-run the generator instead.

`Project` inherits from `_ProjectBuilders` only under TYPE_CHECKING (see
project.py). At runtime, `_ProjectBuilders = object` and builder lookup
falls through to `Project.__getattr__` as before. The methods here exist
solely so that type checkers (ty, pyright, mypy) can flag typos like
`project.Programx(...)` and provide completion. User-registered
`@builder` targets are not in this file and remain typed as `Any`.

The class body is nested under `if TYPE_CHECKING:` so that ty does not
treat the `def Foo(...) -> Target: ...` empty bodies as runtime errors;
type checkers see the declarations, runtime never executes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment as Env
    from pcons.core.node import FileNode, Node
    from pcons.core.target import Target
    from pcons.tools.archive import ArchiveTarget

    class _ProjectBuilders:
        """Typed builder methods for Project (TYPE_CHECKING-only mixin)."""

'''


def generate_project_builder_stubs() -> str:
    """Produce the full content of `_project_builder_stubs.py`."""
    register_builtin_builders()
    parts: list[str] = [_PROJECT_FILE_HEADER]

    methods: list[str] = []
    for name, reg in sorted(BuilderRegistry.all().items()):
        sig = inspect.signature(reg.create_target)
        params = list(sig.parameters.values())[1:]  # drop leading `project`
        params = [p for p in params if p.name not in _DROPPED_PARAMS]
        ret = str(sig.return_annotation)

        description = ""
        owner = _owner_of(reg.create_target)
        if owner is not None and owner.__doc__:
            description = owner.__doc__.strip().splitlines()[0]
        if not description and reg.description:
            description = reg.description
        if reg.platforms:
            description = f"{description} [{'/'.join(reg.platforms)} only]".strip()

        methods.append(_format_method(name, description, params, ret))

    parts.append("\n\n".join(methods))
    parts.append("\n")
    return "".join(parts)


# Well-known tool namespaces registered on Environment by toolchains and
# standalone tools. Unlike Project's builders, there is no single runtime
# registry — each toolchain populates `env._tools` from its own list. Keep
# this in sync when adding a tool to a toolchain's setup; group by source
# so it's easy to audit.
_ENVIRONMENT_TOOL_NAMES: tuple[tuple[str, str], ...] = (
    # name, comment-source
    ("cc", "C/C++ toolchains (gcc, llvm, msvc, clang_cl, emscripten, wasi)"),
    ("cxx", "C/C++ toolchains"),
    ("ar", "GCC/LLVM/emscripten/wasi/gfortran"),
    ("link", "all C/C++ and Fortran toolchains"),
    ("lib", "MSVC and clang-cl"),
    ("rc", "MSVC and clang-cl (Windows resource compiler)"),
    ("ml", "MSVC and clang-cl (assembler)"),
    ("mt", "MSVC (manifest tool)"),
    ("metal", "LLVM on macOS (Apple Metal shader compiler)"),
    ("fc", "gfortran"),
    ("cuda", "CUDA toolchain (added via env.add_toolchain)"),
    ("cython", "Cython toolchain"),
    ("cycc", "Cython toolchain"),
    ("cylink", "Cython toolchain"),
    ("install", "always set up via Environment._setup_standalone_tools"),
    ("archive", "always set up via Environment._setup_standalone_tools"),
)

# Well-known Environment instance variables (initialized in Environment.__init__).
# Unlike tool namespaces, these are real attribute reads, not `_tools` lookups.
_ENVIRONMENT_VAR_TYPES: tuple[tuple[str, str], ...] = (
    ("build_dir", "Path"),
    ("variant", "str"),
)


def _format_attribute_stubs_file(
    module_doc: str,
    extra_typecheck_imports: Sequence[str],
    class_name: str,
    class_doc: str,
    entries: Sequence[tuple[str, str, str | None]],  # name, type, optional comment
) -> str:
    """Format an attribute-stub file: header + class with `name: Type  # comment`.

    Used for the simpler stub files (Environment, ToolConfig, UsageRequirements)
    where there is no method-signature complexity — just a flat list of
    typed instance attributes.
    """
    lines: list[str] = [
        "# SPDX-License-Identifier: MIT",
        "# ruff: noqa",
        "# fmt: off",
        f'"""{module_doc}"""',
        "",
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "from typing import TYPE_CHECKING, Any",
        "",
        "if TYPE_CHECKING:",
    ]
    for imp in extra_typecheck_imports:
        lines.append(f"    {imp}")
    lines.extend(
        [
            "",
            f"    class {class_name}:",
            f'        """{class_doc}"""',
            "",
        ]
    )
    for name, typ, comment in entries:
        suffix = f"  # {comment}" if comment else ""
        lines.append(f"        {name}: {typ}{suffix}")
    lines.append("")
    return "\n".join(lines)


def generate_environment_stubs() -> str:
    """Produce the full content of `_environment_stubs.py`."""
    module_doc = (
        "Typed stub declarations for Environment's tool namespaces and known variables.\n"
        "\n"
        "GENERATED by `python -m pcons._gen_stubs`. Do not edit by hand.\n"
        "\n"
        "Each toolchain registers its own subset of tools into `env._tools` at\n"
        "setup time; there is no single runtime registry to introspect. The\n"
        "tool list is therefore maintained in `_gen_stubs.py`. Adding a new\n"
        "tool to a toolchain requires updating `_ENVIRONMENT_TOOL_NAMES` there.\n"
        "\n"
        "Environment.__getattr__ is intentionally left visible to type checkers\n"
        "(returning `Any`), so user-defined cross-tool variables like\n"
        "`env.my_flag = ...` continue to work without a type:ignore. Known names\n"
        "below are typed more specifically and take precedence."
    )
    entries: Sequence[tuple[str, str, str | None]] = [
        (tool, "ToolConfig", source) for tool, source in _ENVIRONMENT_TOOL_NAMES
    ] + [(var, typ, None) for var, typ in _ENVIRONMENT_VAR_TYPES]
    return _format_attribute_stubs_file(
        module_doc=module_doc,
        extra_typecheck_imports=["from pcons.core.toolconfig import ToolConfig"],
        class_name="_EnvironmentStubs",
        class_doc="Typed mixin for Environment (TYPE_CHECKING-only).",
        entries=entries,
    )


# Well-known per-tool variable names commonly read or set as `env.<tool>.<var>`.
# Toolchains can invent their own; these cover the universal C/C++/link surface.
# Tuple shape: (name, type, comment). Comment may be None.
_TOOLCONFIG_VAR_TYPES: tuple[tuple[str, str, str | None], ...] = (
    ("cmd", "str | list[Any]", "command template (string or token list)"),
    ("flags", "list[Any]", "compile/link flags; can hold strings or subst tokens"),
    ("includes", "list[Path | str]", "include directories"),
    ("defines", "list[str]", "preprocessor defines, e.g. ['FOO=1']"),
    ("libs", "list[Any]", "library names or Target/subst-token entries"),
    ("libdirs", "list[Path | str]", "library search directories"),
    ("frameworks", "list[str]", "macOS frameworks"),
    ("frameworkdirs", "list[Path | str]", "macOS framework search dirs"),
    ("exe", "str", "path to the tool executable"),
)


def generate_toolconfig_stubs() -> str:
    """Produce the full content of `_toolconfig_stubs.py`."""
    module_doc = (
        "Typed stub declarations for ToolConfig's well-known variables.\n"
        "\n"
        "GENERATED by `python -m pcons._gen_stubs`. Do not edit by hand.\n"
        "\n"
        "Variables under a tool namespace (`env.cc.flags`, `env.link.libs`, …)\n"
        "are stored in a dict and accessed via __getattr__, which returns Any.\n"
        "The names below cover the universal C/C++/link surface so editors can\n"
        "type and complete the common operations. Toolchain-specific variables\n"
        "continue to work via __getattr__ — that path stays visible to type\n"
        "checkers so unusual names do not need `type: ignore`."
    )
    entries: list[tuple[str, str, str | None]] = list(_TOOLCONFIG_VAR_TYPES)
    return _format_attribute_stubs_file(
        module_doc=module_doc,
        extra_typecheck_imports=[],  # only needs Path, already in base imports
        class_name="_ToolConfigStubs",
        class_doc="Typed mixin for ToolConfig (TYPE_CHECKING-only).",
        entries=entries,
    )


# Well-known usage-requirement names on target.public / target.private.
# Toolchains can invent their own; these are the C/C++ conventional set.
_USAGE_REQUIREMENT_TYPES: tuple[tuple[str, str, str | None], ...] = (
    (
        "include_dirs",
        "list[Path | str]",
        "directories added to dependents' include path",
    ),
    (
        "compile_flags",
        "list[Any]",
        "flags propagated to dependents (strings + subst tokens)",
    ),
    (
        "link_flags",
        "list[Any]",
        "flags propagated to dependents (strings + subst tokens)",
    ),
    ("defines", "list[str]", "preprocessor defines propagated to dependents"),
    (
        "link_libs",
        "list[Any]",
        "libraries propagated to dependents (str + Target + tokens)",
    ),
)


def generate_usage_requirements_stubs() -> str:
    """Produce the full content of `_usage_requirements_stubs.py`."""
    module_doc = (
        "Typed stub declarations for UsageRequirements's well-known fields.\n"
        "\n"
        "GENERATED by `python -m pcons._gen_stubs`. Do not edit by hand.\n"
        "\n"
        "`target.public.*` and `target.private.*` access values through\n"
        "UsageRequirements.__getattr__, which returns `list` for any name.\n"
        "The names below are the C/C++ conventional set. Toolchains can\n"
        "invent any other names they need; those continue to work via\n"
        "__getattr__ (kept visible to type checkers)."
    )
    entries: list[tuple[str, str, str | None]] = list(_USAGE_REQUIREMENT_TYPES)
    return _format_attribute_stubs_file(
        module_doc=module_doc,
        extra_typecheck_imports=[],
        class_name="_UsageRequirementsStubs",
        class_doc="Typed mixin for UsageRequirements (TYPE_CHECKING-only).",
        entries=entries,
    )


def _stub_file_path(relpath: str) -> Path:
    return Path(__file__).resolve().parent / relpath


def write_or_check(mode: str) -> int:
    targets = _stub_targets()
    rc = 0
    for relpath, producer in targets.items():
        new_content = producer()
        if mode == "print":
            sys.stdout.write(f"# === {relpath} ===\n")
            sys.stdout.write(new_content)
            sys.stdout.write("\n")
            continue

        path = _stub_file_path(relpath)
        current = path.read_text() if path.exists() else ""

        if mode == "check":
            if current != new_content:
                sys.stderr.write(
                    f"{path} is out of date with generated stubs.\n"
                    f"Run: python -m pcons._gen_stubs\n"
                )
                rc = 1
        elif mode == "write":
            if current != new_content:
                path.write_text(new_content)
                print(f"Updated {path}")
            else:
                print(f"{path} is up to date.")
        else:
            raise ValueError(f"unknown mode: {mode}")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="Fail if file is stale")
    g.add_argument("--print", action="store_true", help="Print to stdout")
    args = ap.parse_args(argv)
    mode = "check" if args.check else "print" if args.print else "write"
    return write_or_check(mode)


if __name__ == "__main__":
    sys.exit(main())

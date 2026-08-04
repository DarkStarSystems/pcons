# SPDX-License-Identifier: MIT
"""Template file substitution (CMake-style configure_file and more).

Substitutes variables in a template file and writes the result.
Runs at configure time (during pcons-build.py execution).

Supported styles:

- ``"cmake"``: CMake-compatible ``@VAR@`` substitution plus
  ``#cmakedefine``, ``#cmakedefine01`` directives.
- ``"at"``: Simple ``@VAR@`` substitution only.

Example::

    from pcons import configure_file

    configure_file(
        "config.h.in", "build/config.h",
        {"VERSION": "1.2.3", "HAVE_ZLIB": "1"},
    )
"""

from __future__ import annotations

import re
from pathlib import Path

# Values treated as false by CMake for #cmakedefine / #cmakedefine01
_FALSY_VALUES = frozenset({"", "0", "off", "false", "no"})


def _is_truthy(variables: dict[str, str], var: str) -> bool:
    """Check whether *var* is defined and truthy in *variables*."""
    val = variables.get(var)
    if val is None:
        return False
    return val.lower() not in _FALSY_VALUES


def configure_file(
    template: Path | str,
    output: Path | str,
    variables: dict[str, str],
    *,
    style: str = "cmake",
    strict: bool = True,
) -> Path:
    """Substitute variables in *template* and write *output*.

    Args:
        template: Path to the input template file.
        output: Path to the output file to write.
        variables: Mapping of variable names to string values.
        style: Substitution style.

            ``"cmake"``
                CMake-compatible.  Processes ``#cmakedefine01``,
                ``#cmakedefine``, and ``@VAR@`` substitutions.
            ``"at"``
                Simple ``@VAR@`` replacement only.

        strict: If True (default), raise ``KeyError`` when an ``@VAR@``
            in the template has no matching key in *variables*.
            If False, missing variables are replaced with the empty string.

    Returns:
        The *output* path (as a ``Path`` object), for convenient chaining.

    Raises:
        KeyError: If *strict* is True and a variable is missing.
        FileNotFoundError: If *template* does not exist.
        ValueError: If *style* is not recognised.
    """
    template = Path(template)
    output = Path(output)

    # The template is an input to the build description, so editing it has to
    # re-run pcons: the substitution happens here, at configure time, and
    # nothing in the generated build knows the template exists.
    from pcons.core.project import Project

    try:
        Project.current().add_configure_dependency(template)
    except ValueError:
        pass  # called outside a project (a unit test, or a bare script)

    if style not in ("cmake", "at"):
        raise ValueError(
            f"Unknown configure_file style {style!r}; expected 'cmake' or 'at'"
        )

    text = template.read_text()

    # ── CMake directives (before @VAR@ substitution) ────────────────────
    if style == "cmake":
        # #cmakedefine01 VAR  →  #define VAR 1  or  #define VAR 0
        def _cmakedefine01(m: re.Match[str]) -> str:
            var = m.group(1)
            return (
                f"#define {var} 1" if _is_truthy(variables, var) else f"#define {var} 0"
            )

        text = re.sub(r"#cmakedefine01\s+(\w+)", _cmakedefine01, text)

        # #cmakedefine VAR <value>  →  #define VAR <value>  or  /* #undef VAR */
        def _cmakedefine_val(m: re.Match[str]) -> str:
            var = m.group(1)
            val_suffix = m.group(2)
            if _is_truthy(variables, var):
                return f"#define {var}{val_suffix}"
            return f"/* #undef {var} */"

        text = re.sub(r"#cmakedefine\s+(\w+)([ \t]+.+)", _cmakedefine_val, text)

        # #cmakedefine VAR  (bare, end of line)  →  #define VAR  or  /* #undef VAR */
        def _cmakedefine_bare(m: re.Match[str]) -> str:
            var = m.group(1)
            if _is_truthy(variables, var):
                return f"#define {var}"
            return f"/* #undef {var} */"

        text = re.sub(
            r"#cmakedefine\s+(\w+)[^\S\n]*$",
            _cmakedefine_bare,
            text,
            flags=re.MULTILINE,
        )

    # ── @VAR@ substitution (both styles) ────────────────────────────────
    def _at_replace(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in variables:
            return variables[var]
        if strict:
            raise KeyError(
                f"Variable @{var}@ in {template} has no entry in the variables dict"
            )
        return ""

    text = re.sub(r"@(\w+)@", _at_replace, text)

    return write_file(output, text)


def _anchor(path: Path | str) -> Path:
    """A relative path taken from the project root, like every other in pcons.

    Configure-time writes would otherwise land relative to the working
    directory, which is the source root when the CLI runs a build script but
    anything at all when a script or a test calls in directly.
    """
    path = Path(path)
    if path.is_absolute():
        return path

    from pcons.core.project import Project

    try:
        return Path(Project.current().root_dir) / path
    except ValueError:
        return path  # no project: the working directory is all there is


def write_file(output: Path | str, content: str | bytes) -> Path:
    """Write *content* to *output* at configure time, if it differs.

    For a file whose content the build script computes — an Info.plist, a
    PkgInfo, a version header, a generated .pc. The write happens during
    generate, so the file is an ordinary source by the time the build runs:
    no rule, no process per file, and no quoting to get wrong. Content that
    has not changed keeps its timestamp, so nothing downstream rebuilds.

    Pass ``bytes`` when the exact bytes matter — a classic macOS ``PkgInfo``
    is 8 bytes with no trailing newline.

    The build script is already a configure dependency, so editing the script
    that computes the content re-runs pcons and rewrites the file.

    Args:
        output: Path to write.
        content: Text, or bytes when the encoding is yours to control.

    Returns:
        The *output* path, for use as a source or an ``Install()`` input.

    Example:
        plist = write_file(build_dir / "Info.plist", generate_info_plist(...))
        project.Install(contents_dir, [plist])
    """
    output = _anchor(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(content, bytes):
        if output.exists() and output.read_bytes() == content:
            return output
        output.write_bytes(content)
        return output

    if output.exists() and output.read_text() == content:
        return output
    output.write_text(content)
    return output

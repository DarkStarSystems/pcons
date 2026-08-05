# SPDX-License-Identifier: MIT
"""A small reference reader for the build.ninja syntax pcons emits.

This is an independent transcription of Ninja's lexical rules, not a
reuse of the generator's escaping code -- otherwise the properties built
on it would only prove pcons agrees with itself.

The rules that matter for paths (Ninja manual, "Lexical syntax"): a `$`
escapes a space, a colon or another `$`, continues a line, or introduces
a variable reference. A `$` in front of anything else is a syntax error,
which is what makes an unescaped path in the output detectable here
instead of at build time.
"""

from __future__ import annotations

import re

_IDENT = re.compile(r"[A-Za-z0-9_.-]+")
_SEPARATORS = ("|", "||")


class NinjaSyntaxError(ValueError):
    """Ninja would refuse to parse this file."""


def _lex(line: str, variables: dict[str, str]) -> list[str]:
    """Split one line into tokens, resolving escapes and known variables.

    An unescaped colon is returned as its own token: it is what separates
    a build statement's outputs from its rule.
    """
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    i = 0
    while i < len(line):
        char = line[i]
        if char == "$":
            following = line[i + 1 : i + 2]
            if following in (" ", ":", "$"):
                current.append(following)
                i += 2
                continue
            if following == "{":
                end = line.index("}", i)
                current.append(variables.get(line[i + 2 : end], ""))
                i = end + 1
                continue
            match = _IDENT.match(line, i + 1)
            if match is None:
                raise NinjaSyntaxError(
                    f"bad $-escape (literal $ must be written as $$): {line!r}"
                )
            current.append(variables.get(match.group(), ""))
            i = match.end()
            continue
        if char in " \t":
            flush()
        elif char == ":":
            flush()
            tokens.append(":")
        else:
            current.append(char)
        i += 1

    flush()
    return tokens


def _logical_lines(text: str) -> list[str]:
    """Join `$`-continued lines, and drop comments and blank lines."""
    lines: list[str] = []
    for raw in text.replace("$\n", "").split("\n"):
        if raw.strip() and not raw.lstrip().startswith("#"):
            lines.append(raw)
    return lines


def parse(text: str) -> list[tuple[list[str], list[str]]]:
    """Read build statements as (outputs, inputs), paths fully resolved.

    Raises NinjaSyntaxError on anything Ninja itself would reject --
    including in variable values, where a badly escaped path hides until
    something expands it.
    """
    variables: dict[str, str] = {}
    builds: list[tuple[list[str], list[str]]] = []
    in_rule = False

    for line in _logical_lines(text):
        indented = line[:1] in (" ", "\t")
        stripped = line.strip()

        if not indented:
            in_rule = stripped.startswith(("rule ", "pool "))

        if indented:
            # Rule and edge bodies name $in/$out and other variables that
            # exist only per edge, so their values are checked for valid
            # escaping but not resolved, and never bind at file scope.
            _, _, value = stripped.partition(" = ")
            _lex(value, {})
            continue

        if stripped.startswith("build "):
            tokens = _lex(stripped[len("build ") :], variables)
            colon = tokens.index(":")
            outputs = [t for t in tokens[:colon] if t not in _SEPARATORS]
            # tokens[colon + 1] is the rule name.
            inputs = [t for t in tokens[colon + 2 :] if t not in _SEPARATORS]
            builds.append((outputs, inputs))
        elif not in_rule and " = " in stripped:
            name, _, value = stripped.partition(" = ")
            variables[name] = " ".join(_lex(value, variables))

    return builds

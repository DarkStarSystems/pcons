# SPDX-License-Identifier: MIT
"""Build-time report: a header two targets that link together both moc.

One edge per link closure. Its inputs are the ``automoc.exports.json``
documents the automoc edges of that closure write, each naming the headers
one target moc'ed and the include chain that reached them:

    python -m pcons.toolchains.qt._moc_report --stamp <file> <exports...>

A header that appears in two of them is compiled into both targets, and the
link fails on ``staticMetaObject`` and ``qt_static_metacall`` in files the
author never wrote. The include chain is printed because it is the part the
linker error cannot tell you.

Which headers a target mocs is decided while the build runs, so the report
runs there too. The closure it groups over is the target graph and stays a
configure-time fact, which is what keeps this edge's input list static.

The command line stays argparse rather than click: it runs under
``$qt.python``, which the user may point at an interpreter that has pcons but
none of pcons' own dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPORTS_VERSION = 1


def duplicate_moc_warnings(documents: list[dict[str, object]]) -> list[str]:
    """One message per header that two of *documents* both moc.

    Args:
        documents: The exports documents of one link closure.

    Returns:
        The messages, header order stable.
    """
    headers: dict[str, list[tuple[str, list[str]]]] = {}
    for document in documents:
        target = str(document.get("target", ""))
        exported = document.get("headers")
        if not isinstance(exported, dict):
            continue
        for header, chain in exported.items():
            headers.setdefault(str(header), []).append(
                (target, [str(step) for step in chain])
            )

    messages: list[str] = []
    for header, sharers in sorted(headers.items()):
        if len(sharers) < 2:
            continue
        sharers = sorted(sharers, key=lambda pair: pair[0])
        chains = "\n".join(
            f"  '{target}' reaches it through " + " -> ".join(chain)
            for target, chain in sharers
        )
        names = " and ".join(f"'{target}'" for target, _ in sharers)
        messages.append(
            f"pcons Qt: targets {names} "
            f"{'both' if len(sharers) == 2 else 'all'} run moc on {header} "
            "and link together, so its meta-object code is compiled once per "
            "target and the link fails on duplicate 'staticMetaObject' / "
            f"'qt_static_metacall' symbols.\n{chains}\nOne target has to own "
            "the class. no_moc excludes a file from moc generation only: the "
            "scan still opens it and still follows its includes, so excluding "
            "a header moves the duplicate one include deeper instead of "
            "removing it."
        )
    return messages


def _read(path: Path) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pcons Qt: cannot read '{path}': {exc}", file=sys.stderr)
        return None
    if document.get("version") != EXPORTS_VERSION:
        print(
            f"pcons Qt: unrecognized automoc exports version "
            f"{document.get('version')!r} in '{path}' — re-run pcons to "
            "regenerate the build files.",
            file=sys.stderr,
        )
        return None
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("exports", nargs="+")
    args = parser.parse_args(argv)

    documents = [_read(Path(path)) for path in args.exports]
    if any(document is None for document in documents):
        return 1
    for message in duplicate_moc_warnings(
        [document for document in documents if document is not None]
    ):
        print(message, file=sys.stderr)

    stamp = Path(args.stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

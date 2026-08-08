# SPDX-License-Identifier: MIT
"""Render a catalogue to a text report.

Stands in for an action whose real cost is becoming ready rather than doing
the work: it needs a parser loaded before it can parse anything. A worker
holds that state, so this action starts with it already in place.
"""

import sys
from pathlib import Path

# Before the import, so it reports what this process was handed rather than
# what it did next. Inside a worker's child the module is already loaded.
started_ready = "xml.dom.minidom" in sys.modules

from xml.dom import minidom  # noqa: E402

source, output = sys.argv[1], sys.argv[2]
names: list[str] = []
for node in minidom.parse(source).getElementsByTagName("item"):
    text = node.firstChild
    if text is not None and text.nodeValue:
        names.append(text.nodeValue)
Path(output).write_text(", ".join(names) + "\n", encoding="utf-8")

print("started ready:", started_ready)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the IDE metadata produced for nested projects.

Loads build/pcons_metadata.json and checks that nested Project() instances
are represented correctly:

  - every nested project has its own entry, with the right parent link;
  - each project entry lists only its own targets;
  - no target is duplicated across project entries.
"""

import json
import sys
from pathlib import Path

META = Path(__file__).parent / "build" / "pcons_metadata.json"

data = json.loads(META.read_text())
projects = {p["name"]: p for p in data["projects"]}

errors = []

# 1. Every nested project gets its own entry.
expected = {"nested_root", "nested_child", "nested_grandchild"}
missing = expected - projects.keys()
if missing:
    errors.append(f"Missing project entries: {sorted(missing)}")

# 2. Parent links form the expected root -> child -> grandchild chain.
chain = {
    "nested_root": None,
    "nested_child": "nested_root",
    "nested_grandchild": "nested_child",
}
for name, parent in chain.items():
    if name not in projects:
        continue  # already reported as missing above
    got = projects[name]["parent"]
    if got != parent:
        errors.append(f"{name} parent is {got!r}, expected {parent!r}")

# 3. Each project lists only its own target.
own = {
    "nested_root": ["root_app"],
    "nested_child": ["child_app"],
    "nested_grandchild": ["grandchild_app"],
}
for name, names in own.items():
    if name not in projects:
        continue  # already reported as missing above
    got = sorted(t["name"] for t in projects[name]["targets"])
    if got != sorted(names):
        errors.append(f"{name} targets are {got}, expected {sorted(names)}")

# 4. No target appears in more than one project entry.
seen: dict[str, str] = {}
for p in data["projects"]:
    for t in p["targets"]:
        q = t["qualified_name"]
        if q in seen:
            errors.append(f"Duplicate target {q} in {seen[q]!r} and {p['name']!r}")
        else:
            seen[q] = p["name"]

if errors:
    print("FAIL:", file=sys.stderr)
    for e in errors:
        print("  -", e, file=sys.stderr)
    sys.exit(1)

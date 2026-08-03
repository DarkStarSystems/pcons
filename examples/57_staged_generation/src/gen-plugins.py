#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write one C source per plugin, plus a registry header.

Deliberately naive: it rewrites every file every run, like most real code
generators. ``write_if_different=True`` on the pcons Command is what keeps
that from rebuilding the world — see pcons-build.py.

Usage: gen-plugins.py <plugins-list.txt>   (writes beside the manifest)
"""

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <plugins-list.txt>", file=sys.stderr)
        return 2

    manifest = Path(sys.argv[1])
    out_dir = manifest.parent
    names = manifest.read_text().split()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Each plugin source stands alone -- it does not include the registry
    # header, so adding a plugin doesn't recompile the others.
    for index, name in enumerate(names):
        (out_dir / f"S_{name}.c").write_text(
            f"int plugin_{name}(void) {{ return {index}; }}\n"
        )

    declarations = "".join(f"int plugin_{name}(void);\n" for name in names)
    table = "".join(f'    {{ "{name}", plugin_{name} }},\n' for name in names)
    (out_dir / "plugins.h").write_text(
        "#ifndef PLUGINS_H\n"
        "#define PLUGINS_H\n\n"
        f"{declarations}\n"
        "struct plugin { const char *name; int (*fn)(void); };\n\n"
        "static const struct plugin plugins[] = {\n"
        f"{table}"
        "};\n\n"
        f"#define PLUGIN_COUNT {len(names)}\n\n"
        "#endif\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

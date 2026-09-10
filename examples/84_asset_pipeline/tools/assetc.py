# SPDX-License-Identifier: MIT
"""A three-step asset tool, just for this contrived example: compile a
scene, pack the results, write a manifest.

``compile`` reports the assets it read (the scene, its textures and the
palette) in a make-style depfile, the way a compiler reports the headers it
included, so the build step that runs it discovers its dependencies. It
deliberately does *not* report the options file: that arrives as a flag,
and the tool treats it the way a compiler treats a response file -- read,
not reported. ``pack`` and ``manifest`` report nothing.
"""

import argparse
import sys
from pathlib import Path


def _depfile_path(path: str) -> str:
    return path.replace(" ", "\\ ")


def cmd_compile(args: argparse.Namespace) -> None:
    scene = Path(args.scene)
    options = dict(
        line.split("=", 1) for line in Path(args.options).read_text().split()
    )
    palette_path = Path(args.palette)
    palette = dict(line.split("=", 1) for line in palette_path.read_text().split())
    scale = int(options.get("scale", "1"))
    read: list[str] = [args.scene, args.palette]
    out: list[str] = []
    for line in scene.read_text().split("\n"):
        kind, _, name = line.strip().partition(" ")
        if kind == "texture":
            texture = scene.parent.parent / "textures" / f"{name}.txt"
            read.append(str(texture))
            pixels = texture.read_text().split()
            out.append(f"texture {name} {len(pixels) * scale}")
        elif kind == "color":
            out.append(f"color {name} {palette[name]}")
    Path(args.output).write_text("\n".join(out) + "\n")
    deps = " ".join(_depfile_path(p) for p in read)
    Path(args.depfile).write_text(f"{_depfile_path(args.output)}: {deps}\n")


def cmd_pack(args: argparse.Namespace) -> None:
    chunks = []
    for name in args.inputs:
        body = Path(name).read_text()
        chunks.append(f"[{Path(name).stem}]\n{body}")
    Path(args.output).write_text("".join(chunks))


def cmd_manifest(args: argparse.Namespace) -> None:
    lines = []
    for chunk in Path(args.pack).read_text().split("[")[1:]:
        name, _, body = chunk.partition("]\n")
        lines.append(f"{name} {len(body.split(chr(10))) - 1} entries")
    Path(args.output).write_text("\n".join(lines) + "\n")


def cmd_palette(args: argparse.Namespace) -> None:
    Path(args.output).write_text(Path(args.source).read_text())


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compile")
    p.add_argument("scene")
    p.add_argument("-o", dest="output", required=True)
    p.add_argument("--depfile", required=True)
    p.add_argument("--palette", required=True)
    p.add_argument("--options", required=True)
    p.set_defaults(run=cmd_compile)
    p = sub.add_parser("pack")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", dest="output", required=True)
    p.set_defaults(run=cmd_pack)
    p = sub.add_parser("manifest")
    p.add_argument("pack")
    p.add_argument("-o", dest="output", required=True)
    p.set_defaults(run=cmd_manifest)
    p = sub.add_parser("palette")
    p.add_argument("source")
    p.add_argument("-o", dest="output", required=True)
    p.set_defaults(run=cmd_palette)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main(sys.argv[1:])

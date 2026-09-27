# SPDX-License-Identifier: MIT
"""A PyBuilder that works out its own target, the way project.Program does.

The hex dump below is named after the program it reads, and nothing in this
script can write that name down: a program's file name is the toolchain's
business, ``firmware`` here and ``firmware.exe`` on Windows, and it isn't
settled until pcons resolves the build.

``emitter=`` is where a builder decides that. pcons calls it once the edge's
dependencies have resolved, with the targets and sources the call passed, the
environment, and the call's own keywords, and it returns the pair the edge
really has. Here the call passes no ``target=`` at all, and the emitter reads
the program's resolved path to name one. The edge needs no name either: like
any other, it's labelled by its output, and ``pcons build firmware.hex``
builds it.

The emitter is an ordinary function of this script. It runs while pcons
resolves the build, in this process, so it may read anything the build
description has. The decorated function is the opposite: it travels to build
time in a generated module, so it reads nothing but its own parameters.
"""

from pcons import Project

project = Project("python_builder_emitter")
env = project.Environment(toolchain="c")

firmware = project.Program("firmware", env, sources=["src/main.c"])


def named_after_the_program(targets, sources, env, **kwargs):
    """The edge's targets and sources, settled at resolve.

    ``sources`` holds what the call passed, so the program is still a
    ``Target`` here, and by now it has resolved: its output node is the file
    the linker will write.
    """
    program = sources[0].output_nodes[0].path
    return [*targets, f"{program.stem}.hex"], sources


@env.PyBuilder(emitter=named_after_the_program)
def to_hex(targets, sources):
    from pathlib import Path

    data = Path(sources[0]).read_bytes()
    lines = ["; pcons hex dump", f"; bytes: {len(data)}"]
    lines += [data[at : at + 16].hex(" ") for at in range(0, min(len(data), 64), 16)]
    Path(targets[0]).write_text("\n".join(lines) + "\n", encoding="utf-8")


dump = to_hex(source=[firmware])

project.Default(firmware, dump)

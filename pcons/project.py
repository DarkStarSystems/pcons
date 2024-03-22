# SPDX-License-Identifier: MIT

# A Project represents a complete project including the build graph
# and tools used to traverse the nodes

import inspect
import pathlib
from typing import Union
from pathlib import Path
from pcons.node import Node, FSNode
from pcons.generator import Generator


class Project:
    generator: Generator
    name: str
    defined_at: list[inspect.FrameInfo]  # save stack, for debugging
    targets: list[Node]

    def __init__(self, name: str, generator: Generator):
        self.name = name
        self.defined_at = inspect.stack()
        self.generator = generator
        self.targets = []

    def addToolchain(self, toolchain):
        # XXX
        pass

    def target(self, t: Union[Node, str]):
        if isinstance(t, Node):
            self.targets.append(t)
            return t
        else:
            n = FSNode(t)
            self.targets.append(n)
            return n

    def generate(self, path: Union[str, pathlib.Path]):
        """Write script to build the project"""
        self.generator.generate(self, Path(path))

    def where(self):
        defn = self.defined_at[1]  # constructor's caller
        return f'"{defn.filename}":{defn.lineno} in {defn.function}()'

    def __str__(self):
        return f'Project<"{self.name}" in {self.where()}>'

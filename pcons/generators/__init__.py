# SPDX-License-Identifier: MIT
"""Build file generators for pcons.

Generator classes are imported lazily (PEP 562): most runs use exactly one
generator, and some (Xcode, compile_commands) pull in heavy dependency chains
that a plain `import pcons` should not pay for.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.generators.compile_commands import CompileCommandsGenerator
    from pcons.generators.dot import DotGenerator
    from pcons.generators.generator import BaseGenerator, Generator
    from pcons.generators.makefile import MakefileGenerator
    from pcons.generators.mermaid import MermaidGenerator
    from pcons.generators.metadata import MetadataGenerator
    from pcons.generators.ninja import NinjaGenerator
    from pcons.generators.xcode import XcodeGenerator

_LAZY = {
    "BaseGenerator": "pcons.generators.generator",
    "CompileCommandsGenerator": "pcons.generators.compile_commands",
    "DotGenerator": "pcons.generators.dot",
    "Generator": "pcons.generators.generator",
    "MakefileGenerator": "pcons.generators.makefile",
    "MetadataGenerator": "pcons.generators.metadata",
    "MermaidGenerator": "pcons.generators.mermaid",
    "NinjaGenerator": "pcons.generators.ninja",
    "XcodeGenerator": "pcons.generators.xcode",
}

__all__ = [
    "BaseGenerator",
    "CompileCommandsGenerator",
    "DotGenerator",
    "Generator",
    "MakefileGenerator",
    "MetadataGenerator",
    "MermaidGenerator",
    "NinjaGenerator",
    "XcodeGenerator",
]


def __getattr__(name: str) -> object:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)

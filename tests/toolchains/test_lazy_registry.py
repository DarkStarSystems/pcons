# SPDX-License-Identifier: MIT
"""The lazy toolchain declarations cannot drift from the modules.

`pcons/toolchains/__init__.py` declares, per built-in toolchain, which module
registers which names and in which category (`register_lazy`). The registry
imports the module the first time a name is looked up, so a declaration that
disagrees with the module's own `register()` call fails quietly: a missing
alias is "unknown toolchain", and a wrong category silently drops the
toolchain from auto-detection. These tests import the real modules and hold
the two sides against each other, so drift is a test failure instead of a
user-visible mystery.
"""

from __future__ import annotations

import importlib
import pkgutil

import pcons.toolchains
from pcons.tools.toolchain import toolchain_registry

# The one finder registered eagerly by pcons/toolchains/__init__.py itself,
# not by any lazily-declared module.
_EAGER_FINDER_NAMES = {"c", "c++", "cpp"}


def _all_toolchain_modules() -> list[str]:
    """Every module under pcons/toolchains (the qt package included)."""
    names = []
    for info in pkgutil.walk_packages(
        pcons.toolchains.__path__, prefix="pcons.toolchains."
    ):
        names.append(info.name)
    return names


def test_every_declared_name_is_registered_by_its_module() -> None:
    """Importing the declared module must make the declared name resolvable,
    and an alias's real category must match the declared one (a mismatch
    would silently exclude the toolchain from category auto-detection)."""
    for name, declared in toolchain_registry.lazy_declarations().items():
        importlib.import_module(declared.module)
        entry = toolchain_registry._toolchains.get(name)
        finder = toolchain_registry._finders.get(name)
        assert entry is not None or finder is not None, (
            f"register_lazy declares {name!r} -> {declared.module}, but "
            f"importing it registered no toolchain or finder under that name"
        )
        if entry is not None:
            assert entry.category == declared.category, (
                f"{name!r}: register_lazy says category {declared.category!r} "
                f"but {declared.module} registered {entry.category!r}"
            )


def test_every_builtin_registration_is_declared() -> None:
    """Every name a built-in module registers must be lazily declared, or
    name-based lookup would miss it until some unrelated import happened."""
    for module in _all_toolchain_modules():
        importlib.import_module(module)

    declared = toolchain_registry.lazy_declarations()
    for alias, entry in toolchain_registry._toolchains.items():
        if not entry.toolchain_class.__module__.startswith("pcons.toolchains"):
            continue  # contrib and test toolchains register eagerly
        assert alias in declared, (
            f"pcons/toolchains registers alias {alias!r} but "
            f"register_lazy in its __init__ never declares it"
        )
    for name in toolchain_registry._finders:
        if name in _EAGER_FINDER_NAMES:
            continue
        assert name in declared, (
            f"a pcons/toolchains module registers finder {name!r} but "
            f"register_lazy in its __init__ never declares it"
        )


def test_every_lazy_attr_exists_in_its_module() -> None:
    """Each name in the package's PEP 562 export table must really exist."""
    for name, module in pcons.toolchains._LAZY_ATTRS.items():
        assert hasattr(importlib.import_module(module), name), (
            f"pcons.toolchains lazily exports {name!r} from {module}, "
            f"which has no such attribute"
        )


def test_lazy_exports_cover_all() -> None:
    """__all__ names must be resolvable: real module attributes or lazy."""
    for name in pcons.toolchains.__all__:
        assert getattr(pcons.toolchains, name) is not None

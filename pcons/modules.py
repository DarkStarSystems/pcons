# SPDX-License-Identifier: MIT
"""Module discovery and loading for pcons add-ons.

This module provides the add-on/plugin ecosystem for pcons. Add-on modules
can be placed in:
    1. Paths specified in PCONS_MODULES_PATH environment variable
    2. ~/.pcons/modules/ - User's global modules
    3. ./pcons_modules/ - Project-local modules

Modules are auto-loaded at startup and accessible via `pcons.modules`:

    from pcons.modules import mymodule
    mymodule.setup_env(env)

Module API Convention:
    Modules should follow a simple convention (no mandatory base class):

    ```python
    # ~/.pcons/modules/ofx.py
    '''OFX plugin support for pcons.'''

    __pcons_module__ = {
        "name": "ofx",
        "version": "1.0.0",
        "description": "OFX plugin bundle creation",
    }

    def setup_env(env, platform=None):
        '''Configure environment for plugin building.'''
        env.cxx.flags.append("-fvisibility=hidden")

    def register():
        '''Optional: Register custom builders at load time.'''
        pass
    ```
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Global registry of loaded modules
_loaded_modules: dict[str, ModuleType] = {}


def get_search_paths() -> list[Path]:
    """Get module search paths in priority order (first found wins):
    PCONS_MODULES_PATH, then ~/.pcons/modules/, then ./pcons_modules/.
    Only existing directories are returned.
    """
    paths: list[Path] = []

    env_paths = os.environ.get("PCONS_MODULES_PATH", "")
    if env_paths:
        for p in env_paths.split(os.pathsep):
            path = Path(p).expanduser()
            if path.exists() and path.is_dir():
                paths.append(path)

    user_modules = Path.home() / ".pcons" / "modules"
    if user_modules.exists() and user_modules.is_dir():
        paths.append(user_modules)

    local_modules = Path.cwd() / "pcons_modules"
    if local_modules.exists() and local_modules.is_dir():
        paths.append(local_modules)

    return paths


def load_modules(extra_paths: list[Path | str] | None = None) -> dict[str, ModuleType]:
    """Import all modules found on the search paths; a module's
    ``register()``, if present, is called after import.

    Args:
        extra_paths: Additional paths to search (prepended to default paths).

    Returns:
        Dict mapping module names to loaded module objects.
    """
    global _loaded_modules

    paths = get_search_paths()
    if extra_paths:
        extra = [Path(p).expanduser() for p in extra_paths]
        paths = extra + paths

    for path in paths:
        if not path.exists():
            continue

        for module_file in path.glob("*.py"):
            if module_file.name.startswith("_"):
                continue

            name = module_file.stem
            if name in _loaded_modules:
                # First found wins (higher priority paths searched first)
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"pcons.modules.{name}", module_file
                )
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[f"pcons.modules.{name}"] = module
                spec.loader.exec_module(module)
                _loaded_modules[name] = module

                if hasattr(module, "register") and callable(module.register):
                    module.register()

                logger.debug("Loaded module: %s from %s", name, module_file)

            except (ImportError, ModuleNotFoundError, SyntaxError) as e:
                logger.warning(
                    "Failed to load module %s from %s: %s", name, module_file, e
                )
            except Exception as e:
                # Add-ons are optional: import/register() errors must not be
                # fatal (KeyboardInterrupt/SystemExit still propagate).
                logger.warning(
                    "Skipping module %s from %s: %s: %s",
                    name,
                    module_file,
                    type(e).__name__,
                    e,
                )

    return dict(_loaded_modules)


def get_module(name: str) -> ModuleType | None:
    """Get a loaded module by name, or None."""
    return _loaded_modules.get(name)


def list_modules() -> list[str]:
    """List names of all loaded modules."""
    return list(_loaded_modules.keys())


def clear_modules() -> None:
    """Clear all loaded modules (for testing)."""
    global _loaded_modules
    for name in list(_loaded_modules.keys()):
        sys.modules.pop(f"pcons.modules.{name}", None)
    _loaded_modules.clear()


class _ModulesNamespace(ModuleType):
    """Dynamic namespace exposing loaded modules as attributes
    (``pcons.modules.mymodule``), alongside the module-level functions."""

    def __getattr__(self, name: str) -> ModuleType:
        """Get a loaded module by attribute access."""
        if name.startswith("_"):
            raise AttributeError(name)
        module = _loaded_modules.get(name)
        if module is not None:
            return module
        raise AttributeError(
            f"No module named 'pcons.modules.{name}'. "
            f"Available: {list(_loaded_modules.keys())}"
        )

    def __dir__(self) -> list[str]:
        """List available attributes."""
        return list(_loaded_modules.keys()) + [
            "load_modules",
            "get_module",
            "list_modules",
            "get_search_paths",
            "clear_modules",
        ]


# Replace this module with namespace instance
_namespace = _ModulesNamespace(__name__)
_namespace.__dict__.update(
    {
        "load_modules": load_modules,
        "get_module": get_module,
        "list_modules": list_modules,
        "get_search_paths": get_search_paths,
        "clear_modules": clear_modules,
        "_loaded_modules": _loaded_modules,
        "__doc__": __doc__,
    }
)
sys.modules[__name__] = _namespace

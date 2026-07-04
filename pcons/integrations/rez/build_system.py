# SPDX-License-Identifier: MIT
"""Rez ``build_system`` plugin so ``rez-build`` can drive pcons.

When this module is registered as a rez plugin (via the
``[project.entry-points."rez.plugins.build_system"]`` table in pcons's
``pyproject.toml``), rez auto-detects any package whose source dir
contains ``pcons-build.py`` and uses :class:`PconsBuildSystem` to build
it — same flow rez uses for ``CMakeLists.txt`` with the cmake plugin.

A package opts in by setting ``build_system = "pcons"`` in ``package.py``,
or rez auto-detects pcons if no ``build_system`` is specified. If a
source dir contains both ``pcons-build.py`` and ``CMakeLists.txt``, rez's
auto-detection is order-dependent — set ``build_system = "pcons"`` (or
``"cmake"``) explicitly to disambiguate.

The plugin runs three phases inside the rez-resolved context:

1. **Configure** — invoke ``pcons generate`` (which executes
   ``pcons-build.py`` and writes ``build.ninja``) in the package source
   directory, with ``PCONS_BUILD_DIR``, ``PCONS_INSTALL_DIR``, and
   ``PCONS_GENERATOR`` set as env vars.
2. **Build** — invoke ``ninja -C <build_path>`` (or ``make -C ...``).
3. **Install** — if rez requested it, invoke ``ninja -C <build_path>
   install``. The user's ``pcons-build.py`` must declare ``Install()``
   targets pointing at ``$PCONS_INSTALL_DIR`` for this to do anything.

This module imports :mod:`rez.build_system` at top level; it is only
imported by rez itself (or by tests that explicitly opt in via
``pytest.importorskip("rez")``).
"""

from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any

# ty/Pyright: rez is intentionally not in pcons's dev dependencies. This
# module is loaded by rez itself (during plugin discovery) or by tests
# gated on `pytest.importorskip("rez")`.
from rez.build_process import BuildType  # ty: ignore[unresolved-import]
from rez.build_system import BuildSystem  # ty: ignore[unresolved-import]

if TYPE_CHECKING:
    from rez.resolved_context import ResolvedContext  # ty: ignore[unresolved-import]


_PCONS_BUILD_SCRIPT = "pcons-build.py"


class PconsBuildSystem(BuildSystem):
    """Rez build_system plugin that drives pcons.

    Detected when the package source directory contains a
    ``pcons-build.py`` script.
    """

    @classmethod
    def name(cls) -> str:
        return "pcons"

    @classmethod
    def is_valid_root(cls, path: str, package: Any | None = None) -> bool:
        return os.path.isfile(os.path.join(path, _PCONS_BUILD_SCRIPT))

    @classmethod
    def bind_cli(cls, parser: Any, group: Any) -> None:
        group.add_argument(
            "--pcons-generator",
            default="ninja",
            choices=["ninja", "make"],
            help="pcons backend generator (default: ninja)",
        )
        group.add_argument(
            "--pcons-jobs",
            type=int,
            default=None,
            help="parallel jobs for ninja/make (default: auto)",
        )

    def build(
        self,
        context: ResolvedContext,
        variant: Any,
        build_path: str,
        install_path: str,
        install: bool = False,
        build_type: Any = BuildType.local,
    ) -> dict[str, Any]:
        generator = self._opt("pcons_generator", "ninja")
        jobs = self._opt("pcons_jobs", None)

        actions_callback = functools.partial(
            self._add_build_actions,
            context=context,
            package=self.package,
            variant=variant,
            build_type=build_type,
            install=install,
            build_path=build_path,
            install_path=install_path,
            generator=generator,
        )
        post_actions_callback = functools.partial(
            self.add_pre_build_commands,
            variant=variant,
            build_type=build_type,
            install=install,
            build_path=build_path,
            install_path=install_path,
        )

        # 1. Configure: run pcons-build.py via the pcons CLI.
        configure_cmd = self._pcons_cli(context) + ["generate"]
        retcode, _, _ = context.execute_shell(
            command=configure_cmd,
            block=True,
            cwd=self.working_dir,
            actions_callback=actions_callback,
            post_actions_callback=post_actions_callback,
        )
        if retcode:
            return {"success": False}

        # 2. Build: ninja -C <build_path> (or make).
        builder = ["ninja"] if generator == "ninja" else ["make"]
        if jobs is not None:
            builder.append(f"-j{jobs}")
        build_cmd = builder + ["-C", build_path]
        retcode, _, _ = context.execute_shell(
            command=build_cmd,
            block=True,
            cwd=self.working_dir,
            actions_callback=actions_callback,
            post_actions_callback=post_actions_callback,
        )
        if retcode:
            return {"success": False}

        # 3. Install: ninja -C <build_path> install.
        if install:
            install_cmd = build_cmd + ["install"]
            retcode, _, _ = context.execute_shell(
                command=install_cmd,
                block=True,
                cwd=self.working_dir,
                actions_callback=actions_callback,
                post_actions_callback=post_actions_callback,
            )
            if retcode:
                return {"success": False}

        return {"success": True}

    def _opt(self, name: str, default: Any) -> Any:
        return getattr(self.opts, name, default) if self.opts else default

    @staticmethod
    def _pcons_cli(context: ResolvedContext) -> list[str]:
        """Resolve a callable for the pcons CLI inside the rez context.

        Order of preference:

        1. ``pcons`` in the resolved rez environment (typical when the
           package author added a ``pcons`` rez package to the resolve).
        2. ``python -m pcons`` using the same interpreter that loaded
           this plugin module — i.e. rez's own Python venv. Pcons is
           always present here, since rez can only discover this plugin
           if pcons is installed alongside (the entry point lives in
           pcons's distribution metadata).
        """
        import sys

        pcons = context.which("pcons", fallback=False)
        if pcons:
            return [pcons]
        return [sys.executable, "-m", "pcons"]

    @classmethod
    def _add_build_actions(
        cls,
        executor: Any,
        context: ResolvedContext,
        package: Any,
        variant: Any,
        build_type: Any,
        install: bool,
        build_path: str,
        install_path: str | None,
        generator: str,
    ) -> None:
        cls.add_standard_build_actions(
            executor=executor,
            context=context,
            variant=variant,
            build_type=build_type,
            install=install,
            build_path=build_path,
            install_path=install_path,
        )
        executor.env.PCONS_BUILD_DIR = build_path
        executor.env.PCONS_SOURCE_DIR = package.root
        executor.env.PCONS_GENERATOR = generator
        if install_path:
            executor.env.PCONS_INSTALL_DIR = install_path


def register_plugin() -> type[PconsBuildSystem]:
    """Entry point hook called by rez during plugin discovery."""
    return PconsBuildSystem

# SPDX-License-Identifier: MIT
"""Package finder backed by a rez resolve.

:class:`RezFinder` plugs into pcons's standard finder chain. It is
"available" only when the current process is inside a rez-env shell;
otherwise it short-circuits and lets later finders run.
"""

from __future__ import annotations

import warnings

from pcons.integrations.rez.env import (
    RezLayout,
    is_in_rez_resolve,
    package_description,
    resolved_packages,
)
from pcons.packages.description import PackageDescription
from pcons.packages.finders.base import BaseFinder


class RezFinder(BaseFinder):
    """Find packages via the active rez resolve.

    Example:
        from pcons.integrations.rez import RezFinder

        project.add_package_finder(RezFinder())
        hello = project.find_package("hello_lib")
        app.link(hello)

    Rez has no concept of "components" — passing ``components`` to
    :meth:`find` emits a warning and the argument is otherwise ignored.

    The ``version`` argument to :meth:`find` is silently ignored: the
    rez resolve already pinned every package to one version, and version
    constraints belong in the package's ``requires`` list (or on the
    ``rez-env`` command line), not at build time. The returned
    :class:`PackageDescription` carries the resolved version verbatim.

    Packages that don't follow the default ``include``/``lib`` layout can
    be described explicitly by passing a ``{name: RezLayout}`` map to the
    constructor — see :class:`RezLayout`.
    """

    def __init__(self, layouts: dict[str, RezLayout] | None = None) -> None:
        self._layouts = layouts or {}

    @property
    def name(self) -> str:
        return "rez"

    def is_available(self) -> bool:
        return is_in_rez_resolve()

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        if components:
            warnings.warn(
                f"rez has no components concept; ignoring components="
                f"{components!r} for package {package_name!r}",
                stacklevel=2,
            )
        for pkg in resolved_packages():
            if pkg.name == package_name:
                return package_description(
                    pkg.name, pkg.version, pkg.root, self._layouts.get(pkg.name)
                )
        return None

# SPDX-License-Identifier: MIT
"""The whole of ``env.PyBuilder`` except its public name.

``Environment.PyBuilder`` is a forwarder into :func:`py_builder` here, the
way ``Project.cli_command`` forwards into ``pcons.commands``. Source
extraction and emission are the other half.

A function typed in a build script cannot be pickled. The script runs under
``__name__ == "__pcons__"`` and that module is never importable, so pickle,
which stores a function by module and qualname, has nothing to store. The body
reaches build time as a real file instead. :func:`validate` reads the
function once, at decoration. :func:`check_arguments` reads one call's
arguments. :func:`emit_module` claims the path of a generated module holding
the function's own source, once per path it lands on, and :func:`emit_args`
claims the path of one edge's argument pickle beside it. Both hand back the
node-canonical path the build edge names. Neither writes: the call hands the
bytes to the edge, and the edge writes them when the build is resolved, so a
script that only describes a build leaves no file behind.

The generated module holds the function and nothing else, so a body that uses
a name the build script imported would fail at build time with ``NameError``.
That is caught here instead, along with every other kind of function this
design cannot carry, each with its own :class:`PyBuilderError`.

The parts: :mod:`.function` holds the decorated function and the module it
becomes, :mod:`.arguments` one call's arguments, :mod:`.files` the
generated files and their claims, and :mod:`.builder` the builder and its
edges.
"""

from pcons.tools.pybuilder.builder import PyBuilder, py_builder
from pcons.tools.pybuilder.errors import PyBuilderError

__all__ = ["PyBuilder", "PyBuilderError", "py_builder"]

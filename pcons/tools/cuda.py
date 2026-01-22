# SPDX-License-Identifier: MIT
"""CUDA compiler tool (nvcc).

Provides CUDA compilation support for GPU code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.toolconfig import ToolConfig


class CudaCompiler(BaseTool):
    """NVIDIA CUDA compiler tool (nvcc).

    Variables:
        cmd: Compiler command (default: 'nvcc')
        flags: General compiler flags (list)
        arch: GPU architecture (default: 'sm_75')
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        objcmd: Command template for compiling to object

    Example:
        env.cuda.arch = "sm_86"  # Target Ampere GPUs
        env.cuda.flags.append("-use_fast_math")
    """

    def __init__(self) -> None:
        super().__init__("cuda", language="cuda")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "nvcc",
            "flags": [],
            "arch": "sm_75",  # Turing architecture (default)
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            # nvcc generates .d files with --generate-dependencies
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cuda.cmd",
                "-c",
                "-arch=$cuda.arch",
                "$cuda.flags",
                "${prefix(cuda.iprefix, cuda.includes)}",
                "${prefix(cuda.dprefix, cuda.defines)}",
                "$cuda.depflags",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "CudaObject",
                "cuda",
                "objcmd",
                src_suffixes=[".cu"],
                target_suffixes=[platform.object_suffix],
                language="cuda",
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",  # nvcc uses gcc-style depfiles
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        nvcc = config.find_program("nvcc")
        if nvcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cuda", cmd=str(nvcc.path))
        if nvcc.version:
            tool_config.version = nvcc.version
        return tool_config

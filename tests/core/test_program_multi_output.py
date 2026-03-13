# SPDX-License-Identifier: MIT
"""Tests for multi-output Program support in the resolver.

Verifies that when a linker tool's Program builder is a MultiOutputBuilder,
the resolver populates _build_info["outputs"] and creates secondary output
nodes (e.g. .wasm companion for Emscripten's .js primary).
"""

from pathlib import Path

from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.project import Project
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import SourceHandler

# ---------------------------------------------------------------------------
# Minimal toolchain with a MultiOutputBuilder Program linker
# ---------------------------------------------------------------------------


class _MockCCompiler(BaseTool):
    """Minimal C compiler tool for testing."""

    def __init__(self):
        super().__init__("cc", language="c")

    def default_vars(self):
        return {
            "cmd": "cc",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": [],
            "objcmd": ["$cc.cmd", "-c", "-o"],
        }

    def builders(self):
        return {
            "Object": CommandBuilder(
                "Object",
                "cc",
                "objcmd",
                src_suffixes=[".c"],
                target_suffixes=[".o"],
                language="c",
                single_source=True,
            ),
        }


class _MultiOutputLinker(BaseTool):
    """Linker that produces .js + .wasm (like Emscripten)."""

    def __init__(self):
        super().__init__("link")

    def default_vars(self):
        return {
            "cmd": "emcc",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "progcmd": ["$link.cmd", "-o"],
        }

    def builders(self):
        return {
            "Program": MultiOutputBuilder(
                "Program",
                "link",
                "progcmd",
                outputs=[
                    OutputSpec("primary", ".js"),
                    OutputSpec("wasm", ".wasm"),
                ],
                src_suffixes=[".o"],
                single_source=False,
            ),
        }


class _TestToolchain(UnixToolchain):
    """Toolchain that uses MultiOutputBuilder for Program."""

    def __init__(self):
        super().__init__("test-multi")
        self._tools = {
            "cc": _MockCCompiler(),
            "link": _MultiOutputLinker(),
        }

    def _configure_tools(self, config: object) -> bool:
        return True

    def get_program_name(self, name: str) -> str:
        return f"{name}.js"

    def get_shared_library_name(self, name: str) -> str:
        raise NotImplementedError

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        return []

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        from pcons.core.subst import TargetPath

        if suffix == ".c":
            return SourceHandler("cc", "c", ".o", TargetPath(suffix=".d"), "gcc")
        return None


class TestProgramMultiOutput:
    """Test that resolver creates multi-output nodes for Program targets."""

    def test_program_creates_secondary_outputs(self, tmp_path: Path):
        """When the linker's Program builder is a MultiOutputBuilder,
        resolver should create secondary output nodes and populate outputs dict.
        """
        # Set up a project with our test toolchain
        tc = _TestToolchain()
        project = Project("test", build_dir=str(tmp_path / "build"))
        env = project.Environment(toolchain=tc)

        # Create a C source file (doesn't need to exist on disk)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.c").touch()

        target = project.Program("hello", env, sources=[str(src_dir / "main.c")])
        project.resolve()

        # Should have output nodes for both .js and .wasm
        output_paths = [n.path for n in target.output_nodes]
        js_path = tmp_path / "build" / "hello.js"
        wasm_path = tmp_path / "build" / "hello.wasm"

        assert js_path in output_paths
        assert wasm_path in output_paths

        # The primary node (.js) should have outputs dict in _build_info
        primary_node = None
        for n in target.output_nodes:
            if n.path == js_path:
                primary_node = n
                break
        assert primary_node is not None
        assert "outputs" in primary_node._build_info

        outputs = primary_node._build_info["outputs"]
        assert "primary" in outputs
        assert "wasm" in outputs
        assert outputs["primary"]["path"] == js_path
        assert outputs["wasm"]["path"] == wasm_path
        assert outputs["wasm"]["suffix"] == ".wasm"

    def test_secondary_node_references_primary(self, tmp_path: Path):
        """Secondary output nodes should reference the primary via _build_info."""
        tc = _TestToolchain()
        project = Project("test", build_dir=str(tmp_path / "build"))
        env = project.Environment(toolchain=tc)

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.c").touch()

        target = project.Program("hello", env, sources=[str(src_dir / "main.c")])
        project.resolve()

        wasm_path = tmp_path / "build" / "hello.wasm"
        wasm_node = None
        for n in target.output_nodes:
            if n.path == wasm_path:
                wasm_node = n
                break
        assert wasm_node is not None
        assert "primary_node" in wasm_node._build_info
        assert wasm_node._build_info["output_name"] == "wasm"

    def test_single_output_program_unchanged(self, tmp_path: Path):
        """A toolchain with a single-output Program builder should not
        produce extra output nodes.
        """

        class _SingleLinker(BaseTool):
            def __init__(self):
                super().__init__("link")

            def default_vars(self):
                return {
                    "cmd": "cc",
                    "flags": [],
                    "lprefix": "-l",
                    "libs": [],
                    "Lprefix": "-L",
                    "libdirs": [],
                    "progcmd": ["$link.cmd", "-o"],
                }

            def builders(self):
                return {
                    "Program": CommandBuilder(
                        "Program",
                        "link",
                        "progcmd",
                        src_suffixes=[".o"],
                        target_suffixes=[".js"],
                        single_source=False,
                    ),
                }

        tc = _TestToolchain()
        tc._tools["link"] = _SingleLinker()

        project = Project("test", build_dir=str(tmp_path / "build"))
        env = project.Environment(toolchain=tc)

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.c").touch()

        target = project.Program("hello", env, sources=[str(src_dir / "main.c")])
        project.resolve()

        # Should only have 1 output node (just the .js)
        output_paths = [n.path for n in target.output_nodes]
        assert len(output_paths) == 1
        assert output_paths[0] == tmp_path / "build" / "hello.js"

        # No "outputs" dict in _build_info
        primary_node = target.output_nodes[0]
        assert "outputs" not in primary_node._build_info

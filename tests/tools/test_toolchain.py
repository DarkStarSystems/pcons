# SPDX-License-Identifier: MIT
"""Tests for pcons.tools.toolchain."""

from pcons.core.builder import Builder
from pcons.core.environment import Environment
from pcons.tools.tool import BaseTool, Tool
from pcons.tools.toolchain import BaseToolchain, Toolchain


class MockCTool(BaseTool):
    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {"cmd": "mock-cc", "flags": []}

    def builders(self) -> dict[str, Builder]:
        return {}


class MockCxxTool(BaseTool):
    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {"cmd": "mock-cxx", "flags": []}

    def builders(self) -> dict[str, Builder]:
        return {}


class MockToolchain(BaseToolchain):
    def __init__(self) -> None:
        super().__init__("mock")

    def _configure_tools(self, config: object) -> bool:
        self._tools: dict[str, Tool] = {
            "cc": MockCTool(),
            "cxx": MockCxxTool(),
        }
        return True


class TestToolchainProtocol:
    def test_base_toolchain_is_toolchain(self):
        tc = MockToolchain()
        assert isinstance(tc, Toolchain)


class TestBaseToolchain:
    def test_properties(self):
        tc = MockToolchain()
        assert tc.name == "mock"

    def test_configure(self):
        tc = MockToolchain()
        result = tc.configure(None)
        assert result is True
        assert "cc" in tc.tools
        assert "cxx" in tc.tools

    def test_setup(self, test_project):  # noqa: F811
        tc = MockToolchain()
        tc.configure(None)

        env = Environment()
        tc.setup(env)

        assert env.has_tool("cc")
        assert env.has_tool("cxx")
        assert env.cc.cmd == "mock-cc"
        assert env.cxx.cmd == "mock-cxx"


class TestAuxiliaryInputHandler:
    def test_base_toolchain_returns_none(self):
        """Test that BaseToolchain.get_auxiliary_input_handler returns None by default."""
        tc = MockToolchain()
        handler = tc.get_auxiliary_input_handler(".def")
        assert handler is None

    def test_unknown_suffix_returns_none(self):
        """Test that unknown suffixes return None."""
        tc = MockToolchain()
        handler = tc.get_auxiliary_input_handler(".xyz")
        assert handler is None


class TestInstallDir:
    """Tests for the toolchain-based install-dir convention."""

    def test_program_goes_to_bin(self):
        tc = MockToolchain()
        assert tc.get_install_dir("program") == "bin"

    def test_static_library_goes_to_lib(self):
        tc = MockToolchain()
        assert tc.get_install_dir("static_library") == "lib"

    def test_shared_library_unix_goes_to_lib(self):
        """ELF/Mach-O shared libraries (.so/.dylib) install to lib/."""

        class UnixToolchain(MockToolchain):
            def get_output_suffix(self, target_type: str) -> str:
                return ".so" if target_type == "shared_library" else ""

        assert UnixToolchain().get_install_dir("shared_library") == "lib"

    def test_shared_library_dll_goes_to_bin(self):
        """Windows DLLs install next to executables in bin/."""

        class DllToolchain(MockToolchain):
            def get_output_suffix(self, target_type: str) -> str:
                return ".dll" if target_type == "shared_library" else ".exe"

        assert DllToolchain().get_install_dir("shared_library") == "bin"


class TestLanguagePriority:
    def test_default_priorities(self):
        tc = MockToolchain()
        priority = tc.language_priority
        assert priority["c"] < priority["cxx"]
        assert priority["cxx"] < priority["cuda"]

    def test_get_linker_for_languages_c_only(self):
        tc = MockToolchain()
        linker = tc.get_linker_for_languages({"c"})
        assert linker == "cc"

    def test_get_linker_for_languages_cxx(self):
        tc = MockToolchain()
        linker = tc.get_linker_for_languages({"c", "cxx"})
        # C++ should win (higher priority)
        assert linker == "cxx"

    def test_get_linker_for_languages_empty(self):
        tc = MockToolchain()
        linker = tc.get_linker_for_languages(set())
        assert linker == "link"

    def test_get_linker_for_fortran(self):
        tc = MockToolchain()
        linker = tc.get_linker_for_languages({"c", "fortran"})
        # Base toolchains don't know about Fortran priority, so C wins.
        # GfortranToolchain overrides language_priority to add "fortran": 3.
        assert linker == "cc"

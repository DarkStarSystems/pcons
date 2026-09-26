# SPDX-License-Identifier: MIT
"""``exported_symbols``: a shared library exports only the named symbols,
realized by each toolchain in its linker's own form (#149)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pcons import Project
from pcons.core.environment import Environment
from pcons.core.subst import PathToken
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.clang_cl import ClangClToolchain
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.presets import target_platform_for_triple


def _written(project, token: PathToken) -> Path:
    """The file a build-relative link-input token names."""
    return Path(project.root_dir) / project.build_dir / token.path


def _shared(name: str = "plug") -> Target:
    return Target(name, target_type="shared_library")


class TestUnixRealization:
    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_writes_a_symbol_list(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        mock_platform.return_value.is_windows = False
        target = _shared()
        target.set_option("exported_symbols", ["OfxGetPlugin", "Spark*", "_already"])

        flags = GccToolchain().get_link_flags_for_target(target, "libplug.dylib", [])

        (token,) = [f for f in flags if isinstance(f, PathToken)]
        assert token.prefix == "-Wl,-exported_symbols_list,"
        assert (
            _written(test_project, token).read_text()
            == "_OfxGetPlugin\n_Spark*\n_already\n"
        )

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_writes_a_version_script(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_apple = False
        mock_platform.return_value.is_apple = False
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        mock_platform.return_value.is_windows = False
        target = _shared()
        target.set_option("exported_symbols", ["OfxGetPlugin", "Spark*"])

        flags = GccToolchain().get_link_flags_for_target(target, "libplug.so", [])

        (token,) = [f for f in flags if isinstance(f, PathToken)]
        assert token.prefix == "-Wl,--version-script="
        assert (
            _written(test_project, token).read_text()
            == "{ global: OfxGetPlugin; Spark*; local: *; };\n"
        )

    @patch("pcons.toolchains.unix.get_platform")
    def test_nothing_without_the_option(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        mock_platform.return_value.is_windows = False
        flags = GccToolchain().get_link_flags_for_target(_shared(), "libplug.dylib", [])
        assert not any(isinstance(f, PathToken) for f in flags)

    @patch("pcons.toolchains.unix.get_platform")
    def test_a_linux_executable_takes_a_dynamic_list(self, mock_platform, test_project):  # noqa: F811
        """A version script only restricts what an executable would export,
        which without --export-dynamic is nothing; a dynamic list names
        what it exports."""
        mock_platform.return_value.is_apple = False
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        mock_platform.return_value.is_windows = False
        target = Target("host", target_type="program")
        target.set_option("exported_symbols", ["host_api", "host_*"])

        flags = GccToolchain().get_link_flags_for_target(target, "host", [])

        (token,) = [f for f in flags if isinstance(f, PathToken)]
        assert token.prefix == "-Wl,--dynamic-list="
        assert _written(test_project, token).read_text() == "{ host_api; host_*; };\n"

    @patch("pcons.toolchains.unix.get_platform")
    def test_an_executable_exports_too(self, mock_platform, test_project):  # noqa: F811
        """A host program that plugins call back into names its API the
        same way; it gets no install name, only the export list."""
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        mock_platform.return_value.is_windows = False
        target = Target("host", target_type="program")
        target.set_option("exported_symbols", ["host_api"])

        flags = GccToolchain().get_link_flags_for_target(target, "host", [])

        assert [f.prefix for f in flags if isinstance(f, PathToken)] == [
            "-Wl,-exported_symbols_list,"
        ]
        assert not any("install_name" in str(f) for f in flags)

    @patch("pcons.toolchains.unix.get_platform")
    def test_a_static_library_exports_nothing(self, mock_platform, test_project):  # noqa: F811
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_apple = True
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        mock_platform.return_value.is_windows = False
        target = Target("lib", target_type="static_library")
        target.set_option("exported_symbols", ["api"])
        assert GccToolchain().get_link_flags_for_target(target, "liblib.a", []) == []


class TestMsvcRealization:
    def test_writes_a_def_file(self, test_project):  # noqa: F811
        target = _shared()
        target.set_option("exported_symbols", ["OfxGetPlugin", "PF_Main"])

        flags = ClangClToolchain().get_link_flags_for_target(target, "plug.dll", [])

        (token,) = flags
        assert isinstance(token, PathToken) and token.prefix == "/DEF:"
        assert (
            _written(test_project, token).read_text()
            == "EXPORTS\n    OfxGetPlugin\n    PF_Main\n"
        )

    def test_a_pattern_is_refused(self, test_project):  # noqa: F811
        target = _shared()
        target.set_option("exported_symbols", ["Spark*"])
        with pytest.raises(ValueError, match="exact name"):
            ClangClToolchain().get_link_flags_for_target(target, "plug.dll", [])


class TestTheLinkDependsOnTheList:
    def test_ninja_names_the_file_and_waits_for_it(
        self, tmp_path, monkeypatch, gcc_toolchain
    ):
        # The realization reads what the environment builds for; pin that
        # to macOS so the test means the same on every host.
        monkeypatch.setattr(
            Environment,
            "target",
            PropertyMock(return_value=target_platform_for_triple("arm64-apple-darwin")),
        )
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.c").write_text("int OfxGetPlugin(void) { return 1; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        lib = project.SharedLibrary("plug", env, sources=["a.c"])
        lib.set_option("exported_symbols", ["OfxGetPlugin"])

        project.resolve()
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        text = (tmp_path / "build" / "build.ninja").read_text().replace("\\", "/")
        output = lib.output_nodes[0].path.relative_to("build").as_posix()
        link = next(
            ln
            for ln in text.splitlines()
            if ln.startswith("build ") and output in ln.split(":", 1)[0].split()
        )

        assert "| obj.plug.shared/plug.exports" in link
        assert "-Wl,-exported_symbols_list," in text

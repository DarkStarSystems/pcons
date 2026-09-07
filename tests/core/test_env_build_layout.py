# SPDX-License-Identifier: MIT
"""An environment owns where its targets are built (#96).

``build_prefix`` moves everything the environment writes; the three
``*_directory`` settings place the final artifacts by kind below it.
"""

from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "common.c").write_text("int f(void) { return 1; }\n")
    return src


def _paths(target) -> set[str]:
    return {node.path.as_posix() for node in target.output_nodes}


def _object_paths(target) -> set[str]:
    return {node.path.as_posix() for node in target.intermediate_nodes}


def _artifact(toolchain, directory: str, base: str, kind: str) -> set[str]:
    """The one path *kind* named *base* lands at, named as the platform names it.

    These tests are about where a file goes, so the name comes from the same
    toolchain the target was built with rather than from a Unix spelling.
    """
    prefix = toolchain.get_output_prefix(kind)
    suffix = toolchain.get_output_suffix(kind)
    return {f"{directory}/{prefix}{base}{suffix}"}


def _object(toolchain, directory: str, source: str) -> set[str]:
    """The one object path compiled from *source*, named as the toolchain names it."""
    return {f"{directory}/{source}{toolchain.get_object_suffix()}"}


_WINDOWS_SUFFIXES = {"static_library": ".lib", "shared_library": ".dll"}


@pytest.fixture
def windows_toolchain(gcc_toolchain, monkeypatch):
    """The gcc toolchain, naming its outputs the way MSVC does.

    Patched on the instance rather than subclassed: `_gen_stubs` scrapes
    `BaseToolchain.__subclasses__()`, so a toolchain class defined here would
    leak into the generated preset names and fail the stub freshness test.
    """
    monkeypatch.setattr(
        gcc_toolchain, "get_output_prefix", lambda target_type, target=None: ""
    )
    monkeypatch.setattr(
        gcc_toolchain,
        "get_output_suffix",
        lambda target_type, target=None: _WINDOWS_SUFFIXES.get(target_type, ".exe"),
    )
    return gcc_toolchain


def _outputs(target) -> dict:
    return target.output_nodes[0]._build_info["outputs"]


def _primary_path(target) -> str:
    return _outputs(target)["primary"]["path"].as_posix()


def _import_lib_path(target) -> str:
    return _outputs(target)["import_lib"]["path"].as_posix()


class TestBuildPrefix:
    def test_moves_outputs_and_objects(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert _paths(lib) == _artifact(
            gcc_toolchain, "build/mcu", "common", "static_library"
        )
        assert _object_paths(lib) == _object(
            gcc_toolchain, "build/mcu/obj.common/src", "common.c"
        )

    def test_two_environments_keep_one_name_apart(
        self, tmp_path, source, gcc_toolchain
    ):
        """The #96 shape, without output_name or output_prefix."""
        project = Project("p", root_dir=tmp_path)
        libs = []
        for name in ("mcu", "host"):
            env = project.Environment(toolchain=gcc_toolchain, name=name)
            env.build_prefix = name
            env.archive_directory = "lib"
            libs.append(
                project.StaticLibrary(f"common-{name}", env, sources=["src/common.c"])
            )
            libs[-1].output_name = "common"

        project.resolve()

        assert _paths(libs[0]) == _artifact(
            gcc_toolchain, "build/mcu/lib", "common", "static_library"
        )
        assert _paths(libs[1]) == _artifact(
            gcc_toolchain, "build/host/lib", "common", "static_library"
        )

    def test_env_build_dir_carries_the_prefix(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"

        assert env.build_dir == Path("build/mcu")

    def test_setting_order_does_not_matter(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        first = project.Environment(toolchain=gcc_toolchain, name="a")
        first.build_prefix = "slice"
        first.build_dir = Path("build")

        second = project.Environment(toolchain=gcc_toolchain, name="b")
        second.build_dir = Path("build")
        second.build_prefix = "slice"

        assert first.build_dir == second.build_dir == Path("build/slice")

    def test_clearing_it_restores_the_build_dir(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.build_prefix = None

        assert env.build_dir == Path("build")

    def test_subdirectory_offset_stays_inside_the_prefix(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        (tmp_path / "sub" / "src").mkdir(parents=True)
        (tmp_path / "sub" / "src" / "thing.c").write_text("int g(void) { return 2; }\n")
        with project._enter_subdir("sub"):
            lib = project.StaticLibrary("thing", env, sources=["src/thing.c"])

        project.resolve()

        assert _paths(lib) == _artifact(
            gcc_toolchain, "build/mcu/sub", "thing", "static_library"
        )

    def test_targets_follow_a_named_build_dir(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_dir = "build/rel"
        env.build_prefix = "mcu"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert env.build_dir == Path("build/rel/mcu")
        assert lib.build_dir == Path("build/rel/mcu")
        assert _paths(lib) == _artifact(
            gcc_toolchain, "build/rel/mcu", "common", "static_library"
        )
        assert _object_paths(lib) == _object(
            gcc_toolchain, "build/rel/mcu/obj.common/src", "common.c"
        )

    def test_a_named_build_dir_keeps_the_subdirectory_offset(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_dir = "build/rel"
        env.build_prefix = "mcu"
        (tmp_path / "sub" / "src").mkdir(parents=True)
        (tmp_path / "sub" / "src" / "thing.c").write_text("int g(void) { return 2; }\n")
        with project._enter_subdir("sub"):
            lib = project.StaticLibrary("thing", env, sources=["src/thing.c"])

        project.resolve()

        assert _paths(lib) == _artifact(
            gcc_toolchain, "build/rel/mcu/sub", "thing", "static_library"
        )

    def test_a_named_build_dir_moves_targets_without_a_prefix(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_dir = "build/rel"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert _paths(lib) == _artifact(
            gcc_toolchain, "build/rel", "common", "static_library"
        )

    def test_command_outputs_follow(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="host")
        env.build_prefix = "host"
        (tmp_path / "in.txt").write_text("x")

        bare = env.Command(
            target="gen/version.h",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="bare",
        )
        prefixed = env.Command(
            target=project.build_dir / "gen/other.h",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="prefixed",
        )

        project.resolve()

        assert _paths(bare) == {"build/host/gen/version.h"}
        assert _paths(prefixed) == {"build/host/gen/other.h"}

    @pytest.mark.parametrize(
        "attr", ["build_prefix", "runtime_directory", "library_directory"]
    )
    def test_absolute_is_refused(self, tmp_path, gcc_toolchain, attr):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)

        with pytest.raises(PconsError, match=attr):
            setattr(env, attr, tmp_path / "elsewhere")

    def test_escaping_the_build_dir_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)

        with pytest.raises(PconsError, match="archive_directory"):
            env.archive_directory = "../outside"


class TestOutputDirectories:
    def test_each_kind_lands_in_its_own(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        env.library_directory = "lib"
        env.runtime_directory = "bin"

        (source / "main.c").write_text("int main(void) { return 0; }\n")
        archive = project.StaticLibrary("a", env, sources=["src/common.c"])
        shared = project.SharedLibrary("s", env, sources=["src/common.c"])
        program = project.Program("p", env, sources=["src/main.c"])

        project.resolve()

        assert _paths(archive) == _artifact(
            gcc_toolchain, "build/mcu/lib", "a", "static_library"
        )
        assert _paths(shared) == _artifact(
            gcc_toolchain, "build/mcu/lib", "s", "shared_library"
        )
        assert _paths(program) == _artifact(
            gcc_toolchain, "build/mcu/bin", "p", "program"
        )

    def test_objects_do_not_move(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert _object_paths(lib) == _object(
            gcc_toolchain, "build/mcu/obj.common/src", "common.c"
        )

    def test_unknown_target_type_has_no_directory(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        env.runtime_directory = "bin"

        assert env.output_directory_for("command") is None
        assert env.output_directory_for(None) is None

    def test_output_prefix_still_renames_the_file(
        self, tmp_path, source, gcc_toolchain
    ):
        """The two settings do not fight: one is a directory, one a filename."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])
        lib.output_prefix = ""

        project.resolve()

        suffix = gcc_toolchain.get_output_suffix("static_library")
        assert _paths(lib) == {f"build/mcu/lib/common{suffix}"}


class TestWindowsImportLibrary:
    def test_it_goes_to_the_archive_directory(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        """CMake sends a DLL to RUNTIME/LIBRARY and its import lib to ARCHIVE.

        sys.platform is patched around resolve() only, because tool detection
        reads it too and shutil.which cannot answer for a platform it is not on.
        The toolchain names outputs the way MSVC does whatever the host is, so
        the expected paths do not depend on where the test runs.
        """
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain, name="win")
        env.build_prefix = "win"
        env.library_directory = "bin"
        env.archive_directory = "lib"
        shared = project.SharedLibrary("s", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/win/bin/s.dll"
        assert _import_lib_path(shared) == "build/win/lib/s.lib"

    def test_it_follows_the_dll_with_no_archive_directory(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/foo.dll"
        assert _import_lib_path(shared) == "build/foo.lib"

    def test_the_archive_directory_takes_it_from_the_dll(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        env.archive_directory = "lib"
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/foo.dll"
        assert _import_lib_path(shared) == "build/lib/foo.lib"

    def test_the_library_directory_takes_it_too(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        env.library_directory = "bin"
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/bin/foo.dll"
        assert _import_lib_path(shared) == "build/bin/foo.lib"

    def test_the_archive_directory_wins_over_the_library_one(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        env.library_directory = "bin"
        env.archive_directory = "lib"
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/bin/foo.dll"
        assert _import_lib_path(shared) == "build/lib/foo.lib"

    def test_a_subdirectory_in_output_prefix_is_kept(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])
        shared.output_prefix = "mcu/"

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/mcu/foo.dll"
        assert _import_lib_path(shared) == "build/mcu/foo.lib"

    def test_output_prefix_nests_below_the_archive_directory(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        env.archive_directory = "lib"
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])
        shared.output_prefix = "mcu/"

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        assert _primary_path(shared) == "build/mcu/foo.dll"
        assert _import_lib_path(shared) == "build/lib/mcu/foo.lib"

    def test_other_platforms_have_no_outputs_key(
        self, tmp_path, source, windows_toolchain, monkeypatch
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=windows_toolchain)
        env.archive_directory = "lib"
        shared = project.SharedLibrary("foo", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "linux")
        project.resolve()

        assert "outputs" not in shared.output_nodes[0]._build_info


class TestNamingWithoutAToolchain:
    """An environment with no toolchain still names for what it targets.

    The toolchain normally decides the spelling. Without one there is
    nothing to ask, and the fallback used to name for the build machine,
    which is the same bug one level down.
    """

    def _mingw(self, project, gcc_toolchain):
        from pcons.toolchains.presets import CrossPreset

        env = project.Environment(toolchain=gcc_toolchain, name="mingw")
        env.apply_cross_preset(
            CrossPreset(
                name="mingw64",
                arch="x86_64",
                triple="x86_64-w64-mingw32",
                tool_cmds={
                    "cc": "x86_64-w64-mingw32-gcc",
                    "cxx": "x86_64-w64-mingw32-g++",
                    "link": "x86_64-w64-mingw32-g++",
                    "ar": "x86_64-w64-mingw32-ar",
                },
            )
        )
        return env

    def test_it_reads_the_target_not_the_host(self, tmp_path, gcc_toolchain):
        from pcons.tools.compile_link import CompileLinkFactory

        project = Project("p", root_dir=tmp_path)
        env = self._mingw(project, gcc_toolchain)
        lib = project.SharedLibrary("foo", env)
        env._toolchain = None

        name = CompileLinkFactory._apply_output_naming(lib, env, "shared_library")

        assert name == "foo.dll"

    def test_a_host_environment_is_unchanged(self, tmp_path, gcc_toolchain):
        from pcons.configure.platform import get_platform
        from pcons.tools.compile_link import CompileLinkFactory

        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="host")
        lib = project.SharedLibrary("foo", env)
        env._toolchain = None

        name = CompileLinkFactory._apply_output_naming(lib, env, "shared_library")

        plat = get_platform()
        assert name == f"{plat.shared_lib_prefix}foo{plat.shared_lib_suffix}"

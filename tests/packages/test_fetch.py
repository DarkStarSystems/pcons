# SPDX-License-Identifier: MIT
"""Tests for pcons-fetch CLI."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.fetch import cli as fetch_cli
from pcons.packages.fetch.cli import (
    download_source,
    fetch_package,
    generate_package_description,
    load_deps_file,
)


class TestLoadDepsFile:
    """Tests for load_deps_file."""

    def test_load_valid_deps_file(self, tmp_path: Path) -> None:
        """Test loading a valid deps.toml file."""
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            """\
[packages.zlib]
url = "https://github.com/madler/zlib.git"
version = "1.2.13"
build = "cmake"
"""
        )

        data = load_deps_file(deps_file)
        assert "packages" in data
        assert "zlib" in data["packages"]
        assert data["packages"]["zlib"]["version"] == "1.2.13"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test loading a non-existent file."""
        with pytest.raises(FileNotFoundError):
            load_deps_file(tmp_path / "nonexistent.toml")


class TestGeneratePackageDescription:
    """Tests for generate_package_description."""

    def test_generate_with_include_and_lib(self, tmp_path: Path) -> None:
        """Test generating description with include and lib dirs."""
        install_prefix = tmp_path / "install"
        include_dir = install_prefix / "include"
        lib_dir = install_prefix / "lib"

        include_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)

        # Create some fake libraries
        (lib_dir / "libtest.a").write_text("")
        (lib_dir / "libfoo.so").write_text("")

        pkg, pc_files = generate_package_description(
            name="mylib",
            version="1.0",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert pc_files == []
        assert pkg.name == "mylib"
        assert pkg.version == "1.0"
        # A fetched prefix is third-party by construction, so its headers are
        # system headers unless the deps file says otherwise.
        assert str(include_dir.resolve()) in pkg.system_include_dirs
        assert pkg.include_dirs == []
        assert str(lib_dir.resolve()) in pkg.library_dirs
        assert "test" in pkg.libraries
        assert "foo" in pkg.libraries
        assert "pcons-fetch" in pkg.found_by

    def test_generate_with_system_off(self, tmp_path: Path) -> None:
        """system=False keeps the include dir a plain -I."""
        install_prefix = tmp_path / "install"
        include_dir = install_prefix / "include"
        include_dir.mkdir(parents=True)

        pkg, _ = generate_package_description(
            name="mylib",
            version="1.0",
            install_prefix=install_prefix,
            build_system="cmake",
            system=False,
        )

        assert str(include_dir.resolve()) in pkg.include_dirs
        assert pkg.system_include_dirs == []

    def test_generate_empty_install(self, tmp_path: Path) -> None:
        """Test generating description with empty install prefix."""
        install_prefix = tmp_path / "empty_install"
        install_prefix.mkdir()

        pkg, pc_files = generate_package_description(
            name="empty",
            version="0.1",
            install_prefix=install_prefix,
            build_system="autotools",
        )

        assert pc_files == []
        assert pkg.name == "empty"
        assert pkg.include_dirs == []
        assert pkg.libraries == []

    def test_generate_prefers_pc_files(self, tmp_path: Path) -> None:
        """When .pc files exist, return them instead of scanning libs."""
        install_prefix = tmp_path / "install"
        lib_dir = install_prefix / "lib"
        pc_dir = lib_dir / "pkgconfig"
        pc_dir.mkdir(parents=True)
        (pc_dir / "mylib.pc").write_text("Name: mylib\nVersion: 1.0\n")
        # Also create a library — should be ignored when .pc exists
        (lib_dir / "libmylib.a").write_text("")

        pkg, pc_files = generate_package_description(
            name="mylib",
            version="1.0",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert len(pc_files) == 1
        assert pc_files[0].name == "mylib.pc"
        # When .pc files found, paths/link sections should be empty
        assert pkg.include_dirs == []
        assert pkg.libraries == []

    def test_generate_skips_symlinks_and_versioned_libs(self, tmp_path: Path) -> None:
        """Versioned dylib symlinks should not produce duplicate library names."""
        install_prefix = tmp_path / "install"
        lib_dir = install_prefix / "lib"
        lib_dir.mkdir(parents=True)

        # Real file
        (lib_dir / "libz.1.3.1.dylib").write_text("")
        # Symlinks
        (lib_dir / "libz.1.dylib").symlink_to("libz.1.3.1.dylib")
        (lib_dir / "libz.dylib").symlink_to("libz.1.dylib")
        # Static lib (real file)
        (lib_dir / "libz.a").write_text("")

        pkg, _ = generate_package_description(
            name="zlib",
            version="1.3.1",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert pkg.libraries == ["z"]


def _record_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Replace what each subcommand runs with a recorder, and return the log.

    Each entry is the command's name and the arguments it was handed. The
    callback is swapped rather than the module attribute, because that is the
    one reference both the ordinary dispatch and the group's `ctx.invoke`
    default path go through.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    def recorder(name: str) -> Any:
        def record(**kwargs: Any) -> int:
            calls.append((f"cmd_{name}", dict(kwargs)))
            return 0

        return record

    for name in ("fetch", "list", "clean"):
        monkeypatch.setattr(fetch_cli.cli.commands[name], "callback", recorder(name))
    return calls


def _run(*argv: str) -> int:
    """Run pcons-fetch in this process and return its exit code.

    `main()` rather than `CliRunner().invoke(cli, ...)`, which is what
    tests/test_cli.py uses: there the click group is the unit under test, here
    the entry point is. `pyproject.toml` wires `pcons-fetch` to this function,
    and it is the wrapper that turns click's exceptions into the code the shell
    sees. Going in through the group would leave that untested.

    The commands configure logging with `basicConfig(force=True)`, which binds
    a handler to whatever `sys.stderr` is at the time, here pytest's capture
    buffer. The handlers are restored so that buffer does not swallow the log
    output of every later test in the session, as `_invoke` does in
    tests/test_cli.py.
    """
    handlers = logging.root.handlers[:]
    level = logging.root.level
    try:
        return fetch_cli.main(list(argv))
    finally:
        logging.root.handlers[:] = handlers
        logging.root.setLevel(level)


class TestCLICommands:
    """Each subcommand end to end, running the real handler.

    In process rather than by subprocess: the assertions are the same and a
    body only reached through `subprocess.run` is invisible to coverage, which
    is how these bodies went unmeasured while looking tested.
    `test_module_entry_point` below still pins the `python -m` path.
    """

    def test_help(self, capsys) -> None:
        assert _run("--help") == 0
        out = capsys.readouterr().out
        assert "pcons-fetch" in out
        # Declaration order, as the argparse subparsers listed them.
        assert out.index("fetch") < out.index("list") < out.index("clean")

    def test_short_help_alias(self, capsys) -> None:
        assert _run("-h") == 0
        assert "pcons-fetch" in capsys.readouterr().out

    def test_subcommand_short_help_alias(self, capsys) -> None:
        assert _run("clean", "-h") == 0
        assert "--all" in capsys.readouterr().out

    def test_version(self, capsys) -> None:
        import pcons

        assert _run("--version") == 0
        assert pcons.__version__ in capsys.readouterr().out

    def test_list_no_deps_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert _run("list") == 1
        assert "not found" in capsys.readouterr().err

    def test_list_with_deps_file(self, tmp_path: Path, capsys) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            """\
[packages.zlib]
url = "https://github.com/madler/zlib.git"
version = "1.2.13"
build = "cmake"

[packages.openssl]
url = "https://github.com/openssl/openssl.git"
version = "3.0"
build = "autotools"
"""
        )

        assert _run("list", str(deps_file)) == 0

        out = capsys.readouterr().out
        assert "zlib" in out
        assert "1.2.13" in out
        assert "openssl" in out
        assert "cmake" in out
        assert "autotools" in out

    def test_list_empty_packages(self, tmp_path: Path, capsys) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text("[packages]\n")

        assert _run("list", str(deps_file)) == 0
        assert "No packages defined" in capsys.readouterr().out

    def test_list_unparseable_deps_file(self, tmp_path: Path, capsys) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text("this is not toml [[[\n")

        assert _run("list", str(deps_file)) == 1
        assert "Failed to parse" in capsys.readouterr().err

    def test_clean_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        assert _run("clean", "--deps-dir", str(missing)) == 0

    def test_clean_build_dir(self, tmp_path: Path) -> None:
        deps_dir = tmp_path / ".deps"
        build_dir = deps_dir / "build"
        build_dir.mkdir(parents=True)
        (build_dir / "testfile").write_text("test")

        assert _run("clean", "--deps-dir", str(deps_dir)) == 0

        assert not build_dir.exists()
        assert deps_dir.exists()  # Parent should still exist

    def test_clean_leaves_a_deps_dir_with_no_build(self, tmp_path: Path) -> None:
        deps_dir = tmp_path / ".deps"
        (deps_dir / "src").mkdir(parents=True)

        assert _run("clean", "--deps-dir", str(deps_dir)) == 0

        assert (deps_dir / "src").exists()

    def test_clean_all(self, tmp_path: Path) -> None:
        deps_dir = tmp_path / ".deps"
        deps_dir.mkdir()
        (deps_dir / "testfile").write_text("test")

        assert _run("clean", "--all", "--deps-dir", str(deps_dir)) == 0

        assert not deps_dir.exists()

    def test_fetch_no_deps_file(self, tmp_path: Path, capsys) -> None:
        assert _run("fetch", str(tmp_path / "nonexistent.toml")) == 1
        assert "not found" in capsys.readouterr().err

    def test_fetch_empty_packages(self, tmp_path: Path, capsys) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text("[packages]\n")

        # Should succeed with warning
        assert _run("fetch", str(deps_file)) == 0
        assert "No packages defined" in capsys.readouterr().err

    def test_fetch_unparseable_deps_file(self, tmp_path: Path, capsys) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text("this is not toml [[[\n")

        assert _run("fetch", str(deps_file)) == 1
        assert "Failed to parse" in capsys.readouterr().err

    def test_fetch_reports_the_packages_that_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            '[packages.zlib]\nurl = "https://example.invalid/zlib.tar.gz"\n'
        )
        monkeypatch.setattr(fetch_cli, "fetch_package", lambda *a, **kw: False)

        assert _run("fetch", str(deps_file)) == 1
        assert "Failed to build packages: zlib" in capsys.readouterr().err

    def test_fetch_reports_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            '[packages.zlib]\nurl = "https://example.invalid/zlib.tar.gz"\n'
        )
        monkeypatch.setattr(fetch_cli, "fetch_package", lambda *a, **kw: True)

        assert _run("-v", "fetch", str(deps_file)) == 0
        assert "Successfully built all packages" in capsys.readouterr().err

    def test_module_entry_point(self) -> None:
        """The `python -m` path and the __main__ guard, which in-process misses."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.packages.fetch.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "pcons-fetch" in result.stdout

    def test_unknown_option_is_a_usage_error(self, capsys) -> None:
        assert _run("--nope") == 2
        assert "--nope" in capsys.readouterr().err

    def test_unknown_subcommand_is_a_usage_error(self, capsys) -> None:
        assert _run("nosuchverb") == 2
        assert "nosuchverb" in capsys.readouterr().err

    def test_keyboard_interrupt_is_130(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ctrl-C reaches main() as click's Abort, not as a traceback."""

        def interrupt(**kwargs: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(fetch_cli.cli.commands["list"], "callback", interrupt)
        assert _run("list") == 130


class TestFetchCliDispatch:
    """What each argv spelling asks the handlers to do.

    The subprocess tests above see only an exit code. These see the arguments,
    which is what pins the defaults.
    """

    def test_no_args_with_deps_toml_runs_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare `pcons-fetch` fetches deps.toml when there is one."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "deps.toml").write_text("[packages]\n")
        calls = _record_handlers(monkeypatch)

        assert _run() == 0

        assert calls == [
            (
                "cmd_fetch",
                {
                    "deps_file": "deps.toml",
                    "deps_dir": ".deps",
                    "output_dir": ".",
                    "verbose": False,
                    "debug": False,
                },
            )
        ]

    def test_no_args_without_deps_toml_prints_help(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With nothing to fetch, bare `pcons-fetch` is a help request."""
        monkeypatch.chdir(tmp_path)
        calls = _record_handlers(monkeypatch)

        assert _run() == 0

        assert calls == []
        assert "usage" in capsys.readouterr().out.lower()

    def test_fetch_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`fetch` with no positional still means deps.toml."""
        calls = _record_handlers(monkeypatch)

        assert _run("fetch") == 0

        assert calls[0][1]["deps_file"] == "deps.toml"
        assert calls[0][1]["deps_dir"] == ".deps"
        assert calls[0][1]["output_dir"] == "."

    def test_fetch_short_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """-d and -o are the short forms of --deps-dir and --output-dir."""
        calls = _record_handlers(monkeypatch)

        assert _run("fetch", "other.toml", "-d", "D", "-o", "O") == 0

        assert calls == [
            (
                "cmd_fetch",
                {
                    "deps_file": "other.toml",
                    "deps_dir": "D",
                    "output_dir": "O",
                    "verbose": False,
                    "debug": False,
                },
            )
        ]

    def test_list_takes_a_deps_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _record_handlers(monkeypatch)

        assert _run("list", "other.toml") == 0

        assert calls == [
            ("cmd_list", {"deps_file": "other.toml", "verbose": False, "debug": False})
        ]

    def test_clean_all_short_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _record_handlers(monkeypatch)

        assert _run("clean", "-a", "-d", "D") == 0

        assert calls == [
            (
                "cmd_clean",
                {"deps_dir": "D", "all": True, "verbose": False, "debug": False},
            )
        ]

    def test_verbose_after_the_subcommand(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_handlers(monkeypatch)

        assert _run("fetch", "-v") == 0

        assert calls[0][1]["verbose"] is True

    def test_verbose_before_the_subcommand(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_handlers(monkeypatch)

        assert _run("-v", "fetch") == 0

        assert calls[0][1]["verbose"] is True

    def test_debug_before_the_subcommand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _record_handlers(monkeypatch)

        assert _run("--debug", "list") == 0

        assert calls[0][1]["debug"] is True


class TestVerbosityConfiguresLogging:
    """-v and --debug reach logging, which the command layer owns.

    pcons-fetch has no setup_logging of its own: `MergingCommand` configures
    it from the merged options, the path `pcons` itself takes.
    """

    def _level_while_running(self, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
        """The root level the command actually ran under.

        Read inside the callback, not after `_run` returns: `_run` restores the
        handlers and the level so a captured-stderr handler does not outlive
        the test.
        """
        seen: list[int] = []

        def record(**kwargs: Any) -> int:
            seen.append(logging.getLogger().level)
            return 0

        monkeypatch.setattr(fetch_cli.cli.commands["list"], "callback", record)
        assert _run(*argv) == 0
        assert len(seen) == 1
        return seen[0]

    def test_quiet_is_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._level_while_running(monkeypatch, "list") == logging.WARNING

    def test_verbose_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._level_while_running(monkeypatch, "-v", "list") == logging.INFO

    def test_debug_is_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert (
            self._level_while_running(monkeypatch, "--debug", "list") == logging.DEBUG
        )


class TestDownloadSource:
    """Tests for source download helpers."""

    def test_git_ssh_url_not_split_as_ref(self, tmp_path: Path) -> None:
        """SCP-style SSH URLs should remain intact."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            source_dir = download_source(
                "git@github.com:org/repo.git", tmp_path, "repo"
            )

        assert source_dir == tmp_path / "repo"
        assert commands == [
            [
                "git",
                "clone",
                "--depth=1",
                "git@github.com:org/repo.git",
                str(source_dir),
            ]
        ]

    def test_git_https_url_with_ref_uses_branch(self, tmp_path: Path) -> None:
        """HTTP(S) URLs may append a ref using @ref syntax."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            download_source("git+https://example.com/repo.git@v1.2.3", tmp_path, "repo")

        assert commands == [
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                "v1.2.3",
                "https://example.com/repo.git",
                str(tmp_path / "repo"),
            ]
        ]

    def test_git_url_with_ref_and_dotgit_detected(self, tmp_path: Path) -> None:
        """URLs like https://...repo.git@main should be detected as git."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            download_source("https://github.com/org/repo.git@main", tmp_path, "repo")

        # Should clone with --branch main, not try to download as archive
        assert commands[0][:2] == ["git", "clone"]
        assert "--branch" in commands[0]
        assert "main" in commands[0]

    def test_git_clone_with_commit_sha(self, tmp_path: Path) -> None:
        """Commit SHAs should do full clone + checkout, not --branch."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        with patch("subprocess.run", side_effect=mock_run):
            download_source(f"git+https://example.com/repo.git@{sha}", tmp_path, "repo")

        # First command: full clone (no --depth=1, no --branch)
        assert commands[0][:2] == ["git", "clone"]
        assert "--depth=1" not in commands[0]
        assert "--branch" not in commands[0]
        # Second command: checkout the SHA
        assert commands[1] == ["git", "-C", str(tmp_path / "repo"), "checkout", sha]

    def test_zip_rejects_path_traversal(self, tmp_path: Path) -> None:
        """Zip extraction must reject ../ traversal."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("../escape.txt", "owned")

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="escapes extraction root"),
        ):
            download_source("https://example.com/payload.zip", tmp_path / "dest", "pkg")

        assert not (tmp_path / "dest" / "escape.txt").exists()
        assert not (tmp_path / "escape.txt").exists()

    def test_tar_rejects_symlinks(self, tmp_path: Path) -> None:
        """Tar extraction must reject symlinks."""
        archive_path = tmp_path / "payload.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            info = tarfile.TarInfo("link.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "../escape.txt"
            tf.addfile(info)

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="Refusing to extract link"),
        ):
            download_source(
                "https://example.com/payload.tar.gz", tmp_path / "dest", "pkg"
            )

    def test_archive_sha256_mismatch_fails(self, tmp_path: Path) -> None:
        """Downloaded archives must match the requested SHA-256."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="SHA-256 mismatch"),
        ):
            download_source(
                "https://example.com/payload.zip",
                tmp_path / "dest",
                "pkg",
                sha256="0" * 64,
            )

    def test_archive_sha256_match_succeeds(self, tmp_path: Path) -> None:
        """Matching SHA-256 should allow extraction to proceed."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            source_dir = download_source(
                "https://example.com/payload.zip",
                tmp_path / "dest",
                "pkg",
                sha256=digest,
            )

        assert source_dir == tmp_path / "dest" / "pkg"
        assert (source_dir / "file.txt").read_text() == "ok"


class TestFetchPackage:
    """Tests for the fetch_package end-to-end flow."""

    def test_fetch_package_cmake_from_archive(self, tmp_path: Path) -> None:
        """Test full fetch_package pipeline: download archive, build, generate .pcons-pkg.toml."""
        # Create a zip archive with a fake CMakeLists.txt
        archive_path = tmp_path / "source.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr(
                "mylib/CMakeLists.txt", "cmake_minimum_required(VERSION 3.10)\n"
            )
            zf.writestr("mylib/src/lib.c", "int mylib_init(void) { return 0; }\n")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        deps_dir = tmp_path / ".deps"
        output_dir = tmp_path / "output"
        install_prefix = deps_dir / "install"

        def fake_cmake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            """Simulate cmake: on --install, create include/ and lib/ dirs."""
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            # When cmake --install is called, create the install tree
            if "--install" in cmd:
                inc = install_prefix / "include"
                lib = install_prefix / "lib"
                inc.mkdir(parents=True, exist_ok=True)
                lib.mkdir(parents=True, exist_ok=True)
                (inc / "mylib.h").write_text("#pragma once\nint mylib_init(void);\n")
                (lib / "libmylib.a").write_text("")
            return result

        pkg_config = {
            "url": "https://example.com/mylib-1.0.zip",
            "version": "1.0",
            "build": "cmake",
        }

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            patch("shutil.which", return_value="/usr/bin/cmake"),
            patch("subprocess.run", side_effect=fake_cmake_run),
        ):
            ok = fetch_package("mylib", pkg_config, deps_dir, output_dir)

        assert ok
        pkg_file = output_dir / "mylib.pcons-pkg.toml"
        assert pkg_file.exists()

        data = tomllib.loads(pkg_file.read_text())
        assert data["package"]["name"] == "mylib"
        assert data["package"]["version"] == "1.0"
        assert any("include" in d for d in data["paths"]["system_include_dirs"])
        assert "include_dirs" not in data["paths"]
        assert "mylib" in data["link"]["libraries"]

    def test_fetch_package_honours_system_false(self, tmp_path: Path) -> None:
        """system = false in deps.toml keeps the headers a plain -I."""
        archive_path = tmp_path / "source.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("hdrlib/include/hdrlib.h", "#pragma once\n")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        deps_dir = tmp_path / ".deps"
        output_dir = tmp_path / "output"
        pkg_config = {
            "url": "https://example.com/hdrlib-1.0.zip",
            "version": "1.0",
            "build": "none",
            "system": False,
        }

        with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            ok = fetch_package("hdrlib", pkg_config, deps_dir, output_dir)

        assert ok
        data = tomllib.loads((output_dir / "hdrlib.pcons-pkg.toml").read_text())
        assert any("include" in d for d in data["paths"]["include_dirs"])
        assert "system_include_dirs" not in data["paths"]

    def test_fetch_package_missing_url(self, tmp_path: Path) -> None:
        """fetch_package should fail when no URL is provided."""
        deps_dir = tmp_path / ".deps"
        output_dir = tmp_path / "output"
        ok = fetch_package("bad", {"version": "1.0"}, deps_dir, output_dir)
        assert not ok

    def test_fetch_package_unknown_build_system(self, tmp_path: Path) -> None:
        """fetch_package should fail for an unknown build system."""
        archive_path = tmp_path / "source.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        pkg_config = {
            "url": "https://example.com/pkg.zip",
            "version": "1.0",
            "build": "meson",  # unsupported
        }
        with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            ok = fetch_package("pkg", pkg_config, tmp_path / ".deps", tmp_path / "out")
        assert not ok

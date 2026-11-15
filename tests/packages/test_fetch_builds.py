# SPDX-License-Identifier: MIT
"""Tests for pcons-fetch's downloads, archive extraction and builds.

Everything runs locally: archives are built in the test, git sources are
local repositories, and the autotools projects are small shell scripts that
behave like an autoconf-generated configure.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.fetch import cli as fetch_cli
from pcons.packages.fetch.cli import (
    _safe_extract_tar,
    _safe_extract_zip,
    build_autotools,
    build_cmake,
    download_source,
    fetch_package,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="autotools projects need a POSIX shell"
)
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make not found")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not found")


def _git_reads_drive_letter_urls() -> bool:
    """Git for Windows clones file:///D:/repo; MSYS2's git reads /D:/repo."""
    if sys.platform != "win32" or shutil.which("git") is None:
        return True
    version = subprocess.run(["git", "--version"], capture_output=True, text=True)
    return ".windows." in version.stdout


clones_file_urls = pytest.mark.skipif(
    not _git_reads_drive_letter_urls(),
    reason="MSYS2's git can't clone a file:// URL with a drive letter",
)
needs_cmake = pytest.mark.skipif(
    shutil.which("cmake") is None, reason="cmake not found"
)

# A configure script that behaves like an autoconf-generated one where it
# matters here: it rejects a relative --prefix, records its arguments, and
# writes a Makefile in the directory it runs in that builds from $srcdir.
CONFIGURE = """\
#!/bin/sh
prefix=/usr/local
for arg in "$@"; do
  case $arg in
    --prefix=*) prefix=${arg#--prefix=} ;;
  esac
done
case $prefix in
  /*) ;;
  *) echo "configure: error: expected an absolute directory name for --prefix: $prefix" >&2
     exit 1 ;;
esac
srcdir=$(cd "$(dirname "$0")" && pwd)
echo "$@" > configure.args
printf 'all:\\n\\tcp %s/hello.h hello.h\\ninstall:\\n\\tmkdir -p %s/include\\n\\tcp hello.h %s/include/hello.h\\n' \\
  "$srcdir" "$prefix" "$prefix" > Makefile
"""


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def _autotools_project(root: Path, *, configure: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "hello.h").write_text("#pragma once\n")
    if configure:
        _executable(root / "configure", CONFIGURE)
    return root


def _serve(archive: Path):
    """A stand-in for urlretrieve that "downloads" *archive*."""

    def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
        Path(dest).write_bytes(archive.read_bytes())
        return str(dest), None

    return patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ok(cmd: list[str], **kwargs: Any) -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    return result


class TestSafeExtractZip:
    def test_extracts_files_and_directories(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("pkg/", "")
            zf.writestr("pkg/src/a.c", "int a;\n")
        _safe_extract_zip(archive, tmp_path / "out")
        assert (tmp_path / "out" / "pkg").is_dir()
        assert (tmp_path / "out" / "pkg" / "src" / "a.c").read_text() == "int a;\n"

    def test_rejects_an_absolute_path(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(str(tmp_path / "escape.txt"), "owned")
        with pytest.raises(RuntimeError, match="escapes extraction root"):
            _safe_extract_zip(archive, tmp_path / "out")
        assert not (tmp_path / "escape.txt").exists()

    def test_rejects_a_symlink(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "../escape.txt")
        with pytest.raises(RuntimeError, match="Refusing to extract symlink"):
            _safe_extract_zip(archive, tmp_path / "out")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_keeps_a_script_executable(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo("configure")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(info, "#!/bin/sh\n")
            zf.writestr("README", "text\n")
        _safe_extract_zip(archive, tmp_path / "out")
        assert os.access(tmp_path / "out" / "configure", os.X_OK)
        assert not os.access(tmp_path / "out" / "README", os.X_OK)


class TestSafeExtractTar:
    def _tar(self, path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> Path:
        import io

        with tarfile.open(path, "w:gz") as tf:
            for info, data in members:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data) if data else None)
        return path

    def _file(self, name: str, mode: int = 0o644) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.mode = mode
        return info

    def test_extracts_files_and_directories(self, tmp_path: Path) -> None:
        directory = tarfile.TarInfo("pkg")
        directory.type = tarfile.DIRTYPE
        archive = self._tar(
            tmp_path / "a.tar.gz",
            [(directory, b""), (self._file("pkg/src/a.c"), b"int a;\n")],
        )
        _safe_extract_tar(archive, tmp_path / "out")
        assert (tmp_path / "out" / "pkg" / "src" / "a.c").read_text() == "int a;\n"

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        archive = self._tar(tmp_path / "a.tar.gz", [(self._file("../escape"), b"x")])
        with pytest.raises(RuntimeError, match="escapes extraction root"):
            _safe_extract_tar(archive, tmp_path / "out")
        assert not (tmp_path / "escape").exists()

    def test_rejects_an_absolute_path(self, tmp_path: Path) -> None:
        target = tmp_path / "escape"
        archive = self._tar(tmp_path / "a.tar.gz", [(self._file(str(target)), b"x")])
        with pytest.raises(RuntimeError, match="escapes extraction root"):
            _safe_extract_tar(archive, tmp_path / "out")
        assert not target.exists()

    def test_rejects_a_hard_link(self, tmp_path: Path) -> None:
        link = tarfile.TarInfo("link")
        link.type = tarfile.LNKTYPE
        link.linkname = "/etc/passwd"
        archive = self._tar(tmp_path / "a.tar.gz", [(link, b"")])
        with pytest.raises(RuntimeError, match="Refusing to extract link"):
            _safe_extract_tar(archive, tmp_path / "out")

    def test_skips_a_member_with_no_contents(self, tmp_path: Path) -> None:
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        archive = self._tar(
            tmp_path / "a.tar.gz", [(fifo, b""), (self._file("kept"), b"ok")]
        )
        _safe_extract_tar(archive, tmp_path / "out")
        assert not (tmp_path / "out" / "pipe").exists()
        assert (tmp_path / "out" / "kept").read_text() == "ok"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_keeps_a_script_executable_but_not_setuid(self, tmp_path: Path) -> None:
        archive = self._tar(
            tmp_path / "a.tar.gz",
            [
                (self._file("configure", 0o755), b"#!/bin/sh\n"),
                (self._file("tool", 0o4755), b"#!/bin/sh\n"),
                (self._file("README", 0o644), b"text\n"),
            ],
        )
        _safe_extract_tar(archive, tmp_path / "out")
        out = tmp_path / "out"
        assert os.access(out / "configure", os.X_OK)
        assert not (out / "tool").stat().st_mode & stat.S_ISUID
        assert not os.access(out / "README", os.X_OK)


class TestDownloadArchives:
    def test_an_existing_source_dir_is_reused(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        with patch("urllib.request.urlretrieve") as fetch:
            assert download_source("https://x/pkg.zip", tmp_path, "pkg") == (
                tmp_path / "pkg"
            )
        fetch.assert_not_called()

    def test_an_unknown_url_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="Unsupported URL"):
            download_source("https://example.com/pkg.rar", tmp_path, "pkg")

    def test_a_single_top_directory_is_hoisted(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("pkg-1.0/a.c", "a")
            zf.writestr("pkg-1.0/sub/b.c", "b")
        with _serve(archive):
            source = download_source("https://x/pkg-1.0.zip", tmp_path / "d", "pkg")
        assert sorted(p.name for p in source.iterdir()) == ["a.c", "sub"]

    def test_a_top_directory_holding_its_own_name_is_hoisted(
        self, tmp_path: Path
    ) -> None:
        """GitHub's archive of repo "fmt", whose headers are in fmt/."""
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("fmt/fmt/core.h", "h")
            zf.writestr("fmt/CMakeLists.txt", "c")
        with _serve(archive):
            source = download_source("https://x/fmt.zip", tmp_path / "d", "fmt")
        assert (source / "fmt" / "core.h").read_text() == "h"
        assert (source / "CMakeLists.txt").read_text() == "c"

    def test_several_top_entries_stay_as_they_are(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("include/a.h", "a")
            zf.writestr("lib/liba.a", "a")
        with _serve(archive):
            source = download_source("https://x/a.zip", tmp_path / "d", "a")
        assert sorted(p.name for p in source.iterdir()) == ["include", "lib"]

    def test_a_tarball_is_extracted(self, tmp_path: Path) -> None:
        tree = tmp_path / "tree" / "pkg-1.0"
        tree.mkdir(parents=True)
        (tree / "a.c").write_text("a")
        archive = tmp_path / "pkg-1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(tree, arcname="pkg-1.0")
        with _serve(archive):
            source = download_source("https://x/pkg-1.0.tar.gz", tmp_path / "d", "p")
        assert (source / "a.c").read_text() == "a"
        assert list((tmp_path / "d").iterdir()) == [source]  # no archive left

    def test_a_rejected_archive_leaves_nothing_to_reuse(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ok.txt", "fine")
            zf.writestr("../escape.txt", "owned")
        with _serve(archive), pytest.raises(RuntimeError):
            download_source("https://x/a.zip", tmp_path / "d", "pkg")
        assert not (tmp_path / "d" / "pkg").exists()

    def test_a_network_error_is_a_download_failure(self, tmp_path: Path) -> None:
        error = urllib.error.URLError("no route to host")
        with (
            patch("urllib.request.urlretrieve", side_effect=error),
            pytest.raises(RuntimeError, match="no route to host"),
        ):
            download_source("https://x/a.tar.gz", tmp_path / "d", "pkg")
        assert not (tmp_path / "d" / "pkg").exists()


@needs_git
class TestDownloadGit:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "upstream"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        (repo / "version.txt").write_text("1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "one")
        _git(repo, "tag", "v1")
        (repo / "version.txt").write_text("2\n")
        _git(repo, "commit", "-q", "-am", "two")
        return repo

    @clones_file_urls
    def test_clones_the_default_branch(self, repo: Path, tmp_path: Path) -> None:
        source = download_source(f"git+{repo.as_uri()}", tmp_path / "d", "pkg")
        assert (source / "version.txt").read_text() == "2\n"

    @clones_file_urls
    def test_clones_a_tag(self, repo: Path, tmp_path: Path) -> None:
        source = download_source(f"git+{repo.as_uri()}@v1", tmp_path / "d", "pkg")
        assert (source / "version.txt").read_text() == "1\n"

    @clones_file_urls
    def test_checks_out_a_commit(self, repo: Path, tmp_path: Path) -> None:
        first = _git(repo, "rev-list", "--max-parents=0", "HEAD")
        source = download_source(f"git+{repo.as_uri()}@{first}", tmp_path / "d", "p")
        assert (source / "version.txt").read_text() == "1\n"

    @clones_file_urls
    def test_an_unknown_commit_leaves_no_clone(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The clone is at the wrong commit, so the next run mustn't reuse it."""
        with pytest.raises(RuntimeError, match="checkout"):
            download_source(f"git+{repo.as_uri()}@{'0' * 40}", tmp_path / "d", "pkg")
        assert not (tmp_path / "d" / "pkg").exists()

    @pytest.mark.parametrize("ref", ["", "@" + "a" * 40])
    def test_a_missing_repository_fails(self, tmp_path: Path, ref: str) -> None:
        missing = (tmp_path / "nowhere").as_uri()
        with pytest.raises(RuntimeError, match="Git clone failed"):
            download_source(f"git+{missing}{ref}", tmp_path / "d", "pkg")

    def test_git_not_installed_is_a_download_failure(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        with pytest.raises(RuntimeError, match="git"):
            download_source(f"git+{repo.as_uri()}", tmp_path / "d", "pkg")


class TestBuildCmake:
    def test_without_cmake_it_fails(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            assert not build_cmake(tmp_path, tmp_path / "b", tmp_path / "i")

    def test_builds_and_installs_the_release_configuration(
        self, tmp_path: Path
    ) -> None:
        """Multi-config generators (Visual Studio) build Debug by default."""
        commands: list[list[str]] = []

        def run(cmd: list[str], **kwargs: Any) -> MagicMock:
            commands.append(cmd)
            return _ok(cmd)

        with (
            patch("shutil.which", return_value="cmake"),
            patch("subprocess.run", side_effect=run),
        ):
            assert build_cmake(
                tmp_path, tmp_path / "b", tmp_path / "i", {"BUILD_SHARED_LIBS": "ON"}
            )
        configure, build, install = commands
        assert "-DBUILD_SHARED_LIBS=ON" in configure
        assert "-DCMAKE_BUILD_TYPE=Release" in configure
        assert build[-2:] == ["--config", "Release"]
        assert install[-2:] == ["--config", "Release"]

    @pytest.mark.parametrize("failing", ["-S", "--build", "--install"])
    def test_a_failing_step_fails_the_build(self, tmp_path: Path, failing: str) -> None:
        def run(cmd: list[str], **kwargs: Any) -> MagicMock:
            result = _ok(cmd)
            if failing in cmd:
                result.returncode = 1
            return result

        with (
            patch("shutil.which", return_value="cmake"),
            patch("subprocess.run", side_effect=run),
        ):
            assert not build_cmake(tmp_path, tmp_path / "b", tmp_path / "i")

    @needs_cmake
    def test_installs_a_real_project_from_relative_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paths as `pcons-fetch fetch` passes them: relative to where it runs."""
        source = tmp_path / ".deps" / "src" / "hello"
        source.mkdir(parents=True)
        (source / "hello.h").write_text("#pragma once\n")
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.10)\n"
            "project(hello NONE)\n"
            "install(FILES hello.h DESTINATION include)\n"
        )
        monkeypatch.chdir(tmp_path)
        assert build_cmake(
            Path(".deps/src/hello"), Path(".deps/build/hello"), Path(".deps/install")
        )
        assert (tmp_path / ".deps" / "install" / "include" / "hello.h").exists()


@posix_only
@needs_make
class TestBuildAutotools:
    def test_configures_builds_and_installs(self, tmp_path: Path) -> None:
        source = _autotools_project(tmp_path / "src")
        prefix = tmp_path / "install"
        assert build_autotools(source, tmp_path / "build", prefix, ["--enable-x"])
        assert (prefix / "include" / "hello.h").exists()
        args = (tmp_path / "build" / "configure.args").read_text().split()
        assert args == [f"--prefix={prefix}", "--enable-x"]

    def test_works_from_relative_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paths as `pcons-fetch fetch` passes them: relative to where it runs."""
        _autotools_project(tmp_path / ".deps" / "src" / "hello")
        monkeypatch.chdir(tmp_path)
        assert build_autotools(
            Path(".deps/src/hello"), Path(".deps/build/hello"), Path(".deps/install")
        )
        assert (tmp_path / ".deps" / "install" / "include" / "hello.h").exists()

    def test_runs_autogen_when_there_is_no_configure(self, tmp_path: Path) -> None:
        source = _autotools_project(tmp_path / "src", configure=False)
        (source / "configure.in").write_text(CONFIGURE)
        _executable(
            source / "autogen.sh",
            "#!/bin/sh\ncp configure.in configure\nchmod +x configure\n",
        )
        assert build_autotools(source, tmp_path / "build", tmp_path / "install")

    def test_runs_autoreconf_for_a_configure_ac(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _autotools_project(tmp_path / "src", configure=False)
        (source / "configure.ac").write_text(CONFIGURE)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _executable(
            bin_dir / "autoreconf",
            "#!/bin/sh\ncp configure.ac configure\nchmod +x configure\n",
        )
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        assert build_autotools(source, tmp_path / "build", tmp_path / "install")

    def test_without_autoreconf_it_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _autotools_project(tmp_path / "src", configure=False)
        (source / "configure.ac").write_text("AC_INIT\n")
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        assert not build_autotools(source, tmp_path / "build", tmp_path / "install")

    def test_without_a_configure_script_it_fails(self, tmp_path: Path) -> None:
        source = _autotools_project(tmp_path / "src", configure=False)
        assert not build_autotools(source, tmp_path / "build", tmp_path / "install")

    @pytest.mark.parametrize(
        "script",
        ["autogen.sh", "autoreconf", "configure", "make", "make install"],
    )
    def test_a_failing_step_fails_the_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
    ) -> None:
        source = _autotools_project(tmp_path / "src", configure=script == "configure")
        if script == "configure":
            _executable(source / "configure", "#!/bin/sh\nexit 1\n")
        elif script == "autogen.sh":
            _executable(source / "autogen.sh", "#!/bin/sh\nexit 1\n")
        elif script == "autoreconf":
            (source / "configure.ac").write_text("AC_INIT\n")
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            _executable(bin_dir / "autoreconf", "#!/bin/sh\nexit 1\n")
            monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        else:
            # make builds the first target: "all" fails, or "install" after it.
            makefile = (
                "all:\\n\\tfalse\\n"
                if script == "make"
                else "all:\\n\\ttrue\\ninstall:\\n\\tfalse\\n"
            )
            _executable(
                source / "configure", f"#!/bin/sh\nprintf '{makefile}' > Makefile\n"
            )
        assert not build_autotools(source, tmp_path / "build", tmp_path / "install")

    def test_without_make_it_fails(self, tmp_path: Path) -> None:
        source = _autotools_project(tmp_path / "src")
        with patch.object(
            fetch_cli.shutil,
            "which",
            side_effect=lambda cmd: None if cmd == "make" else shutil.which(cmd),
        ):
            assert not build_autotools(source, tmp_path / "build", tmp_path / "i")

    def test_make_runs_one_job_per_cpu(self, tmp_path: Path) -> None:
        """A bare -j starts every job at once."""
        source = _autotools_project(tmp_path / "src")
        commands: list[list[str]] = []
        real_run = subprocess.run

        def run(cmd: list[str], **kwargs: Any) -> Any:
            commands.append(cmd)
            return real_run(cmd, **kwargs)

        with patch("subprocess.run", side_effect=run):
            assert build_autotools(source, tmp_path / "build", tmp_path / "install")
        make = next(c for c in commands if Path(c[0]).name == "make" and len(c) == 2)
        assert make[1] == f"-j{os.cpu_count() or 1}"


class TestFetchPackageBuilds:
    def test_a_download_failure_fails_the_package(self, tmp_path: Path) -> None:
        error = urllib.error.HTTPError("https://x/a.zip", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with patch("urllib.request.urlretrieve", side_effect=error):
            assert not fetch_package(
                "a", {"url": "https://x/a.zip"}, tmp_path / ".deps", tmp_path
            )

    def test_a_build_failure_fails_the_package(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a/CMakeLists.txt", "")
        with (
            _serve(archive),
            patch.object(fetch_cli, "build_cmake", return_value=False),
        ):
            assert not fetch_package(
                "a", {"url": "https://x/a.zip"}, tmp_path / ".deps", tmp_path / "o"
            )
        assert not (tmp_path / "o" / "a.pcons-pkg.toml").exists()

    def test_pkg_config_files_are_pointed_to(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a/lib/pkgconfig/a.pc", "Name: a\n")
        with _serve(archive):
            assert fetch_package(
                "a",
                {"url": "https://x/a.zip", "build": "none"},
                tmp_path / ".deps",
                tmp_path / "o",
            )
        data = tomllib.loads((tmp_path / "o" / "a.pcons-pkg.toml").read_text())
        (pc_dir,) = data["pkgconfig"]["pc_dirs"]
        assert Path(pc_dir).name == "pkgconfig"

    @posix_only
    @needs_make
    def test_an_autotools_tarball_builds_where_the_cli_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole path `pcons-fetch fetch` takes, with its relative .deps."""
        tree = _autotools_project(tmp_path / "tree" / "hello-1.0")
        archive = tmp_path / "hello-1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(tree, arcname="hello-1.0")
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        with _serve(archive):
            assert fetch_package(
                "hello",
                {"url": "https://x/hello-1.0.tar.gz", "build": "autotools"},
                Path(".deps"),
                Path("."),
            )
        assert (work / ".deps" / "install" / "include" / "hello.h").exists()
        data = tomllib.loads((work / "hello.pcons-pkg.toml").read_text())
        assert data["package"]["name"] == "hello"


class TestClean:
    def test_removes_read_only_files_as_git_leaves_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Git's object files are read-only, which stops rmtree on Windows."""
        objects = tmp_path / ".deps" / "src" / "pkg" / ".git" / "objects"
        objects.mkdir(parents=True)
        (objects / "pack").write_text("x")
        (objects / "pack").chmod(stat.S_IREAD)
        monkeypatch.chdir(tmp_path)
        assert fetch_cli.cli.main(["clean", "--all"], standalone_mode=False) == 0
        assert not (tmp_path / ".deps").exists()

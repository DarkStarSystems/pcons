# SPDX-License-Identifier: MIT
"""Tests for pcons.pyproject PEP 517 build backend."""

from __future__ import annotations

import hashlib
import sys
import sysconfig
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

import pcons.pyproject as backend

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pyproject(tmp_path: Path, content: str) -> Path:
    """Write a pyproject.toml and return the directory."""
    (tmp_path / "pyproject.toml").write_text(content)
    return tmp_path


def _make_fake_extension(build_dir: Path, name: str = "myext") -> Path:
    """Create a dummy .so file that looks like a built extension."""
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    build_dir.mkdir(parents=True, exist_ok=True)
    ext = build_dir / f"{name}{ext_suffix}"
    ext.write_bytes(b"\x7fELF fake extension")
    return ext


# ---------------------------------------------------------------------------
# _load_pyproject
# ---------------------------------------------------------------------------


class TestLoadPyproject:
    def test_returns_full_dict(self, tmp_path: Path) -> None:
        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "1.2.3"\n',
        )
        data = backend._load_pyproject(tmp_path)
        assert data["project"]["name"] == "mypkg"
        assert data["project"]["version"] == "1.2.3"

    def test_tool_pcons_section(self, tmp_path: Path) -> None:
        _make_pyproject(
            tmp_path,
            '[project]\nname = "p"\nversion = "0"\n'
            '[tool.pcons]\nvariant = "debug"\n[tool.pcons.variables]\nFOO = "bar"\n',
        )
        data = backend._load_pyproject(tmp_path)
        cfg = data["tool"]["pcons"]
        assert cfg["variant"] == "debug"
        assert cfg["variables"]["FOO"] == "bar"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="pyproject.toml"):
            backend._load_pyproject(tmp_path)

    def test_missing_project_section_returns_empty(self, tmp_path: Path) -> None:
        _make_pyproject(tmp_path, "[build-system]\nrequires = []\n")
        data = backend._load_pyproject(tmp_path)
        assert data.get("project") is None


# ---------------------------------------------------------------------------
# _wheel_tag
# ---------------------------------------------------------------------------


class TestWheelTag:
    def test_format(self) -> None:
        python_tag, abi_tag, platform_tag = backend._wheel_tag()
        vi = sys.version_info
        assert python_tag == f"cp{vi.major}{vi.minor}"
        assert abi_tag == python_tag
        # platform tag must not contain raw hyphens or dots
        assert "-" not in platform_tag
        assert "." not in platform_tag


# ---------------------------------------------------------------------------
# _sha256_record
# ---------------------------------------------------------------------------


class TestSha256Record:
    def test_prefix(self) -> None:
        assert backend._sha256_record(b"hello").startswith("sha256=")

    def test_value(self) -> None:
        import base64

        data = b"test data"
        expected = "sha256=" + base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()
        ).decode().rstrip("=")
        assert backend._sha256_record(data) == expected

    def test_no_padding(self) -> None:
        result = backend._sha256_record(b"x")
        assert "=" not in result.split("sha256=", 1)[1]


# ---------------------------------------------------------------------------
# _find_extensions
# ---------------------------------------------------------------------------


class TestFindExtensions:
    def test_finds_extension(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        found = backend._find_extensions(tmp_path / "build")
        assert ext in found

    def test_recursive(self, tmp_path: Path) -> None:
        subdir = tmp_path / "build" / "obj"
        ext = _make_fake_extension(subdir)
        found = backend._find_extensions(tmp_path / "build")
        assert ext in found

    def test_empty_when_none(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        assert backend._find_extensions(tmp_path / "build") == []

    def test_ignores_other_files(self, tmp_path: Path) -> None:
        build = tmp_path / "build"
        build.mkdir()
        (build / "output.o").write_bytes(b"obj")
        (build / "libfoo.a").write_bytes(b"archive")
        assert backend._find_extensions(build) == []


# ---------------------------------------------------------------------------
# _write_wheel
# ---------------------------------------------------------------------------


class TestWriteWheel:
    def test_creates_zip(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        assert zipfile.is_zipfile(wheel_path)

    def test_contains_dist_info(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        with zipfile.ZipFile(wheel_path) as zf:
            names = zf.namelist()
        assert "mypkg-1.0.dist-info/WHEEL" in names
        assert "mypkg-1.0.dist-info/METADATA" in names
        assert "mypkg-1.0.dist-info/RECORD" in names

    def test_wheel_meta_content(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        with zipfile.ZipFile(wheel_path) as zf:
            wheel_meta = zf.read("mypkg-1.0.dist-info/WHEEL").decode()
        assert "Wheel-Version: 1.0" in wheel_meta
        assert "Root-Is-Purelib: false" in wheel_meta
        assert "Tag: cp314-cp314-linux_x86_64" in wheel_meta

    def test_metadata_content(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        with zipfile.ZipFile(wheel_path) as zf:
            metadata = zf.read("mypkg-1.0.dist-info/METADATA").decode()
        assert "Name: mypkg" in metadata
        assert "Version: 1.0" in metadata

    def test_extension_included(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build", name="myext")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        with zipfile.ZipFile(wheel_path) as zf:
            assert ext.name in zf.namelist()

    def test_record_lists_all_entries(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path, "mypkg", "1.0", [ext], "cp314", "cp314", "linux_x86_64"
        )
        with zipfile.ZipFile(wheel_path) as zf:
            record = zf.read("mypkg-1.0.dist-info/RECORD").decode()
        assert ext.name in record
        assert "mypkg-1.0.dist-info/WHEEL" in record
        assert "mypkg-1.0.dist-info/METADATA" in record
        # RECORD entry for itself must have no hash
        assert "mypkg-1.0.dist-info/RECORD,," in record


# ---------------------------------------------------------------------------
# get_requires_for_build_*
# ---------------------------------------------------------------------------


def test_get_requires_for_build_wheel_empty() -> None:
    assert backend.get_requires_for_build_wheel() == []


def test_get_requires_for_build_sdist_empty() -> None:
    assert backend.get_requires_for_build_sdist() == []


# ---------------------------------------------------------------------------
# prepare_metadata_for_build_wheel
# ---------------------------------------------------------------------------


class TestPrepareMetadata:
    def test_returns_dist_info_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "2.0"\n')
        monkeypatch.chdir(tmp_path)
        meta_dir = tmp_path / "meta"
        result = backend.prepare_metadata_for_build_wheel(str(meta_dir))
        assert result == "mypkg-2.0.dist-info"

    def test_creates_wheel_and_metadata_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "2.0"\n')
        monkeypatch.chdir(tmp_path)
        meta_dir = tmp_path / "meta"
        backend.prepare_metadata_for_build_wheel(str(meta_dir))
        dist_info = meta_dir / "mypkg-2.0.dist-info"
        assert (dist_info / "WHEEL").exists()
        assert (dist_info / "METADATA").exists()

    def test_hyphen_normalized_to_underscore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "my-pkg"\nversion = "1.0"\n')
        monkeypatch.chdir(tmp_path)
        meta_dir = tmp_path / "meta"
        result = backend.prepare_metadata_for_build_wheel(str(meta_dir))
        assert result == "my_pkg-1.0.dist-info"


# ---------------------------------------------------------------------------
# build_wheel (mocked build steps)
# ---------------------------------------------------------------------------


class TestBuildWheel:
    def _setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        """Write pyproject.toml, create a fake pcons-build.py, return (src, wheel_dir)."""
        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "0.1"\n'
            '[tool.pcons]\nvariant = "release"\n[tool.pcons.variables]\nTC = "gcc"\n',
        )
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        return tmp_path, tmp_path / "dist"

    def test_returns_wheel_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _ext = _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch("pcons.pyproject._run_ninja"),
        ):
            result = backend.build_wheel(str(wheel_dir))

        mock_pcons.assert_called_once_with(
            src, src / "build", variant="release", variables={"TC": "gcc"}
        )
        python_tag, abi_tag, platform_tag = backend._wheel_tag()
        assert result == f"mypkg-0.1-{python_tag}-{abi_tag}-{platform_tag}.whl"

    def test_wheel_file_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            filename = backend.build_wheel(str(wheel_dir))

        assert (wheel_dir / filename).exists()
        assert zipfile.is_zipfile(wheel_dir / filename)

    def test_passes_variant_and_variables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch("pcons.pyproject._run_ninja"),
        ):
            backend.build_wheel(str(wheel_dir))

        _, kwargs = mock_pcons.call_args
        assert kwargs["variant"] == "release"
        assert kwargs["variables"] == {"TC": "gcc"}

    def test_no_extensions_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        (src / "build").mkdir()  # empty build dir

        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
            pytest.raises(RuntimeError, match="No extension modules"),
        ):
            backend.build_wheel(str(wheel_dir))


# ---------------------------------------------------------------------------
# build_sdist
# ---------------------------------------------------------------------------


class TestBuildSdist:
    def test_returns_tarball_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        monkeypatch.chdir(tmp_path)
        sdist_dir = tmp_path / "dist"
        result = backend.build_sdist(str(sdist_dir))
        assert result == "mypkg-0.1.tar.gz"

    def test_tarball_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        monkeypatch.chdir(tmp_path)
        sdist_dir = tmp_path / "dist"
        filename = backend.build_sdist(str(sdist_dir))
        assert (sdist_dir / filename).exists()

    def test_includes_pyproject_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        monkeypatch.chdir(tmp_path)
        sdist_dir = tmp_path / "dist"
        filename = backend.build_sdist(str(sdist_dir))
        with tarfile.open(sdist_dir / filename) as tf:
            names = tf.getnames()
        assert any("pyproject.toml" in n for n in names)

    def test_hyphen_normalized_in_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(tmp_path, '[project]\nname = "my-pkg"\nversion = "1.0"\n')
        monkeypatch.chdir(tmp_path)
        result = backend.build_sdist(str(tmp_path / "dist"))
        assert result == "my_pkg-1.0.tar.gz"


# ---------------------------------------------------------------------------
# build_editable / get_requires_for_build_editable
# ---------------------------------------------------------------------------


def test_get_requires_for_build_editable_empty() -> None:
    assert backend.get_requires_for_build_editable() == []


class TestBuildEditable:
    def _setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        return tmp_path, tmp_path / "dist"

    def test_returns_wheel_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            result = backend.build_editable(str(wheel_dir))

        python_tag, abi_tag, platform_tag = backend._wheel_tag()
        assert result == f"mypkg-0.1-{python_tag}-{abi_tag}-{platform_tag}.whl"

    def test_wheel_file_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            filename = backend.build_editable(str(wheel_dir))

        assert (wheel_dir / filename).exists()
        assert zipfile.is_zipfile(wheel_dir / filename)

    def test_contains_pth_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)
        _make_fake_extension(src / "build")

        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            filename = backend.build_editable(str(wheel_dir))

        with zipfile.ZipFile(wheel_dir / filename) as zf:
            names = zf.namelist()
            pth_files = [n for n in names if n.endswith(".pth")]
            assert len(pth_files) == 1
            pth_content = zf.read(pth_files[0]).decode()
            assert str((src / "build").resolve()) in pth_content
            # Extension must NOT be bundled in the editable wheel
            ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
            assert not any(n.endswith(ext_suffix) for n in names)

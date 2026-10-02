# SPDX-License-Identifier: MIT
"""Tests for pcons.pyproject PEP 517 build backend."""

from __future__ import annotations

import hashlib
import sys
import sysconfig
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _stage_extension_side_effect(
    build_dir: Path, targets: list[str] | None = None, jobs: int | None = None
) -> None:
    """Stand-in for _run_ninja that stages a fake extension into the wheel dir.

    The non-editable wheel build packages whatever the install target copies
    into ``build/.wheel-staging``, this mimics that so build_wheel tests don't
    need a real ninja run.
    """
    _make_fake_extension(build_dir / ".wheel-staging")


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
# _render_metadata
# ---------------------------------------------------------------------------


class TestRenderMetadata:
    def test_minimal(self, tmp_path: Path) -> None:
        meta = backend._render_metadata("mypkg", "1.0", {}, tmp_path)
        assert "Metadata-Version: 2.4" in meta
        assert "Name: mypkg" in meta
        assert "Version: 1.0" in meta

    def test_requires_python(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"requires-python": ">=3.11"}, tmp_path
        )
        assert "Requires-Python: >=3.11" in meta

    def test_dependencies_become_requires_dist(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"dependencies": ["requests>=2", "rich; python_version >= '3.8'"]},
            tmp_path,
        )
        assert "Requires-Dist: requests>=2" in meta
        assert "Requires-Dist: rich; python_version >= '3.8'" in meta

    def test_unknown_field_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="frobnicate"):
            backend._render_metadata("mypkg", "1.0", {"frobnicate": "x"}, tmp_path)

    def test_non_empty_dynamic_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="dynamic"):
            backend._render_metadata("mypkg", "1.0", {"dynamic": ["version"]}, tmp_path)

    def test_error_lists_every_unsupported_field(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError) as exc:
            backend._render_metadata(
                "mypkg", "1.0", {"frobnicate": "x", "dynamic": ["version"]}, tmp_path
            )
        message = str(exc.value)
        assert "frobnicate" in message
        assert "dynamic" in message

    def test_empty_unsupported_field_is_ignored(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"dynamic": [], "keywords": []}, tmp_path
        )
        assert "Name: mypkg" in meta

    def test_description_becomes_summary(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"description": "A tool"}, tmp_path
        )
        assert "Summary: A tool\n" in meta

    @pytest.mark.parametrize("description", ["a\nb", "a\rb", "a\r\nb"])
    def test_multiline_description_raises(
        self, tmp_path: Path, description: str
    ) -> None:
        with pytest.raises(RuntimeError, match="description"):
            backend._render_metadata(
                "mypkg", "1.0", {"description": description}, tmp_path
            )

    @pytest.mark.parametrize(
        ("filename", "content_type"),
        [
            ("README.md", "text/markdown"),
            ("README.rst", "text/x-rst"),
            ("README.txt", "text/plain"),
        ],
    )
    def test_readme_string(
        self, tmp_path: Path, filename: str, content_type: str
    ) -> None:
        (tmp_path / filename).write_text("Hello\nworld\n")
        meta = backend._render_metadata("mypkg", "1.0", {"readme": filename}, tmp_path)
        assert f"Description-Content-Type: {content_type}\n" in meta
        head, _, body = meta.partition("\n\n")
        assert "Hello" not in head
        assert body == "Hello\nworld\n"

    def test_readme_table_file(self, tmp_path: Path) -> None:
        (tmp_path / "DOC").write_text("body\n")
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"readme": {"file": "DOC", "content-type": "text/plain"}},
            tmp_path,
        )
        assert "Description-Content-Type: text/plain\n" in meta
        assert meta.endswith("\n\nbody\n")

    def test_readme_table_text(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"readme": {"text": "inline", "content-type": "text/markdown"}},
            tmp_path,
        )
        assert meta.endswith("\n\ninline")

    def test_readme_table_needs_content_type(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="content-type"):
            backend._render_metadata(
                "mypkg", "1.0", {"readme": {"text": "x"}}, tmp_path
            )

    def test_readme_table_file_and_text_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="exactly one"):
            backend._render_metadata(
                "mypkg",
                "1.0",
                {"readme": {"file": "R", "text": "x", "content-type": "text/plain"}},
                tmp_path,
            )

    def test_readme_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="README.md"):
            backend._render_metadata("mypkg", "1.0", {"readme": "README.md"}, tmp_path)

    def test_readme_unknown_suffix_raises(self, tmp_path: Path) -> None:
        (tmp_path / "README.adoc").write_text("x")
        with pytest.raises(RuntimeError, match="content type"):
            backend._render_metadata(
                "mypkg", "1.0", {"readme": "README.adoc"}, tmp_path
            )

    def test_readme_outside_project_raises(self, tmp_path: Path) -> None:
        (tmp_path / "outside.md").write_text("x")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        with pytest.raises(RuntimeError, match="outside"):
            backend._render_metadata(
                "mypkg", "1.0", {"readme": "../outside.md"}, project_dir
            )

    def test_license_expression(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"license": "MIT OR Apache-2.0"}, tmp_path
        )
        assert "License-Expression: MIT OR Apache-2.0\n" in meta

    def test_license_table_text(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"license": {"text": "Custom"}}, tmp_path
        )
        assert "License: Custom\n" in meta

    def test_license_table_file_folds_lines(self, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text("line one\nline two")
        meta = backend._render_metadata(
            "mypkg", "1.0", {"license": {"file": "LICENSE"}}, tmp_path
        )
        assert "License: line one\n        line two\n" in meta

    def test_license_files_listed_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text("a")
        (tmp_path / "NOTICE").write_text("b")
        (tmp_path / "licenses").mkdir()
        (tmp_path / "licenses" / "X.txt").write_text("c")
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"license-files": ["NOTICE", "LICENSE*", "licenses/*.txt"]},
            tmp_path,
        )
        lines = [ln for ln in meta.splitlines() if ln.startswith("License-File:")]
        assert lines == [
            "License-File: LICENSE",
            "License-File: NOTICE",
            "License-File: licenses/X.txt",
        ]

    def test_license_table_file_and_text_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="exactly one"):
            backend._render_metadata(
                "mypkg", "1.0", {"license": {"file": "L", "text": "x"}}, tmp_path
            )

    def test_license_files_skip_directories(self, tmp_path: Path) -> None:
        (tmp_path / "licenses" / "sub").mkdir(parents=True)
        (tmp_path / "licenses" / "X.txt").write_text("c")
        meta = backend._render_metadata(
            "mypkg", "1.0", {"license-files": ["licenses/*"]}, tmp_path
        )
        lines = [ln for ln in meta.splitlines() if ln.startswith("License-File:")]
        assert lines == ["License-File: licenses/X.txt"]

    def test_license_files_without_match_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="matches no file"):
            backend._render_metadata(
                "mypkg", "1.0", {"license-files": ["LICENSE*"]}, tmp_path
            )

    @pytest.mark.parametrize(
        "pattern",
        [
            "../LICENSE",
            "/LICENSE",
            "C:LICENSE",
            "C:/LICENSE",
            "sub\\LICENSE",
            "\\LICENSE",
        ],
    )
    def test_license_files_outside_project_raises(
        self, tmp_path: Path, pattern: str
    ) -> None:
        with pytest.raises(RuntimeError, match="outside"):
            backend._render_metadata(
                "mypkg", "1.0", {"license-files": [pattern]}, tmp_path
            )

    @pytest.mark.skipif(sys.platform == "win32", reason="needs symlink privilege")
    def test_license_files_symlink_escape_raises(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        (project / "LICENSE").symlink_to(secret)
        with pytest.raises(RuntimeError, match="outside"):
            backend._render_metadata(
                "mypkg", "1.0", {"license-files": ["LICENSE"]}, project
            )

    @pytest.mark.skipif(sys.platform == "win32", reason="needs symlink privilege")
    def test_license_files_symlink_inside_is_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "REAL").write_text("text")
        (tmp_path / "LICENSE").symlink_to(tmp_path / "REAL")
        meta = backend._render_metadata(
            "mypkg", "1.0", {"license-files": ["LICENSE"]}, tmp_path
        )
        assert "License-File: LICENSE\n" in meta

    def test_people(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {
                "authors": [
                    {"name": "Ada", "email": "ada@example.com"},
                    {"email": "bare@example.com"},
                    {"name": "Bob"},
                ],
                "maintainers": [{"name": "Eve", "email": "eve@example.com"}],
            },
            tmp_path,
        )
        assert "Author: Bob\n" in meta
        assert "Author-email: Ada <ada@example.com>, bare@example.com\n" in meta
        assert "Maintainer-email: Eve <eve@example.com>\n" in meta
        assert "Maintainer:" not in meta

    def test_person_name_with_comma_is_quoted(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"authors": [{"name": "Doe, Jane", "email": "j@example.com"}]},
            tmp_path,
        )
        assert 'Author-email: "Doe, Jane" <j@example.com>\n' in meta

    def test_non_ascii_person_name_stays_utf8(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"authors": [{"name": "Zo\u00eb M\u00fcller", "email": "z@example.com"}]},
            tmp_path,
        )
        assert "Author-email: Zo\u00eb M\u00fcller <z@example.com>\n" in meta
        assert "=?" not in meta

    def test_keywords_classifiers_urls(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {
                "keywords": ["build", "ninja"],
                "classifiers": ["License :: OSI Approved :: MIT License", "Topic :: X"],
                "urls": {"Homepage": "https://example.com", "Docs": "https://d.io"},
            },
            tmp_path,
        )
        assert "Keywords: build,ninja\n" in meta
        assert "Classifier: License :: OSI Approved :: MIT License\n" in meta
        assert "Classifier: Topic :: X\n" in meta
        assert "Project-URL: Homepage, https://example.com\n" in meta
        assert "Project-URL: Docs, https://d.io\n" in meta

    def test_parses_as_email_message(self, tmp_path: Path) -> None:
        from email import message_from_string

        (tmp_path / "README.md").write_text("# Title\n")
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"description": "S", "readme": "README.md", "license": "MIT"},
            tmp_path,
        )
        msg = message_from_string(meta)
        assert msg["Summary"] == "S"
        assert msg["License-Expression"] == "MIT"
        assert msg.get_payload() == "# Title\n"


class TestOptionalDependencies:
    def _lines(self, tmp_path: Path, extras: dict[str, list[str]]) -> list[str]:
        meta = backend._render_metadata(
            "mypkg", "1.0", {"optional-dependencies": extras}, tmp_path
        )
        return [
            ln
            for ln in meta.splitlines()
            if ln.startswith(("Provides-Extra", "Requires-Dist"))
        ]

    def test_plain_requirement(self, tmp_path: Path) -> None:
        assert self._lines(tmp_path, {"test": ["pytest>=8"]}) == [
            "Provides-Extra: test",
            'Requires-Dist: pytest>=8; extra == "test"',
        ]

    def test_marker_is_parenthesized(self, tmp_path: Path) -> None:
        assert self._lines(tmp_path, {"test": ['numpy; python_version < "3.13"']}) == [
            "Provides-Extra: test",
            'Requires-Dist: numpy; (python_version < "3.13") and extra == "test"',
        ]

    def test_url_requirement_with_marker(self, tmp_path: Path) -> None:
        assert self._lines(
            tmp_path, {"dev": ['pkg @ https://example.com/p.whl ; os_name == "nt"']}
        ) == [
            "Provides-Extra: dev",
            "Requires-Dist: pkg @ https://example.com/p.whl; "
            '(os_name == "nt") and extra == "dev"',
        ]

    def test_names_are_normalized_and_sorted(self, tmp_path: Path) -> None:
        assert self._lines(tmp_path, {"Zed_Extra": ["a"], "my.dev__tools": ["b"]}) == [
            "Provides-Extra: my-dev-tools",
            'Requires-Dist: b; extra == "my-dev-tools"',
            "Provides-Extra: zed-extra",
            'Requires-Dist: a; extra == "zed-extra"',
        ]

    def test_names_colliding_after_normalization_raise(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="a-b"):
            self._lines(tmp_path, {"a_b": ["x"], "A.b": ["y"]})

    @pytest.mark.parametrize("extra", ["_dev", "dev!", "-dev", "", "d e v"])
    def test_invalid_extra_name_raises(self, tmp_path: Path, extra: str) -> None:
        with pytest.raises(RuntimeError, match="extra"):
            self._lines(tmp_path, {extra: ["x"]})

    def test_empty_extra_still_provided(self, tmp_path: Path) -> None:
        assert self._lines(tmp_path, {"none": []}) == ["Provides-Extra: none"]

    def test_runtime_dependencies_come_first(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"dependencies": ["rich"], "optional-dependencies": {"t": ["pytest"]}},
            tmp_path,
        )
        assert meta.index("Requires-Dist: rich\n") < meta.index("Provides-Extra: t")


class TestDistInfoExtras:
    def test_license_files_land_under_licenses(self, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_bytes(b"MIT text")
        extras = backend._dist_info_extras({"license-files": ["LICENSE"]}, tmp_path)
        assert extras == {"licenses/LICENSE": b"MIT text"}

    def test_empty_without_fields(self, tmp_path: Path) -> None:
        assert backend._dist_info_extras({}, tmp_path) == {}

    def test_entry_points_text(self, tmp_path: Path) -> None:
        project = {
            "scripts": {"zed": "pkg:z", "alpha": "pkg.cli:main"},
            "gui-scripts": {"win": "pkg.gui:run"},
            "entry-points": {
                "pcons.plugins": {"b": "pkg:b", "a": "pkg:a"},
                "another.group": {"x": "pkg:x"},
            },
        }
        extras = backend._dist_info_extras(project, tmp_path)
        assert extras["entry_points.txt"].decode() == (
            "[another.group]\n"
            "x = pkg:x\n"
            "\n"
            "[console_scripts]\n"
            "alpha = pkg.cli:main\n"
            "zed = pkg:z\n"
            "\n"
            "[gui_scripts]\n"
            "win = pkg.gui:run\n"
            "\n"
            "[pcons.plugins]\n"
            "a = pkg:a\n"
            "b = pkg:b\n"
        )

    def test_empty_entry_points_write_nothing(self, tmp_path: Path) -> None:
        project = {"scripts": {}, "entry-points": {"g": {}}}
        assert backend._dist_info_extras(project, tmp_path) == {}

    @pytest.mark.parametrize("group", ["console_scripts", "gui_scripts"])
    def test_reserved_entry_point_group_raises(
        self, tmp_path: Path, group: str
    ) -> None:
        project = {"entry-points": {group: {"x": "pkg:x"}}}
        with pytest.raises(RuntimeError, match=group):
            backend._dist_info_extras(project, tmp_path)

    def test_scripts_are_honored_fields(self, tmp_path: Path) -> None:
        meta = backend._render_metadata(
            "mypkg",
            "1.0",
            {"scripts": {"hello": "pkg:main"}, "gui-scripts": {"g": "pkg:g"}},
            tmp_path,
        )
        assert "Name: mypkg" in meta


class TestNameVersion:
    def test_returns_name_and_version(self) -> None:
        assert backend._name_version({"name": "mypkg", "version": "1.0"}) == (
            "mypkg",
            "1.0",
        )

    def test_normalizes_hyphen_to_underscore(self) -> None:
        assert backend._name_version({"name": "my-pkg", "version": "1.0"})[0] == (
            "my_pkg"
        )

    def test_missing_name_raises(self) -> None:
        with pytest.raises(RuntimeError, match="name"):
            backend._name_version({"version": "1.0"})

    def test_missing_version_raises(self) -> None:
        with pytest.raises(RuntimeError, match="version"):
            backend._name_version({"name": "mypkg"})

    def test_missing_both_raises_listing_both(self) -> None:
        with pytest.raises(RuntimeError) as exc:
            backend._name_version({})
        message = str(exc.value)
        assert "name" in message and "version" in message


# ---------------------------------------------------------------------------
# _write_wheel
# ---------------------------------------------------------------------------


class TestWriteWheel:
    def test_creates_zip(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
        )
        assert zipfile.is_zipfile(wheel_path)

    def test_contains_dist_info(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
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
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
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
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
        )
        with zipfile.ZipFile(wheel_path) as zf:
            metadata = zf.read("mypkg-1.0.dist-info/METADATA").decode()
        assert "Name: mypkg" in metadata
        assert "Version: 1.0" in metadata

    def test_extension_included(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build", name="myext")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
        )
        with zipfile.ZipFile(wheel_path) as zf:
            assert ext.name in zf.namelist()

    def test_record_lists_all_entries(self, tmp_path: Path) -> None:
        ext = _make_fake_extension(tmp_path / "build")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path,
            "mypkg",
            "1.0",
            [ext],
            tmp_path / "build",
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
        )
        with zipfile.ZipFile(wheel_path) as zf:
            record = zf.read("mypkg-1.0.dist-info/RECORD").decode()
        assert ext.name in record
        assert "mypkg-1.0.dist-info/WHEEL" in record
        assert "mypkg-1.0.dist-info/METADATA" in record
        # RECORD entry for itself must have no hash
        assert "mypkg-1.0.dist-info/RECORD,," in record

    def test_preserves_package_directory_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "build" / ".wheel-staging"
        (root / "mypkg").mkdir(parents=True)
        (root / "mypkg" / "__init__.py").write_text("from ._ext import *\n")
        ext = _make_fake_extension(root / "mypkg", name="_ext")
        wheel_path = tmp_path / "out.whl"
        backend._write_wheel(
            wheel_path,
            "mypkg",
            "1.0",
            [root / "mypkg" / "__init__.py", ext],
            root,
            backend._render_metadata("mypkg", "1.0", {}, tmp_path),
            "cp314",
            "cp314",
            "linux_x86_64",
        )
        with zipfile.ZipFile(wheel_path) as zf:
            names = zf.namelist()
        assert "mypkg/__init__.py" in names
        assert f"mypkg/{ext.name}" in names


# ---------------------------------------------------------------------------
# get_requires_for_build_*
# ---------------------------------------------------------------------------


def test_get_requires_for_build_wheel_needs_ninja_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend.shutil, "which", lambda _: None)
    assert backend.get_requires_for_build_wheel() == ["ninja"]


def test_get_requires_for_build_wheel_skips_ninja_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend.shutil, "which", lambda _: "/usr/bin/ninja")
    assert backend.get_requires_for_build_wheel() == []


def test_get_requires_honors_ninja_override_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NINJA points at an alternative runner resolvable on PATH: ninja is
    # available even though plain "ninja" is not.
    monkeypatch.setenv("NINJA", "n2")
    monkeypatch.setattr(
        backend.shutil, "which", lambda name: "/usr/bin/n2" if name == "n2" else None
    )
    assert backend.get_requires_for_build_wheel() == []


def test_get_requires_provisions_ninja_when_override_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NINJA names something that isn't on PATH and isn't an absolute path, and
    # plain "ninja" is absent too: provision the wheel.
    monkeypatch.setenv("NINJA", "n2")
    monkeypatch.setattr(backend.shutil, "which", lambda _: None)
    assert backend.get_requires_for_build_wheel() == ["ninja"]


def test_get_requires_for_build_sdist_empty() -> None:
    # An sdist never runs ninja, so it must not pull it in even when absent.
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

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            result = backend.build_wheel(str(wheel_dir))

        mock_pcons.assert_called_once_with(
            src,
            src / "build",
            variant="release",
            variables={
                "TC": "gcc",
                "PCONS_INSTALL_PREFIX": str(src / "build" / ".wheel-staging"),
                "PCONS_BUILD_WHEEL": "1",
            },
        )
        python_tag, abi_tag, platform_tag = backend._wheel_tag()
        assert result == f"mypkg-0.1-{python_tag}-{abi_tag}-{platform_tag}.whl"

    def test_wheel_file_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)

        with (
            patch("pcons.pyproject._run_pcons"),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            filename = backend.build_wheel(str(wheel_dir))

        assert (wheel_dir / filename).exists()
        assert zipfile.is_zipfile(wheel_dir / filename)

    def test_passes_variant_and_variables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src, wheel_dir = self._setup(tmp_path, monkeypatch)

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            backend.build_wheel(str(wheel_dir))

        _, kwargs = mock_pcons.call_args
        assert kwargs["variant"] == "release"
        assert kwargs["variables"] == {
            "TC": "gcc",
            "PCONS_INSTALL_PREFIX": str(src / "build" / ".wheel-staging"),
            "PCONS_BUILD_WHEEL": "1",
        }

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

    def test_includes_nested_source_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "hello.cpp").write_text("int main() {}\n")
        (tmp_path / "src" / "hello.hpp").write_text("#pragma once\n")
        monkeypatch.chdir(tmp_path)
        filename = backend.build_sdist(str(tmp_path / "dist"))
        with tarfile.open(tmp_path / "dist" / filename) as tf:
            names = tf.getnames()
        # The old top-level-only globs dropped these; they must be present now.
        assert "mypkg-0.1/src/hello.cpp" in names
        assert "mypkg-0.1/src/hello.hpp" in names

    def test_contains_pkg_info(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "0.1"\nrequires-python = ">=3.11"\n',
        )
        monkeypatch.chdir(tmp_path)
        filename = backend.build_sdist(str(tmp_path / "dist"))
        with tarfile.open(tmp_path / "dist" / filename) as tf:
            member = tf.extractfile("mypkg-0.1/PKG-INFO")
            assert member is not None
            pkg_info = member.read().decode()
        assert "Metadata-Version:" in pkg_info
        assert "Name: mypkg" in pkg_info
        assert "Version: 0.1" in pkg_info
        assert "Requires-Python: >=3.11" in pkg_info

    def test_excludes_build_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "artifact.o").write_bytes(b"junk")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"junk")
        monkeypatch.chdir(tmp_path)
        filename = backend.build_sdist(str(tmp_path / "dist"))
        with tarfile.open(tmp_path / "dist" / filename) as tf:
            names = tf.getnames()
        assert not any("artifact.o" in n for n in names)
        assert not any(".pyc" in n for n in names)


# ---------------------------------------------------------------------------
# build_editable / get_requires_for_build_editable
# ---------------------------------------------------------------------------


def test_get_requires_for_build_editable_needs_ninja_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend.shutil, "which", lambda _: None)
    assert backend.get_requires_for_build_editable() == ["ninja"]


def test_get_requires_for_build_editable_skips_ninja_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend.shutil, "which", lambda _: "/usr/bin/ninja")
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


_RICH_PYPROJECT = """\
[project]
name = "mypkg"
version = "0.1"
description = "A package"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]

[project.scripts]
hello = "mypkg:main"
"""


def _rich_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _make_pyproject(tmp_path, _RICH_PYPROJECT)
    (tmp_path / "README.md").write_text("# Hello\n")
    (tmp_path / "LICENSE").write_text("MIT text\n")
    (tmp_path / "pcons-build.py").write_text("# stub")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestDistInfoExtrasInDistributions:
    def test_wheel_carries_license_and_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _rich_project(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons"),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            filename = backend.build_wheel(str(src / "dist"))
        with zipfile.ZipFile(src / "dist" / filename) as zf:
            assert zf.read("mypkg-0.1.dist-info/licenses/LICENSE") == b"MIT text\n"
            record = zf.read("mypkg-0.1.dist-info/RECORD").decode()
            metadata = zf.read("mypkg-0.1.dist-info/METADATA").decode()
        assert "mypkg-0.1.dist-info/licenses/LICENSE,sha256=" in record
        assert "mypkg-0.1.dist-info/entry_points.txt,sha256=" in record
        assert "License-File: LICENSE\n" in metadata
        assert "Summary: A package\n" in metadata

    def test_editable_wheel_carries_license_and_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _rich_project(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            filename = backend.build_editable(str(src / "dist"))
        with zipfile.ZipFile(src / "dist" / filename) as zf:
            assert zf.read("mypkg-0.1.dist-info/licenses/LICENSE") == b"MIT text\n"
            record = zf.read("mypkg-0.1.dist-info/RECORD").decode()
            entry_points = zf.read("mypkg-0.1.dist-info/entry_points.txt").decode()
        assert "mypkg-0.1.dist-info/licenses/LICENSE,sha256=" in record
        assert "mypkg-0.1.dist-info/entry_points.txt,sha256=" in record
        assert entry_points == "[console_scripts]\nhello = mypkg:main\n"

    def test_prepare_metadata_writes_license(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rich_project(tmp_path, monkeypatch)
        meta_dir = tmp_path / "meta"
        backend.prepare_metadata_for_build_wheel(str(meta_dir))
        dist_info = meta_dir / "mypkg-0.1.dist-info"
        assert (dist_info / "licenses" / "LICENSE").read_text() == "MIT text\n"
        assert (dist_info / "entry_points.txt").read_text() == (
            "[console_scripts]\nhello = mypkg:main\n"
        )
        assert "Summary: A package" in (dist_info / "METADATA").read_text()

    def test_prepared_metadata_is_byte_identical_to_wheel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = _rich_project(tmp_path, monkeypatch)
        (src / "README.md").write_text("# H\u00e9llo \u2603\n", encoding="utf-8")
        meta_dir = tmp_path / "meta"
        backend.prepare_metadata_for_build_wheel(str(meta_dir))
        prepared = (meta_dir / "mypkg-0.1.dist-info" / "METADATA").read_bytes()
        with (
            patch("pcons.pyproject._run_pcons"),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            filename = backend.build_wheel(str(src / "dist"))
        with zipfile.ZipFile(src / "dist" / filename) as zf:
            assert zf.read("mypkg-0.1.dist-info/METADATA") == prepared
        assert b"\r" not in prepared
        assert "H\u00e9llo \u2603".encode() in prepared

    def test_sdist_pkg_info_has_new_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        src = _rich_project(tmp_path, monkeypatch)
        filename = backend.build_sdist(str(src / "dist"))
        with tarfile.open(src / "dist" / filename) as tf:
            member = tf.extractfile("mypkg-0.1/PKG-INFO")
            assert member is not None
            pkg_info = member.read().decode()
            assert "mypkg-0.1/LICENSE" in tf.getnames()
        assert "Metadata-Version: 2.4" in pkg_info
        assert "License-Expression: MIT\n" in pkg_info
        assert pkg_info.endswith("\n\n# Hello\n")


class TestSdistValidation:
    def test_reserved_entry_point_group_fails_sdist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "0.1"\n'
            '[project.entry-points.console_scripts]\nx = "pkg:x"\n',
        )
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="console_scripts"):
            backend.build_sdist(str(tmp_path / "dist"))
        assert not (tmp_path / "dist").exists()


class TestBuildDir:
    def _setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_dir: str
    ) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        _make_pyproject(
            src,
            '[project]\nname = "mypkg"\nversion = "0.1"\n'
            f"[tool.pcons]\nbuild-dir = {build_dir!r}\n",
        )
        (src / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(src)
        return src

    def test_default_is_build(self, tmp_path: Path) -> None:
        assert backend._build_dir(tmp_path, {}) == tmp_path / "build"

    def test_absolute_path_is_kept(self, tmp_path: Path) -> None:
        out = tmp_path / "elsewhere"
        assert backend._build_dir(tmp_path / "src", {"build-dir": str(out)}) == out

    @pytest.mark.parametrize(
        "value",
        ["", ".", "..", "../..", "sub/..", str(Path.cwd().anchor), 3, "C:build"],
    )
    def test_invalid_value_raises(self, tmp_path: Path, value: object) -> None:
        with pytest.raises(RuntimeError, match="build-dir"):
            backend._build_dir(tmp_path / "proj", {"build-dir": value})

    def test_sibling_of_project_is_allowed(self, tmp_path: Path) -> None:
        assert backend._build_dir(tmp_path / "proj", {"build-dir": "../out"}) == (
            tmp_path / "proj" / "../out"
        )

    def test_wheel_uses_build_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch, "out")

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ) as mock_ninja,
        ):
            backend.build_wheel(str(src / "dist"))

        assert mock_pcons.call_args.args[1] == src / "out"
        assert mock_ninja.call_args.args[0] == src / "out"
        _, kwargs = mock_pcons.call_args
        assert kwargs["variables"]["PCONS_INSTALL_PREFIX"] == str(
            src / "out" / ".wheel-staging"
        )

    def test_editable_ignores_build_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch, "out")

        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            patch("pcons.pyproject._run_ninja"),
        ):
            filename = backend.build_editable(str(src / "dist"))

        assert mock_pcons.call_args.args[1] == src / "build"
        with zipfile.ZipFile(src / "dist" / filename) as zf:
            pth = next(n for n in zf.namelist() if n.endswith(".pth"))
            assert zf.read(pth).decode() == f"{(src / 'build').resolve()}\n"

    def test_sdist_excludes_build_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        src = self._setup(tmp_path, monkeypatch, "out/wheel")
        (src / "out" / "wheel").mkdir(parents=True)
        (src / "out" / "wheel" / "build.ninja").write_text("junk")
        (src / "out" / "keep.txt").write_text("source")
        filename = backend.build_sdist(str(src / "dist"))
        with tarfile.open(src / "dist" / filename) as tf:
            names = tf.getnames()
        assert "mypkg-0.1/out/keep.txt" in names
        assert not any("build.ninja" in n for n in names)

    def test_sdist_with_build_dir_outside(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tarfile

        src = self._setup(tmp_path, monkeypatch, str(tmp_path / "out"))
        filename = backend.build_sdist(str(src / "dist"))
        with tarfile.open(src / "dist" / filename) as tf:
            assert "mypkg-0.1/pcons-build.py" in tf.getnames()


class TestRunPcons:
    def test_does_not_persist(self, tmp_path: Path) -> None:
        (tmp_path / "pcons-build.py").write_text("# stub")
        with patch("pcons.cli.run_script", return_value=(0, [])) as mock_run:
            backend._run_pcons(tmp_path, tmp_path / "build", variables={"A": "1"})
        assert mock_run.call_args.kwargs["persist"] is False
        assert mock_run.call_args.kwargs["fresh"] is True

    def test_ignores_and_keeps_the_developer_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        (tmp_path / "pcons-build.py").write_text(
            "import json\n"
            "from pathlib import Path\n"
            "from pcons import Project, get_var, get_variant\n"
            "Project('p')\n"
            "Path('seen.json').write_text(json.dumps({\n"
            "    'variant': get_variant('unset'),\n"
            "    'somevar': get_var('SOMEVAR'),\n"
            "    'a': get_var('A'),\n"
            "}))\n"
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        cache_file = build_dir / "pcons_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "source_dir": str(tmp_path),
                    "vars": {"SOMEVAR": "ON"},
                    "variant": "debug",
                    "generator": "make",
                }
            )
        )
        before = cache_file.read_bytes()
        monkeypatch.chdir(tmp_path)

        backend._run_pcons(tmp_path, build_dir, variables={"A": "1"})

        assert json.loads((tmp_path / "seen.json").read_text()) == {
            "variant": "unset",
            "somevar": None,
            "a": "1",
        }
        assert (build_dir / "build.ninja").exists()
        assert not (build_dir / "Makefile").exists()
        assert cache_file.read_bytes() == before

    def test_writes_no_cache_into_a_fresh_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\nProject('p')\n"
        )
        build_dir = tmp_path / "build"

        backend._run_pcons(tmp_path, build_dir, variables={"SOMEVAR": "OFF"})

        assert (build_dir / "build.ninja").exists()
        assert not (build_dir / "pcons_cache.json").exists()


class TestSharedBuildDirWarning:
    _MESSAGE = "was last configured by something other than a wheel build"

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @staticmethod
    def _configure(
        src: Path,
        build_dir: Path,
        variant: str | None = None,
        variables: dict[str, str] | None = None,
    ) -> None:
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "build.ninja").write_text(f"# wheel {variables}\n")

    def _wheel(self, src: Path) -> None:
        with (
            patch("pcons.pyproject._run_pcons", side_effect=self._configure),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            backend.build_wheel(str(src / "dist"))

    def _warned(self, caplog: pytest.LogCaptureFixture) -> bool:
        return any(self._MESSAGE in r.getMessage() for r in caplog.records)

    def test_fresh_dir_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        self._wheel(src)
        assert not self._warned(caplog)
        assert (src / "build" / backend._WHEEL_STAMP).exists()

    def test_wheel_after_wheel_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        self._wheel(src)
        self._wheel(src)
        assert not self._warned(caplog)

    def test_dir_configured_by_another_build_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        (src / "build").mkdir()
        (src / "build" / "build.ninja").write_text("# cli\n")
        self._wheel(src)
        assert self._warned(caplog)
        record = next(r for r in caplog.records if self._MESSAGE in r.getMessage())
        assert record.levelname == "WARNING"

    def test_reconfigure_after_wheel_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        self._wheel(src)
        (src / "build" / "build.ninja").write_text("# editable\n")
        self._wheel(src)
        assert self._warned(caplog)

    def test_same_size_rewrite_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import os

        src = self._setup(tmp_path, monkeypatch)
        self._wheel(src)
        ninja_file = src / "build" / "build.ninja"
        stat = ninja_file.stat()
        os.utime(ninja_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self._wheel(src)
        assert self._warned(caplog)

    def test_unreadable_stamp_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        self._wheel(src)
        (src / "build" / backend._WHEEL_STAMP).write_text("not json")
        self._wheel(src)
        assert self._warned(caplog)

    def test_failed_ninja_still_stamps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons", side_effect=self._configure),
            patch("pcons.pyproject._run_ninja", side_effect=RuntimeError("ninja")),
            pytest.raises(RuntimeError, match="ninja"),
        ):
            backend.build_wheel(str(src / "dist"))
        assert (src / "build" / backend._WHEEL_STAMP).exists()

    def test_unwritable_stamp_does_not_mask_ninja_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        (src / "build" / backend._WHEEL_STAMP).mkdir(parents=True)
        with (
            patch("pcons.pyproject._run_pcons", side_effect=self._configure),
            patch("pcons.pyproject._run_ninja", side_effect=RuntimeError("ninja")),
            pytest.raises(RuntimeError, match="ninja"),
        ):
            backend.build_wheel(str(src / "dist"))

    def test_unreadable_build_ninja_counts_as_absent(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "file"
        not_a_dir.write_text("x")
        assert backend._ninja_file_state(not_a_dir) is None

    def test_editable_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        (src / "build").mkdir()
        (src / "build" / "build.ninja").write_text("# cli\n")
        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            backend.build_editable(str(src / "dist"))
        assert not self._warned(caplog)

    def test_wheel_in_own_build_dir_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "0.1"\n'
            '[tool.pcons]\nbuild-dir = "build-wheel"\n',
        )
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "build.ninja").write_text("# editable\n")
        self._wheel(tmp_path)
        assert not self._warned(caplog)
        assert (tmp_path / "build-wheel" / backend._WHEEL_STAMP).exists()


class TestJobs:
    def test_unset_is_none(self) -> None:
        assert backend._jobs(None, {}) is None
        assert backend._jobs({}, {}) is None

    def test_config_setting_string(self) -> None:
        assert backend._jobs({"jobs": "4"}, {}) == 4

    def test_repeated_config_setting_takes_last(self) -> None:
        assert backend._jobs({"jobs": ["2", "6"]}, {}) == 6

    def test_empty_config_setting_list_falls_back(self) -> None:
        assert backend._jobs({"jobs": []}, {"jobs": 3}) == 3
        assert backend._jobs({"jobs": []}, {}) is None

    def test_pyproject_key(self) -> None:
        assert backend._jobs(None, {"jobs": 3}) == 3

    @pytest.mark.parametrize("value", [[3], [], ["2", "6"]])
    def test_pyproject_list_is_refused(self, value: list[object]) -> None:
        with pytest.raises(RuntimeError, match=r"\[tool.pcons\] jobs must be"):
            backend._jobs(None, {"jobs": value})

    def test_config_setting_wins(self) -> None:
        assert backend._jobs({"jobs": "8"}, {"jobs": 3}) == 8

    @pytest.mark.parametrize("value", ["0", "-1", "four", "2.5", ""])
    def test_bad_config_setting_raises(self, value: str) -> None:
        with pytest.raises(RuntimeError, match="-C jobs must be a positive integer"):
            backend._jobs({"jobs": value}, {})

    @pytest.mark.parametrize("value", [0, -2, True, 2.0, "x"])
    def test_bad_pyproject_key_raises(self, value: object) -> None:
        with pytest.raises(RuntimeError, match=r"\[tool.pcons\] jobs must be"):
            backend._jobs(None, {"jobs": value})

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        _make_pyproject(
            tmp_path,
            '[project]\nname = "mypkg"\nversion = "0.1"\n[tool.pcons]\njobs = 2\n',
        )
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_wheel_passes_jobs_to_ninja(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons"),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ) as mock_ninja,
        ):
            backend.build_wheel(str(src / "dist"), {"jobs": "4"})
        assert mock_ninja.call_args.kwargs["jobs"] == 4

    def test_editable_passes_jobs_to_ninja(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja") as mock_ninja,
        ):
            backend.build_editable(str(src / "dist"))
        assert mock_ninja.call_args.kwargs["jobs"] == 2

    def test_bad_jobs_fails_before_configure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._run_pcons") as mock_pcons,
            pytest.raises(RuntimeError, match="jobs"),
        ):
            backend.build_wheel(str(src / "dist"), {"jobs": "0"})
        mock_pcons.assert_not_called()

    def test_run_ninja_forwards_jobs(self, tmp_path: Path) -> None:
        with patch("pcons.cli.run_ninja", return_value=0) as mock_run:
            backend._run_ninja(tmp_path, targets=["install"], jobs=3)
        mock_run.assert_called_once_with(tmp_path, targets=["install"], jobs=3)


class TestBuildEnvIsTemporary:
    @staticmethod
    def _layout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path, Path]:
        temp = tmp_path / "temp"
        cache = tmp_path / "cache"
        prefix = cache / "builds" / "env"
        pcons_dir = prefix / "site-packages"
        for d in (temp, pcons_dir):
            d.mkdir(parents=True)
        purelib = tmp_path / "target" / "site-packages"
        purelib.mkdir(parents=True)
        real_is_cache_dir = backend._is_cache_dir
        monkeypatch.setattr(backend.tempfile, "gettempdir", lambda: str(temp))
        monkeypatch.setattr(backend.sys, "prefix", str(prefix))
        monkeypatch.setattr(backend.sys, "executable", str(prefix / "bin" / "python"))
        monkeypatch.setattr(
            backend.sysconfig, "get_paths", lambda: {"purelib": str(purelib)}
        )
        monkeypatch.setattr(
            backend,
            "_is_cache_dir",
            lambda path: (
                path.is_relative_to(tmp_path.resolve()) and real_is_cache_dir(path)
            ),
        )
        return temp, cache, prefix

    def test_pcons_under_temp_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        temp, _, _ = self._layout(tmp_path, monkeypatch)
        overlay = temp / "pip-build-env" / "overlay"
        overlay.mkdir(parents=True)
        assert backend._build_env_is_temporary(overlay)

    def test_prefix_inside_tagged_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, cache, prefix = self._layout(tmp_path, monkeypatch)
        (cache / "CACHEDIR.TAG").write_bytes(backend._CACHEDIR_SIGNATURE + b"\n")
        assert backend._build_env_is_temporary(prefix / "site-packages")

    def test_tag_on_prefix_itself_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, prefix = self._layout(tmp_path, monkeypatch)
        (prefix / "CACHEDIR.TAG").write_bytes(backend._CACHEDIR_SIGNATURE)
        assert not backend._build_env_is_temporary(prefix / "site-packages")

    def test_tag_without_signature_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, cache, prefix = self._layout(tmp_path, monkeypatch)
        (cache / "CACHEDIR.TAG").write_text("not a cache\n")
        assert not backend._build_env_is_temporary(prefix / "site-packages")

    def test_symlinked_temp_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._layout(tmp_path, monkeypatch)
        real = tmp_path / "private" / "var"
        pcons_dir = real / "pip-build-env" / "overlay"
        pcons_dir.mkdir(parents=True)
        link = tmp_path / "var"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        monkeypatch.setattr(backend.tempfile, "gettempdir", lambda: str(link))
        assert backend._build_env_is_temporary(pcons_dir.resolve())

    def _pip_isolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        temp, _, _ = self._layout(tmp_path, monkeypatch)
        target = tmp_path / "venv"
        purelib = target / "lib" / "site-packages"
        purelib.mkdir(parents=True)
        monkeypatch.setattr(backend.sys, "prefix", str(target))
        monkeypatch.setattr(backend.sys, "executable", str(target / "bin" / "python"))
        monkeypatch.setattr(
            backend.sysconfig, "get_paths", lambda: {"purelib": str(purelib)}
        )
        overlay = temp / "pip-build-env" / "overlay"
        overlay.mkdir(parents=True)
        return overlay, purelib

    def test_target_env_with_pcons_survives(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overlay, purelib = self._pip_isolation(tmp_path, monkeypatch)
        (purelib / "pcons").mkdir()
        (purelib / "pcons" / "__init__.py").write_text("")
        assert not backend._build_env_is_temporary(overlay)

    def test_target_env_without_pcons_is_temporary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overlay, _ = self._pip_isolation(tmp_path, monkeypatch)
        assert backend._build_env_is_temporary(overlay)

    def test_pcons_inside_target_purelib_is_not_temporary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, purelib = self._pip_isolation(tmp_path, monkeypatch)
        (purelib / "pcons").mkdir()
        (purelib / "pcons" / "__init__.py").write_text("")
        assert not backend._build_env_is_temporary(purelib.resolve() / "pcons")

    def test_interpreter_outside_prefix_is_temporary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overlay, purelib = self._pip_isolation(tmp_path, monkeypatch)
        (purelib / "pcons").mkdir()
        (purelib / "pcons" / "__init__.py").write_text("")
        monkeypatch.setattr(
            backend.sys, "executable", str(tmp_path / "elsewhere" / "python")
        )
        assert backend._build_env_is_temporary(overlay)

    def test_target_env_under_temp_dir_is_temporary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overlay, purelib = self._pip_isolation(tmp_path, monkeypatch)
        (purelib / "pcons").mkdir()
        (purelib / "pcons" / "__init__.py").write_text("")
        monkeypatch.setattr(backend.tempfile, "gettempdir", lambda: str(tmp_path))
        assert backend._build_env_is_temporary(overlay)


class TestIsolationWarning:
    _MESSAGE = "runs in an isolated build environment"

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        _make_pyproject(tmp_path, '[project]\nname = "mypkg"\nversion = "0.1"\n')
        (tmp_path / "pcons-build.py").write_text("# stub")
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def _editable(self, src: Path, probe: MagicMock) -> str:
        with (
            patch("pcons.pyproject._build_env_is_temporary", probe),
            patch("pcons.pyproject._run_pcons"),
            patch("pcons.pyproject._run_ninja"),
        ):
            return backend.build_editable(str(src / "dist"))

    def _warnings(self, caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            r.getMessage() for r in caplog.records if self._MESSAGE in r.getMessage()
        ]

    def test_temporary_env_warns_and_builds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        filename = self._editable(src, MagicMock(return_value=True))
        (message,) = self._warnings(caplog)
        assert "--no-build-isolation" in message
        assert sys.executable in message
        assert str(src / "build") in message
        assert (src / "dist" / filename).exists()

    def test_surviving_env_does_not_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        filename = self._editable(src, MagicMock(return_value=False))
        assert not self._warnings(caplog)
        assert (src / "dist" / filename).exists()

    def test_failing_probe_does_not_warn_or_fail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        filename = self._editable(src, MagicMock(side_effect=RuntimeError("probe")))
        assert not self._warnings(caplog)
        assert (src / "dist" / filename).exists()

    def test_wheel_build_does_not_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = self._setup(tmp_path, monkeypatch)
        with (
            patch("pcons.pyproject._build_env_is_temporary") as probe,
            patch("pcons.pyproject._run_pcons"),
            patch(
                "pcons.pyproject._run_ninja", side_effect=_stage_extension_side_effect
            ),
        ):
            backend.build_wheel(str(src / "dist"))
        probe.assert_not_called()

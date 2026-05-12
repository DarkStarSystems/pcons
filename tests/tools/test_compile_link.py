# SPDX-License-Identifier: MIT
"""Unit tests for pcons.tools.compile_link helpers."""

from __future__ import annotations

from pathlib import Path

from pcons.tools.compile_link import _is_link_input


class TestIsLinkInput:
    """`_is_link_input` classifies a dep output as linker-ready or not.

    The split matters when a code-generator dep produces *both* a
    library and a sibling artifact (e.g., cargo + cbindgen produces a
    .a and a .h). The .a belongs on the link command line; the .h is
    a compile-time dep, not a link input.
    """

    def test_static_libs(self):
        assert _is_link_input(Path("libfoo.a"))
        assert _is_link_input(Path("foo.lib"))

    def test_shared_libs(self):
        assert _is_link_input(Path("libfoo.so"))
        assert _is_link_input(Path("libfoo.dylib"))
        assert _is_link_input(Path("foo.dll"))
        assert _is_link_input(Path("libfoo.tbd"))

    def test_versioned_shared_libs(self):
        # Versioned shared libs use multiple suffixes; the last suffix
        # is just the version number.
        assert _is_link_input(Path("libfoo.so.1"))
        assert _is_link_input(Path("libfoo.so.1.2.3"))
        assert _is_link_input(Path("libfoo.1.dylib"))

    def test_object_files(self):
        assert _is_link_input(Path("foo.o"))
        assert _is_link_input(Path("foo.obj"))

    def test_headers_are_not_link_inputs(self):
        assert not _is_link_input(Path("foo.h"))
        assert not _is_link_input(Path("foo.hpp"))
        assert not _is_link_input(Path("foo.hxx"))

    def test_misc_artifacts_are_not_link_inputs(self):
        assert not _is_link_input(Path("foo.txt"))
        assert not _is_link_input(Path("manifest.json"))
        assert not _is_link_input(Path("script.py"))

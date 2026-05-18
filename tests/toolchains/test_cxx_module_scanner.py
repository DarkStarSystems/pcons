# SPDX-License-Identifier: MIT
"""Unit tests for the configure-time C++ module scanner API.

These tests pre-canned P1689R5 JSON dicts directly into TuScanResult so the
classification logic (is_module_provider, is_interface, logical_name,
required_logical_names), the module-name -> file-path map, and the dyndep
output can be exercised without invoking a real scanner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.toolchains.cxx_module_scanner import (
    CxxModuleScannerNotFound,
    StdModuleFlagSpec,
    TuScanResult,
    TuScanSpec,
    build_module_map,
    module_file_for,
    run_scan_deps,
    run_scan_deps_msvc,
    select_modules_scope,
    select_std_module_flags,
    wire_std_into_targets,
    write_dyndep,
    write_dyndep_from_results,
)


def _spec(obj_rel: str) -> TuScanSpec:
    """Build a minimal scan spec; src and compiler are placeholders."""
    return TuScanSpec(
        src=Path(f"/src/{obj_rel}.cpp"),
        obj_rel=obj_rel,
        compiler="cl.exe",
        compile_flags=[],
    )


def _result(
    obj_rel: str,
    *,
    provides_name: str | None = None,
    is_interface: bool = True,
    requires: list[str] | None = None,
) -> TuScanResult:
    """Build a TuScanResult with a synthesized P1689 payload."""
    rule: dict = {"primary-output": obj_rel}
    if provides_name is not None:
        rule["provides"] = [
            {"logical-name": provides_name, "is-interface": is_interface}
        ]
    if requires:
        rule["requires"] = [{"logical-name": ln} for ln in requires]
    return TuScanResult(spec=_spec(obj_rel), p1689={"rules": [rule]})


class TestTuScanResultClassification:
    def test_consumer_only(self) -> None:
        r = _result("consumer.o", requires=["MyMod"])
        assert not r.is_module_provider
        assert r.logical_name == ""
        assert r.required_logical_names == ["MyMod"]

    def test_primary_interface(self) -> None:
        r = _result("MyMod.o", provides_name="MyMod", is_interface=True)
        assert r.is_module_provider
        assert r.is_interface
        assert r.logical_name == "MyMod"

    def test_partition_interface(self) -> None:
        r = _result("Constants.o", provides_name="Calc:Constants", is_interface=True)
        assert r.is_module_provider
        assert r.is_interface
        assert r.logical_name == "Calc:Constants"

    def test_internal_partition(self) -> None:
        # A `module M:P;` (no export) — scanner reports is-interface=false.
        r = _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False)
        assert r.is_module_provider
        assert not r.is_interface

    def test_failed_scan(self) -> None:
        r = TuScanResult(spec=_spec("foo.o"), p1689=None)
        assert not r.is_module_provider
        assert r.logical_name == ""
        assert r.required_logical_names == []


class TestModuleFileFor:
    def test_primary_module(self) -> None:
        assert (
            module_file_for("MyMod", "cxx_modules", ".pcm") == "cxx_modules/MyMod.pcm"
        )

    def test_partition_replaces_colon(self) -> None:
        # ':' would be an invalid filename character on Windows; replaced with '-'.
        assert (
            module_file_for("Calc:Constants", "cxx_modules", ".ifc")
            == "cxx_modules/Calc-Constants.ifc"
        )


class TestBuildModuleMap:
    def test_only_providers_appear(self) -> None:
        results = [
            _result("MyMod.o", provides_name="MyMod"),
            _result("consumer.o", requires=["MyMod"]),
        ]
        m = build_module_map(results, "cxx_modules", ".ifc")
        assert m == {"MyMod": "cxx_modules/MyMod.ifc"}

    def test_partitions_get_dash_filename(self) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False),
        ]
        m = build_module_map(results, "mods", ".ifc")
        assert m == {
            "Calc": "mods/Calc.ifc",
            "Calc:Constants": "mods/Calc-Constants.ifc",
            "Calc:Helpers": "mods/Calc-Helpers.ifc",
        }


class TestWriteDyndep:
    def test_full_partition_graph(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("Helpers.o", provides_name="Calc:Helpers", is_interface=False),
            _result(
                "main.o",
                requires=["Calc"],
            ),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        write_dyndep_from_results(results, m, out)

        text = out.read_text()
        assert text.startswith("ninja_dyndep_version = 1")
        # Provides are emitted as implicit outputs; requires as implicit inputs.
        assert "build Calc.o | mods/Calc.pcm: dyndep | mods/Calc-Constants.pcm" in text
        assert "build Constants.o | mods/Calc-Constants.pcm: dyndep" in text
        assert "build Helpers.o | mods/Calc-Helpers.pcm: dyndep" in text
        assert "build main.o: dyndep | mods/Calc.pcm" in text

    def test_unresolved_requires_dropped(self, tmp_path: Path) -> None:
        # If a required logical name has no provider in the result set
        # (e.g. `std` before std-module support is wired up), it must be
        # silently dropped rather than emitted as an unbuildable dep.
        results = [_result("user.o", requires=["std"])]
        m = build_module_map(results, "mods", ".ifc")
        out = tmp_path / "deps.dyndep"
        write_dyndep_from_results(results, m, out)
        assert "std" not in out.read_text()

    def test_write_if_unchanged_keeps_mtime(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"

        write_dyndep_from_results(results, m, out)
        first_mtime = out.stat().st_mtime_ns

        # Re-emitting identical content must not rewrite the file.
        write_dyndep_from_results(results, m, out)
        second_mtime = out.stat().st_mtime_ns

        assert first_mtime == second_mtime

    def test_write_creates_digest_file(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"

        write_dyndep_from_results(results, m, out)

        digest_file = tmp_path / "deps.dyndep.sha256"
        assert digest_file.exists()
        assert len(digest_file.read_bytes()) == 32

    def test_stale_digest_same_content_rewrites(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        digest_file = tmp_path / "deps.dyndep.sha256"

        write_dyndep_from_results(results, m, out)
        first_content = out.read_text(encoding="utf-8")

        digest_file.write_bytes(b"\x00" * 32)
        write_dyndep_from_results(results, m, out)

        assert out.read_text(encoding="utf-8") == first_content
        assert digest_file.read_bytes() != b"\x00" * 32

    def test_stale_digest_different_size_rewrites(self, tmp_path: Path) -> None:
        results = [
            _result("Calc.o", provides_name="Calc"),
        ]
        m = build_module_map(results, "mods", ".pcm")
        out = tmp_path / "deps.dyndep"
        digest_file = tmp_path / "deps.dyndep.sha256"

        write_dyndep_from_results(results, m, out)
        out.write_text("x\n", encoding="utf-8")
        digest_file.write_bytes(b"bad\n")

        write_dyndep_from_results(results, m, out)

        assert out.read_text(encoding="utf-8").startswith("ninja_dyndep_version = 1")
        assert len(digest_file.read_bytes()) == 32

    def test_deterministic_output_with_result_reordering(self, tmp_path: Path) -> None:
        base_results = [
            _result("Calc.o", provides_name="Calc", requires=["Calc:Constants"]),
            _result("Constants.o", provides_name="Calc:Constants"),
            _result("main.o", requires=["Calc"]),
        ]
        module_map = build_module_map(base_results, "mods", ".pcm")

        out_a = tmp_path / "a.dyndep"
        out_b = tmp_path / "b.dyndep"

        write_dyndep_from_results(base_results, module_map, out_a)
        write_dyndep_from_results(list(reversed(base_results)), module_map, out_b)

        assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")

    def test_fallback_to_manifest_mod_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate scanner failure for an interface TU: write_dyndep should
        # still emit the manifest-provided module file as an implicit output.
        def _scan_fail(
            scanner: str,
            compiler: str,
            compile_flags: list[str],
            src: str,
            obj: str,
        ) -> None:
            return None

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.run_scan_deps", _scan_fail
        )

        out = tmp_path / "deps.dyndep"
        manifest = [
            {
                "src": str(tmp_path / "MyMod.cppm"),
                "obj": "obj/MyMod.o",
                "is_module_interface": True,
                "pcm": "mods/MyMod.pcm",
                "compiler": "clang++",
                "compile_flags": ["-std=c++20"],
            }
        ]

        write_dyndep(manifest, "mods", str(out), "clang-scan-deps", "clang")
        text = out.read_text(encoding="utf-8")
        assert "ninja_dyndep_version = 1" in text
        assert "build obj/MyMod.o | mods/MyMod.pcm: dyndep" in text


class _FakeCxxNamespace:
    """Stand-in for env.cxx with just the `modules` attribute the helper reads."""

    def __init__(self, modules: bool) -> None:
        self.modules = modules


class _FakeEnv:
    def __init__(self, modules: bool) -> None:
        self.cxx = _FakeCxxNamespace(modules)


class _FakeObj:
    """Stand-in FileNode-ish duck-type for select_modules_scope."""

    def __init__(self, env: _FakeEnv) -> None:
        self._build_info = {"env": env}


class TestSelectModulesScope:
    def test_no_module_extensions_no_optin_skips(self) -> None:
        env = _FakeEnv(modules=False)
        obj = _FakeObj(env)
        # cxx_pairs only — no .cppm/.ixx, env didn't opt in.
        scope = select_modules_scope({"cxx": [(Path("/src/main.cpp"), obj)]})
        assert scope == ([], [])

    def test_extension_implicit_optin_includes_cxx_pairs(self) -> None:
        env = _FakeEnv(modules=False)
        mod_obj = _FakeObj(env)
        cxx_obj = _FakeObj(env)
        # The .cppm in this env qualifies; sibling .cpp files in the same
        # env come along so partition units in .cpp can be detected.
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx_module": [(Path("/src/MyMod.cppm"), mod_obj)],
                "cxx": [(Path("/src/Helper.cpp"), cxx_obj)],
            }
        )
        assert len(m_pairs) == 1
        assert len(c_pairs) == 1

    def test_explicit_optin_without_extensions(self) -> None:
        env = _FakeEnv(modules=True)
        cxx_obj = _FakeObj(env)
        m_pairs, c_pairs = select_modules_scope(
            {"cxx": [(Path("/src/main.cpp"), cxx_obj)]}
        )
        assert m_pairs == []
        assert len(c_pairs) == 1

    def test_other_envs_filtered_out(self) -> None:
        # Two envs in the same project — only one opted in. The other env's
        # TUs must NOT be scanned (would slow the build and may produce
        # spurious flags).
        env_modules = _FakeEnv(modules=True)
        env_plain = _FakeEnv(modules=False)
        m_obj = _FakeObj(env_modules)
        p_obj = _FakeObj(env_plain)
        m_pairs, c_pairs = select_modules_scope(
            {
                "cxx": [
                    (Path("/m.cpp"), m_obj),
                    (Path("/p.cpp"), p_obj),
                ],
            }
        )
        assert m_pairs == []
        assert len(c_pairs) == 1
        assert c_pairs[0][1] is m_obj


class TestScannerNotFound:
    """A missing scanner executable must raise an actionable error.

    Configure used to silently warn and return None when the scanner wasn't
    on PATH; that produced empty dyndep files and confusing downstream
    failures. Now we raise CxxModuleScannerNotFound with install hints.
    """

    def test_clang_scan_deps_missing_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _enoent(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _enoent
        )
        with pytest.raises(CxxModuleScannerNotFound, match="clang-scan-deps"):
            run_scan_deps(
                "clang-scan-deps", "clang++", ["-std=c++20"], "/x/y.cpp", "y.o"
            )

    def test_msvc_cl_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _enoent(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.subprocess.run", _enoent
        )
        with pytest.raises(CxxModuleScannerNotFound, match="vcvars64"):
            run_scan_deps_msvc("cl.exe", ["/std:c++20"], "C:/x/y.cpp")


class _FakeNode:
    """Minimal stand-in for FileNode for wire_std_into_targets tests."""

    def __init__(self) -> None:
        self.explicit_deps: list[_FakeNode] = []


class _FakeTarget:
    def __init__(
        self, intermediates: list[_FakeNode], outputs: list[_FakeNode]
    ) -> None:
        self.intermediate_nodes = intermediates
        self.output_nodes = outputs


class _FakeProject:
    def __init__(self, targets: list[_FakeTarget]) -> None:
        self.targets = targets


class TestWireStdIntoTargets:
    """The shared helper that links synthesized std-module .obj/.o files
    into every target whose TUs `import std;` (or `import std.compat;`).

    Toolchain-agnostic: the same logic is correct for MSVC and clang.
    """

    def test_links_std_into_importing_target(self) -> None:
        consumer_obj = _FakeNode()  # this TU's `import std;` requirement
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        results = [
            TuScanResult(
                spec=consumer_spec,
                p1689={"rules": [{"requires": [{"logical-name": "std"}]}]},
            )
        ]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)

        assert std_obj in target.intermediate_nodes
        assert std_obj in target_output.explicit_deps

    def test_skips_targets_that_do_not_import_std(self) -> None:
        consumer_obj = _FakeNode()
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        # No `requires` — this TU doesn't import std.
        results = [TuScanResult(spec=consumer_spec, p1689={"rules": [{}]})]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        assert std_obj not in target.intermediate_nodes
        assert std_obj not in target_output.explicit_deps

    def test_idempotent(self) -> None:
        # Running twice must not duplicate the std obj on the target.
        consumer_obj = _FakeNode()
        target_output = _FakeNode()
        target = _FakeTarget(intermediates=[consumer_obj], outputs=[target_output])
        project = _FakeProject(targets=[target])

        std_obj = _FakeNode()
        std_obj_nodes = {"std": std_obj}

        consumer_spec = _spec("consumer.o")
        results = [
            TuScanResult(
                spec=consumer_spec,
                p1689={"rules": [{"requires": [{"logical-name": "std"}]}]},
            )
        ]
        spec_to_obj = {id(consumer_spec): consumer_obj}

        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)
        assert target.intermediate_nodes.count(std_obj) == 1
        assert target_output.explicit_deps.count(std_obj) == 1


_CLANG_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"-frtti", "-fno-rtti", "-fexperimental-library"}),
    prefixes=("-std=", "-stdlib=", "-isysroot="),
    paired=frozenset({"-target", "-isysroot"}),
    define_prefix="-D",
    define_glob_prefixes=("_LIBCPP_",),
)


_MSVC_LIKE_SPEC = StdModuleFlagSpec(
    exact=frozenset({"/MD", "/MDd", "/MT", "/MTd", "/EHsc", "/GR-"}),
    prefixes=("/std:", "/Zc:", "/arch:"),
    paired=frozenset(),
    define_prefix="/D",
    define_glob_prefixes=("_HAS_", "_ITERATOR_DEBUG_LEVEL"),
)


class TestSelectStdModuleFlags:
    """Picks ABI-affecting flags from a user's compile flags so the
    std-module compile and consumer TUs agree on the std library's ABI.

    Mismatches here range from silent corruption (mismatched RTTI) to
    iterator heap corruption (`_ITERATOR_DEBUG_LEVEL`) — the spec is
    load-bearing.
    """

    def test_clang_minimum_set(self) -> None:
        # User flags carry the things every std-module compile needs.
        out = select_std_module_flags(
            ["-std=c++23", "-stdlib=libc++", "-O2", "-Wall", "-fno-rtti"],
            _CLANG_LIKE_SPEC,
        )
        assert "-std=c++23" in out
        assert "-stdlib=libc++" in out
        assert "-fno-rtti" in out
        # Optimization and warning flags are not ABI-relevant; they must
        # NOT propagate (or `-Werror` would turn libc++'s deliberate
        # warnings into hard errors).
        assert "-O2" not in out
        assert "-Wall" not in out

    def test_libcxx_define_propagates(self) -> None:
        # `_LIBCPP_HARDENING_MODE` is the canonical example: the std
        # module must be compiled with the same value as consumer TUs,
        # otherwise libc++ ABI varies between them.
        out = select_std_module_flags(
            [
                "-std=c++23",
                "-D_LIBCPP_HARDENING_MODE=fast",
                "-DAPP_VERSION=42",
                "-DFOO",
            ],
            _CLANG_LIKE_SPEC,
        )
        assert "-D_LIBCPP_HARDENING_MODE=fast" in out
        # User-app defines unrelated to libc++ must NOT propagate — they
        # could break the std-module compile or change preprocessor state.
        assert "-DAPP_VERSION=42" not in out
        assert "-DFOO" not in out

    def test_paired_flag_carries_value_token(self) -> None:
        # GCC-style `-target X86_64-...` and `-isysroot /sdk/path`: both
        # halves must propagate together, in order.
        out = select_std_module_flags(
            ["-std=c++23", "-target", "x86_64-apple-darwin", "-O2"],
            _CLANG_LIKE_SPEC,
        )
        i_target = out.index("-target")
        assert out[i_target + 1] == "x86_64-apple-darwin"

    def test_paired_flag_at_end_is_dropped(self) -> None:
        # If a paired flag appears as the last token (no value), drop it
        # rather than spilling off the end.
        out = select_std_module_flags(["-target"], _CLANG_LIKE_SPEC)
        assert out == []

    def test_msvc_runtime_library_propagates(self) -> None:
        # `/MDd` vs `/MD` is the canonical MSVC ABI footgun: a debug-CRT
        # consumer linked with a release-CRT std module is undefined
        # behavior. The spec MUST carry it.
        out = select_std_module_flags(
            ["/std:c++latest", "/MDd", "/Zc:char8_t-", "/Wall", "/O2"],
            _MSVC_LIKE_SPEC,
        )
        assert "/std:c++latest" in out
        assert "/MDd" in out
        assert "/Zc:char8_t-" in out
        assert "/Wall" not in out
        assert "/O2" not in out

    def test_msvc_iterator_debug_level_propagates(self) -> None:
        # `_ITERATOR_DEBUG_LEVEL` mismatch corrupts the heap. Must propagate.
        out = select_std_module_flags(
            ["/std:c++latest", "/D_ITERATOR_DEBUG_LEVEL=2", "/DUSER_FOO=1"],
            _MSVC_LIKE_SPEC,
        )
        assert "/D_ITERATOR_DEBUG_LEVEL=2" in out
        assert "/DUSER_FOO=1" not in out

    def test_preserves_input_order(self) -> None:
        # Order matters for prefixes that override later (e.g., the user
        # writing `-stdlib=libstdc++` after pcons inserts `-stdlib=libc++`).
        out = select_std_module_flags(
            ["-stdlib=libc++", "-std=c++20", "-D_LIBCPP_FOO=1", "-frtti"],
            _CLANG_LIKE_SPEC,
        )
        assert out == [
            "-stdlib=libc++",
            "-std=c++20",
            "-D_LIBCPP_FOO=1",
            "-frtti",
        ]

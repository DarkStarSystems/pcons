# SPDX-License-Identifier: MIT
"""What mixed-toolchain environments put on each tool and each link.

One environment per row of toolchains, on a pinned platform, with each
knob and feature applied, and the inputs a project brings from outside
(packages, imported targets, a library from another environment). The
result is compared with ``mixed_toolchains.snapshot``, checked in beside
this file, so a change in what any of them produces shows up as a diff
to review. Regenerate it with::

    PCONS_UPDATE_SNAPSHOT=1 uv run pytest tests/toolchains/test_mixed_toolchains_snapshot.py

No compiler runs: toolchains are built from their tool classes, and the
probes that would ask a compiler (its target triple, runtime directory,
version) answer with fixed values.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

import pytest

from pcons import Project
from pcons.configure import platform as platform_mod
from pcons.core.environment import Environment
from pcons.core.subst import PathToken, TargetPath
from pcons.packages.description import PackageDescription
from pcons.packages.imported import ImportedTarget
from pcons.tools.toolchain import BaseToolchain, toolchain_registry

SNAPSHOT = Path(__file__).with_name("mixed_toolchains.snapshot")

FEATURES = (
    "warnings",
    "werror",
    "sanitize",
    "profile",
    "lto",
    "hardened",
    "pthread",
    "coverage",
    "fast-math",
    "openmp",
)
VARIANTS = ("debug", "release", "relwithdebinfo")

SOURCES = {
    ".c": "int c_helper(void) { return 1; }\n",
    ".cpp": 'extern "C" int cxx_helper() { return 2; }\n',
    ".f90": "program main\nend program\n",
    ".swift": "public func swiftHelper() -> Int { 3 }\n",
    ".cu": "__global__ void kernel() {}\n",
}


# =============================================================================
# Platforms and toolchains
# =============================================================================


def _platform(os_name: str) -> platform_mod.Platform:
    if os_name == "windows":
        return platform_mod.Platform(
            os="windows",
            arch="x86_64",
            is_64bit=True,
            exe_suffix=".exe",
            shared_lib_suffix=".dll",
            shared_lib_prefix="",
            static_lib_suffix=".lib",
            static_lib_prefix="",
            object_suffix=".obj",
        )
    return platform_mod.Platform(
        os=os_name,
        arch="arm64" if os_name == "darwin" else "x86_64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".dylib" if os_name == "darwin" else ".so",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )


def _ifx_msvc() -> BaseToolchain:
    from pcons.toolchains.ifx import IfxMsvcCompiler, IfxMsvcToolchain
    from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker

    return _built(IfxMsvcToolchain(), IfxMsvcCompiler, MsvcLibrarian, MsvcLinker)


def _ifx_linux() -> BaseToolchain:
    from pcons.toolchains.gcc import GccArchiver
    from pcons.toolchains.ifx import IfxCompiler, IfxLinker, IfxToolchain

    return _built(IfxToolchain(), IfxCompiler, GccArchiver, IfxLinker)


def _built(toolchain: BaseToolchain, *tool_classes: type) -> BaseToolchain:
    toolchain._tools = {}
    for cls in tool_classes:
        tool = cls()
        toolchain._tools[tool.name] = tool
    toolchain._configured = True
    return toolchain


def _make(name: str) -> BaseToolchain:
    """A toolchain with its tools, configured without finding them."""
    if name == "ifx-msvc":
        return _ifx_msvc()
    if name == "ifx":
        return _ifx_linux()
    entry = toolchain_registry.get("swiftc" if name == "swift" else name)
    assert entry is not None, name
    return entry.create_toolchain()


# (platform, primary, added...) — each a row of the snapshot.
ENVIRONMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("linux", ("gcc",)),
    ("linux", ("gcc", "gfortran")),
    ("linux", ("gfortran", "gcc")),
    ("linux", ("llvm", "gfortran")),
    ("linux", ("llvm", "flang")),
    ("linux", ("flang", "llvm")),
    ("linux", ("gcc", "ifx")),
    ("linux", ("gcc", "cuda")),
    ("darwin", ("llvm", "gfortran")),
    ("darwin", ("llvm", "flang")),
    ("darwin", ("swift", "llvm")),
    ("windows", ("gcc", "gfortran")),
    ("windows", ("clang-cl", "flang")),
    ("windows", ("flang", "clang-cl")),
    ("windows", ("msvc", "ifx-msvc")),
    ("windows", ("clang-cl", "ifx-msvc")),
    ("windows", ("llvm", "ifx-msvc")),
    ("windows", ("llvm", "flang")),
)


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Pin the platform and every compiler probe; returns a setter for
    the platform, since it changes per row."""
    from pcons.toolchains import fortran as fortran_mod
    from pcons.toolchains import unix as unix_mod

    monkeypatch.setattr(unix_mod, "target_triple", lambda compiler: None)
    monkeypatch.setattr(
        unix_mod.UnixToolchain, "_compiler_is_apple_clang", lambda self: False
    )
    monkeypatch.setattr(
        fortran_mod.FortranToolchainBase,
        "runtime_libdir",
        property(lambda self: f"/rt/{self.name}"),
    )
    monkeypatch.setattr(
        fortran_mod.FortranToolchainBase,
        "compiler_path",
        property(lambda self: f"/bin/{self.COMPILER.COMMANDS[0]}"),
    )
    from pcons.toolchains import clang_cl as clang_cl_mod
    from pcons.toolchains import ifx as ifx_mod
    from pcons.toolchains import msvc as msvc_mod
    from pcons.toolchains import swift as swift_mod

    # Only ever repoints commands at the host's Visual Studio.
    monkeypatch.setattr(msvc_mod, "use_msvc_tool_paths", lambda env, exes: None)
    monkeypatch.setattr(ifx_mod, "use_msvc_tool_paths", lambda env, exes: None)

    monkeypatch.setattr(
        clang_cl_mod.ClangClToolchain, "_libomp_dir", lambda self: "/rt/libomp"
    )
    monkeypatch.setattr(
        swift_mod.SwiftToolchain, "_runtime_libdirs", lambda self: ["/rt/swift"]
    )
    # GccArchiver takes the gcc-ar beside gcc where there is one.
    from pcons.toolchains import gcc as gcc_mod

    monkeypatch.setattr(gcc_mod, "gcc_archiver_for", lambda driver: None)
    # LlvmArchiver takes llvm-ar where PATH has one.
    which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: None if name == "llvm-ar" else which(name, *a, **k),
    )
    for var in ("CC", "CXX", "FC", "AR", "LD", "SWIFTC", "CUDACXX", "RC"):
        monkeypatch.delenv(var, raising=False)

    def set_platform(os_name: str) -> None:
        monkeypatch.setattr(platform_mod, "_platform", _platform(os_name))

    return set_platform


# =============================================================================
# Rendering
# =============================================================================


def _token(token: Any) -> str:
    if isinstance(token, TargetPath):
        return f"{token.prefix}<target{token.index or ''}>"
    if isinstance(token, PathToken):
        return f"{token.prefix}<{PurePath(str(token.path)).name}>"
    if isinstance(token, (Path, PurePath)):
        return PurePath(token).as_posix()
    path = getattr(token, "path", None)  # ProjectPath and the like
    if path is not None:
        return PurePath(str(path)).as_posix()
    # A path a package gave as /pkg/lib reads \pkg\lib on a Windows host.
    text = str(token)
    return text.replace("\\", "/") if text.startswith("\\") else text


def _tokens(values: Any) -> str:
    return " ".join(_token(v) for v in values or ())


def _cmd(cmd: Any) -> str:
    """A command's program, without the directory PATH put it in, or the
    ``.exe`` a Windows host adds."""
    if not cmd:
        return "-"
    name = PurePath(str(cmd).replace("\\", "/")).name
    return name[:-4] if name.lower().endswith(".exe") else name


def _tool_lines(env: Environment) -> Iterator[str]:
    for name in sorted(env.tool_names()):
        if name in ("install", "archive"):
            continue
        tool = getattr(env, name)
        parts = [f"cmd={_cmd(getattr(tool, 'cmd', None))}"]
        for field in ("flags", "defines", "libs", "libdirs", "includes"):
            value = getattr(tool, field, None)
            if isinstance(value, list) and value:
                parts.append(f"{field}=[{_tokens(value)}]")
        yield f"  {name}: " + " ".join(parts)


def _edge_lines(label: str, node: Any) -> Iterator[str]:
    info = node._build_info or {}
    context = info.get("context")
    env = info.get("env")
    tool = info.get("tool")
    if context is None or env is None or tool is None:
        return
    overrides = context.get_env_overrides()
    base = getattr(env, tool)
    parts = [
        f"tool={tool}",
        f"cmd={_cmd(overrides.get('cmd', getattr(base, 'cmd', None)))}",
        f"flags=[{_tokens(overrides.get('flags', getattr(base, 'flags', [])))}]",
    ]
    if tool == "link":
        parts.append(f"libs=[{_tokens(overrides.get('libs', base.get('libs')))}]")
        libdirs = overrides.get("libdirs", base.get("libdirs"))
        parts.append(f"libdirs=[{_tokens(libdirs)}]")
    yield f"  {label}: " + " ".join(parts)


def _handled(env: Environment) -> list[str]:
    """The sample source suffixes this environment compiles, in order."""
    return [
        suffix
        for suffix in SOURCES
        if any(tc.get_source_handler(suffix) for tc in env.toolchains)
    ]


def _target_lines(project: Project, targets: list[Any]) -> Iterator[str]:
    project.resolve()
    for target in targets:
        for obj in sorted(target.intermediate_nodes, key=lambda n: str(n.path)):
            yield from _edge_lines(f"{target.name}/{obj.path.name}", obj)
        for out in target.output_nodes:
            yield from _edge_lines(f"{target.name}/{out.path.name}", out)


@dataclass
class Row:
    os_name: str
    toolchains: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"{self.os_name} {'+'.join(self.toolchains)}"


# =============================================================================
# Scenarios
# =============================================================================


def _new_project(tmp_path: Path, tag: str) -> Project:
    root = tmp_path / tag
    root.mkdir()
    for suffix, text in SOURCES.items():
        (root / f"src{suffix}").write_text(text)
    return Project("snap", root_dir=root, build_dir="build")


def _environment(project: Project, row: Row, *, name: str = "env") -> Environment:
    primary, *added = row.toolchains
    env = project.Environment(toolchain=_make(primary), name=name)
    for other in added:
        env.add_toolchain(_make(other))
    return env


def _attempt(lines: list[str], action: Callable[[], Iterator[str]]) -> None:
    try:
        lines.extend(action())
    except Exception as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else ""
        lines.append(f"  ERROR {type(e).__name__}: {first}")


def _knob_scenarios(row: Row) -> list[tuple[str, Callable[[Environment], None]]]:
    from pcons.toolchains.presets import linux_cross

    scenarios: list[tuple[str, Callable[[Environment], None]]] = [
        ("baseline", lambda env: None),
    ]
    for variant in VARIANTS:
        scenarios.append(
            (f"variant {variant}", lambda env, v=variant: env.set_variant(v))
        )
    for feature in FEATURES:
        scenarios.append(
            (f"feature {feature}", lambda env, f=feature: env.apply_preset(f))
        )
    if any(t in ("gcc", "llvm", "clang-cl", "msvc", "swift") for t in row.toolchains):
        scenarios.append(("cxx standard 20", lambda env: env.cxx.set_standard("c++20")))
    if row.os_name == "darwin":
        scenarios.append(("arch x86_64", lambda env: env.set_target_arch("x86_64")))
    if row.os_name != "windows":
        scenarios.append(
            (
                "cross aarch64-linux-gnu",
                lambda env: env.apply_cross_preset(linux_cross("aarch64-linux-gnu")),
            )
        )
    return scenarios


def _knob_section(tmp_path: Path, row: Row) -> Iterator[str]:
    for i, (label, apply) in enumerate(_knob_scenarios(row)):
        lines = [f"### {row.title} | {label}"]

        def run(
            apply: Callable[[Environment], None] = apply, i: int = i
        ) -> Iterator[str]:
            project = _new_project(tmp_path, f"k{i}")
            env = _environment(project, row)
            apply(env)
            yield from _tool_lines(env)
            suffixes = _handled(env)
            app = project.Program("app", env, sources=[f"src{s}" for s in suffixes])
            lib = project.SharedLibrary(
                "lib",
                env,
                sources=[f"src{s}" for s in suffixes if s != ".f90"]
                or [f"src{suffixes[0]}"],
            )
            targets = [app, lib]
            if ".f90" in suffixes and len(suffixes) > 1:
                # Fortran alone: linked by the Fortran toolchain's driver
                # where it can read the link tool's command line.
                targets.append(project.Program("fmain", env, sources=["src.f90"]))
            yield from _target_lines(project, targets)

        _attempt(lines, run)
        yield from lines


def _late_section(tmp_path: Path, row: Row) -> Iterator[str]:
    """Knobs applied before add_toolchain(): what the added toolchain gets."""
    from pcons.toolchains.presets import linux_cross

    if len(row.toolchains) < 2:
        return
    late: list[tuple[str, Callable[[Environment], None]]] = [
        ("variant debug", lambda env: env.set_variant("debug")),
        ("feature warnings", lambda env: env.apply_preset("warnings")),
    ]
    if row.os_name != "windows":
        late.append(
            (
                "cross aarch64-linux-gnu",
                lambda env: env.apply_cross_preset(linux_cross("aarch64-linux-gnu")),
            )
        )
    primary, *added = row.toolchains
    for i, (label, apply) in enumerate(late):
        lines = [f"### {row.title} | before add_toolchain: {label}"]

        def run(
            apply: Callable[[Environment], None] = apply, i: int = i
        ) -> Iterator[str]:
            project = _new_project(tmp_path, f"l{i}")
            env = project.Environment(toolchain=_make(primary), name="env")
            apply(env)
            for other in added:
                env.add_toolchain(_make(other))
            yield from _tool_lines(env)

        _attempt(lines, run)
        yield from lines


PACKAGE = PackageDescription(
    name="pkg",
    include_dirs=["/pkg/include"],
    defines=["PKG_DEFINE"],
    compile_flags=["-pthread"],
    library_dirs=["/pkg/lib"],
    libraries=["pkg"],
    link_flags=["-Wl,--as-needed"],
)


def _upstream_section(tmp_path: Path, row: Row) -> Iterator[str]:
    """Inputs from outside the environment's own toolchains."""
    if row.os_name != "linux" or len(row.toolchains) < 2:
        return

    def used(project: Project, env: Environment) -> list[Any]:
        env.use(PACKAGE)
        return [project.Program("app", env, sources=[f"src{s}" for s in _handled(env)])]

    def imported(project: Project, env: Environment) -> list[Any]:
        pkg = ImportedTarget.from_package(PACKAGE)
        app = project.Program("app", env, sources=[f"src{s}" for s in _handled(env)])
        return [app.link(pkg)]

    def other_env(project: Project, env: Environment) -> list[Any]:
        # A static library from each single-toolchain environment of the
        # row's toolchains, linked into a program of the first one alone.
        libs = []
        for i, name in enumerate(row.toolchains):
            lib_env = project.Environment(toolchain=_make(name), name=f"lib{i}")
            suffix = _handled(lib_env)[0]
            libs.append(
                project.StaticLibrary(f"lib{i}", lib_env, sources=[f"src{suffix}"])
            )
        linking = [n for n in row.toolchains if "link" in _make(n).tools]
        app_env = project.Environment(toolchain=_make(linking[-1]), name="app")
        suffix = _handled(app_env)[0]
        app = project.Program("app", app_env, sources=[f"src{suffix}"])
        for lib in libs:
            app.link(lib)
        return [app]

    for i, (label, build) in enumerate(
        (
            ("env.use(package)", used),
            ("linked ImportedTarget", imported),
            ("static libraries from single-toolchain environments", other_env),
        )
    ):
        lines = [f"### {row.title} | upstream: {label}"]

        def run(
            build: Callable[..., list[Any]] = build, i: int = i, tools: bool = i == 0
        ) -> Iterator[str]:
            project = _new_project(tmp_path, f"u{i}")
            env = _environment(project, row)
            targets = build(project, env)
            if tools:  # env.use() changes the tools; the others, the edges
                yield from _tool_lines(env)
            yield from _target_lines(project, targets)

        _attempt(lines, run)
        yield from lines


def _conan_section(tmp_path: Path, row: Row) -> Iterator[str]:
    """The Conan profile a script gets, passing env.toolchain or env alone."""
    from pcons.packages.finders import conan as conan_mod

    if row.os_name != "linux" or len(row.toolchains) < 2:
        return
    for i, how in enumerate(("env.toolchain", "env")):
        lines = [f"### {row.title} | conan profile from {how}"]

        def run(i: int = i, how: str = how) -> Iterator[str]:
            project = _new_project(tmp_path, f"c{i}")
            env = _environment(project, row)
            env.set_variant("debug")
            if env.has_tool("cxx"):
                env.cxx.set_standard("c++20")
            finder = conan_mod.ConanFinder(output_folder=tmp_path / f"c{i}" / "deps")
            finder._platform = _platform("linux")
            finder._detect_compiler_version = lambda *a, **k: "16"  # type: ignore[method-assign]
            toolchain = env.toolchain if how == "env.toolchain" else None
            profile = finder.sync_profile(toolchain, env=env)
            for text in profile.read_text().splitlines():
                if text.strip():
                    key, _, value = text.partition("=")
                    yield (
                        f"  {key}={_cmd(value)}"
                        if key in ("CC", "CXX")
                        else f"  {text}"
                    )

        _attempt(lines, run)
        yield from lines


def _snapshot(tmp_path: Path, set_platform: Callable[[str], None]) -> str:
    out: list[str] = [
        "# Generated by test_mixed_toolchains_snapshot.py; see its docstring.",
        "",
    ]
    for n, (os_name, toolchains) in enumerate(ENVIRONMENTS):
        row = Row(os_name, toolchains)
        set_platform(os_name)
        out.append(f"## {row.title}")
        row_dir = tmp_path / f"row{n}"
        row_dir.mkdir()
        for section in (
            _knob_section,
            _late_section,
            _upstream_section,
            _conan_section,
        ):
            sub = row_dir / section.__name__
            sub.mkdir()
            out.extend(section(sub, row))
        out.append("")
    return "\n".join(out)


def test_mixed_toolchains_snapshot(tmp_path: Path, pinned, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    actual = _snapshot(tmp_path, pinned)
    if os.environ.get("PCONS_UPDATE_SNAPSHOT"):
        SNAPSHOT.write_text(actual)
        return
    expected = SNAPSHOT.read_text() if SNAPSHOT.exists() else ""
    assert actual == expected, (
        "Mixed-toolchain output changed. Review the difference, and if it's "
        "intended regenerate the snapshot with PCONS_UPDATE_SNAPSHOT=1."
    )

# SPDX-License-Identifier: MIT
"""MkDocs macros hook — exposes template variables in markdown.

Variables:
    {{ version }}          — version string with optional git dev info
    {{ toolchain_table }}  — auto-generated markdown table of registered toolchains
    {{ builder_table }}    — auto-generated markdown table of registered builders
"""

import re
import subprocess
import sys
from pathlib import Path


def _get_version() -> str:
    """Get version string, with git info for unreleased builds.

    On a tagged release:  "0.6.0"
    Past a tag:           "0.6.0.dev3 (g9abe7cc, 2026-01-30)"
    No tags at all:       "0.6.0.dev (9abe7cc, 2026-01-30)"
    Git unavailable:      "0.6.0"
    """
    # Parse version from pcons/__init__.py without importing
    init_file = Path(__file__).parent.parent / "pcons" / "__init__.py"
    version = "unknown"
    for line in init_file.read_text().splitlines():
        m = re.match(r'^__version__\s*=\s*["\']([^"\']+)["\']', line)
        if m:
            version = m.group(1)
            break

    # Try git describe to detect unreleased commits
    try:
        desc = subprocess.check_output(
            ["git", "describe", "--tags", "--long", "--always"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # git describe --long gives "v0.6.0-3-g9abe7cc" or just "9abe7cc"
        m = re.match(r"v?[\d.]+-(\d+)-g([0-9a-f]+)", desc)
        if m:
            commits_past = int(m.group(1))
            short_hash = m.group(2)
            if commits_past > 0:
                # Get commit date
                date = subprocess.check_output(
                    ["git", "log", "-1", "--format=%cs"],
                    cwd=Path(__file__).parent.parent,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                return f"{version}.dev{commits_past} ({short_hash}, {date})"
        # else: exactly on a tag, just use __version__
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return version


# ── Platform name mapping ────────────────────────────────────────────────────

_PLATFORM_DISPLAY = {
    "linux": "Linux",
    "darwin": "macOS",
    "win32": "Windows",
}


def _format_platforms(platforms: list[str]) -> str:
    """Convert sys.platform values to human-readable names."""
    if not platforms:
        return "Any"
    return ", ".join(_PLATFORM_DISPLAY.get(p, p) for p in platforms)


# ── Toolchain table ──────────────────────────────────────────────────────────


def _get_toolchain_table() -> str:
    """Generate a markdown table of all registered toolchains."""
    # Ensure pcons package is importable
    pcons_root = Path(__file__).parent.parent
    if str(pcons_root) not in sys.path:
        sys.path.insert(0, str(pcons_root))

    from pcons.tools.toolchain import toolchain_registry

    # Import contrib toolchains so their registrations appear in the table
    import pcons.contrib.latex.toolchain  # noqa: F401

    # Collect unique toolchains (each may be registered under multiple aliases)
    seen_classes: set[type] = set()
    rows: list[dict[str, str]] = []

    for entry in toolchain_registry._toolchains.values():
        if entry.toolchain_class in seen_classes:
            continue
        seen_classes.add(entry.toolchain_class)
        finder = f"`{entry.finder}`" if entry.finder else ""
        rows.append({
            "name": entry.toolchain_class.__name__.removesuffix("Toolchain"),
            "aliases": ", ".join(f"`{a}`" for a in entry.aliases),
            "category": entry.category,
            "check_command": f"`{entry.check_command}`",
            "platforms": _format_platforms(entry.platforms),
            "description": entry.description,
            "finder": finder,
        })

    # Sort: C toolchains first (GCC/LLVM before MSVC/Clang-CL), then others
    category_order = {"c": 0, "cuda": 1, "wasm": 2, "python": 3}
    # Within C category, prefer well-known names first
    c_name_order = {"Gcc": 0, "Llvm": 1, "Msvc": 2, "ClangCl": 3}
    rows.sort(key=lambda r: (
        category_order.get(r["category"], 99),
        c_name_order.get(r["name"], 99),
        r["name"],
    ))

    # Build markdown table
    lines = [
        "| Toolchain | Finder | Platforms | Description |",
        "|-----------|--------|-----------|-------------|",
    ]
    for r in rows:
        lines.append(
            f"| **{r['name']}** | {r['finder']} "
            f"| {r['platforms']} | {r['description']} |"
        )

    return "\n".join(lines)


# ── Builder table ────────────────────────────────────────────────────────────


def _get_builder_table() -> str:
    """Generate a markdown table of all registered builders."""
    pcons_root = Path(__file__).parent.parent
    if str(pcons_root) not in sys.path:
        sys.path.insert(0, str(pcons_root))

    from pcons.core.builder_registry import BuilderRegistry

    rows: list[dict[str, str]] = []
    for name, reg in sorted(BuilderRegistry.all().items()):
        # Clean up the description: first sentence only
        desc = reg.description.strip().split("\n")[0].rstrip(".")
        platforms = _format_platforms(reg.platforms) if reg.platforms else "All"
        rows.append({
            "name": name,
            "target_type": reg.target_type.name.replace("_", " ").title(),
            "platforms": platforms,
            "description": desc,
        })

    lines = [
        "| Builder | Type | Platforms | Description |",
        "|---------|------|-----------|-------------|",
    ]
    for r in rows:
        # Use non-breaking spaces in method name to prevent wrapping
        method = f"project.{r['name']}()"
        lines.append(
            f"| `{method}` | {r['target_type']} "
            f"| {r['platforms']} | {r['description']} |"
        )

    return "\n".join(lines)


# ── MkDocs entry point ───────────────────────────────────────────────────────


def define_env(env):
    """Define template variables for mkdocs-macros."""
    env.variables["version"] = _get_version()
    env.variables["toolchain_table"] = _get_toolchain_table()
    env.variables["builder_table"] = _get_builder_table()

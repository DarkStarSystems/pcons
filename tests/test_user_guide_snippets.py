# SPDX-License-Identifier: MIT
"""Build every code snippet of the guide's PyAction section.

Phase 1 of this feature shipped a snippet claiming two environments worked
"with no extra work" when the second was in fact refused. Nothing read the
guide, so nothing noticed. This does: it extracts the fenced ``python``
blocks from that section, wraps each in the preamble its own text says it
needs, and runs ``pcons generate`` and ``ninja`` on the result.

A snippet is a promise that the reader can type it. This is what keeps the
promise mechanical instead of point-in-time.

The pipeline snippet really downloads a document, so it is skipped unless
``PCONS_TEST_NETWORK=1``. The default suite must pass with no network at all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "user-guide.md"
SECTION_START = "### Python Functions as Build Steps: env.PyAction()"
SECTION_END = "### Post-Build Commands"

COMMON = """from pcons import Project

project = Project("snippet")
env = project.Environment({toolchain})
host = env
src = project.root_dir / "src"
"""

REPORT_ACTION = """

@env.PyAction()
def report(sources, targets, title):
    from pathlib import Path

    lines = [title]
    for name in sources:
        text = Path(name).read_text(encoding="utf-8")
        lines.append(f"{Path(name).name}: {len(text.split())}")
    Path(targets[0]).write_text("\\n".join(lines) + "\\n", encoding="utf-8")
"""

PIPELINE_ACTIONS = """

@env.PyAction()
def fetch(sources, targets, url, field):
    import json
    from pathlib import Path
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "pcons-example"})
    raw = ""
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw)
        for key in field.split("."):
            payload = payload[key]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"{url} could not be fetched: {exc}. Got {raw[:200]!r}")
    Path(targets[0]).write_bytes(payload.encode("utf-8"))


@env.PyAction()
def embed(sources, targets, symbol):
    from pathlib import Path

    data = Path(sources[0]).read_bytes()
    body = ", ".join(str(byte) for byte in data)
    Path(targets[0]).write_text(
        f"static const unsigned char {symbol}[] = {{{body}}};\\n"
        "int main(void) { return 0; }\\n",
        encoding="utf-8",
    )


name = "lorem"
url = "https://www.lipsum.com/feed/json?amount=2&what=paras&start=yes"
"""


def snippets() -> list[str]:
    """Every fenced python block of the PyAction section, in order."""
    text = GUIDE.read_text(encoding="utf-8")
    body = text[text.index(SECTION_START) : text.index(SECTION_END)]
    return re.findall(r"```python\n(.*?)```", body, re.S)


def preamble(body: str) -> str:
    """What a snippet needs around it, read from the snippet itself.

    Keyed on the snippet's own text rather than on its position, so adding
    one in the middle of the section does not silently give its neighbours
    the wrong setup.
    """
    toolchain = 'toolchain="c"' if "Program(" in body else ""
    parts = [COMMON.format(toolchain=toolchain)]
    if "report(" in body and "def report(" not in body:
        parts.append(REPORT_ACTION)
    if ("fetch(" in body or "embed(" in body) and "def fetch(" not in body:
        parts.append(PIPELINE_ACTIONS)
    return "".join(parts)


def needs_network(body: str) -> bool:
    """Whether building this snippet reaches somebody else's machine."""
    return "fetch(" in body


def label(body: str) -> str:
    """A test id a reader can match against the guide."""
    first = next(line for line in body.splitlines() if line.strip())
    return first.strip()[:60]


ALL = snippets()

pytestmark = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)


def test_the_section_still_has_snippets() -> None:
    """A regex that silently matches nothing would make every test below pass."""
    assert len(ALL) >= 5


@pytest.mark.parametrize("body", ALL, ids=[label(b) for b in ALL])
def test_a_snippet_builds(body: str, tmp_path: Path) -> None:
    if needs_network(body) and os.environ.get("PCONS_TEST_NETWORK") != "1":
        pytest.skip("downloads a document; set PCONS_TEST_NETWORK=1 to run it")

    (tmp_path / "src").mkdir()
    for letter, words in (("a", 4), ("b", 7), ("c", 6), ("d", 5)):
        (tmp_path / "src" / f"{letter}.txt").write_text(" ".join(["w"] * words) + "\n")
    (tmp_path / "pcons-build.py").write_text(preamble(body) + "\n" + body)

    generate = subprocess.run(
        [sys.executable, "-m", "pcons", "generate", "-B", "build"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generate.returncode == 0, generate.stderr or generate.stdout

    build = subprocess.run(
        ["ninja", "-C", "build"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert any((tmp_path / "build").rglob("*")), "the snippet built nothing"

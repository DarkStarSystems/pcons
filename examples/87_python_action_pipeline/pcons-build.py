# SPDX-License-Identifier: MIT
"""Two PyActions, each called twice, feeding a real compile and link.

``fetch`` downloads a JSON document and writes one field of it to a file.
``embed`` turns that file's bytes into a C program that writes them back out.
``project.Program`` compiles and links the result. Nothing here is declared
twice: ``embed`` takes the ``Target`` that ``fetch`` returned as its
``source=``, and ``project.Program`` takes the ``Target`` that ``embed``
returned, which is how ninja learns the order.

Each action is decorated once and called twice, once per document. That is
the point of a builder: two edges, one generated module, one argument pickle
per edge. The arguments differ, the body does not.

Why every call passes ``name=``: an edge is named after its first target's
stem, and the pickle is named after the edge. Here ``lorem.txt`` and
``lorem.c`` share the stem ``lorem``, and so does the program, so three edges
of one chain would want one pickle. ``name=`` parts them, and it is also what
``ninja lorem-text`` then means.

Why a byte array rather than a C string literal: the fetched document is
arbitrary bytes. Escaping it into a string literal means handling quotes,
backslashes, newlines, trigraphs and anything non-ASCII, a rabbit hole that
teaches nothing about PyAction. A byte array is four lines and is correct for
any input, a NUL byte included. ``src/awkward.bin`` is a third chain that
proves it, with no network in the way: it holds a NUL, an invalid UTF-8 byte,
a quote, a backslash, a trigraph and an escape.

Why the program writes to a file named on its command line rather than to
stdout: stdout is a text stream on Windows, so a newline would come out as
two bytes and the comparison would fail there and nowhere else.

Why the request sets a User-Agent: lipsum.com answers an empty ``text/html``
body, with status 200, to the default ``Python-urllib`` agent, and the real
JSON to anything else. Without it the build fails at ``json.loads`` on an
empty string, which is why the parse is wrapped to name the URL.

This example reaches the network, the way ``07_conan_example`` does. There is
no offline fallback on purpose: a fallback would make the example build two
different things depending on the machine.
"""

from pcons import Project

LOREM_URL = "https://www.lipsum.com/feed/json?amount=3&what=paras&start=yes"
MOTTO_URL = "https://www.lipsum.com/feed/json?amount=12&what=words"

project = Project("python_action_pipeline")
env = project.Environment(toolchain="c")


@env.PyAction()
def fetch(sources, targets, url, field):
    import json
    from pathlib import Path
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "pcons-example"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(
            f"{url} did not answer JSON: {exc}. Got {raw[:200]!r}"
        ) from exc
    for key in field.split("."):
        payload = payload[key]
    Path(targets[0]).write_text(payload, encoding="utf-8")


@env.PyAction()
def embed(sources, targets, symbol):
    from pathlib import Path

    data = Path(sources[0]).read_bytes()
    body = ", ".join(str(byte) for byte in data)
    Path(targets[0]).write_text(
        "#include <stdio.h>\n\n"
        f"static const unsigned char {symbol}[] = {{{body}}};\n\n"
        "int main(int argc, char **argv) {\n"
        "    if (argc < 2) return 1;\n"
        '    FILE *out = fopen(argv[1], "wb");\n'
        "    if (!out) return 1;\n"
        f"    fwrite({symbol}, 1, sizeof {symbol}, out);\n"
        "    return fclose(out) == 0 ? 0 : 1;\n"
        "}\n",
        encoding="utf-8",
    )


for name, url in (("lorem", LOREM_URL), ("motto", MOTTO_URL)):
    text = fetch(
        target=project.build_dir / f"{name}.txt",
        name=f"{name}-text",
        url=url,
        field="feed.lipsum",
    )
    source = embed(
        target=project.build_dir / f"{name}.c",
        name=f"{name}-source",
        source=[text],
        symbol=name,
    )
    project.Default(project.Program(name, env, sources=[source]))

local = embed(
    target=project.build_dir / "awkward.c",
    name="awkward-source",
    source=[project.root_dir / "src" / "awkward.bin"],
    symbol="awkward",
)
project.Default(project.Program("awkward", env, sources=[local]))

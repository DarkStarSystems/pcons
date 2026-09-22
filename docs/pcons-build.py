# SPDX-License-Identifier: MIT
"""Build the pcons documentation site, with pcons.

The site is mkdocs-based (mkdocs.yml at the repo root); ReadTheDocs and
`make docs-site` invoke mkdocs themselves. This wraps the same build so
`pcons -C docs` produces it too, into docs/build/site/, and rebuilds only
when a page or the configuration changed.

The custom-tools pipeline that used to live here is now
examples/69_custom_tools_pipeline.
"""

import sys
from pathlib import Path

from pcons import Project

project = Project("pcons-docs")
env = project.Environment()

# Every page is an input, so an edit rebuilds the site and an untouched
# tree is a no-op. Globbed at configure time: a brand-new page appears on
# the next pcons run (page *edits* need only ninja). Resolved, because
# some pages are symlinks (architecture.md -> ../ARCHITECTURE.md) and the
# real dependency is the file behind the link.
pages = sorted(p.resolve() for p in Path(".").glob("*.md"))
config = [Path("../mkdocs.yml").resolve(), Path("macros.py").resolve()]

# The site goes where every other way of building it puts it: site/ at
# the repo root (gitignored). It cannot live under docs/ — mkdocs refuses
# a site_dir inside its docs_dir — and mkdocs resolves -d relative to the
# config file, not the working directory, so the location is absolute.
site_dir = Path("../site").resolve()

site = env.Command(
    target=site_dir / "sitemap.xml",
    source=[*pages, *config],
    command=[
        sys.executable,
        "-m",
        "mkdocs",
        "build",
        "--strict",
        "-f",
        "$SRCDIR/../mkdocs.yml",
        "-d",
        str(site_dir),
    ],
)

project.Default(site)


@project.cli_command()
def dev() -> None:
    """Serve the documentation site in a browser, live-reloading on edits.

    Serves over HTTP rather than opening the files directly, because
    mkdocs-material needs a real server for parts of its behavior. The
    server is mkdocs' own livereload server (the one behind `mkdocs
    serve`), so a saved edit rebuilds the site through pcons and the
    browser refreshes itself. Ctrl-C to stop.
    """
    import subprocess

    from mkdocs.livereload import LiveReloadServer

    def rebuild() -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pcons", "build"], cwd=project.root_dir
        )
        if result.returncode:
            print("docs build failed; fix the page and save again")

    server = LiveReloadServer(
        builder=rebuild, host="127.0.0.1", port=8642, root=str(site_dir)
    )
    # Watch exactly the build's declared inputs, so the server and ninja
    # agree on what a relevant edit is (and the build tree is not watched,
    # which would re-trigger on every rebuild).
    for path in [*pages, *config]:
        server.watch(str(path), recursive=False)

    try:
        server.serve(open_in_browser=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


dev.depends(site)

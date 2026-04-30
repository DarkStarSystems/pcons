# Rez integration example

Demonstrates pcons's two-sided integration with [rez](https://rez.readthedocs.io):

| What it shows | How to run |
| --- | --- |
| **Pcons reads the rez resolve** — `pcons-build.py` calls `rez_environment(env)` to pick up include/lib paths from every resolved rez package. | `rez-env hello_lib -- uvx pcons` (from this directory) |
| **Rez drives pcons** — rez auto-detects `pcons-build.py` in `rez_packages/hello_app/` and uses pcons's `build_system` plugin. | `cd rez_packages/hello_app && rez-build -i` |

## One-time setup

Skip this if you already have rez running with a local packages path.

### Install rez

Use rez's official installer — see [rez's installation
guide](https://rez.readthedocs.io/en/stable/installation.html) for the
authoritative version. Quick walk-through:

```bash
git clone https://github.com/AcademySoftwareFoundation/rez
python rez/install.py /opt/rez       # any path works; ~/rez also fine

export PATH=/opt/rez/bin/rez:$PATH    # add to your shell profile
# macOS: keep this dir ahead of /usr/bin (a Carbon-era /usr/bin/Rez
# will otherwise shadow rez on case-insensitive filesystems).
rez --version
```

### Configure a local packages path

```bash
mkdir -p ~/rez_packages
cat > ~/.rezconfig <<'EOF'
packages_path:
  - ~/rez_packages
local_packages_path: ~/rez_packages
release_packages_path: ~/rez_packages
EOF
rez-config packages_path  # should list ~/rez_packages
```

### Make the pcons rez plugin discoverable

For rez to pick up packages declaring `build_system = "pcons"`, pcons
must live in rez's bundled Python venv. Use `rez-python -m pip`:

```bash
# Production:
/opt/rez/bin/rez/rez-python -m pip install pcons

# Or, for development against a local pcons checkout:
/opt/rez/bin/rez/rez-python -m pip install -e /path/to/pcons

rez-build --help  # should list "pcons" under -b {make,pcons}
```

`rez-python` is rez's wrapped Python interpreter — installing into it
puts pcons on the same `sys.path` rez sees during plugin discovery.

### Build the test rez packages

```bash
cd rez_packages/hello_lib && rez-build -i && cd ../..
cd rez_packages/hello_app && rez-build -i && cd ../..
rez-env hello_app -- hello_app
# → Hello, rez, from rez-resolved hello_lib!
```

`hello_lib` is built with rez's built-in `cmake` build_system; `hello_app`
is built with the `pcons` build_system plugin.

## Troubleshooting

If `rez-build` fails on `hello_app` (or any package declaring
`build_system = "pcons"`) with a traceback ending in:

```
rez.exceptions.RezPluginError: Unrecognised build system plugin: 'pcons'
```

…pcons isn't installed in rez's bundled Python venv. Re-run the
[plugin setup step](#make-the-pcons-rez-plugin-discoverable) — that
puts pcons on the same `sys.path` rez uses for plugin discovery.

The traceback comes out raw because rez's argparse subparser setup
runs *outside* its own error formatter; this same shape happens for
any unregistered or misspelled `build_system` value, including
built-in ones.

## Running this example

The top-level `pcons-build.py` in this directory is a plain pcons script
that uses `rez_environment(env)` to pick up `hello_lib` from the rez
resolve:

```bash
rez-env hello_lib -- uvx pcons
./build/rez_demo
# → Hello, rez (env-only), from rez-resolved hello_lib!
```

Outside a rez shell the script prints usage instructions and exits
cleanly — it doesn't try to build.

## What's in this directory

```
35_rez_integration/
├── README.md            # this file
├── pcons-build.py       # pcons reads the rez resolve
├── test.toml            # CI test config
├── src/main.cpp
└── rez_packages/
    ├── hello_lib/       # rez package built with rez's cmake plugin
    │   ├── package.py
    │   ├── CMakeLists.txt
    │   ├── include/hello_lib.h
    │   └── src/hello_lib.cpp
    └── hello_app/       # rez package built with pcons's build_system plugin
        ├── package.py   # build_system = "pcons"
        ├── pcons-build.py
        └── src/main.cpp
```

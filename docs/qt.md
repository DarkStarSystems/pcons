# Qt

pcons has first-class Qt 6 support: discovery of Qt modules and tools,
automatic moc/uic/rcc, and high-level builders that make a Qt Widgets
application a five-line build script.

```python
from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("myapp")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Widgets", "Network"])

app = project.QtProgram(
    "myapp",
    env,
    sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
    link=[qt.Widgets, qt.Network],
)
```

That's the whole build. `.ui` and `.qrc` files go straight into
`sources`; classes with `Q_OBJECT` are found automatically; all platform
quirks (macOS frameworks, MSVC's `/Zc:__cplusplus /permissive-`, Windows
debug `d`-suffix libraries) are handled by `find_qt`.

## How it's better than CMake's AUTOMOC

pcons deliberately fixes the well-known pain points of CMake + Qt:

| CMake + Qt | pcons |
|---|---|
| Opaque `<target>_autogen` step; mystery rebuilds | Every moc/uic/rcc run is a plain, visible ninja edge (`ninja -t commands`) |
| `mocs_compilation.cpp` aggregate: touching one moc'ed header recompiles all moc output | Each `moc_*.cpp` is its own translation unit |
| Build-time source scanning on every build | Scan happens once, when pcons generates; builds run zero scanning |
| Silent no-op when a `.cpp` has `Q_OBJECT` but no `#include "foo.moc"` | Hard error at generate time, with the exact line to add |
| Adding `Q_OBJECT` without re-running CMake → undefined-vtable link errors | A cheap guard edge fails the build with *"re-run pcons"* naming the file |

Incremental correctness comes from the tools' own depfiles: moc runs with
`--output-dep-file` (re-runs when any transitively-included header
changes), rcc with `--depfile` (re-runs when a file listed in the .qrc
changes), and uic is a pure `input → output` rule.

## Discovery: find_qt()

```python
qt = find_qt(
    project,
    env,
    modules=["Widgets"],  # short names; Core is always included
    version=">=6.4",  # optional constraint
    qt_root="/opt/Qt/6.7.0/gcc_64",  # optional; also $PCONS_QT_ROOT
    probe="auto",  # "auto" | "pkg-config" | "qtpaths"
    private_headers=["Core"],  # opt-in to QtCore/x.y.z/private
)

qt.version  # "6.9.3"
qt.Widgets  # ImportedTarget — use in link=[...] or app.link(...)
qt.tool_path("lupdate")
```

Probing order:

1. **pkg-config** (`Qt6Core.pc`, `Qt6Widgets.pc`, ...) — present on Linux
   distributions and Homebrew macOS; handles framework linking.
2. **qtpaths/qmake introspection** (`qtpaths6 -query`) — for installs
   without pkg-config files, e.g. the official Qt installer and Windows.
   This route resolves inter-module dependencies from a built-in table, so
   asking for `Qml` also brings in `Network` and `QmlIntegration`. The
   latter is header-only: it contributes an include directory, which every
   `QML_ELEMENT` header needs, and links nothing.

### Choosing the probe

`probe=` runs one of them instead of both. `"auto"` is the default and the
order above; `"pkg-config"` and `"qtpaths"` run that probe alone and fail
if it finds nothing.

A cross Qt is the reason this exists. Such an install has two halves: the
libraries and headers you link against, built for the target, and the
moc/uic/rcc binaries, built for the machine running the build. pkg-config
cannot describe that split. Its `libexecdir` is `${prefix}/bin`, which for
a Windows target Qt holds Windows executables, so `qt.tool_path("moc")`
comes back `None` and nothing explains why. `qtpaths6 -query` on the same
install reports `QT_HOST_BINS` and `QT_HOST_LIBEXECS`, which is where the
runnable tools are, and the qtpaths probe prefers them.

So when the target Qt ships complete `.pc` files, pkg-config wins the
`"auto"` race with the wrong answer, and the way out is to ask for the
other probe:

```python
host_qt = find_qt(project, host, modules=["Widgets"])
cross_qt = find_qt(project, cross, modules=["Widgets"],
                   qt_root="/opt/qt6-mingw", probe="qtpaths")
```

`qt_root` alone does not do this: it *scopes* the pkg-config search to
that prefix rather than skipping it, so pointing it at the cross Qt only
makes pkg-config answer with more confidence.

`qt_module_available()` is unaffected: it asks whether a module is
installed at all, not which install a target builds against, so it always
tries both probes and takes no `probe` argument.

Passing `env` adds the `qt` toolchain to the environment (tool paths for
moc/uic/rcc), enabling the builders below. Discovery is cached per project
and per environment name; call `find_qt` again with the same environment to
add modules, and once per environment to build for two of them:

```python
host_qt = find_qt(project, host, modules=["Widgets"])
mcu_qt = find_qt(project, mcu, modules=["Core"])
```

Each environment gets its own install and its own module targets, told
apart by `Qt6Core@host` and `Qt6Core@mcu`. Two environments without names
share one install, because nothing tells them apart.

A Qt target belongs to the environment it was declared in, like any other
target, so one name can be built for both:

```python
project.QtProgram("app", host, sources=["main.cpp"], link=[host_qt.Widgets])
project.QtProgram("app", mcu, sources=["main.cpp"], link=[mcu_qt.Core])
```

They are `app@host` and `app@mcu`, in separate build directories.

## The automoc scan

`QtProgram` scans the target's sources, their same-basename headers, and
the closure of project-local `#include "..."` files for `Q_OBJECT`,
`Q_GADGET`, and `Q_NAMESPACE` — at **generate time**, mtime-cached, never
during the build. Unlike CMake's line-anchored regex, declarations like
`class C : public QObject { Q_OBJECT };` on one line are found too.

Because the scan runs when pcons runs, a header that *gains* `Q_OBJECT`
afterward would be missed — so each Qt target also gets a tiny
`scan.ok` build edge whose depfile covers every scanned file and
directory. When the scan result would change, the build stops:

```
pcons Qt: the moc scan for target 'myapp' is out of date:
  src/newthing.h now needs moc (header gained a Qt macro)

Re-run pcons to regenerate the build files.
```

Escape hatches: `automoc=False`, `autouic=False`, `autorcc=False`, and
`no_moc=["src/weird.h"]`.

A `.cpp` file with `Q_OBJECT` needs its moc output included at the end
of the file (`#include "myfile.moc"`); pcons errors at generate time if
the include is missing.

## Resources without .qrc XML

```python
res = project.QtResources(
    "assets", env, files=["images/*.png", "data/config.json"], prefix="/"
)
app.link(res)
```

pcons synthesizes the `.qrc` (globs expanded, aliases relative to the
project root), runs rcc with a depfile, and returns an object target.
Files are reachable as `:/images/logo.png` etc.

!!! note "Static libraries"
    Resources compiled into a *static* library need
    `Q_INIT_RESOURCE(name);` in the consuming application, or the
    linker may drop the auto-registration object.

## Low-level builders

The Meson-style explicit API, for when you want full control (this is
exactly what QtProgram automates):

```python
moc_cpp = env.qt.Moc(sources="mainwindow.h")  # → moc_mainwindow.cpp
dot_moc = env.qt.Moc(sources="widget.cpp")  # → widget.moc
ui_hdr = env.qt.Uic(sources="mainwindow.ui")  # → ui_mainwindow.h
res_cpp = env.qt.Rcc(sources="icons.qrc", name="icons")

app = project.Program("myapp", env, sources=["main.cpp", moc_cpp[0], res_cpp[0]])
app.link(qt.Widgets)
app.depends(ui_hdr[0])
env.cxx.includes.append(str(project.build_dir / "qt.gen"))
```

moc needs Qt's include paths and defines to parse headers
(`env.qt.mocincludes`, `env.qt.mocdefines`); QtProgram fills them from
the targets you `link=`.

Related `env.qt` variables: `mocflags`, `uicflags`, `rccflags`,
`mocpredefs` (compiler-builtin macros via `--include moc_predefs.h`,
generated automatically for GCC/Clang).

## Generated file layout

| What | Where |
|---|---|
| QtProgram("app", ...) codegen | `build/qt.app/<source-relative-dir>/` |
| Low-level builders (default) | `build/qt.gen/<source-relative-dir>/` |
| QtResources | `build/qt.res/` |
| Scan manifest + stamp | `build/qt.app/scan-manifest.json`, `scan.ok` |

## Current limitations

Worth knowing before porting a large CMake project:

- **Flags are captured when the Qt target is created.** `QtProgram`
  snapshots the environment (and computes moc's view of the world) at
  the call; `env.cxx.defines.append(...)` *after* the call doesn't reach
  that target. Pass Qt modules via `link=[...]` at construction — moc
  needs their include paths, and a later `app.link(qt.Widgets)` is too
  late for moc (pcons warns when this happens).
- **Windows debug builds:** the `d`-suffixed Qt libraries are selected
  by the variant at `find_qt()` time — call `find_qt()` *after*
  `env.set_variant()`, and build debug and release in separate pcons
  runs (not as two variants of one project).
- **Prebuilt Qt-based SDKs:** the automoc scan follows includes into
  directories you list in `env.cxx.includes` — including out-of-project
  ones. Headers from a *prebuilt* Qt-based SDK reached that way would
  get spurious moc edges; exclude them with `no_moc=[...]`. (Libraries
  found via `find_package`/`find_qt` are excluded automatically.)
- **pcons cannot see what the target Qt was built with.** moc, uic and
  rcc run on the build machine, from the host Qt, and their output is
  compiled against the Qt you link. When the two were configured
  differently the generated code can reference something the target does
  not have: rcc compresses with zstd by default, and a target Qt built
  without zstd then fails to link on `qResourceFeatureZstd`. Neither
  `qtpaths6 --query` nor pkg-config reports a Qt install's feature set
  (only paths, mkspec and version), so pcons cannot detect this. Spell
  the flag out on the environment that needs it, which is per
  environment like everything else:

  ```python
  cross.qt.rccflags.append("--no-zstd")
  ```

  The target Qt's feature list is in
  `<prefix>/mkspecs/qconfig.pri` (`QT.global.enabled_features`) and in
  `<prefix>/include/QtCore/qconfig.h` (`QT_FEATURE_zstd`) if you need to
  check which way it was built.
- **Not yet implemented:** qmlcachegen AOT compilation, QML plugin
  libraries / singletons / subdirectory QML files, static-Qt plugin
  imports (`Q_IMPORT_PLUGIN`), per-file resource compression options and
  big-resource two-pass rcc, lupdate's automatic per-target source
  collection, and Designer plugin builds. Branch switches that change
  the source list need a pcons re-run (there is no CMake-style
  self-regeneration yet); the scan guard reports this for moc changes.

## Platform notes

- **macOS**: framework builds (Homebrew, official installer) link with
  `-F`/`-framework` automatically. On Apple Silicon with Qt < 6.10,
  `find_qt` also pre-includes `<arm_acle.h>` to work around
  `qyieldcpu.h`'s bare `__yield()` (fixed upstream in Qt 6.10).
- **Windows**: MSVC and clang-cl get `/Zc:__cplusplus /permissive-`
  (required by Qt headers); debug variants link the `d`-suffixed
  libraries; moc runs with `--compiler-flavor msvc`.
- **Linux**: distro Qt (apt/dnf/pacman) is found via pkg-config; the
  official installer via `qtpaths` or `qt_root=`/`$PCONS_QT_ROOT`.
- **Android**: Qt names every library after the ABI, so an
  `android_arm64_v8a` install holds `libQt6Core_arm64-v8a.so` and no
  unsuffixed file. `find_qt` reads the suffix off the install and links
  `-lQt6Core_arm64-v8a`. The module target keeps its plain name,
  `qt.Core`. Qt reports no ABI of its own, so this needs the `qtpaths`
  probe and a real install to read.

## QML modules

`QtQmlModule` bundles QML files and `QML_ELEMENT` C++ classes into a
module the engine loads by URI:

```python
qt = find_qt(project, env, modules=["Qml"])

ui = project.QtQmlModule(
    "app_ui",
    env,
    uri="com.example.app",
    version="1.0",
    qml_files=["qml/Main.qml"],
    sources=["src/backend.cpp"],  # classes marked QML_ELEMENT
    link=[qt.Qml],
)

app = project.QtProgram("app", env, sources=["src/main.cpp"], link=[qt.Qml])
app.link(ui)
```

```cpp
QQmlApplicationEngine engine;
engine.loadFromModule("com.example.app", "Main");   // that's it
```

One call replaces CMake's `qt_add_qml_module` plumbing: moc emits JSON
metadata, `qmltyperegistrar` generates the type registrations (plus a
`.qmltypes` for tooling), a `qmldir` is synthesized, and everything
embeds under `:/qt/qml/<uri>/` — the engine's default import path. The
module builds as an *object* target, so linking it into the app can't
dead-strip the registrations — no plugin/backing-target split, no
`Q_INIT_RESOURCE`, no import-path setup.

A QML file starting with `pragma Singleton` is declared `singleton` in the
qmldir, so the engine hands out the instance rather than the type. Nothing
to pass: the pragma is read from the file, and editing one re-runs pcons so
the qmldir keeps up. A generated QML file that does not exist yet when the
build is described reads as not a singleton.

Not yet included: `qmlcachegen` ahead-of-time QML compilation (the
embedded QML runs through the normal engine path — functionally
identical, slightly slower startup) and separate QML plugin libraries.
`qml_files` entries are embedded under their base name, so a nested layout
is flattened and two files with one base name collide.

## Translations

```python
tr = project.QtTranslations(
    "i18n",
    env,
    ts_files=["i18n/app_de.ts", "i18n/app_fr.ts"],
    lupdate_sources=["src/main.cpp", "src/mainwindow.cpp"],
)
app.link(tr)
```

Each `.ts` catalog compiles with lrelease and embeds under `:/i18n/`:

```cpp
QTranslator translator;
translator.load(QLocale(), "app", "_", ":/i18n");
QCoreApplication::installTranslator(&translator);
```

Refreshing the catalogs from sources is **`ninja lupdate`** — a utility
target that is *never* part of the default build or `ninja all`, because
it writes into the source tree. (This uses `target.build_by_default =
False`, available for any utility target.)

## Deployment

```python
project.QtDeploy("deploy", env, app=app, bundle="MyApp.app")  # macOS
project.QtDeploy("deploy", env, app=app, deploy_dir="deploy")  # Windows
```

`ninja deploy` runs macdeployqt (fixes up a `.app` bundle in place —
build the bundle first, e.g. with `pcons.contrib.bundle` or Install
targets) or windeployqt (copies DLLs/plugins next to the executable).
Never part of the default build. Linux deployment is out of scope —
use linuxdeploy/appimagetool on the installed tree.

!!! note "Homebrew Qt and macdeployqt"
    macdeployqt is most reliable with the official Qt installer. With
    Homebrew's framework layout it can leave stray `@rpath` references
    (e.g. QtGui → QtDBus) — a known macdeployqt limitation that affects
    CMake builds identically.

### Packaging into installers

Deployed Qt apps compose with the installer generators in
`pcons.contrib.installers` (both flows are tested):

```python
# macOS: .app -> macdeployqt -> .pkg
pkg = installers_macos.create_pkg(
    project,
    env,
    name="MyApp",
    version="1.0.0",
    identifier="com.example.myapp",
    sources=["build/MyApp.app"],
)
pkg.depends(deploy)

# Windows: windeployqt dir -> .msix (the directory stages as a
# subfolder, so the executable path includes it)
msix = installers_windows.create_msix(
    project,
    env,
    name="MyApp",
    version="1.0.0.0",
    publisher="CN=Example",
    sources=["build/deploy"],
    executable="deploy\\myapp.exe",
)
msix.depends(deploy)
```

Build in two steps so packaging always sees the deployed tree:
`ninja deploy && ninja MyApp-1.0.0.pkg`.

## Examples

- `examples/52_qt_widgets` — the high-level QtProgram flow.
- `examples/53_qt_explicit` — the explicit Moc/Rcc flow.
- `examples/54_qt_qml` — a QML module with C++ types.
- `examples/55_qt_translations` — embedded catalogs + `ninja lupdate`.
- `examples/56_qt_deploy` — a relocatable .app via `ninja deploy`.

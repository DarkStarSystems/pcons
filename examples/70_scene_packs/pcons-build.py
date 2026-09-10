# SPDX-License-Identifier: MIT
"""Content-discovered build order, over sources this build generates itself.

Scenes reference other scenes by name. Packing a scene embeds a digest of every
pack it references, so a referenced pack has to be built first -- but *which*
pack that is, is a fact about file content, and nothing in this script says it.
A `Scanner` settles it at build time. No compiler is involved anywhere here.

1. **Only the scanner reads a `ref` line.** `tools/scan_scene.py` reports what
   each pack provides and requires; pcons collates those reports into a ninja
   dyndep file, and the ordering shows up in the build graph. Nothing below
   encodes "level2 after level1" -- move a `ref` line to another scene and the
   build order follows it, with no edit to this script.

2. **Discovered facts reach the command line, too.** `edge_args` has collate
   write each pack edge a `.refs` file listing the packs that edge must read,
   and appends `$SCENE_REFS` to its command. The path is fixed at configure
   time; only the content is decided at build time. So `tools/pack_scene.py`
   never parses a scene for refs -- it is told.

3. **Target dependencies carry the *exports*.** A scope resolves a required
   name against the scopes it depends on, so `pack_level2` needs
   `depends(pack_level1)` to see the name "level1" at all. That
   dependency says where to look (and builds level1 first, as any dependency
   does); the scene's content decides what is used and in which order.

4. **Generated sources need no phases.** Two generations of them here: the
   build assembles `genscene1.py` from checked-in fragments, runs it to get a
   scanned scene *and* a fragment of the next generator, assembles
   `genscene2.py` from that, and runs it for another scanned scene. A scan edge
   takes a generated scene as an input like any other, so its producer's
   ordering comes from the node graph -- no staging, no reconfigure, no
   existence checks. (Scanning before the generator has run is what deadlocks
   a whole-project scan pass; see issue #105 for the C++ modules version.)
"""

import sys
from pathlib import Path
from typing import Any

from pcons import ArgsFormat, EdgeArgsSpec, Project, Scanner, Target
from pcons.core.subst import NodeVar

project = Project("scene_packs")
env = project.Environment()

python = sys.executable
bin_dir = project.build_dir / "bin"
gen_dir = project.build_dir / "gen"
packs_dir = project.build_dir / "packs"

# --- Generation 1: assemble a generator, then run it -----------------------
# write_if_different on both steps: a fragment edited to the same text, or a
# generator that rewrites an identical scene, must not cascade into repacking.

genscene1 = env.Command(
    target=bin_dir / "genscene1.py",
    source=["src/gen_head.pyfrag", "src/gen1_payload.pyfrag"],
    command=[python, "$SRCDIR/tools/concat.py", "$TARGET", "$SOURCES"],
    depends=["tools/concat.py"],
    name="genscene1",
    write_if_different=True,
)

# $SOURCE is the program the step above just wrote. It runs under this
# interpreter, so there is no execute bit to set and no "./" to prepend.
scenes1 = env.Command(
    target=[gen_dir / "generated1.scene", gen_dir / "gen2_payload.pyfrag"],
    source=[bin_dir / "genscene1.py"],
    command=[python, "$SOURCE", "${TARGETS[0]}", "${TARGETS[1]}"],
    name="run_genscene1",
    write_if_different=True,
)

# --- Generation 2: the same again, from a generated fragment ---------------

genscene2 = env.Command(
    target=bin_dir / "genscene2.py",
    source=["src/gen_head.pyfrag", gen_dir / "gen2_payload.pyfrag"],
    command=[python, "$SRCDIR/tools/concat.py", "$TARGET", "$SOURCES"],
    depends=["tools/concat.py"],
    name="genscene2",
    write_if_different=True,
)

scenes2 = env.Command(
    target=gen_dir / "generated2.scene",
    source=[bin_dir / "genscene2.py"],
    command=[python, "$SOURCE", "$TARGET"],
    name="run_genscene2",
    write_if_different=True,
)

# --- The packs -------------------------------------------------------------
# $SCENE_REFS, appended by the scanner's edge_args, is the last argument.


def pack(name: str, *scenes: str | Path) -> Target:
    return env.Command(
        target=packs_dir / f"{name}.pack",
        source=list(scenes),
        command=[python, "$SRCDIR/tools/pack_scene.py", "$TARGET", "$SOURCES"],
        depends=["tools/pack_scene.py"],
        name=f"pack_{name}",
    )


pack_common = pack("common", "assets/common.scene")
pack_level1 = pack("level1", "assets/level1.scene", gen_dir / "generated1.scene")
pack_level2 = pack("level2", gen_dir / "generated2.scene")

# Carry the exports: see point 3 above.
pack_level1.depends(pack_common)
pack_level2.depends(pack_level1)


def pack_of_edge(env: Any, scenes: Any, governed: Any) -> dict[str, str]:
    """Tell each scan edge which pack its scenes are going into.

    A pack serves every scene inside it, so its scan info gives all of them
    that one path. (`provide_template` is the other way to answer this, for a
    scanner whose every logical name gets an artifact of its own -- a C++
    module's BMI, say. Here they share one.) `scan_vars` puts the value on the
    build statement, so all the scan edges still share a single ninja rule.
    """
    return {"PACK": f"{packs_dir.name}/{governed.path.name}"}


scene_refs = Scanner(
    "scene-refs",
    source_suffixes=[".scene"],
    scan_command=[
        python,
        "$SRCDIR/tools/scan_scene.py",
        NodeVar("PACK"),
        "$SOURCES",
        "$TARGET",
    ],
    scan_deps=["tools/scan_scene.py"],
    scan_vars=pack_of_edge,
    # A ref no scene provides is a broken asset, not something an outside
    # library might satisfy later.
    on_unresolved="error",
    edge_args=EdgeArgsSpec(
        suffix=".refs",
        var="SCENE_REFS",
        token="$SCENE_REFS",
        format=ArgsFormat(line="{name} {path}"),
        include="requires",
    ),
)
scene_refs.attach(pack_common, pack_level1, pack_level2)

project.Default(pack_common, pack_level1, pack_level2)

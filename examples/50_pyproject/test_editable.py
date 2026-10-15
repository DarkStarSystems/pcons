import importlib.metadata as metadata
import os
import shutil
import subprocess

# The extension only exists after the example is built and installed.
import pcons_hello_ext  # ty: ignore[unresolved-import]

print(pcons_hello_ext.say_hello("world"))
assert "build" in pcons_hello_ext.__file__, (
    f"Expected extension in build dir (editable install), found: {pcons_hello_ext.__file__}"
)

scripts = metadata.entry_points(group="console_scripts", name="pcons-hello-ext")
assert [e.value for e in scripts] == ["pcons_hello_ext:hello_world"]

# build/ records this environment's Python, so editing the build script
# regenerates build.ninja instead of failing on a deleted build environment.
os.utime("pcons-build.py")
ninja = subprocess.run(
    [shutil.which("ninja") or "ninja", "-C", "build"],
    capture_output=True,
    text=True,
)
assert ninja.returncode == 0, ninja.stdout + ninja.stderr
assert "Regenerating" in ninja.stdout, ninja.stdout

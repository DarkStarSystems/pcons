import importlib.metadata as metadata

# The extension only exists after the example is built and installed.
import pcons_hello_ext  # ty: ignore[unresolved-import]

print(pcons_hello_ext.say_hello("world"))
assert "site-packages" in pcons_hello_ext.__file__, (
    f"Expected installed (non-editable) extension in site-packages, found: {pcons_hello_ext.__file__}"
)

meta = metadata.metadata("pcons_hello_ext")
assert meta["Summary"] == "Example nanobind extension built with the pcons backend"
assert meta["License-Expression"] == "MIT"
assert meta["Description-Content-Type"] == "text/markdown"

scripts = metadata.entry_points(group="console_scripts", name="pcons-hello-ext")
assert [e.value for e in scripts] == ["pcons_hello_ext:hello_world"]

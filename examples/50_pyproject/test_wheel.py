# The extension only exists after the example is built and installed.
import pcons_hello_ext  # ty: ignore[unresolved-import]

print(pcons_hello_ext.say_hello("world"))
assert "site-packages" in pcons_hello_ext.__file__, (
    f"Expected installed (non-editable) extension in site-packages, found: {pcons_hello_ext.__file__}"
)

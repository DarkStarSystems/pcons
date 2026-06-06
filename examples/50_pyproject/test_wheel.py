import pcons_hello_ext

print(pcons_hello_ext.say_hello("world"))
assert "site-packages" in pcons_hello_ext.__file__, (
    f"Expected installed (non-editable) extension in site-packages, found: {pcons_hello_ext.__file__}"
)

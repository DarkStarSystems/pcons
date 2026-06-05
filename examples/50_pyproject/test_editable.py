import pcons_hello_ext

print(pcons_hello_ext.say_hello("world"))
assert "build" in pcons_hello_ext.__file__, (
    f"Expected extension in build dir (editable install), found: {pcons_hello_ext.__file__}"
)

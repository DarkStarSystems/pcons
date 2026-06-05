import glob
import tarfile

ns = tarfile.open(glob.glob("dist/*.tar.gz")[0]).getnames()

assert any(n.endswith("/PKG-INFO") for n in ns), f"PKG-INFO missing from sdist: {ns}"
assert any(n.endswith("src/hello.cpp") for n in ns), (
    f"hello.cpp missing from sdist: {ns}"
)
assert any(n.endswith("src/hello.hpp") for n in ns), (
    f"hello.hpp missing from sdist: {ns}"
)

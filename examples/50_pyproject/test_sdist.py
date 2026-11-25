import tarfile
from pathlib import Path

sdist = Path("dist/pcons_hello_ext-0.0.1.tar.gz")
assert sdist.is_file(), f"{sdist} missing: {sorted(Path('dist').iterdir())}"

with tarfile.open(sdist) as tf:
    ns = tf.getnames()
    pkg_info = tf.extractfile("pcons_hello_ext-0.0.1/PKG-INFO")
    assert pkg_info is not None, f"PKG-INFO missing from sdist: {ns}"
    assert "Version: 0.0.1\n" in pkg_info.read().decode(), "PKG-INFO lacks the version"

assert ns.count("pcons_hello_ext-0.0.1/PKG-INFO") == 1, f"PKG-INFO not unique: {ns}"
assert any(n.endswith("src/hello.cpp") for n in ns), (
    f"hello.cpp missing from sdist: {ns}"
)
assert any(n.endswith("src/hello.hpp") for n in ns), (
    f"hello.hpp missing from sdist: {ns}"
)
assert any(n.endswith("/LICENSE") for n in ns), f"LICENSE missing from sdist: {ns}"
assert any(n.endswith("/version_provider.py") for n in ns), (
    f"version_provider.py missing from sdist: {ns}"
)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copy an APK and add native libraries under lib/<abi>/.

The SDK build tools have no command for this: aapt2 packages the manifest
and the resources, and Gradle is what normally puts the libraries in. The
entries are stored uncompressed so `zipalign -p` can page-align them, which
is what lets Android map a library straight out of the APK.

    pack_libs.py <base.apk> <out.apk> <abi> <lib.so>...
"""

import shutil
import sys
import zipfile
from pathlib import Path


def main(argv: list[str]) -> int:
    base, out, abi, *libs = argv
    shutil.copyfile(base, out)
    with zipfile.ZipFile(out, "a", zipfile.ZIP_STORED) as apk:
        for lib in libs:
            apk.write(lib, f"lib/{abi}/{Path(lib).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

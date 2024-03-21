
# Pcons: a python-based software build system

PCons is currently very much a work in progress; my goal is for it to be a software build tool inspired by [SCons](https://scons.org) and [CMake](https://cmake.org). The basic idea is to use modern python as the language to describe the build dependency tree and tools, and emit project definitions for Ninja or Makefiles to actually run the build. 

---
# pcons

[![codecov](https://codecov.io/gh/garyo/pcons/branch/main/graph/badge.svg?token=pcons_token_here)](https://codecov.io/gh/garyo/pcons)
[![CI](https://github.com/garyo/pcons/actions/workflows/main.yml/badge.svg)](https://github.com/garyo/pcons/actions/workflows/main.yml)

pcons -- created by garyo

## Install it from PyPI

```bash
pip install pcons
```

## Usage

```py
from pcons import BaseClass
from pcons import base_function

BaseClass().base_method()
base_function()
```

```bash
$ python -m pcons
#or
$ pcons
```

## Development

Read the [CONTRIBUTING.md](CONTRIBUTING.md) file.

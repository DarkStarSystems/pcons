// SPDX-License-Identifier: MIT
// Primary module interface in a .cpp file (fmt-style — `src/fmt.cc` does
// the same thing). Pcons would not normally scan this for module syntax;
// the project opts in via `env.cxx.modules = True` so the scanner runs
// regardless of file extension.
export module Math;

export int double_it(int x) {
    return x * 2;
}

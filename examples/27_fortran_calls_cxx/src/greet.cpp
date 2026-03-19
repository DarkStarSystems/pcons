// SPDX-License-Identifier: MIT
// C++ function called from Fortran via BIND(C) interface.
#include <cstdio>

extern "C" {
    void greet_from_cxx(const char* name) {
        std::printf("Hello from C++, %s!\n", name);
    }
}

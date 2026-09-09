// SPDX-License-Identifier: MIT
// A C++ TU in a freestanding build: no exceptions, no RTTI, no libstdc++.

#include "blink.h"

namespace {
constexpr const char *kBanner = "banner from C++\n";
}

extern "C" const char *firmware_banner()
{
    return kBanner;
}

// SPDX-License-Identifier: MIT
#pragma once

#include <string>

namespace hello_lib {

// Returns a friendly greeting. Demonstrates that pcons can compile against
// a rez-resolved package's headers and link against its static library.
std::string greet(const std::string& name);

}  // namespace hello_lib

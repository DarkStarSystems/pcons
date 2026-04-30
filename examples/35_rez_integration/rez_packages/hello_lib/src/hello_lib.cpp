// SPDX-License-Identifier: MIT
#include "hello_lib.h"

namespace hello_lib {

std::string greet(const std::string& name) {
    return "Hello, " + name + ", from rez-resolved hello_lib!";
}

}  // namespace hello_lib

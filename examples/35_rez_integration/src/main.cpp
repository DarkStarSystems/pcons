// SPDX-License-Identifier: MIT
#include <iostream>

#include "hello_lib.h"

int main() {
    std::cout << hello_lib::greet("rez (env-only)") << std::endl;
    return 0;
}

// SPDX-License-Identifier: MIT
#include "local.hpp"

import std;
import Greet;

int main()
{
    std::println("{}", greet("modules"));
    return local_value() == 7 ? 0 : 1;
}

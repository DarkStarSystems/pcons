// SPDX-License-Identifier: MIT
export module Greet;

import std;

export auto greet(std::string_view who) -> std::string
{
    return std::format("Hello, {}!", who);
}

// SPDX-License-Identifier: MIT
export module Greet;

import std;

export std::string greet(std::string_view who) {
    std::string out = "Hello, ";
    out += who;
    out += "!";
    return out;
}

// SPDX-License-Identifier: MIT
// C++20 module interface unit: defines module MyMod
module;

#include <MyHeader.hpp>

export module Mod2;

export namespace mod2 {
int answer() { return get_answer(); }
} // namespace mod2

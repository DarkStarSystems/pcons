// SPDX-License-Identifier: MIT
// Module IMPLEMENTATION unit for module Calc (no partition name).
// Pulls in the interface partition and the internal partition.
module Calc;
import :Constants;
import :Helpers;

int compute_answer() {
    return kAnswer * helper_internal();
}

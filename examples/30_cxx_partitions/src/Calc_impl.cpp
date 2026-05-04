// SPDX-License-Identifier: MIT
// Module IMPLEMENTATION unit for module Calc (no partition name).
// Pulls in the interface partition and the internal partition.
module Calc;
import :Constants;

int helper_internal();  // forward decl for the internal partition's symbol

int compute_answer() {
    return kAnswer * helper_internal();
}

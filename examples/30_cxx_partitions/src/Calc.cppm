// SPDX-License-Identifier: MIT
// Primary interface unit for module Calc.
// Re-exports the partition interface and exposes a top-level function that
// uses both an interface partition and an internal (implementation) partition.
export module Calc;

export import :Constants;

export int compute_answer();

// SPDX-License-Identifier: MIT
// Internal partition (implementation) unit: `module Calc:Helpers;` (no
// `export`). On MSVC this needs /internalPartition (which is incompatible
// with /interface); pcons picks the right flag from the P1689 scan output.
module Calc:Helpers;

int helper_internal() {
    return 1;
}

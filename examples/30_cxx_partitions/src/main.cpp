// SPDX-License-Identifier: MIT
// Consumer: imports the Calc module (which transitively imports its
// interface partition).
import Calc;

int main() {
    return compute_answer() == 42 ? 0 : 1;
}

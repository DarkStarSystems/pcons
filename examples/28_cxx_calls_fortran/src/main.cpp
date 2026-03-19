// SPDX-License-Identifier: MIT
// C++ main program calling a Fortran subroutine via BIND(C).
#include <cstdio>

extern "C" {
    void fortran_sum(double a, double b, double* result);
}

int main() {
    double result = 0.0;
    fortran_sum(3.0, 4.0, &result);
    std::printf("3 + 4 = %.0f (computed by Fortran)\n", result);
    return 0;
}

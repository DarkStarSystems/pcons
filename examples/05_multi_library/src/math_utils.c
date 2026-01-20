/* SPDX-License-Identifier: MIT */
#include "math_utils.h"
#include <math.h>

double vec_length(double x, double y, double z) {
    return sqrt(x*x + y*y + z*z);
}

double vec_dot(double x1, double y1, double z1, double x2, double y2, double z2) {
    return x1*x2 + y1*y2 + z1*z2;
}

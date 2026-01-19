/*
 * Main program that uses the math_ops library
 */

#include <stdio.h>
#include "math_ops.h"

int main(void) {
    int a = 5, b = 3;

    printf("a = %d, b = %d\n", a, b);
    printf("add(%d, %d) = %d\n", a, b, add(a, b));
    printf("multiply(%d, %d) = %d\n", a, b, multiply(a, b));

    return 0;
}

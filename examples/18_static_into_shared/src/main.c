/* SPDX-License-Identifier: MIT */
/* Main program that uses the shared library. */

#include <stdio.h>

/* Declaration of function from shared lib */
extern int wrapper_get_value(void);

int main(void)
{
    printf("Value from wrapper: %d\n", wrapper_get_value());
    return 0;
}

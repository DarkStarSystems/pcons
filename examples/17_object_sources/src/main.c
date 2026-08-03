/* SPDX-License-Identifier: MIT */
/* Main program that calls a function from a separately compiled object. */

#include <stdio.h>

/* Declaration of function in helper.c */
extern int get_value(void);
/* ...and in tuned.c, which is compiled with its own flags */
extern int report_tuning(void);

int main(void)
{
    printf("Value from helper: %d\n", get_value());
    report_tuning();
    return 0;
}

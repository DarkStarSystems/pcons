/* SPDX-License-Identifier: MIT */
/* A file the project wants compiled with different flags from its
 * neighbours -- see pcons-build.py. */

#include <stdio.h>

int report_tuning(void)
{
#ifdef TUNED
    printf("tuned.c was compiled with its own flags\n");
    return 1;
#else
    printf("tuned.c got the target's default flags\n");
    return 0;
#endif
}

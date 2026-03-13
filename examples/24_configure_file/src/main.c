/* SPDX-License-Identifier: MIT */
#include <stdio.h>
#include "config.h"

int main(void) {
    printf("Version: %s\n", VERSION);
    printf("HAVE_THREADS: %d\n", HAVE_THREADS);
#ifdef HAVE_ZLIB
    printf("zlib: yes\n");
#else
    printf("zlib: no\n");
#endif
    return 0;
}

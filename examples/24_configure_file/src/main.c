/* SPDX-License-Identifier: MIT */
#include <stdio.h>
#include "config.h"

int main(void) {
    printf("Version: %s\n", VERSION);
    printf("HAVE_STDINT_H: %d\n", HAVE_STDINT_H);
#ifdef HAVE_FROBNICATE_H
    printf("frobnicate: yes\n");
#else
    printf("frobnicate: no\n");
#endif
    printf("sizeof(void*): %d\n", SIZEOF_VOIDP);
    return 0;
}

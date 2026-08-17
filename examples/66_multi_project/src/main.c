/* SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "common.h"

int main(void) {
#ifdef DEVICE_BUILD
    printf("device checksum=%u\n", checksum("pcons"));
#else
    printf("host checksum=%u\n", checksum("pcons"));
#endif
    return 0;
}

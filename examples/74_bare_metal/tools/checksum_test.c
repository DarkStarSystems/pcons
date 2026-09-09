/* SPDX-License-Identifier: MIT */
/* Host-side test of the checksum the firmware uses, built from the same
   source with the host compiler. */

#include <stdio.h>
#include <string.h>

#include "common.h"

int main(void)
{
    static const char frame[] = "pcons";
    const uint16_t expected = 0x1f0fu;
    const uint16_t got = frame_checksum((const uint8_t *)frame, strlen(frame));

    printf("checksum 0x%04x\n", got);
    if (got != expected) {
        printf("expected 0x%04x\n", expected);
        return 1;
    }
    return 0;
}

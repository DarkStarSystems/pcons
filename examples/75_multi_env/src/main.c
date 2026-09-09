/* SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "checksum.h"
#include "parity.h"

int main(void)
{
    static const uint8_t frame[] = {1, 2, 3, 4};

    uint16_t sum = checksum(frame, sizeof(frame));

    printf("%s checksum %u, %s parity %d\n", checksum_flavor(), (unsigned)sum,
           parity_flavor(), parity(sum));
    return 0;
}

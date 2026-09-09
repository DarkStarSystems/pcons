/* SPDX-License-Identifier: MIT */
/* Host-side generator: writes a version header the firmware includes. */

#include <stdio.h>

int main(int argc, char **argv)
{
    if (argc < 2)
        return 1;

    FILE *f = fopen(argv[1], "w");
    if (!f)
        return 1;

    fputs("/* generated */\n#pragma once\n#define FW_VERSION \"1.4.0\"\n", f);
    fclose(f);
    return 0;
}

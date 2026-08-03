/* SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "plugins.h"

int main(void) {
    printf("%d plugins\n", PLUGIN_COUNT);
    for (int i = 0; i < PLUGIN_COUNT; i++) {
        printf("  %s -> %d\n", plugins[i].name, plugins[i].fn());
    }
    return 0;
}

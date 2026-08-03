/* SPDX-License-Identifier: MIT */
#include <stdio.h>

extern const char *const entries[];
extern const int entry_count;

int main(void) {
    printf("%d entries\n", entry_count);
    for (int i = 0; i < entry_count; i++) {
        printf("  %s\n", entries[i]);
    }
    return 0;
}

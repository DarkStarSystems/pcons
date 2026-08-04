/* SPDX-License-Identifier: MIT */
#include <stdio.h>

extern const char *const items[];
extern const int item_count;

int main(void) {
    int i;
    printf("%d items:", item_count);
    for (i = 0; i < item_count; i++) {
        printf(" %s", items[i]);
    }
    printf("\n");
    return 0;
}

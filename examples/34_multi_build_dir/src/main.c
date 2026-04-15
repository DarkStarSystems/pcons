/*
 * Example demonstrating separate build directories for debug/release.
 */

#include <stdio.h>

int main(void) {
#ifdef DEBUG
    printf("Running in DEBUG mode\n");
#else
    printf("Running in RELEASE mode\n");
#endif

#ifdef NDEBUG
    printf("Assertions disabled (NDEBUG defined)\n");
#else
    printf("Assertions enabled\n");
#endif

    return 0;
}

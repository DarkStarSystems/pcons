/*
 * Example demonstrating debug/release build variants
 */

#include <stdio.h>

int main(void) {
#ifdef DEBUG
    printf("Running in DEBUG mode\n");
    printf("Extra debug info would go here...\n");
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

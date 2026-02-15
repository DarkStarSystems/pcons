#include <stdio.h>

int main(void) {
#ifdef _MSC_VER
    printf("Hello from MSVC %d (full: %d)\n", _MSC_VER, _MSC_FULL_VER);
#else
    printf("Hello (not MSVC)\n");
#endif
    return 0;
}

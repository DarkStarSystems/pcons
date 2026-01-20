#include <stdio.h>

extern void extra_function(void);

int main(void) {
    printf("Hello from main\n");
    extra_function();
    return 0;
}

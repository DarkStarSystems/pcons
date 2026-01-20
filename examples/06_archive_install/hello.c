/* Simple Hello World program for testing pcons */
#include <stdio.h>
#include "hello.h"

void say_hello(void) {
    printf("%s\n", GREETING);
}

int main(void) {
    say_hello();
    return 0;
}

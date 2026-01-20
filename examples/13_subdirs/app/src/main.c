/* Application using libfoo */
#include <stdio.h>
#include "foo.h"

int main(void) {
    printf("Subdirs example app\n");
    foo_greet("World");
    return 0;
}

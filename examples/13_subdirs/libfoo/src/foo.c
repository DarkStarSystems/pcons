/* Library source file */
#include <stdio.h>
#include "foo.h"
#include "bar.h"

void foo_greet(const char* name) {
    printf("Hello, %s%s\n", name, bar_suffix());
}

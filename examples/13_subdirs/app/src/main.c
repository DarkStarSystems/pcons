/* Application using libfoo */
#include <stdio.h>
#include "foo.h"
#include "bar.h"

int main(void) {
    printf("Subdirs example app, libbar %s\n", bar_version());
    foo_greet("World");
    return 0;
}

/* Application using libfoo */
#include <stdio.h>
#include "foo.h"
#include "bar.h"
#include "app_config.h"

int main(void) {
    printf("Subdirs example app, libbar %s\n", bar_version());
    foo_greet(APP_GREETING);
    return 0;
}

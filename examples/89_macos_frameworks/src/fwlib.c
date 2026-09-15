/* SPDX-License-Identifier: MIT */
#include <CoreFoundation/CoreFoundation.h>

#include "fwlib.h"

long fwlib_string_length(void) {
    CFStringRef s = CFSTR("Hello, Frameworks!");
    return (long)CFStringGetLength(s);
}

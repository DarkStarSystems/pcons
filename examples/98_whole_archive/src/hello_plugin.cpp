// SPDX-License-Identifier: MIT
#include "registry.h"

// Nothing refers to this file, so a linker drops it from the archive
// unless the whole archive is linked.
static int registered = register_plugin("hello");

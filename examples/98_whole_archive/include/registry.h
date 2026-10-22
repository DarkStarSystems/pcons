// SPDX-License-Identifier: MIT
#pragma once

// Plugins call this from a static constructor; nothing calls them.
int register_plugin(const char* name);
void list_plugins();

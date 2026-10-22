// SPDX-License-Identifier: MIT
#include "registry.h"

#include <cstdio>
#include <string>
#include <vector>

namespace {
std::vector<std::string>& plugins() {
    static std::vector<std::string> registered;
    return registered;
}
}  // namespace

int register_plugin(const char* name) {
    plugins().push_back(name);
    return 0;
}

void list_plugins() {
    std::printf("plugins:");
    for (const auto& name : plugins()) {
        std::printf(" %s", name.c_str());
    }
    std::printf("\n");
}

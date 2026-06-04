// SPDX-License-Identifier: MIT
#include <cstdio>

int consume();  // provided by lib1 (consumer.cpp)

int main() {
    std::printf("answer = %d\n", consume());
    return consume() == 42 ? 0 : 1;
}

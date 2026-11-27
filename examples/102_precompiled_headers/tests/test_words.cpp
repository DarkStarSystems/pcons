#include <iostream>

#include "words.h"

int main() {
    auto counts = count_words({"a", "b", "a"});
    if (counts["a"] != 2 || counts["b"] != 1) {
        std::cout << "tests failed\n";
        return 1;
    }
    std::cout << "tests passed\n";
    return 0;
}

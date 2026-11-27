#include <iostream>

#include "checksum.h"
#include "words.h"

int main() {
    auto counts = count_words({"to", "be", "or", "not", "to", "be"});
    std::cout << "to: " << counts["to"] << ", checksum: " << checksum("pcons") << "\n";
    return 0;
}

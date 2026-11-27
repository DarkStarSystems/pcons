#include "words.h"

std::map<std::string, int> count_words(const std::vector<std::string>& words) {
    std::map<std::string, int> counts;
    for (const auto& word : words) {
        ++counts[word];
    }
    return counts;
}

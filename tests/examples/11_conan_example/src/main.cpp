// Example using fmt library from Conan
#include <fmt/core.h>

int main() {
    fmt::print("Hello from fmt {}!\n", FMT_VERSION);
    fmt::print("The answer is {}\n", 42);
    return 0;
}

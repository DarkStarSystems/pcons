// SPDX-License-Identifier: MIT
import std;
import Greet;

int main() {
    std::string msg = greet("modules");
    std::println("{}", msg);
    return msg == "Hello, modules!" ? 0 : 1;
}

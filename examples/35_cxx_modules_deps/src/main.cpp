// SPDX-License-Identifier: MIT
// C++20 consumer: imports MyMod
import Mod1;
import Mod2;

int main() {
  return mod1::answer() == mod2::answer() && mod1::answer() == 42 ? 0 : 1;
}

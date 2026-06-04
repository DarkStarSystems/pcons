#include <nanobind/nanobind.h>

#include <nanobind/stl/string.h>

#include <format>
#include <hello.hpp>

namespace nb = nanobind;

using namespace nb::literals;

NB_MODULE(pcons_hello_ext, m) {
  m.doc() = "This is a \"hello world\" example with pcons/nanobind";
  m.def(
      "say_hello",
      [](std::string const &name) { return std::format("{}, {}!", hello(), name); },
      "name"_a);
}

cmake_minimum_required(VERSION 3.15)
project(hello_lib CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(hello_lib STATIC src/hello_lib.cpp)
target_include_directories(hello_lib PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

install(TARGETS hello_lib ARCHIVE DESTINATION lib)
install(DIRECTORY include/ DESTINATION include)

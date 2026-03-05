cmake_minimum_required(VERSION 3.20)
project(tli_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(tli_native MODULE module.cpp)

if(MSVC)
  target_compile_options(tli_native PRIVATE /W4)
else()
  target_compile_options(tli_native PRIVATE -Wall -Wextra -Wpedantic)
endif()

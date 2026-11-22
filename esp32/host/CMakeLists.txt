# Host build of the firmware's DSP core, for checking it against the Python
# analyzer:  cmake -S esp32/host -B build/host && cmake --build build/host
cmake_minimum_required(VERSION 3.16)
project(wgdsp_host CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(DSP ${CMAKE_CURRENT_SOURCE_DIR}/../lib/wgdsp/src)
add_executable(wgcli wgcli.cpp ${DSP}/wgdsp.cpp ${DSP}/wgsim.cpp)
target_include_directories(wgcli PRIVATE ${DSP})
target_compile_options(wgcli PRIVATE -Wall -Wextra -Wno-unused-parameter)

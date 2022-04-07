cmake_minimum_required(VERSION 3.21)
project(flute-ffmpeg)

set(CMAKE_CXX_STANDARD 17)

find_package(spdlog REQUIRED)
find_package(Poco REQUIRED Foundation)
find_package(Boost REQUIRED)

add_subdirectory(lib)

add_executable(flute-ffmpeg main.cpp src/FluteFfmpeg.cpp src/FluteFfmpeg.h)

target_link_libraries(flute-ffmpeg
        config++
        flute
        spdlog::spdlog
        Poco::Foundation)

cmake_minimum_required(VERSION 3.16)
project(flute-ffmpeg)

set(CMAKE_CXX_STANDARD 17)

find_package(spdlog REQUIRED)
find_package(Poco REQUIRED Foundation)
find_package(Boost REQUIRED)
find_package(PkgConfig)
find_package(spdlog REQUIRED)

pkg_check_modules(Pistache REQUIRED IMPORTED_TARGET libpistache)

add_subdirectory(lib)

add_executable(flute-ffmpeg main.cpp src/FluteFfmpeg.cpp src/FluteFfmpeg.h)
add_executable(httpserver main_server.cpp src/HttpHandler.cpp src/HttpHandler.h)

target_link_libraries(flute-ffmpeg
        config++
        flute
        spdlog::spdlog
        Poco::Foundation)

target_link_libraries(httpserver
        config++
        PkgConfig::Pistache
        spdlog::spdlog)
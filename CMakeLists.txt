cmake_minimum_required(VERSION 3.20)

project(microstructure_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)

if(NOT pybind11_DIR)
    execute_process(
        COMMAND "${Python_EXECUTABLE}" -m pybind11 --cmakedir
        OUTPUT_VARIABLE PYBIND11_CMAKE_DIR
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
    if(PYBIND11_CMAKE_DIR)
        list(APPEND CMAKE_PREFIX_PATH "${PYBIND11_CMAKE_DIR}")
    endif()
endif()

find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_cpp_lob cpp/lob_preprocessing.cpp)
target_compile_options(_cpp_lob PRIVATE
    $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-O3 -Wall -Wextra -Wpedantic>
)
set_target_properties(_cpp_lob PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/strategies/crypto"
)

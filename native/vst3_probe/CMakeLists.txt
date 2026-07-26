cmake_minimum_required(VERSION 3.22)

project(VocalProcessVst3Probe VERSION 0.1.0)

set(JUCE_ROOT "" CACHE PATH "Path to a JUCE checkout")
if(NOT JUCE_ROOT)
    set(_default_juce_root "${CMAKE_CURRENT_LIST_DIR}/../../extern/JUCE")
    if(EXISTS "${_default_juce_root}/CMakeLists.txt")
        set(JUCE_ROOT "${_default_juce_root}")
    endif()
endif()

if(NOT EXISTS "${JUCE_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR "JUCE_ROOT must point to a JUCE checkout. Expected CMakeLists.txt at: ${JUCE_ROOT}")
endif()

add_subdirectory("${JUCE_ROOT}" "${CMAKE_BINARY_DIR}/JUCE" EXCLUDE_FROM_ALL)

add_executable(VocalProcessVst3Probe
    Source/main.cpp)

target_compile_features(VocalProcessVst3Probe PRIVATE cxx_std_17)

target_compile_definitions(VocalProcessVst3Probe
    PRIVATE
        _CRT_SECURE_NO_WARNINGS
        JUCE_PLUGINHOST_VST3=1
        JUCE_USE_CURL=0
        JUCE_WEB_BROWSER=0)

target_compile_options(VocalProcessVst3Probe
    PRIVATE
        $<$<CXX_COMPILER_ID:MSVC>:/utf-8>)

target_link_libraries(VocalProcessVst3Probe
    PRIVATE
        juce::juce_audio_processors_headless
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)

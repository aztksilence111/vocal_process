cmake_minimum_required(VERSION 3.22)

project(VocalProcessBridge VERSION 0.1.0)

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

juce_add_plugin(VocalProcessBridge
    COMPANY_NAME "VocalProcess"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PLUGIN_MANUFACTURER_CODE VPrs
    PLUGIN_CODE Vpb1
    FORMATS VST3
    PRODUCT_NAME "VocalProcess Bridge")

juce_generate_juce_header(VocalProcessBridge)

target_sources(VocalProcessBridge
    PRIVATE
        Source/PluginEditor.cpp
        Source/PluginProcessor.cpp)

target_compile_features(VocalProcessBridge PRIVATE cxx_std_17)

target_compile_definitions(VocalProcessBridge
    PUBLIC
        _CRT_SECURE_NO_WARNINGS
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0)

target_compile_options(VocalProcessBridge
    PRIVATE
        $<$<CXX_COMPILER_ID:MSVC>:/utf-8>)

target_link_libraries(VocalProcessBridge
    PRIVATE
        juce::juce_audio_utils
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)

#include <juce_audio_processors_headless/juce_audio_processors_headless.h>
#include <juce_events/juce_events.h>

#include <iostream>


namespace
{
int fail (const std::string& message, int code)
{
    std::cerr << "ERROR: " << message << '\n';
    return code;
}

void printDescription (const juce::PluginDescription& description)
{
    std::cout << "name=" << description.name << '\n';
    std::cout << "manufacturer=" << description.manufacturerName << '\n';
    std::cout << "format=" << description.pluginFormatName << '\n';
    std::cout << "category=" << description.category << '\n';
    std::cout << "identifier=" << description.fileOrIdentifier << '\n';
    std::cout << "uniqueId=" << description.uniqueId << '\n';
    std::cout << "isSynth=" << (description.isInstrument ? "true" : "false") << '\n';
}
}

int main (int argc, char* argv[])
{
    juce::ignoreUnused (argc);
    juce::ScopedJuceInitialiser_GUI juceInitialiser;

    if (argc < 2)
        return fail ("Usage: VocalProcessVst3Probe <plugin.vst3>", 64);

    const juce::File plugin { juce::String::fromUTF8 (argv[1]) };

    if (! plugin.exists())
        return fail ("Plugin path does not exist: " + plugin.getFullPathName().toStdString(), 66);

    juce::VST3PluginFormatHeadless format;
    juce::OwnedArray<juce::PluginDescription> descriptions;
    format.findAllTypesForFile (descriptions, plugin.getFullPathName());

    std::cout << "path=" << plugin.getFullPathName() << '\n';
    std::cout << "descriptions=" << descriptions.size() << '\n';

    if (descriptions.isEmpty())
        return fail ("No VST3 plug-in descriptions were found", 2);

    const auto& description = *descriptions.getFirst();
    printDescription (description);

    std::unique_ptr<juce::AudioPluginInstance> instance;
    juce::AudioPluginFormatManager formatManager;
    formatManager.addFormat (std::make_unique<juce::VST3PluginFormatHeadless>());

    juce::String creationError;
    instance = formatManager.createPluginInstance (description, 44100.0, 512, creationError);

    if (instance == nullptr)
        return fail ("Instantiation failed: " + creationError.toStdString(), 3);

    instance->prepareToPlay (44100.0, 512);

    juce::AudioBuffer<float> buffer (2, 512);
    buffer.clear();
    juce::MidiBuffer midi;
    instance->processBlock (buffer, midi);
    instance->releaseResources();

    std::cout << "instantiated=true" << '\n';
    std::cout << "hasEditor=" << (instance->hasEditor() ? "true" : "false") << '\n';
    std::cout << "inputs=" << instance->getTotalNumInputChannels() << '\n';
    std::cout << "outputs=" << instance->getTotalNumOutputChannels() << '\n';

    return 0;
}

#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cstdlib>


namespace
{
const juce::Identifier stateType { "VocalProcessBridgeState" };
const juce::Identifier helperPathKey { "helperPath" };
const juce::Identifier computeDeviceKey { "computeDevice" };
const juce::Identifier sourceSeparationKey { "sourceSeparation" };
const juce::Identifier dawTimelineExportKey { "dawTimelineExport" };
}


VocalProcessBridgeAudioProcessor::VocalProcessBridgeAudioProcessor()
    : AudioProcessor (BusesProperties()
                          .withInput  ("Input",  juce::AudioChannelSet::stereo(), true)
                          .withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      state (stateType)
{
    if (const auto* helperPath = std::getenv ("VOCAL_PROCESS_HELPER"))
        state.setProperty (helperPathKey, juce::String (helperPath), nullptr);
    else
        state.setProperty (helperPathKey, "VocalProcess.exe", nullptr);

    state.setProperty (computeDeviceKey, "auto", nullptr);
    state.setProperty (sourceSeparationKey, "auto", nullptr);
    state.setProperty (dawTimelineExportKey, true, nullptr);
}

void VocalProcessBridgeAudioProcessor::prepareToPlay (double, int) {}

void VocalProcessBridgeAudioProcessor::releaseResources() {}

bool VocalProcessBridgeAudioProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    const auto& input = layouts.getMainInputChannelSet();
    const auto& output = layouts.getMainOutputChannelSet();

    return input == output && (output == juce::AudioChannelSet::mono()
                               || output == juce::AudioChannelSet::stereo());
}

void VocalProcessBridgeAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages)
{
    juce::ScopedNoDenormals noDenormals;
    midiMessages.clear();

    for (auto channel = getTotalNumInputChannels(); channel < getTotalNumOutputChannels(); ++channel)
        buffer.clear (channel, 0, buffer.getNumSamples());
}

juce::AudioProcessorEditor* VocalProcessBridgeAudioProcessor::createEditor()
{
    return new VocalProcessBridgeAudioProcessorEditor (*this);
}

bool VocalProcessBridgeAudioProcessor::hasEditor() const
{
    return true;
}

const juce::String VocalProcessBridgeAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool VocalProcessBridgeAudioProcessor::acceptsMidi() const
{
    return false;
}

bool VocalProcessBridgeAudioProcessor::producesMidi() const
{
    return false;
}

bool VocalProcessBridgeAudioProcessor::isMidiEffect() const
{
    return false;
}

double VocalProcessBridgeAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int VocalProcessBridgeAudioProcessor::getNumPrograms()
{
    return 1;
}

int VocalProcessBridgeAudioProcessor::getCurrentProgram()
{
    return 0;
}

void VocalProcessBridgeAudioProcessor::setCurrentProgram (int) {}

const juce::String VocalProcessBridgeAudioProcessor::getProgramName (int)
{
    return {};
}

void VocalProcessBridgeAudioProcessor::changeProgramName (int, const juce::String&) {}

void VocalProcessBridgeAudioProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    const juce::ScopedLock lock (stateLock);
    juce::MemoryOutputStream stream (destData, false);
    state.writeToStream (stream);
}

void VocalProcessBridgeAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    auto restored = juce::ValueTree::readFromData (data, static_cast<size_t> (sizeInBytes));
    if (! restored.isValid() || restored.getType() != stateType)
        return;

    const juce::ScopedLock lock (stateLock);
    state = restored;
}

juce::String VocalProcessBridgeAudioProcessor::getSetting (const juce::Identifier& key) const
{
    const juce::ScopedLock lock (stateLock);
    return state.getProperty (key).toString();
}

bool VocalProcessBridgeAudioProcessor::getBoolSetting (const juce::Identifier& key, bool fallback) const
{
    const juce::ScopedLock lock (stateLock);
    return state.hasProperty (key) ? static_cast<bool> (state.getProperty (key)) : fallback;
}

void VocalProcessBridgeAudioProcessor::setSetting (const juce::Identifier& key, const juce::String& value)
{
    const juce::ScopedLock lock (stateLock);
    state.setProperty (key, value, nullptr);
}

void VocalProcessBridgeAudioProcessor::setBoolSetting (const juce::Identifier& key, bool value)
{
    const juce::ScopedLock lock (stateLock);
    state.setProperty (key, value, nullptr);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new VocalProcessBridgeAudioProcessor();
}

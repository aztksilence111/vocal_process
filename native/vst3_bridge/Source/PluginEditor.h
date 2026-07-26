#pragma once

#include "PluginProcessor.h"


class VocalProcessBridgeAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                                     private juce::Button::Listener,
                                                     private juce::Timer
{
public:
    explicit VocalProcessBridgeAudioProcessorEditor (VocalProcessBridgeAudioProcessor&);
    ~VocalProcessBridgeAudioProcessorEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void buttonClicked (juce::Button*) override;
    void timerCallback() override;

    void initialisePathRow (juce::Label& label,
                            juce::TextEditor& editor,
                            juce::TextButton& browseButton,
                            const juce::String& labelText,
                            const juce::Identifier& settingKey);
    void layoutPathRow (juce::Label& label,
                        juce::TextEditor& editor,
                        juce::TextButton& browseButton,
                        juce::Rectangle<int> row);

    void browseForPath (juce::TextEditor& editor, bool directory, bool save, const juce::String& title);
    void loadSettings();
    void saveSettings();

    void runRenderRequest();
    void runAnalysisRequest();
    void startHelperProcess (const juce::StringArray& args,
                             const juce::File& responseFile,
                             const juce::String& operation);
    bool validateInputs (bool renderMode);
    juce::File bridgeWorkDirectory() const;
    static juce::String createRequestId();

    void updateStatus (const juce::String& text, bool error = false);
    void finishHelperProcess();
    juce::String describeResponse (const juce::File& responseFile, const juce::String& operation) const;

    VocalProcessBridgeAudioProcessor& processor;

    juce::Label helperLabel;
    juce::TextEditor helperEditor;
    juce::TextButton helperBrowseButton { "Browse" };

    juce::Label referenceLabel;
    juce::TextEditor referenceEditor;
    juce::TextButton referenceBrowseButton { "Browse" };

    juce::Label materialLabel;
    juce::TextEditor materialEditor;
    juce::TextButton materialBrowseButton { "Browse" };

    juce::Label outputLabel;
    juce::TextEditor outputEditor;
    juce::TextButton outputBrowseButton { "Browse" };

    juce::Label lyricsLabel;
    juce::TextEditor lyricsEditor;
    juce::TextButton lyricsBrowseButton { "Browse" };

    juce::Label computeLabel;
    juce::ComboBox computeDeviceBox;
    juce::Label sourceSeparationLabel;
    juce::ComboBox sourceSeparationBox;
    juce::ToggleButton dawTimelineToggle { "DAW timeline" };

    juce::TextButton renderButton { "Render" };
    juce::TextButton analyzeButton { "Analyze" };
    juce::Label statusLabel;

    juce::ChildProcess helperProcess;
    std::unique_ptr<juce::FileChooser> fileChooser;
    juce::File pendingResponseFile;
    juce::String pendingOperation;
    juce::String helperOutput;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VocalProcessBridgeAudioProcessorEditor)
};

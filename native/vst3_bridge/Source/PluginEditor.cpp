#include "PluginEditor.h"


namespace
{
const juce::Identifier helperPathKey { "helperPath" };
const juce::Identifier referencePathKey { "referencePath" };
const juce::Identifier materialDirectoryKey { "materialDirectory" };
const juce::Identifier outputPathKey { "outputPath" };
const juce::Identifier lyricsFileKey { "lyricsFile" };
const juce::Identifier computeDeviceKey { "computeDevice" };
const juce::Identifier sourceSeparationKey { "sourceSeparation" };
const juce::Identifier dawTimelineExportKey { "dawTimelineExport" };

constexpr int margin = 12;
constexpr int rowHeight = 30;
constexpr int labelWidth = 104;
constexpr int browseWidth = 76;
}


VocalProcessBridgeAudioProcessorEditor::VocalProcessBridgeAudioProcessorEditor (VocalProcessBridgeAudioProcessor& owner)
    : AudioProcessorEditor (&owner),
      processor (owner)
{
    initialisePathRow (helperLabel, helperEditor, helperBrowseButton, "Helper", helperPathKey);
    initialisePathRow (referenceLabel, referenceEditor, referenceBrowseButton, "Reference", referencePathKey);
    initialisePathRow (materialLabel, materialEditor, materialBrowseButton, "Materials", materialDirectoryKey);
    initialisePathRow (outputLabel, outputEditor, outputBrowseButton, "Output", outputPathKey);
    initialisePathRow (lyricsLabel, lyricsEditor, lyricsBrowseButton, "Lyrics", lyricsFileKey);

    computeLabel.setText ("Compute", juce::dontSendNotification);
    computeLabel.setJustificationType (juce::Justification::centredLeft);
    addAndMakeVisible (computeLabel);

    computeDeviceBox.addItem ("auto", 1);
    computeDeviceBox.addItem ("cpu", 2);
    computeDeviceBox.addItem ("cuda", 3);
    computeDeviceBox.onChange = [this] { saveSettings(); };
    addAndMakeVisible (computeDeviceBox);

    sourceSeparationLabel.setText ("Vocals", juce::dontSendNotification);
    sourceSeparationLabel.setJustificationType (juce::Justification::centredLeft);
    addAndMakeVisible (sourceSeparationLabel);

    sourceSeparationBox.addItem ("auto", 1);
    sourceSeparationBox.addItem ("never", 2);
    sourceSeparationBox.addItem ("always", 3);
    sourceSeparationBox.onChange = [this] { saveSettings(); };
    addAndMakeVisible (sourceSeparationBox);

    dawTimelineToggle.onClick = [this] { saveSettings(); };
    addAndMakeVisible (dawTimelineToggle);

    renderButton.addListener (this);
    analyzeButton.addListener (this);
    addAndMakeVisible (renderButton);
    addAndMakeVisible (analyzeButton);

    statusLabel.setText ("Ready", juce::dontSendNotification);
    statusLabel.setJustificationType (juce::Justification::centredLeft);
    statusLabel.setColour (juce::Label::textColourId, juce::Colours::whitesmoke);
    addAndMakeVisible (statusLabel);

    loadSettings();
    setSize (650, 330);
}

VocalProcessBridgeAudioProcessorEditor::~VocalProcessBridgeAudioProcessorEditor()
{
    stopTimer();
    if (helperProcess.isRunning())
        helperProcess.kill();
}

void VocalProcessBridgeAudioProcessorEditor::paint (juce::Graphics& graphics)
{
    graphics.fillAll (juce::Colour (0xff202124));
    graphics.setColour (juce::Colour (0xfff1f3f4));
    graphics.setFont (juce::FontOptions (18.0f, juce::Font::bold));
    graphics.drawText ("VocalProcess Bridge", getLocalBounds().removeFromTop (34).reduced (margin, 0),
                       juce::Justification::centredLeft);
}

void VocalProcessBridgeAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced (margin);
    bounds.removeFromTop (34);

    layoutPathRow (helperLabel, helperEditor, helperBrowseButton, bounds.removeFromTop (rowHeight));
    bounds.removeFromTop (6);
    layoutPathRow (referenceLabel, referenceEditor, referenceBrowseButton, bounds.removeFromTop (rowHeight));
    bounds.removeFromTop (6);
    layoutPathRow (materialLabel, materialEditor, materialBrowseButton, bounds.removeFromTop (rowHeight));
    bounds.removeFromTop (6);
    layoutPathRow (outputLabel, outputEditor, outputBrowseButton, bounds.removeFromTop (rowHeight));
    bounds.removeFromTop (6);
    layoutPathRow (lyricsLabel, lyricsEditor, lyricsBrowseButton, bounds.removeFromTop (rowHeight));
    bounds.removeFromTop (10);

    auto computeRow = bounds.removeFromTop (rowHeight);
    computeLabel.setBounds (computeRow.removeFromLeft (labelWidth));
    computeDeviceBox.setBounds (computeRow.removeFromLeft (130));
    computeRow.removeFromLeft (14);
    sourceSeparationLabel.setBounds (computeRow.removeFromLeft (58));
    sourceSeparationBox.setBounds (computeRow.removeFromLeft (110));
    computeRow.removeFromLeft (14);
    dawTimelineToggle.setBounds (computeRow.removeFromLeft (160));
    bounds.removeFromTop (12);

    auto buttonRow = bounds.removeFromTop (34);
    renderButton.setBounds (buttonRow.removeFromLeft (112));
    buttonRow.removeFromLeft (8);
    analyzeButton.setBounds (buttonRow.removeFromLeft (112));
    bounds.removeFromTop (10);

    statusLabel.setBounds (bounds.removeFromTop (48));
}

void VocalProcessBridgeAudioProcessorEditor::buttonClicked (juce::Button* button)
{
    if (button == &helperBrowseButton)
        browseForPath (helperEditor, false, false, "Select VocalProcess.exe");
    else if (button == &referenceBrowseButton)
        browseForPath (referenceEditor, false, false, "Select reference audio");
    else if (button == &materialBrowseButton)
        browseForPath (materialEditor, true, false, "Select material folder");
    else if (button == &outputBrowseButton)
        browseForPath (outputEditor, false, true, "Select output file");
    else if (button == &lyricsBrowseButton)
        browseForPath (lyricsEditor, false, false, "Select optional lyrics file");
    else if (button == &renderButton)
        runRenderRequest();
    else if (button == &analyzeButton)
        runAnalysisRequest();
}

void VocalProcessBridgeAudioProcessorEditor::timerCallback()
{
    if (helperProcess.isRunning())
        return;

    stopTimer();
    finishHelperProcess();
}

void VocalProcessBridgeAudioProcessorEditor::initialisePathRow (juce::Label& label,
                                                               juce::TextEditor& editor,
                                                               juce::TextButton& browseButton,
                                                               const juce::String& labelText,
                                                               const juce::Identifier& settingKey)
{
    label.setText (labelText, juce::dontSendNotification);
    label.setJustificationType (juce::Justification::centredLeft);
    label.setColour (juce::Label::textColourId, juce::Colours::whitesmoke);
    addAndMakeVisible (label);

    editor.setText (processor.getSetting (settingKey), juce::dontSendNotification);
    editor.onTextChange = [this] { saveSettings(); };
    addAndMakeVisible (editor);

    browseButton.addListener (this);
    addAndMakeVisible (browseButton);
}

void VocalProcessBridgeAudioProcessorEditor::layoutPathRow (juce::Label& label,
                                                           juce::TextEditor& editor,
                                                           juce::TextButton& browseButton,
                                                           juce::Rectangle<int> row)
{
    label.setBounds (row.removeFromLeft (labelWidth));
    browseButton.setBounds (row.removeFromRight (browseWidth));
    row.removeFromRight (8);
    editor.setBounds (row);
}

void VocalProcessBridgeAudioProcessorEditor::browseForPath (juce::TextEditor& editor,
                                                           bool directory,
                                                           bool save,
                                                           const juce::String& title)
{
    auto initial = editor.getText().isNotEmpty()
                       ? juce::File (editor.getText())
                       : juce::File::getSpecialLocation (juce::File::userHomeDirectory);
    fileChooser = std::make_unique<juce::FileChooser> (title, initial, juce::String(), true, false, this);

    auto chooserFlags = static_cast<int> (save ? juce::FileBrowserComponent::saveMode
                                               : juce::FileBrowserComponent::openMode);
    chooserFlags |= static_cast<int> (directory ? juce::FileBrowserComponent::canSelectDirectories
                                                : juce::FileBrowserComponent::canSelectFiles);

    auto safeThis = juce::Component::SafePointer<VocalProcessBridgeAudioProcessorEditor> (this);
    auto* targetEditor = &editor;
    fileChooser->launchAsync (chooserFlags, [safeThis, targetEditor] (const juce::FileChooser& chooser)
    {
        if (safeThis == nullptr)
            return;

        const auto result = chooser.getResult();
        if (result == juce::File())
            return;

        targetEditor->setText (result.getFullPathName(), juce::sendNotification);
        safeThis->saveSettings();
    });
}

void VocalProcessBridgeAudioProcessorEditor::loadSettings()
{
    helperEditor.setText (processor.getSetting (helperPathKey), juce::dontSendNotification);
    referenceEditor.setText (processor.getSetting (referencePathKey), juce::dontSendNotification);
    materialEditor.setText (processor.getSetting (materialDirectoryKey), juce::dontSendNotification);
    outputEditor.setText (processor.getSetting (outputPathKey), juce::dontSendNotification);
    lyricsEditor.setText (processor.getSetting (lyricsFileKey), juce::dontSendNotification);

    const auto compute = processor.getSetting (computeDeviceKey);
    computeDeviceBox.setSelectedId (compute == "cpu" ? 2 : (compute == "cuda" ? 3 : 1), juce::dontSendNotification);
    const auto sourceSeparation = processor.getSetting (sourceSeparationKey);
    sourceSeparationBox.setSelectedId (sourceSeparation == "never" ? 2 : (sourceSeparation == "always" ? 3 : 1),
                                       juce::dontSendNotification);
    dawTimelineToggle.setToggleState (processor.getBoolSetting (dawTimelineExportKey, true), juce::dontSendNotification);
}

void VocalProcessBridgeAudioProcessorEditor::saveSettings()
{
    processor.setSetting (helperPathKey, helperEditor.getText());
    processor.setSetting (referencePathKey, referenceEditor.getText());
    processor.setSetting (materialDirectoryKey, materialEditor.getText());
    processor.setSetting (outputPathKey, outputEditor.getText());
    processor.setSetting (lyricsFileKey, lyricsEditor.getText());
    processor.setSetting (computeDeviceKey, computeDeviceBox.getText());
    processor.setSetting (sourceSeparationKey, sourceSeparationBox.getText());
    processor.setBoolSetting (dawTimelineExportKey, dawTimelineToggle.getToggleState());
}

void VocalProcessBridgeAudioProcessorEditor::runRenderRequest()
{
    if (! validateInputs (true))
        return;

    saveSettings();
    const auto requestId = createRequestId();
    const auto workDir = bridgeWorkDirectory();
    workDir.createDirectory();
    const auto requestFile = workDir.getChildFile (requestId + ".request.json");
    const auto responseFile = workDir.getChildFile (requestId + ".response.json");

    auto* object = new juce::DynamicObject();
    object->setProperty ("format", "vocal_process_vst3_bridge_request_v1");
    object->setProperty ("request_id", requestId);
    object->setProperty ("command", "render_timeline");
    object->setProperty ("reference_path", referenceEditor.getText());
    object->setProperty ("material_directory", materialEditor.getText());
    object->setProperty ("lyrics_file", lyricsEditor.getText());
    object->setProperty ("output_path", outputEditor.getText());
    object->setProperty ("daw_timeline_export", dawTimelineToggle.getToggleState());
    object->setProperty ("compute_device", computeDeviceBox.getText());
    object->setProperty ("source_separation", sourceSeparationBox.getText());
    object->setProperty ("overwrite", true);

    const juce::var request (object);
    requestFile.replaceWithText (juce::JSON::toString (request, true));

    juce::StringArray args;
    args.add (helperEditor.getText());
    args.add ("vst3-bridge");
    args.add (requestFile.getFullPathName());
    args.add ("--response");
    args.add (responseFile.getFullPathName());
    startHelperProcess (args, responseFile, "render");
}

void VocalProcessBridgeAudioProcessorEditor::runAnalysisRequest()
{
    if (! validateInputs (false))
        return;

    saveSettings();
    const auto requestId = createRequestId();
    const auto workDir = bridgeWorkDirectory();
    workDir.createDirectory();
    const auto responseFile = workDir.getChildFile (requestId + ".analysis.json");

    juce::StringArray args;
    args.add (helperEditor.getText());
    args.add ("analyze");
    args.add (referenceEditor.getText());
    args.add (materialEditor.getText());
    args.add ("--output");
    args.add (responseFile.getFullPathName());
    args.add ("--compute-device");
    args.add (computeDeviceBox.getText());
    args.add ("--source-separation");
    args.add (sourceSeparationBox.getText());
    if (lyricsEditor.getText().isNotEmpty())
    {
        args.add ("--lyrics-file");
        args.add (lyricsEditor.getText());
    }

    startHelperProcess (args, responseFile, "analyze");
}

void VocalProcessBridgeAudioProcessorEditor::startHelperProcess (const juce::StringArray& args,
                                                                const juce::File& responseFile,
                                                                const juce::String& operation)
{
    if (helperProcess.isRunning())
    {
        updateStatus ("Helper is already running", true);
        return;
    }

    helperOutput.clear();
    pendingResponseFile = responseFile;
    pendingOperation = operation;

    if (! helperProcess.start (args, juce::ChildProcess::wantStdOut | juce::ChildProcess::wantStdErr))
    {
        updateStatus ("Could not start helper: " + helperEditor.getText(), true);
        return;
    }

    updateStatus ("Running " + operation + " helper...");
    startTimer (500);
}

bool VocalProcessBridgeAudioProcessorEditor::validateInputs (bool renderMode)
{
    if (helperEditor.getText().trim().isEmpty())
    {
        updateStatus ("Set the helper executable path first", true);
        return false;
    }

    if (! juce::File (referenceEditor.getText()).existsAsFile())
    {
        updateStatus ("Reference audio does not exist", true);
        return false;
    }

    if (! juce::File (materialEditor.getText()).isDirectory())
    {
        updateStatus ("Material folder does not exist", true);
        return false;
    }

    if (renderMode && outputEditor.getText().trim().isEmpty())
    {
        updateStatus ("Set an output file before rendering", true);
        return false;
    }

    if (lyricsEditor.getText().isNotEmpty() && ! juce::File (lyricsEditor.getText()).existsAsFile())
    {
        updateStatus ("Lyrics file does not exist", true);
        return false;
    }

    return true;
}

juce::File VocalProcessBridgeAudioProcessorEditor::bridgeWorkDirectory() const
{
    return juce::File::getSpecialLocation (juce::File::tempDirectory)
        .getChildFile ("VocalProcessBridge");
}

juce::String VocalProcessBridgeAudioProcessorEditor::createRequestId()
{
    return "vst3-" + juce::Uuid().toString();
}

void VocalProcessBridgeAudioProcessorEditor::updateStatus (const juce::String& text, bool error)
{
    statusLabel.setColour (juce::Label::textColourId, error ? juce::Colours::lightsalmon
                                                            : juce::Colours::whitesmoke);
    statusLabel.setText (text, juce::dontSendNotification);
}

void VocalProcessBridgeAudioProcessorEditor::finishHelperProcess()
{
    helperOutput += helperProcess.readAllProcessOutput();
    const auto message = describeResponse (pendingResponseFile, pendingOperation);
    const auto failed = message.containsIgnoreCase ("failed")
                     || message.containsIgnoreCase ("review_required")
                     || ! pendingResponseFile.existsAsFile();
    updateStatus (message, failed);
}

juce::String VocalProcessBridgeAudioProcessorEditor::describeResponse (const juce::File& responseFile,
                                                                       const juce::String& operation) const
{
    if (! responseFile.existsAsFile())
        return "Helper failed without response: " + helperOutput.trim().substring (0, 220);

    const auto responseText = responseFile.loadFileAsString();
    const auto parsed = juce::JSON::parse (responseText);
    if (auto* object = parsed.getDynamicObject())
    {
        if (operation == "render")
        {
            const auto ok = static_cast<bool> (object->getProperty ("ok"));
            const auto outputPath = object->getProperty ("output_path").toString();
            const auto diagnosticsPath = object->getProperty ("diagnostics_path").toString();
            if (ok)
                return "Render complete: " + outputPath + " | Diagnostics: " + diagnosticsPath;

            return "Render failed: " + object->getProperty ("message").toString();
        }

        const auto status = object->getProperty ("status").toString();
        const auto summaryVar = object->getProperty ("summary");
        const auto warningCount = summaryVar.getDynamicObject() != nullptr
                                      ? summaryVar.getDynamicObject()->getProperty ("warning_count").toString()
                                      : juce::String();
        return "Analysis " + status + ": " + responseFile.getFullPathName()
             + " | warnings: " + warningCount;
    }

    return "Helper finished; response is not valid JSON: " + responseFile.getFullPathName();
}

# VocalProcess VST3 Bridge

This folder contains the native VST3 bridge plug-in for VocalProcess.

The plug-in is intentionally a DAW-facing control surface, not a real-time model
or FFmpeg processor. Audio passes through unchanged. The editor collects paths
and starts the VocalProcess helper process outside the audio callback:

- `VocalProcess.exe vst3-bridge <request.json> --response <response.json>`
- `VocalProcess.exe analyze <reference> <materials> --output <analysis.json>`

## Build

Requirements:

1. Visual Studio with C++ and CMake tools.
2. A JUCE checkout at `extern\JUCE`, or pass `-JuceRoot`.

Build command:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_vst3_bridge.ps1
```

Output:

```text
build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3
```

If this bundle exists before running `scripts\build_portable.ps1`, the portable
package will include it under:

```text
VocalProcess\plugins\VocalProcess Bridge.vst3
```

## Runtime

In the plug-in editor:

1. Set `Helper` to the packaged `VocalProcess.exe`.
2. Select a reference audio file and material folder.
3. Optionally select a lyrics file.
4. Set `Vocals` to `never` only when the reference is already an isolated vocal stem.
5. Use `Analyze` to write a preflight report before rendering.
6. Use `Render` to send a bridge request to the helper and write the requested output.

Set the `VOCAL_PROCESS_HELPER` environment variable to prefill the helper path.

## Validation

Build and probe the generated VST3 bundle:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\probe_vst3_bridge.ps1
```

Install the bundle to the common 64-bit VST3 folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_vst3_bridge.ps1 -Force
```

Host checks currently available in this repository:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\host_test_reaper_vst3.ps1
powershell -ExecutionPolicy Bypass -File scripts\host_test_flstudio_vst3.ps1
powershell -ExecutionPolicy Bypass -File scripts\check_melodyne_context.ps1
```

The REAPER script performs an isolated scan and checks REAPER's VST cache. The
FL Studio script launches Plugin Manager and checks whether the verified plug-in
database changed; if it reports no automatic scan result, finish the test with
Plugin Manager > Find installed plugins. The Melodyne script reports whether
standard 64-bit or legacy 32-bit Melodyne/Celemony paths exist; Melodyne is not
treated as a generic VST3 host for this bridge.

# DAW Integration Plan

## Current Goal

The project is moving from a single rendered WAV output toward DAW-facing workflows where stretched material clips remain independently editable on a timeline.

## Current Architecture Summary

1. Python/Tkinter GUI remains the local operator interface.
2. FFmpeg/FFprobe remain the audio rendering and metadata tools.
3. Rubber Band through FFmpeg remains the stretch/compression engine.
4. The original audio is a timing reference.
5. The material folder is the source set for rendered output.

## Phase 1: DAW Timeline Export

Status: implemented in this branch.

Output shape:

```text
<reference>_daw/
  <reference>.rpp
  timeline.json
  timeline.csv
  audio/
    0001_<clip>.wav
    0002_<clip>.wav
```

Behavior:

1. Material clips are ordered by the model-assisted planner when the batch workflow is used.
2. Filename order remains only a fallback when recognition and filename text provide no usable signal.
3. Each source material clip receives its own target duration instead of relying only on one global ratio.
4. Each clip is time-stretched individually with FFmpeg `rubberband` before timeline placement.
5. Pitch and formants are preserved through `pitch=1` and `formant=preserved`.
6. `timeline.json` and `timeline.csv` preserve start time, target duration, per-clip tempo, quality warning, source path, and rendered path.
7. The generated REAPER `.rpp` places each rendered clip as a separate item on the timeline.

This phase solves the immediate editing requirement: after import, each material clip remains individually movable, trimmable, replaceable, and editable in the DAW.

## Phase 2: Broader DAW Interchange

Status: started.

1. Add more host-specific project exporters where practical.
2. Keep the timeline manifest as the internal source of truth.
3. Generate DAW-specific output from the same manifest.
4. Avoid proprietary or underdocumented interchange formats until there is a clear target host and test environment.

Candidate formats:

1. REAPER `.rpp`: practical first format because it is text-based and easy to generate.
2. Generic `timeline.json` and `timeline.csv`: useful for debugging, scripts, and future converters.
3. Full-length timeline lane WAVs: practical host-agnostic fallback for tools that can import audio but do not read clip offsets.
4. Broadcast Wave timestamp WAVs: practical for VEGAS-style handoff when the host supports timestamp placement.
5. AAF/OMF: possible later, but higher risk and needs dedicated library/tooling and DAW validation.

Current interchange progress:

1. `audio_processor.handoff` is the shared handoff layer for host-specific exports.
2. `audio-processor export-melodyne` writes `melodyne_full.wav`, per-clip full-timeline lane WAVs, `melodyne_handoff.json`, `melodyne_handoff.csv`, and a parallel REAPER `.rpp` timeline.
3. `audio-processor export-vegas` writes `vegas_full.wav`, full-timeline lane WAVs, manifest/CSV, and BWF timestamp WAV clips under `vegas_bwf`.
4. The handoff manifest records clip start seconds, target duration, rendered clip path, lane path, BWF path when applicable, stretch strategy, and text hint.
5. Melodyne is handled as a manual pitch-edit/import target. The robust path is a full continuous WAV or full-length lane WAVs, not treating Melodyne as a VST3 host.
6. VEGAS is handled through file import semantics first. Direct VEGAS project/session manipulation is deferred until there is a stable local automation path and manual host validation.

## Phase 3: VST3 Bridge

VST3 is not just another file export. It is a plug-in/host integration layer. The official Steinberg SDK is a C++ SDK, and Windows VST3 delivery is a plug-in bundle built around a native binary.

Recommended bridge architecture:

1. Keep the Python application as the offline renderer and project exporter.
2. Add a native VST3 plug-in as a separate package only after the timeline export workflow is stable.
3. Let the VST3 plug-in communicate with the existing renderer through a small bridge protocol, not by embedding all Python GUI logic inside the plug-in.
4. Keep real-time audio processing inside the native VST3 side minimal and deterministic.
5. Use the Python side for heavy offline rendering, file analysis, project export, and batch operations.

Possible bridge model:

1. VST3 plug-in exposes parameters and a small UI entry point inside the DAW.
2. Plug-in sends render/export requests to a local helper process.
3. Helper process uses the current Python/FFmpeg engine.
4. Generated WAV clips and timeline metadata are returned to the user or written beside the DAW project.

Current bridge progress:

1. `audio_processor.vst3_bridge` defines a JSON request/response protocol for a future native VST3 plug-in or host script.
2. `audio-processor vst3-bridge --template` prints a request template.
3. `audio-processor vst3-bridge request.json --response response.json` runs the same offline batch renderer through a file-based bridge helper.
4. The bridge defaults `.rpp` requests to DAW timeline export and returns output and diagnostics paths.
5. `audio-processor vst3-bridge --watch <requests> --responses <responses>` runs a persistent helper loop for DAW/native clients.
6. `audio-processor vst3-bridge --contract` prints the file naming and heartbeat contract.
7. `native\vst3_bridge` contains a JUCE/MSVC VST3 plug-in that passes audio through unchanged and calls `VocalProcess.exe` for render/analyze work outside the audio callback.
8. `scripts\build_vst3_bridge.ps1` builds `VocalProcess Bridge.vst3`; `scripts\build_portable.ps1` bundles it under `VocalProcess\plugins` when the VST3 build exists.
9. This is intentionally offline and non-real-time; the native VST3 side calls a helper process rather than running Python or FFmpeg inside the audio callback.
10. `native\vst3_probe` and `scripts\probe_vst3_bridge.ps1` provide a headless JUCE host probe that scans and instantiates the generated VST3 bundle.
11. `scripts\install_vst3_bridge.ps1` installs the bundle into the common 64-bit VST3 folder for host testing.
12. The VST3 editor and JSON bridge requests expose `source_separation` so a user can skip Demucs when the reference is already isolated vocals.
13. Portable packaging is split into `VocalProcess-portable.zip` without VST3 and `VocalProcess-portable-vst3.zip` with the native bridge bundle.
14. The helper runtime is pinned to Python 3.11, while UVR headless separation is isolated in a Python 3.10 worker and reached only through a process boundary.

Host validation status on this machine:

1. JUCE headless probe: passed. The probe found one VST3 description for `VocalProcess Bridge`, instantiated it, and confirmed 2 inputs / 2 outputs.
2. REAPER 7.33 x64: passed. `scripts\host_test_reaper_vst3.ps1` launched REAPER with an isolated config and confirmed `VocalProcess Bridge (VocalProcess)` in `reaper-vstplugins64.ini`.
3. FL Studio 2024: partial. The bridge is installed under `C:\Program Files\Common Files\VST3`, and Plugin Manager launches, but no documented non-interactive scan command is available. Final FL registration should be done with Plugin Manager > Find installed plugins rather than editing FL's database files by hand.
4. Melodyne/Celemony: considered as an import/edit workflow target. This machine's practical compatibility target is Melodyne 3.2 at `E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe`. The standalone launch smoke passed, and generated full-timeline WAV handoff can be opened by argument. Use exported PCM WAV, full-length timeline lanes, or DAW timeline output with Melodyne/ARA inside a compatible DAW.
5. VEGAS: current support is a generated file handoff. `export-vegas` writes Broadcast Wave timestamp clips and full-length fallback lanes; manual VEGAS import validation remains the next host-side check.

Runtime optimization status:

1. Reference analysis caching avoids repeating Demucs/ASR/speaker embedding work for the same reference, lyrics, compute device, ASR backend, and source separation mode.
2. Loaded ASR/VAD/speaker models are cached inside the helper process.
3. DAW timeline export reuses exact duplicate stretched clip renders.
4. Preflight analysis reports repeated text and duplicate render groups as safe review hints.

Risks:

1. VST3 requires MSVC/CMake and SDK setup on Windows.
2. DAW hosts differ in how plug-ins may trigger file writes or UI flows.
3. Python and FFmpeg are not appropriate for hard real-time audio callbacks.
4. Code signing and installer layout become more important for end users.

## Immediate Next Work

1. Run the FL Studio Plugin Manager manual scan and confirm the bridge appears in the verified plug-in database.
2. Manually import `export-vegas` BWF clips into VEGAS and confirm timestamp placement behavior in the installed VEGAS version.
3. Continue testing the bridge in the next available 64-bit hosts from the user list, prioritizing hosts that support normal VST3 scanning.
4. Keep Melodyne support focused on rendered WAV/DAW timeline handoff unless a current Melodyne 3.x or 5.x ARA/SDK path becomes an explicit requirement.
5. Add project exporters for confirmed hosts only.

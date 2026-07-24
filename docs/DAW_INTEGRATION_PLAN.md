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

1. Material clips are sorted by filename.
2. A global tempo ratio is computed as material total duration divided by reference duration.
3. Each source material clip is time-stretched individually with the same ratio.
4. Pitch and formants are preserved through FFmpeg `rubberband` options.
5. Each stretched clip is rendered as its own DAW-friendly WAV file.
6. `timeline.json` and `timeline.csv` preserve start time, target duration, source path, and rendered path.
7. The generated REAPER `.rpp` places each rendered clip as a separate item on the timeline.

This phase solves the immediate editing requirement: after import, each material clip remains individually movable, trimmable, replaceable, and editable in the DAW.

## Phase 2: Broader DAW Interchange

Next target after user testing:

1. Add more host-specific project exporters where practical.
2. Keep the timeline manifest as the internal source of truth.
3. Generate DAW-specific output from the same manifest.
4. Avoid proprietary or underdocumented interchange formats until there is a clear target host and test environment.

Candidate formats:

1. REAPER `.rpp`: practical first format because it is text-based and easy to generate.
2. Generic `timeline.json` and `timeline.csv`: useful for debugging, scripts, and future converters.
3. AAF/OMF: possible later, but higher risk and needs dedicated library/tooling and DAW validation.

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

Risks:

1. VST3 requires MSVC/CMake and SDK setup on Windows.
2. DAW hosts differ in how plug-ins may trigger file writes or UI flows.
3. Python and FFmpeg are not appropriate for hard real-time audio callbacks.
4. Code signing and installer layout become more important for end users.

## Immediate Next Work

1. Test the generated `.rpp` in REAPER.
2. Ask target DAW users which hosts must be supported first.
3. Add project exporters for confirmed hosts only.
4. Define the VST3 bridge protocol after the timeline manifest stabilizes.

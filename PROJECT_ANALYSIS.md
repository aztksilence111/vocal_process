# Project Analysis

## Standing Project Rules

The durable project rules are maintained in `PROJECT_RULES.md`. In particular, completed work branches should replace `main` after verification, while the previous `main` is backed up to a separate archive/backup branch first. Major verified changes should be committed and pushed to GitHub automatically unless the user explicitly asks to keep them local.

## 2026-07-23: Python and FFmpeg Environment Setup

### Goal

The project requires normal system access to Python and FFmpeg. The environment must use standard installation and PATH configuration instead of project-level wrappers, bundled binaries, or hard-coded bypasses.

### Initial State

1. `python` and `py` resolved first to WindowsApps execution aliases, which failed in this Codex session.
2. A user-local Python shim existed at `C:\Users\WIN11\AppData\Local\Python\bin\python.exe` and could run Python 3.14.4 when invoked by full path.
3. `ffmpeg` was not installed or not available on PATH.
4. Chocolatey was installed and usable.

### Decision

Use Chocolatey as the standard Windows package manager for this machine:

1. Install Python through the Chocolatey `python` package.
2. Install FFmpeg through the Chocolatey `ffmpeg` package.
3. Rely on normal Machine PATH entries created by the installers.
4. Verify with command-line checks and a Python-to-FFmpeg subprocess call.

### Result

Installed packages include:

1. `python 3.14.6`
2. `python3 3.14.6`
3. `python314 3.14.6`
4. `ffmpeg 8.1.2`

Resolved command paths in a refreshed shell environment:

1. `python`: `C:\Python314\python.exe`
2. `py`: `C:\WINDOWS\py.exe`
3. `ffmpeg`: `C:\ProgramData\chocolatey\bin\ffmpeg.exe`

Verification passed:

1. `python --version`: `Python 3.14.6`
2. `pip --version`: pip from `C:\Python314\Lib\site-packages`
3. `py --version`: `Python 3.14.6`
4. `ffmpeg -version`: `ffmpeg version 8.1.2`
5. Python successfully invoked `ffmpeg -version` through `subprocess.run`.

### Notes

The current Codex process inherited the old PATH before installation. A newly opened terminal should use the updated Machine PATH automatically. For commands run inside this existing Codex session, refreshing PATH from the Machine and User environment variables before verification reflects the new terminal state.

## 2026-07-23: Audio Processor MVP

### Goal

Continue the project with a first usable Python and FFmpeg implementation. The MVP should avoid non-standard workarounds and use normal system tools through PATH.

### Current Context

1. The repository had no application source files yet.
2. Python and FFmpeg were verified at the system level.
3. The branch for this work is `codex/update-python-and-continue-project`.
4. `choco upgrade python -y` reported that Python 3.14.6 is already the latest version available from the configured Chocolatey source.

### Scope

Build a command-line MVP with:

1. Environment check for Python, FFmpeg, and FFprobe.
2. Audio metadata probing through FFprobe JSON output.
3. Audio processing and conversion through FFmpeg.
4. Basic controls for trimming, gain, normalization, high-pass/low-pass filters, sample rate, channels, and codec.
5. Unit tests for command construction and probe summary formatting.

### Project Shape

The MVP uses a small standard Python package:

1. `audio_processor/cli.py` contains the command-line interface.
2. `audio_processor/engine.py` contains FFmpeg/FFprobe integration and validation.
3. `pyproject.toml` defines package metadata and a future console script entry point.
4. `tests/` contains focused unit tests.

### Notes

FFmpeg is invoked with `subprocess.run` using argument lists, not shell strings. This keeps quoting and path handling predictable on Windows.

### Verification

Passed:

1. `python -m unittest discover`
2. `python -m compileall audio_processor tests`
3. `python -m audio_processor check`
4. FFmpeg generated `.tmp\input.wav`
5. `python -m audio_processor process .tmp\input.wav .tmp\output.mp3 --normalize --gain-db -3 --sample-rate 44100 --channels 2 --overwrite`
6. `python -m audio_processor probe .tmp\output.mp3`

## 2026-07-23: GUI, Batch Queue, Config, Progress, and Install Entry

### Goal

Extend the command-line MVP into a usable local desktop workflow while staying with standard Python and system FFmpeg. The requested scope is GUI, batch queue, saved configuration, progress feedback, and install/packaging support.

### Technical Direction

1. Use Tkinter and ttk from the Python standard library for the GUI.
2. Keep FFmpeg as the processing engine and continue invoking it through argument lists.
3. Add FFmpeg progress parsing with `-progress pipe:1` so the GUI can show item and queue progress.
4. Store user defaults as JSON under the normal user config directory.
5. Expose both CLI and GUI entry points through `pyproject.toml`.

### Scope

Implement:

1. File queue with add/remove/clear controls.
2. Shared processing settings for queued files.
3. Sequential batch processing with per-item status.
4. Progress bar and textual status updates.
5. Persistent settings such as output directory, output extension, overwrite flag, gain, normalization, filters, sample rate, channel count, and codec.
6. Standard package entry points: `audio-processor` and `audio-processor-gui`.

### Non-goals For This Pass

1. Single-file Windows EXE bundling. That should be handled later with PyInstaller or a similar packaging tool after the workflow stabilizes.
2. Parallel FFmpeg jobs. Sequential processing is simpler and safer for a first desktop MVP.
3. Per-file custom settings. Shared settings keep the queue predictable for the initial GUI.

### Implementation Result

Added:

1. `audio_processor/gui.py` with a Tkinter desktop interface.
2. `audio_processor/batch.py` with sequential batch queue execution.
3. `audio_processor/settings.py` with JSON-backed user settings.
4. FFmpeg progress parsing in `audio_processor/engine.py`.
5. `audio-processor-gui` package script and `audio-processor gui` CLI subcommand.
6. Expanded tests for progress arguments, settings persistence, queue output naming, and CLI registration.

### Behavior

The GUI supports:

1. Adding multiple files to a queue.
2. Removing selected files and clearing the queue.
3. Shared output and processing settings.
4. Saved settings under the user config directory.
5. Sequential batch processing.
6. Per-item status and progress plus overall queue progress.
7. Cancellation.

The output path generator avoids overwriting the input file when the output extension matches the source extension by appending `_processed`.

### Packaging Notes

Editable install and wheel building are supported through `pyproject.toml`.

During verification, `pip install -e .` initially failed because the isolated build environment could not download `setuptools>=69` from the default source. Chocolatey source search also reported a source access issue. The project-level fix was to install `setuptools` into the virtual environment from official PyPI and run standard local builds with `--no-build-isolation`.

Verified commands:

1. `.venv\Scripts\python -m pip install setuptools -i https://pypi.org/simple`
2. `.venv\Scripts\python -m pip install -e . --no-build-isolation`
3. `.venv\Scripts\audio-processor check`
4. `.venv\Scripts\audio-processor process .tmp\installed_cli_input.wav .tmp\installed_cli_output.mp3 --normalize --overwrite`
5. `.venv\Scripts\audio-processor probe .tmp\installed_cli_output.mp3`
6. `.venv\Scripts\python -m pip wheel . -w .tmp\wheelhouse --no-deps --no-build-isolation`
7. `.venv\Scripts\python -m unittest discover`
8. `.venv\Scripts\python -m compileall audio_processor tests`

## 2026-07-23: GUI Language Switch

### Goal

Add a language switch control to the graphical interface so users can choose Chinese or English. All fixed GUI text should be localizable, and Chinese should be available across the full GUI.

### Scope

Implement:

1. A GUI language switch button/menu.
2. Chinese and English translation resources.
3. Persisted language preference in the existing settings JSON.
4. Runtime refresh of window title, toolbar buttons, table headings, setting labels, action buttons, dialogs, status text, and known queue status messages.
5. Tests for language normalization, settings persistence, and translation coverage for the GUI keys.

### Non-goals

1. Translating raw FFmpeg output or operating-system file dialog chrome.
2. Translating command-line help in this pass. The request is specifically for the graphical interface.

### Implementation Result

Added:

1. `audio_processor/i18n.py` with Chinese and English GUI translation resources.
2. `language` field in `ProcessingSettings`, persisted to the existing settings JSON.
3. A language menu button in the GUI toolbar.
4. Runtime refresh for window title, toolbar buttons, table headings, settings labels, action buttons, dialog titles/messages, status text, and known batch status messages.
5. Tests for language normalization, translation key coverage, translated status labels, and persisted language settings.

### Behavior

The GUI now defaults to Chinese. Users can switch between Chinese and English from the language button. The choice is saved immediately and reused on the next launch.

Raw FFmpeg messages and operating-system file dialog chrome remain controlled by FFmpeg/Windows and are not translated by the application.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover`
2. `.venv\Scripts\python -m compileall -q audio_processor tests`
3. `.venv\Scripts\python -c "from audio_processor.i18n import translate; print(translate('zh','language_menu')); print(translate('en','language_menu'))"`
4. `.venv\Scripts\audio-processor check`
5. `.venv\Scripts\python -m pip wheel . -w .tmp\wheelhouse --no-deps --no-build-isolation`

## 2026-07-24: Split GUI Upload Areas

### Goal

Change the GUI so uploaded inputs are no longer represented as one large combined file area. The interface should clearly separate original audio files, the material set, and the lyrics file into three distinct upload regions, each showing its supported format rules.

### Scope

Implement:

1. Original audio upload region for supported audio files such as `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, `.aac`, `.aiff`, `.alac`, and `.wma`.
2. Material set upload region that accepts a folder only.
3. Lyrics upload region for lyric/document files such as `.txt`, `.doc`, `.docx`, `.lrc`, and `.srt`.
4. Persisted material folder and lyrics file paths in the existing settings JSON.
5. GUI controls for selecting and clearing each source type.
6. Runtime validation that the material set is a directory and the lyrics input is a supported file.
7. Chinese and English GUI text for the new regions and validation messages.

### Non-goals

1. Parsing `.doc` or `.docx` lyrics content in this pass. The GUI should accept and track the file path.
2. Using the material folder to drive audio processing rules in this pass. The current processing queue still operates on original audio files.

### Implementation Result

Added:

1. Three separate GUI upload regions: original audio, material set, and lyrics file.
2. Visible supported-format hints for each region.
3. Material set selection through a folder picker only.
4. Lyrics selection through a file picker filtered to `.txt`, `.doc`, `.docx`, `.lrc`, and `.srt`.
5. `material_directory` and `lyrics_file` fields in persisted settings.
6. Runtime validation for material folder and lyrics file paths before saving settings or starting a batch.
7. Startup/batch log entries that show active material folder and lyrics file when provided.
8. Chinese and English translation keys for the new controls, hints, and validation messages.

### Behavior

Original audio files still create the processing queue and are the only inputs passed to FFmpeg. The material set and lyrics file are now first-class GUI inputs: they are selected separately, displayed separately, validated, persisted, and shown in logs, but they are not parsed or consumed by the audio processing engine yet.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover`
2. `.venv\Scripts\python -m compileall -q audio_processor tests`
3. `.venv\Scripts\python -c "from audio_processor.i18n import TRANSLATIONS; print(len(TRANSLATIONS['zh']), len(TRANSLATIONS['en']))"`
4. `.venv\Scripts\audio-processor check`

## 2026-07-24: Material Audio Stretch Assembly

### Goal

Correct the core processing model. The original audio is the timing/reference target, while audio clips in the material set should be combined and time-stretched/compressed to match that target. The material set must be consumed by the processing engine, not merely saved as a path.

### Critical Audio Constraint

Material clips must not be looped, hard-cut, or truncated to fit. If the material audio is shorter or longer than the target timing, it should be time-stretched or time-compressed. The stretch operation should preserve pitch and formants as much as possible, because those properties strongly affect listening quality, pronunciation recognition, and the perceived identity of single syllables.

### Technical Direction

Use FFmpeg's `rubberband` filter when available:

1. `tempo` controls duration.
2. `pitch=1` preserves pitch.
3. `formant=preserved` preserves formants.
4. Other quality-oriented options should favor intelligibility over speed.

The current machine's FFmpeg build exposes the `rubberband` filter and supports `formant=preserved`.

### Implementation Scope

1. Scan material folders for supported audio files.
2. Sort material files deterministically by filename.
3. Concatenate material clips in order.
4. Compare material total duration against the original audio duration.
5. Apply high-quality time-stretch/compression so the assembled material duration matches the original reference duration.
6. Export a DAW-importable audio file.
7. Keep GUI behavior aligned: original audio is the reference; material set is the source audio to assemble; output is the stretched assembled material.

### Non-goals

1. Perfect phoneme-level alignment in this pass.
2. Full lyric parsing/alignment in this pass.
3. Claiming zero perceptual change; the implementation should minimize audible damage, but extreme stretch ratios will still affect quality.

### Implementation Result

Added:

1. Material folder scanning through the shared supported-audio extension list.
2. Deterministic material ordering by filename.
3. Material assembly FFmpeg command generation using `concat` followed by `rubberband`.
4. One-file material support through the same `rubberband` chain, without a special loop/cut path.
5. Progress-aware material assembly used by the batch queue whenever a material folder is selected.
6. GUI start validation that requires a material folder for assembly, while still allowing settings to be saved before every source is selected.
7. Default `.wav` output and automatic `pcm_s24le` codec for WAV files.
8. GUI trim start and duration fields removed from the assembly workflow to avoid implying direct material truncation.

### Behavior

For each queued original audio file:

1. The original audio is probed only for reference duration.
2. Supported audio files in the material set are concatenated in filename order.
3. The total material duration is compared to the reference duration.
4. FFmpeg `rubberband` applies `tempo = material_duration / reference_duration`.
5. `pitch=1` and `formant=preserved` are used to reduce pitch/formant damage.
6. The graph does not use `stream_loop`, `atrim`, or direct duration truncation.
7. If `rubberband` returns a slightly short result, `apad=whole_dur=<reference_duration>` pads only the tail to the reference duration.

### Verification

Passed:

1. `.venv\Scripts\python -m compileall -q audio_processor tests`
2. `.venv\Scripts\python -m unittest discover`
3. Generated a 4 second reference WAV and two 1 second material WAV files with FFmpeg.
4. Ran the project assembly engine on those files.
5. FFprobe confirmed the assembled output is `format_name=wav`, `codec_name=pcm_s24le`, and `duration=4.000000`.

## 2026-07-24: Portable Windows Package

### Goal

Create a first portable Windows ZIP for users who do not have Python or FFmpeg installed. The expected user workflow is unzip, open the GUI executable, select source files/folders, and process audio without command-line setup.

### Technical Direction

1. Use PyInstaller to build a windowed GUI executable.
2. Ship the real FFmpeg and FFprobe binaries beside the app, not Chocolatey shim executables.
3. Update runtime tool resolution so bundled `bin\ffmpeg.exe` and `bin\ffprobe.exe` are preferred over system PATH.
4. Include FFmpeg license and README files in the portable package.
5. Keep the package as a folder inside a ZIP so all required files stay together.

### Implementation Result

Added:

1. `packaging/vocal_process_gui.py` as a stable PyInstaller GUI entry point.
2. `scripts/build_portable.ps1` to rebuild `dist\VocalProcess-portable.zip`.
3. `packaging/README_PORTABLE.txt` for end users.
4. `packaging/THIRD_PARTY_NOTICES.txt` for bundled FFmpeg notices.
5. Engine runtime lookup for bundled tools in the executable directory or `bin` subdirectory.
6. Test coverage proving bundled `bin\ffmpeg.exe` is preferred when PATH is unavailable.

### Package Layout

The generated ZIP contains:

1. `VocalProcess\VocalProcess.exe`
2. `VocalProcess\bin\ffmpeg.exe`
3. `VocalProcess\bin\ffprobe.exe`
4. `VocalProcess\licenses\FFmpeg-LICENSE.txt`
5. `VocalProcess\licenses\FFmpeg-README.txt`
6. `VocalProcess\README_PORTABLE.txt`
7. `VocalProcess\THIRD_PARTY_NOTICES.txt`
8. PyInstaller `_internal` runtime files.

### Verification

Passed:

1. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`
2. `.venv\Scripts\python -m unittest discover`
3. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1`
4. Package created at `dist\VocalProcess-portable.zip`, size about 87 MB.
5. Bundled `bin\ffmpeg.exe -version` and `bin\ffprobe.exe -version` both run and include `--enable-librubberband`.
6. `VocalProcess.exe` smoke-tested by starting the GUI process for five seconds; it did not exit or crash.
7. ZIP extraction smoke test passed into `.tmp\portable-extract-test`.

### Remaining Test Gap

The package still needs a clean-machine test in Windows Sandbox or another Windows account with no Python/FFmpeg installed. The current machine has development tools installed, so this pass verifies package structure and executable startup, not a fully isolated end-user machine.

## 2026-07-24: Portable FFprobe JSON Error Hardening

### Goal

Fix the portable GUI error reported by two users:

`the JSON object must be str, bytes or bytearray, not NoneType`

The fix should preserve the material stretch assembly workflow and make failures actionable for GUI users.

### Investigation

There are two JSON parsing paths in the project:

1. Settings loading in `audio_processor/settings.py`.
2. FFprobe metadata parsing in `audio_processor/engine.py`.

The settings path reads text from disk before calling `json.loads()`, so it cannot normally pass `None` to `json.loads()`. The FFprobe path called `json.loads(result.stdout)` directly. If FFprobe returned success but produced no captured stdout, or if the packaged/windowed runtime yielded an empty captured output for a specific user input, the raw Python `TypeError` would escape to the GUI.

The relevant runtime path is:

1. GUI starts batch processing.
2. `run_batch_queue()` chooses material assembly when a material folder is selected.
3. `assemble_material_to_reference_with_progress()` probes the reference and material files for duration.
4. `probe_audio()` invokes FFprobe and parses JSON.

Local reproduction with generated WAV files and the bundled `bin\ffprobe.exe` returned valid JSON, so the immediate defect is not the stretch/concat graph. The defect is missing boundary validation around FFprobe JSON output.

### Implementation Result

Changed:

1. `probe_audio()` now rejects `None` or empty FFprobe stdout with `AudioProcessorError`.
2. Invalid JSON output is wrapped as `AudioProcessorError` with a short preview of the returned text.
3. Unexpected non-object JSON output is rejected.
4. Tests now cover both `stdout=None` and invalid JSON.

This does not make unreadable/corrupt user audio magically processable, but it prevents the unhelpful low-level `json.loads(None)` error from reaching users. The GUI will now show a clearer FFprobe metadata error identifying the file path.

### Build Output Cleanup

PyInstaller writes ordinary INFO/WARNING diagnostics to stderr, which the Codex terminal renders as red text. The build itself was successful. The build script now captures PyInstaller output and only prints it when PyInstaller exits with a non-zero code. Successful builds show only the generated ZIP path.

The build script also validates deletion targets before removing old portable output, so generated paths must stay under the project root even when the optional app name parameter is changed.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 21 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`.
3. Generated reference/material WAV files and assembled output through the real FFmpeg workflow.
4. Bundled `bin\ffprobe.exe` confirmed output `format_name=wav`, `codec_name=pcm_s24le`, and `duration=3.000000` for the assembled result.
5. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1` rebuilt `dist\VocalProcess-portable.zip` with clean success output.
6. ZIP extraction structure check passed.
7. Bundled FFmpeg and FFprobe version checks passed and include `--enable-librubberband`.
8. Final post-script-change verification repeated unit tests, compile checks, portable rebuild, and ZIP extraction.
9. `scripts/smoke_portable.ps1` was added as the standard portable smoke test and passed after user approval.

### Remaining Test Gap

The hidden GUI EXE smoke test now passes through `scripts/smoke_portable.ps1`. A clean-machine test is still useful: run the rebuilt ZIP in Windows Sandbox or another Windows account with no Python/FFmpeg installed, using the same original audio and material set that triggered the user report.

## 2026-07-24: Standard Portable Smoke Test

### Goal

Make the portable package verification repeatable and auditable. Every future portable build should be smoke-tested by the assistant before the ZIP is handed to users.

### Implementation Result

Added `scripts/smoke_portable.ps1`.

The script:

1. Verifies `dist\VocalProcess-portable.zip` exists.
2. Extracts it into `.tmp\portable-smoke-test`.
3. Checks for `VocalProcess.exe`.
4. Checks bundled `bin\ffmpeg.exe`.
5. Checks bundled `bin\ffprobe.exe`.
6. Starts `VocalProcess.exe` hidden for a short runtime.
7. Fails if the GUI executable exits immediately.
8. Closes or kills the process after the smoke-test window.

The script validates its extraction directory before deleting old smoke-test output, so the cleanup target must stay under the project root.

### Permission Flow

Launching a GUI executable requires escalated permission in the Codex sandbox. The user approved and saved this command prefix:

`powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1`

Future portable-package work should run this script first after every rebuild.

### Build Auditing

The portable build now includes `BUILD_INFO.txt` in the ZIP. It records:

1. Build time.
2. Git branch.
3. Git commit.
4. Whether the build included uncommitted working-tree changes.

The current confirmed ZIP includes a build marker from branch `codex/fix-portable-json-probe`, commit `4f08ee2`, with uncommitted working-tree changes included. Final confirmed build time: `2026-07-24 12:52:46 +08:00`. Final confirmed SHA256: `246BBD9B0BF762AEC3A48822E6A028470FFB8C7CAF326637270F03B47850B31A`.

### Verification

Passed:

1. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1`.
2. ZIP extraction and `BUILD_INFO.txt` readback.
3. `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1`.
4. `.venv\Scripts\python -m unittest discover`.
## 2026-07-24: Structured Diagnostics and Model Assisted Vocal Pipeline

### Goal

Address the user-reported failure where material vocal stretch assembly often finished without useful results and sometimes failed silently. The new work should first make failures diagnosable, then prepare the architecture for open-source model-assisted vocal recognition and ordering.

### Technical Direction

1. Add per-run JSONL diagnostics so every batch item writes an auditable trail.
2. Record the processing mode, settings snapshot, reference metadata, material metadata, and exception details.
3. Add a model-assist layer that can represent Demucs, Silero VAD, pyannote.audio, WhisperX, Whisper, and SpeechBrain as optional backends.
4. Keep the current portable package small by making the model backends optional and not bundled by default.
5. Expose the architecture through CLI and documentation before wiring heavy inference into the GUI.

### Implementation Result

Added:

1. `audio_processor/diagnostics.py` for JSONL event logging.
2. Batch-level logging in `audio_processor/batch.py`.
3. `audio_processor/model_assist.py` for candidate models, backend availability checks, transcript-based ordering helpers, and pipeline planning.
4. `audio_processor cli models` for inspection and JSON export.
5. `docs/MODEL_ASSISTED_VOCAL_PIPELINE.md` for root-cause analysis and future architecture.
6. README and portable-user docs updated with the current limitation and log location.

### Verification

Passed:

1. `python -m unittest discover` with 33 tests.
2. `python -m compileall -q audio_processor tests packaging`.
3. Real end-to-end batch run on generated reference and material WAV files.
4. The run produced `reference.diagnostics.jsonl` with stages `batch.item.started`, `inputs.reference`, `inputs.materials`, and `batch.item.completed`.
5. `python -m audio_processor models` listed the candidate open-source backends.
6. `python -m audio_processor models --json` printed the pipeline plan as JSON.

### Notes

The project now has real inference wiring for the core backends: Demucs, Whisper, Silero VAD, and a guarded SpeechBrain speaker-embedding path. The remaining blockers are external:

1. `pyannote.audio` requires Hugging Face authorization/token and has not been installed yet.
2. `whisperx` is currently blocked in this Python 3.14 environment because its published dependency pin expects `ctranslate2==4.4.0`, which is not available for this runtime.
3. SpeechBrain embedding is intentionally cache-gated so user runs do not hang on first-time Hugging Face downloads.

The present change is now a working model-assisted pipeline, not just a placeholder architecture.
## 2026-07-24: Portable Zip Synced With Local Model Runtime

The current user request was to sync and smoke-test the portable zip first, with no size optimization yet, then prepare git upload and record the outcome.

Completed work:

1. The portable build now includes the lazy model runtime packages by copying `.venv\Lib\site-packages` into the frozen app `_internal` folder.
2. The portable build now bundles `.tmp\model-cache` into `VocalProcess\models` so Whisper and Silero caches travel with the package.
3. `audio_processor.model_runtime` now falls back through portable, project-local, config, and temp cache roots instead of trying `%AppData%` first.
4. `scripts/smoke_portable.ps1` now supports `-ReuseExtract` so the assistant can finish the startup check without re-extracting a large zip.

Verified results:

1. `.venv\Scripts\python -m unittest discover` passed with 35 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging` passed.
3. `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1 -ReuseExtract` passed.
4. `dist\VocalProcess-portable.zip` exists and contains the bundled runtime, models, and build marker.

Build artifact:

- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `7F1E9ED10374A01139AD2717A3596B12408467F7EA9BDC365A3ECAB00B451820`
- Size: `753,289,753` bytes
- Build marker branch: `codex/local-pretrained-portable-sync`
## 2026-07-24: Final Artifact Refresh After Commit

The final portable zip was rebuilt after the local commit so the build marker now matches the committed source.

Final artifact:

- Commit: `30a66f3`
- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `9A52C2FFA215E32E87E064F27FB124B12CC5683EE98D2A97568ADD417829A88D`
- Smoke test: `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1 -ReuseExtract -ExtractRoot dist\VocalProcess-portable`
## 2026-07-24: Final Portable Batch Verification

The portable EXE now supports a headless `batch` command through the same wrapper that opens the GUI when no arguments are given. This allowed a real portable model-assisted run, not just a startup smoke test.

Verified portable result:

1. `VocalProcess.exe check` works from the portable package.
2. `VocalProcess.exe batch ...` produced `reference.wav` and `reference.diagnostics.jsonl`.
3. Diagnostics recorded `model.ordering.completed`, `batch.item.completed`, and Demucs vocal separation cache paths.
4. The portable smoke helper `scripts/smoke_portable_model.ps1` now creates TTS fixtures, runs the portable batch command, and checks the generated output.

Final artifact:

- Commit: `fa1935d`
- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `35DBB2AFBD2A79A0D1CF72C8441E4CA19BDF93B87B9A4399470FEC7C2507A68D`
- Smoke command: `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable_model.ps1 -PortableRoot dist\VocalProcess-portable\VocalProcess -WorkRoot .tmp\portable-model-smoke-final`

## 2026-07-25: Robust Diagnostics, Cache, Device, and UI Runtime Work

### Root Cause

The user-provided red error log showed `FFprobe returned no JSON metadata` during `_log_input_diagnostics()`. That diagnostics step was probing every reference/material file before model ordering. When FFprobe returned empty stdout for a WAV file, diagnostics raised `AudioProcessorError`, so rendering could fail before the actual model pipeline or FFmpeg render stage had a chance to run.

### Implementation Result

1. `probe_audio()` now falls back to Python `wave` metadata for WAV files and to parsing FFmpeg stderr when FFprobe JSON is empty, invalid, or the ffprobe command fails.
2. Input diagnostics now record reference/material metadata failures as warning fields instead of aborting the job.
3. Batch items now track elapsed runtime; completion, cancellation, and errors write elapsed seconds into diagnostics.
4. Model ordering accepts `compute_device` and resolves `auto` to CUDA when available, otherwise CPU.
5. Demucs, WhisperX, Whisper, Silero VAD, torch-hub Silero, and SpeechBrain paths now receive the resolved device where supported.
6. Material folders now store `.vocalprocess_material_cache.json`; cache reuse is keyed by file path, suffix, size, mtime, file count, and ASR model.
7. Material ordering now uses filename pronunciation text as a correction signal alongside ASR transcript, helping clips named by syllable/word when ASR is weak.
8. Lyrics are explicitly optional in GUI behavior and logging.
9. GUI now displays elapsed runtime, keeps live progress bars, supports edge resize, and exposes saved window sizes.
10. CLI `batch` now accepts `--compute-device`.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 41 tests.
2. `.venv\Scripts\python -m py_compile audio_processor\model_runtime.py audio_processor\model_assist.py audio_processor\batch.py audio_processor\engine.py audio_processor\gui.py audio_processor\settings.py audio_processor\i18n.py audio_processor\cli.py tests\test_engine.py`.
3. Actual source batch smoke without lyrics: `.venv\Scripts\python -m audio_processor.cli batch .tmp\local-model-smoke-current\reference.wav .tmp\local-model-smoke-current\out\reference.wav --material-directory .tmp\local-model-smoke-current\materials --compute-device cpu --overwrite`.
4. Second source batch run reused `.vocalprocess_material_cache.json` and completed in about 9 seconds.

### Next Tasks

1. Rebuild and smoke-test the portable ZIP from the current source.
2. Commit, push, and refresh release artifact if portable verification passes.
3. Continue VST3/DAW bridge work after this diagnostics/model-stability round is released.

## 2026-07-25: Per-Clip Stretch Planning and VST3 Bridge Helper

### Goal

Respond to manual test feedback that material vocal recognition/order and stretch rendering were still not producing intelligible results consistently. The priority is to make ordering more explainable, reduce avoidable stretch damage to one-syllable/one-character clips, and continue the VST3 bridge path without attempting unsafe real-time Python/FFmpeg processing inside a plug-in callback.

### Technical Decisions

1. Keep model-assisted ordering mandatory when a material folder is selected.
2. Treat filename text as an explicit pronunciation hint because manual tests showed ASR can be weak on short material clips.
3. Score duration closeness separately because extreme stretch ratios are a direct cause of degraded syllable intelligibility.
4. Do not invent speaker similarity when no real embedding is available.
5. Render flat WAV output with per-clip Rubber Band stretching before concatenation, not one global stretch after concatenating all material.
6. Use the same per-clip stretch plan for DAW timeline export.
7. Continue VST3 as an offline bridge first: native VST3 should call a helper process through a small protocol, not embed Python/model inference in the real-time path.

### Implementation Result

1. `audio_processor.model_assist` now records transcript, filename, duration, speaker, and VAD score components.
2. Short-reference scoring weights filename and duration more heavily so one-syllable material can be matched when ASR output is empty or wrong.
3. `audio_processor.model_runtime` attaches per-material target duration hints to ordering decisions and diagnostics reports.
4. `audio_processor.engine` now exposes `MaterialStretchClip`, `plan_material_stretch_clips()`, and `render_material_stretch_plan()`.
5. Flat WAV material assembly now builds a filter graph that stretches each input independently, then concatenates the stretched clips.
6. `audio_processor.batch` writes a `render.stretch_plan` JSONL event before rendering.
7. `audio_processor.daw` now uses the same per-clip target durations and records per-clip tempo and quality warnings in manifest/CSV output.
8. `audio_processor.vst3_bridge` adds a JSON bridge request/response helper.
9. CLI adds `vst3-bridge --template` and `vst3-bridge request.json --response response.json`.
10. Documentation was updated for the current per-clip stretch strategy and VST3 helper boundary.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 48 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`.
3. Actual source batch smoke using cached local model analysis.
4. Diagnostics contained `render.stretch_plan` with per-clip tempos and no quality warnings in the smoke sample.
5. FFprobe confirmed the smoke output WAV as `pcm_s24le`, `22050 Hz`, mono, about `5.19s`.
6. `audio_processor.cli vst3-bridge --template` returned a valid request template.
7. Portable ZIP rebuild succeeded.
8. Portable GUI smoke passed after reusing the extracted tree.
9. Portable model smoke initially exposed that a windowed PyInstaller EXE cannot be invoked like a normal console process in PowerShell smoke scripts.
10. `scripts/smoke_portable_model.ps1` now validates Tcl/Tk data folders and uses `Start-Process -PassThru` to wait for the windowed EXE and read its exit code.
11. Portable model smoke passed after the script fix.

### Remaining Work

1. Commit and push this branch after portable verification.
2. Rebuild the portable ZIP once more after commit so `BUILD_INFO.txt` points at the final source commit.
3. Test the JSON bridge helper from a DAW-side script or minimal native VST3 prototype.
4. Keep tuning ordering weights using real manual-test diagnostics, especially bad ASR, bad filename hints, and extreme stretch cases.

## 2026-07-25: Native VST3 Bridge and Preflight Gate

### Goal

Turn the VST3 work from a placeholder helper into a functional bridge path, and reduce manual-test friction by making recognition/order quality inspectable before rendering.

### Implementation Notes

1. The Python bridge helper now supports both blocking single-file requests and a persistent watch loop:
   - request glob: `*.request.json`;
   - response: `<request_id>.response.json`;
   - heartbeat: `bridge.heartbeat.json`;
   - processed request archive: `<request_id>.done.json`.
2. `audio-processor vst3-bridge --contract` exposes the bridge contract for a native plug-in, DAW script, or external controller.
3. `audio-processor analyze` generates `analysis.json` without rendering audio. This report combines:
   - model ordering report;
   - transcript, filename, duration, speaker, and VAD scores;
   - target duration per clip;
   - per-clip Rubber Band tempo;
   - moderate/extreme stretch warnings;
   - low-match and weak-text review warnings.
4. Batch diagnostics now emits `model.ordering.review_required` if the lowest match score is below the safety threshold or if the stretch plan contains warnings.
5. A native JUCE/MSVC VST3 bridge plug-in was added:
   - source: `native\vst3_bridge`;
   - build script: `scripts\build_vst3_bridge.ps1`;
   - output: `build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3`.
6. The plug-in is deliberately a pass-through control surface. It does not run Python, FFmpeg, model inference, or file rendering inside `processBlock`.
7. The plug-in launches the helper process from its editor UI for `analyze` and `render` operations. This is the correct boundary for DAW safety.
8. Portable builds now include the VST3 bundle under `VocalProcess\plugins` if the native bundle has already been built.

### Verification

1. Unit tests passed: 54 tests.
2. Python compile check passed for `audio_processor`, `tests`, and `packaging`.
3. Native VST3 Release build passed with Visual Studio 18/CMake/JUCE 8.0.13.
4. Generated bundle structure was inspected and includes:
   - `Contents\x86_64-win\VocalProcess Bridge.vst3`;
   - `Contents\Resources\moduleinfo.json`.

### Remaining Risk

1. The native VST3 still needs real host validation in REAPER/Cubase/Studio One/Ableton. Build success confirms a loadable VST3 bundle is produced, but host behavior must be tested in each DAW.
2. `analysis.json` improves manual review but does not guarantee perfect ASR on noisy or one-character clips. The review warnings are intended to prevent silent trust in low-quality ordering.
3. JUCE licensing must be respected when distributing the plug-in.

## 2026-07-25: VST3 Host Validation and Melodyne Handling

### Goal

Move the native bridge from "built successfully" to "known loadable in real host paths", while handling Melodyne honestly as a workflow target rather than pretending an unavailable 64-bit host test passed.

### Implementation Notes

1. Added `native\vst3_probe`, a small JUCE host-side executable that scans and instantiates a supplied VST3 bundle.
2. Added `scripts\probe_vst3_bridge.ps1` to build the probe with the same JUCE/MSVC environment used by the bridge and run it against the current `VocalProcess Bridge.vst3`.
3. Added `scripts\host_test_reaper_vst3.ps1` to launch REAPER with an isolated config, point `vstpath64` at the bridge build directory, wait for scanning, and verify `reaper-vstplugins64.ini`.
4. Added `scripts\install_vst3_bridge.ps1` to copy the complete VST3 bundle into the common 64-bit VST3 folder for DAW scanning.
5. Added `scripts\host_test_flstudio_vst3.ps1` to launch FL Studio Plugin Manager and inspect its verified database after a scan attempt.
6. Added `scripts\check_melodyne_context.ps1` to distinguish current 64-bit Melodyne/Celemony availability from legacy 32-bit installs.

### Verification

1. JUCE headless probe passed:
   - found one `VocalProcess Bridge` VST3 description;
   - instantiated it successfully;
   - reported 2 inputs and 2 outputs.
2. REAPER 7.33 x64 passed:
   - used an isolated config under `.tmp\reaper-vst3-host-test`;
   - cached `VocalProcess Bridge (VocalProcess)` in `reaper-vstplugins64.ini`.
3. The bridge was installed to:
   - `C:\Program Files\Common Files\VST3\VocalProcess Bridge.vst3`.
4. FL Studio 2024 Plugin Manager launched, but did not expose a documented non-interactive scan command or create a new automatic verified entry during the scripted run.
5. Melodyne/Celemony detection now reports standard 64-bit paths on `E:\` and legacy 32-bit paths on `D:\`/`E:\`. Melodyne is still not treated as a generic VST3 host for validating the bridge.

### Current DAW Position

1. REAPER is validated as a real VST3 scanner/host path.
2. FL Studio should scan the installed bridge from the common VST3 folder, but the final registration step requires Plugin Manager > Find installed plugins.
3. Melodyne should be treated as an editor/ARA workflow target. The practical supported path is exported PCM WAV or DAW timeline output, then Melodyne editing inside a compatible 64-bit DAW.
4. No hand-written host database edits should be used for FL Studio or Melodyne because that would create avoidable manual-test risk.

## 2026-07-25: Runtime Optimization and Portable Package Split

### Goal

Reduce manual-test friction and runtime without lowering output correctness. The immediate issue is that a 28-second reference vocal can take 7-10 minutes, which would scale poorly to 2-5 minute songs.

### Open-Source Research Direction

The practical candidates for this project are:

1. Faster Whisper / CTranslate2 for faster Whisper-compatible ASR.
2. whisper.cpp for possible future native CPU-first ASR packaging.
3. SpeechBrain ECAPA for speaker embedding and voice similarity.
4. pyannote.audio for diarization when Hugging Face token/model terms are available.
5. Librosa and MSAF for future repeated-section and self-similarity analysis.

### Implemented Decisions

1. Package variants:
   - default no-VST3 package: `VocalProcess-portable.zip`;
   - VST3 host-test package: `VocalProcess-portable-vst3.zip`.
2. `build_portable.ps1` now accepts `-IncludeVst3Bridge`; `build_portable_variants.ps1` builds both variants.
3. The non-VST3 package is compressed with optimal ZIP compression and no longer automatically includes the VST3 bundle.
4. A `source_separation` setting was added:
   - `auto`: current conservative behavior;
   - `never`: skip Demucs when the reference is already isolated vocals;
   - `always`: force source separation when available.
5. GUI exposes source separation as direct radio buttons, because this is a common manual-test decision.
6. CLI and VST3 bridge requests expose the same setting.
7. Reference analysis is now cached by reference file snapshot, lyrics file snapshot, ASR backend, compute device, and source separation mode.
8. Model objects are cached inside the process to avoid reloading Whisper/VAD/SpeechBrain-style models for every material clip.
9. Optional Faster Whisper support is implemented but non-blocking.
10. DAW timeline export reuses exact duplicate stretched clip renders.
11. Preflight analysis now reports safe duplicate render reuse groups and repeated reference text groups.

### Why This Is Safe

1. Skipping Demucs is user-controlled. It is only correct when the reference is already isolated vocals.
2. Model object caching does not change recognition results; it removes repeated model initialization.
3. Reference analysis caching is keyed by source file, lyrics file, ASR backend, compute device, and source-separation mode.
4. Render reuse only applies when source file, target duration, tempo, and render options are identical.
5. Repeated verse/chorus detection is currently a report hint, not an automatic replacement for ASR/text matching.

### Verification

1. Python compile check passed.
2. Unit tests passed with 57 tests.
3. Faster Whisper install was attempted but timed out; current environment still lacks `faster_whisper` and `ctranslate2`.
4. Native VST3 rebuild passed after adding source-separation UI.
5. JUCE headless VST3 probe passed after rebuild.
6. Both portable package variants were built and inspected; the standard package has no VST3 entries, and the VST3 package includes the bridge.
7. Portable GUI smoke passed for both variants using completed extraction directories.
8. REAPER isolated VST3 scan passed after rebuild.
9. Melodyne context script found standard 64-bit Celemony/Melodyne Studio 4 paths plus legacy 32-bit paths.

### Remaining Work

1. Rebuild both portable package variants and run smoke tests.
2. Rebuild the native VST3 after the editor source-separation UI change.
3. Retry Faster Whisper installation with a longer timeout or a Python/runtime version known to have compatible CTranslate2 wheels.
4. Add real music-structure analysis only after the exact/review-only paths are stable. The next safe step is section-timing hints, not automatic lyric substitution.

## 2026-07-26: Runtime Version Direction

### Decision

The project should move away from the machine-default Python 3.14 runtime for production work. The long-term layout should be:

1. Main application/runtime: Python 3.11.
2. UVR headless separation worker: isolated Python 3.10 environment.
3. Native VST3 bridge: remains a JUCE/MSVC plug-in and should call the packaged helper or Python-side bridge by process boundary only.

### Rationale

1. Python 3.11 has better compatibility with the current model stack than Python 3.14, including Whisper-related packages and common PyTorch/audio wheels.
2. `uvr-headless-runner` is not suitable for the main Python 3.11 environment because it targets Python versions below 3.11.
3. Separating UVR into a worker avoids pinning the entire project to Python 3.10 while still allowing UVR5-style model execution through a controlled headless process.

### Implementation Direction

1. Tighten the main project Python requirement to the selected 3.11 line.
2. Add bootstrap scripts for:
   - `.venv311`: main app/runtime and packaging work;
   - `.uvr-worker`: UVR headless runner on Python 3.10.
3. Update build and package scripts to prefer the explicit Python 3.11 runtime instead of accidentally using Python 3.14.
4. Add project-side UVR worker detection and clear diagnostics before enabling automatic separation through UVR.

### Completed So Far

1. Python 3.11.9 is installed and used by `.venv311`.
2. Python 3.10.11 is installed and used by `.uvr-worker`.
3. The main project now requires Python `>=3.11,<3.12`.
4. Heavy model dependencies were moved out of default install requirements so the main runtime environment can be created quickly and predictably.
5. UVR worker setup is isolated in `requirements\uvr-worker-py310.txt` and pins `setuptools<81` for `pkg_resources` compatibility.
6. `audio_processor.uvr_worker` detects, validates, and calls the UVR headless runner by process boundary.
7. Source separation now tries the UVR worker before falling back to in-process Demucs when UVR is available and source separation is not explicitly skipped.
8. `scripts\check_uvr_worker.ps1` validates the Python 3.10 worker, runner entry points, package metadata, and installed-model lists without running full inference.

### Verified State

1. `.venv311` compile check passed.
2. `.venv311` unit tests passed with 60 tests.
3. `audio_processor check` reports `UVR Headless Runner: available`.
4. `audio_processor models` reports `UVR Headless Runner [source_separation]: available`.
5. `scripts\check_uvr_worker.ps1` passed for Demucs, MDX, and VR runner entry points.
6. Interim lightweight no-VST3 portable ZIP was rebuilt from Python 3.11 and smoke tested; current size was about 86 MB.
7. Interim lightweight VST3 portable ZIP was rebuilt from Python 3.11 and smoke tested; current size was about 89 MB and contains the native bridge bundle.
8. The default UVR `htdemucs` model was downloaded, `uvr-demucs --model-info htdemucs` resolves it from cache, and a real 1-second UVR separation smoke test produced a vocals stem.

### Superseded Risk Note

This interim risk state was superseded by the full portable runtime correction below. The tens-of-MB builds were reclassified as lightweight smoke packages, the default portable outputs were rebuilt as full model-runtime packages, and the bundled UVR worker path now has a real separation smoke test.

## 2026-07-26: Full Portable Runtime Correction

### Trigger

The user challenged why the new lightweight packages were only tens of MB and asked whether they were functionally complete. That challenge was valid: the packages built with `-SkipModelRuntime` were startup/UI/VST3 smoke packages, not full local model-runtime packages.

### Corrected Package Semantics

1. Default portable builds are now full model-runtime packages:
   - `dist\VocalProcess-portable.zip`;
   - `dist\VocalProcess-portable-vst3.zip`.
2. Lightweight packages are explicitly marked with `-lite` and must be requested with `scripts\build_portable_variants.ps1 -Lite`.
3. Full packages copy:
   - `.venv311\Lib\site-packages` into the frozen app runtime;
   - `.tmp\model-cache` into `VocalProcess\models`;
   - `.uvr-worker` into `VocalProcess\uvr-worker`.
4. Lite packages do not copy the complete model runtime, model cache, or UVR worker, and should only be used for startup/UI/VST3 packaging checks.

### Current Full Runtime

The Python 3.11 full runtime now includes PyTorch CPU, torchaudio, OpenAI Whisper, Demucs, SpeechBrain, Faster Whisper, Silero VAD, and Librosa. `pyannote.audio` and WhisperX remain outside the default full package because they are authorization-dependent or still experimental in this environment.

### Verified Outputs

1. `dist\VocalProcess-portable.zip`: 1,239,961,593 bytes, no VST3, full model runtime bundled.
2. `dist\VocalProcess-portable-vst3.zip`: 1,242,394,260 bytes, VST3 bridge bundled, full model runtime bundled.
3. `dist\VocalProcess-portable-lite.zip`: 388,107,297 bytes, lightweight startup/package smoke only.
4. `dist\VocalProcess-portable-lite-vst3.zip`: 390,541,294 bytes, lightweight VST3 package smoke only.

### Verification

1. Compile check passed in `.venv311`.
2. Unit tests passed with 60 tests in `.venv311`.
3. `audio_processor check` reports Demucs, UVR, Faster Whisper, OpenAI Whisper, Silero VAD, SpeechBrain, and Librosa available.
4. Standard full portable startup smoke passed.
5. VST3 full portable startup smoke passed.
6. Full portable model smoke passed for the already-isolated source-separation-skip path.
7. Full portable UVR worker smoke passed and produced a vocals stem from the bundled worker.

### Remaining Risk

1. CPU-only model inference can still be slow on full songs.
2. More UVR model variants need broader manual validation, but the bundled default worker/model path has a real smoke test now.

## 2026-07-26: WhisperX and pyannote Runtime Inclusion

### Decision

WhisperX and pyannote should be included in the default full Python 3.11 runtime rather than kept in the experimental requirements file. The latest available pair was not the best project fit because it forced a large PyTorch 2.8 runtime move during packaging. The stable inclusion target is:

1. `whisperx==3.4.5`.
2. `pyannote.audio==3.4.0`.
3. `ctranslate2==4.4.0`.
4. `transformers==4.57.6`.

### Compatibility Note

`pyannote.audio 3.4.0` expects legacy top-level `torchaudio` APIs that are absent from the current `torchaudio 2.11.0+cpu` package. The project now prepares a local compatibility layer before importing WhisperX or pyannote. This preserves the current main model runtime while allowing pyannote/WhisperX imports to succeed.

### Verified So Far

1. The packages are installed in `.venv311`.
2. `pip check` reports no broken requirements.
3. Direct import through the project compatibility layer works for WhisperX and pyannote.audio.
4. `audio_processor check` reports WhisperX and pyannote.audio available.
5. Compile check passed.
6. Unit tests passed with 60 tests.

### Remaining Before Release

1. Rebuild both full portable packages after the expanded runtime.
2. Smoke-test startup, model path, and bundled UVR worker from the rebuilt packages.
3. Commit and push branch `codex/runtime-env-uvr-worker`.

### Rebuild Result

1. `dist\VocalProcess-portable.zip`: 1,413,022,068 bytes.
2. `dist\VocalProcess-portable-vst3.zip`: 1,415,457,165 bytes.
3. Both packages passed startup smoke tests.
4. The no-VST3 full package passed model smoke with source separation skipped for an already-isolated fixture.
5. The no-VST3 full package passed bundled UVR worker separation smoke.
6. Portable `VocalProcess.exe check` reports WhisperX and pyannote.audio available.

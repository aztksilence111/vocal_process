# Project Analysis

## Standing Project Rules

The durable project rules are maintained in `PROJECT_RULES.md`. In particular, completed work branches should replace `main` after verification, while the previous `main` is backed up to a separate archive/backup branch first. Major verified changes should be committed and pushed to GitHub automatically unless the user explicitly asks to keep them local. Whenever `main` is updated, `README.md` must include an update log directly after the download section.

## 2026-07-27: Full Portable Release Assets

GitHub Release `v2026.07.27-portable-full-runtime` publishes the two full portable ZIP packages: `VocalProcess-portable.zip` for normal GUI/CLI and local model testing, and `VocalProcess-portable-vst3.zip` for DAW/VST3 bridge testing. The `-lite` ZIPs remain smoke-test-only assets and should not be presented as full functional packages.

## 2026-07-27: Core Ordering v2 Implementation

The core material ordering path now uses a long-term planner shape instead of another local scoring patch. `audio_processor.model_assist` builds a full reference-segment by material-clip score matrix and uses global assignment when there are enough reference segments for one-to-one matching. Each decision records transcript, filename, phonetic, duration, speaker, VAD, evidence count, confidence label, and reference segment index.

Lyrics timestamps are parsed for LRC/SRT, but they are treated as timing priors only. When ASR/acoustic segments exist, acoustic timing remains primary and lyric text is paired onto those segments. Timing conflicts are reported through `lyric_timing_conflict` notes and preflight warnings instead of being silently trusted.

Short material handling now includes pinyin/phonetic matching through `pypinyin` when installed, short-clip evidence gating, and a first syllable-safe stretch strategy. Extreme expansion of short material clips uses `syllable_safe_expand_with_tail_padding`, limiting core Rubber Band stretching and filling the remaining target duration with padding.

Runtime and integration optimizations added in the same architecture pass:

1. Automatic ASR skips uncached Faster Whisper/WhisperX models unless `VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD=1`, avoiding repeated offline Hub lookup delays during manual tests.
2. Normal flat WAV assembly can render clips through `.vocalprocess_render_cache` and reuse exact duplicate source/target/render-option matches before final concatenation.
3. VST3 bridge requests accept `progress_path` and write atomic JSON progress updates while the helper runs.

Verification passed with `.venv311\Scripts\python.exe -m compileall -q audio_processor tests packaging`, `.venv311\Scripts\python.exe -m unittest discover` with 66 tests, `pip check`, `audio_processor check`, and a real CLI `batch` smoke that produced output WAV plus diagnostics containing `score_matrix`, `phonetic_similarity`, `syllable_safe_expand_with_tail_padding`, and `batch.item.completed`.

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

## 2026-07-27: Melodyne 3.x Compatibility Target

### Result

1. The local Melodyne 3.2 standalone at `E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe` launches successfully and can be closed cleanly.
2. This machine has no detected Melodyne 5 executable in the checked Celemony paths.
3. Melodyne compatibility work should target Melodyne 3.x here, with 5.x only if a valid local installation becomes available.

### Follow-Up

1. Keep Melodyne smoke tests pointed at the 3.x executable path.
2. Treat Melodyne as an editor/ARA workflow target rather than a generic host for the VST3 bridge.

## 2026-07-27: Timeline Handoff Export for Melodyne and VEGAS

### Decision

The user clarified that the required Melodyne support is not using Melodyne as a VST3 host, but importing rendered audio while preserving the original time-axis relationship. The durable architecture is a shared timeline handoff layer, with host-specific output profiles generated from the same clip timeline data.

### Implementation

1. Added `audio_processor.handoff` as a common host handoff module.
2. Added `export-melodyne`, which renders:
   - `melodyne_full.wav` as the continuous timeline reference;
   - full-length per-clip lane WAVs with leading silence before each clip;
   - `melodyne_handoff.json` and `melodyne_handoff.csv`;
   - the existing REAPER `.rpp`, `timeline.json`, `timeline.csv`, and stretched clip audio for deeper DAW editing.
3. Added `export-vegas`, which renders the same complete reference/lane/manifest outputs and also writes Broadcast Wave timestamp clips under `vegas_bwf`.
4. The BWF `time_reference` value is computed as `round(start_seconds * sample_rate)`, so timestamp placement remains tied to audio sample positions rather than approximate frame labels.

### Verification

1. Added unit tests for Melodyne lane rendering command construction, full-timeline duration trimming, VEGAS BWF timestamp metadata, and CLI command registration.
2. Targeted unit tests passed for `TimelineHandoffTests` and `CliTests`.

### Remaining Host-Side Work

1. Manually import the generated `vegas_bwf` clips into VEGAS and confirm that the installed version places Broadcast Wave audio by timestamp as expected.
2. Manually review Melodyne full-timeline/lane import behavior in Melodyne 3.2 after real user audio is available.

## 2026-07-27: Portable Runtime Failure From Manual Test

### User Report

A remote/manual test on another machine failed during model-assisted material ordering. The first report's console and `.diagnostics.jsonl` both show the same fatal path:

1. `inputs.reference.metadata_failed` appeared first as a warning. This means reference metadata probing failed but the batch was still allowed to continue.
2. The actual fatal failure occurred during reference ASR: `Whisper transcription failed ... No module named 'torch._C'`.
3. The material folder metadata collection had progressed, so the immediate failure was not material discovery; it was the bundled speech-recognition runtime.

The second remote/manual test failed with a bare Windows message: `系统找不到指定的文件`. The diagnostics screenshot shows material metadata had been collected and the GUI displayed a raw system-level file-not-found message, which indicates an unwrapped external process start failure.

### Root Cause Assessment

`torch._C` is PyTorch's native extension. If Python can find the `torch` package but cannot import `torch._C`, the practical causes are:

1. an incomplete portable package;
2. using a `-lite` package for full model testing;
3. copying only `VocalProcess.exe` without `_internal`;
4. an extraction/antivirus failure that removed `_internal\torch\_C.cp311-win_amd64.pyd` or `torch\lib` DLLs.

The local freshly rebuilt full package contains `_internal\torch\_C.cp311-win_amd64.pyd` and `torch\lib`, and the extracted no-VST3 full package passed the real portable model smoke test.

The second failure is most consistent with OpenAI Whisper or WhisperX internally invoking `ffmpeg` by executable name. That call does not use `audio_processor.engine.resolve_tool()`. If a tester's machine has no FFmpeg on system PATH, Whisper can fail with a bare Windows `FileNotFoundError` even though the portable package contains `bin\ffmpeg.exe`.

### Fixes

1. Runtime availability now validates PyTorch's native extension, not just the presence of the `torch` package directory.
2. ASR starts with a speech-runtime preflight. If all usable ASR backends are blocked by a broken native runtime, the batch fails before rendering with a direct portable-runtime message.
3. Batch diagnostics now include `model.runtime.preflight`, recording ASR backend availability, torch native status, and model-cache state.
4. `scripts\build_portable.ps1` now fails full-package builds if required torch/Whisper files are missing.
5. Added `scripts\check_portable_runtime.ps1` for quick ZIP or extracted-directory validation before manual testing.
6. The application now prepends bundled runtime tool directories such as `VocalProcess\bin` to process `PATH`, so third-party ASR libraries can start the packaged FFmpeg.
7. Whisper/WhisperX `FileNotFoundError` is now rewritten as an explicit FFmpeg startup failure with a portable-folder check hint.

### Remaining Validation

1. Rebuild the full no-VST3 and VST3 packages after these changes.
2. Run portable runtime check and model smoke against the rebuilt package.
3. Upload refreshed Release assets so testers do not keep using a package that can produce the `torch._C` failure.

## 2026-07-28: Cancel Semantics and Pronunciation-First Matching Target

### Manual Test Finding

The GUI can show "cancellation requested" while underlying work keeps running. The architectural issue is that cancellation was only reliably checked at queue boundaries and during FFmpeg progress loops. Long model phases, including reference transcription, material transcription, VAD, speaker embedding, and cached ordering preparation, did not receive a shared cancellation signal. FFmpeg progress processing also depended on progress stdout producing another line before the cancellation check ran.

### Correction

Cancellation must be treated as a cross-layer cooperative control signal:

1. GUI owns the cancellation event.
2. Batch passes `should_cancel` into every stage.
3. Model runtime checks before and after expensive operations and while looping material clips.
4. FFmpeg child processes are actively terminated/killed on cancellation.
5. Diagnostics must record `batch.item.cancelled` rather than leaving users with an apparent running task.

### Pronunciation Matching Direction

The current v2 ordering engine already has score matrices, global assignment, and a phonetic score using `pypinyin`, but it is not strict enough for one-character/one-syllable materials. The next architecture target is pronunciation-first matching:

1. Treat material filenames as high-value human labels. Chinese characters, pinyin, and romanized filenames must be converted to pronunciation units before scoring.
2. Match pinyin/romanized filename units against the reference's Chinese text phonetic sequence, not only against literal transcript text.
3. Use phonetic position as an ordering signal, so filenames like `wo.wav`, `shi.wav`, `ni.wav` can align to reference text `我是你`.
4. Keep acoustic timing, duration, VAD, and speaker similarity as independent evidence. Filename pronunciation can be strong evidence, but low or conflicting acoustic evidence should still produce review diagnostics.
5. Do not claim true 100% automatic accuracy. The implementation target is to make failures visible as `review_required` when the evidence is insufficient; real 100% requires manual confirmation or a stronger forced-alignment backend.

### Open-Source Reference Assessment

Typeless/OpenTypeless-like systems are relevant as speech-input product references, especially provider orchestration, personal vocabulary, and post-processing. They are not enough for this project's core because the project needs character-level audio-to-timeline alignment, not only speech-to-text.

More directly relevant references are Chinese ASR and forced-alignment systems:

1. FunASR / Paraformer-style timestamped Chinese ASR for character or sentence timing.
2. Qwen3 ForcedAligner and CTC forced-alignment approaches for aligning known text to audio.
3. Montreal Forced Aligner for dictionary/phoneme-aware forced alignment and confidence boundaries.
4. WeNet-style CTC ASR as a future local Chinese recognition/alignment candidate.
5. WhisperX and Faster Whisper as already-integrated multilingual ASR/alignment backends.

### Active Implementation Plan

1. Complete cancellation propagation through model runtime.
2. Add pronunciation-position scoring for Chinese reference text versus pinyin/romanized material filenames.
3. Raise the weight of filename pronunciation for short references while still requiring corroborating evidence for a strong confidence label.
4. Extend diagnostics and tests around cancellation and pinyin filename ordering.
5. Continue toward forced-alignment integration after this cancellation/matching reliability pass.

### Implementation Result

This pass treated cancellation reliability as required infrastructure for the two long-term core metrics: pronunciation-first material ordering and pronunciation-level stretch alignment to the reference timeline.

Completed changes:

1. Cancellation is now a shared cooperative signal across batch, model runtime, ASR, VAD, speaker embedding, source separation, and external FFmpeg/UVR subprocess boundaries.
2. FFmpeg progress processes and UVR headless worker processes are actively terminated when cancellation is requested. In-process third-party model calls still cannot be interrupted mid-call, but every practical boundary before and after those calls now checks the signal.
3. Material filename pronunciation evidence was upgraded from general phonetic similarity to phonetic position matching. Chinese reference text is converted to pinyin units and matched against Chinese, pinyin, or romanized material filename hints.
4. Pinyin/romanized filename units are normalized by removing tone numbers and accepting common `v/u-umlaut/u` variants.
5. The ordering diagnostics continue to expose `phonetic_score`, `text_position`, `reference_segment_index`, confidence labels, and target durations. A pinyin filename can now provide both a pronunciation match and an ordering position.
6. Single reference segments matched to multiple per-character/per-syllable materials now produce split target durations based on their text/phonetic positions. This is the first durable bridge from ordering accuracy to pronunciation-level stretch alignment.
7. Score-matrix diagnostics now include exact pinyin/phonetic units, phonetic positions, and pronunciation-position candidate counts. Repeated or homophone positions are downweighted instead of being treated as unique strong matches.
8. Preflight warnings now include `ambiguous_phonetic_position` when a filename pronunciation matches multiple reference positions.
9. Ordering reports include a `timeline_alignment` summary that identifies split reference segments.
10. Added regression tests for:
   - pinyin filename ordering against Chinese reference text;
   - tone-number pinyin filename ordering;
   - repeated/homophone phonetic position downweighting;
   - ambiguous phonetic preflight warnings;
   - cancellation stopping material analysis before VAD/speaker embedding continues;
   - per-syllable target duration splitting from one reference segment;
   - phonetic position/unit diagnostics and timeline split summaries.

Verification:

1. `.venv311\Scripts\python.exe -m compileall -q audio_processor tests packaging` passed.
2. `.venv311\Scripts\python.exe -m unittest discover` passed with 80 tests.
3. `.venv311\Scripts\python.exe -m audio_processor check` passed and reported FFmpeg, UVR Headless Runner, Demucs, Faster Whisper, OpenAI Whisper, Silero VAD, SpeechBrain, WhisperX, pyannote.audio, and Librosa availability in the local Python 3.11 runtime.
4. `git diff --check` reported no whitespace errors, only CRLF conversion warnings.
5. Git commit is blocked in the active runtime because Git cannot create `.git\index.lock` even though a simple `.git` root write probe succeeds.
6. The refreshed no-VST3 portable package was rebuilt and passed:
   - portable runtime check;
   - extracted startup smoke;
   - model-assisted smoke with source separation skipped;
   - bundled UVR worker smoke.
7. The first VST3 portable ZIP build was interrupted by the 30-minute tool timeout after producing an incomplete about-234-MB ZIP. That file was renamed to `dist\VocalProcess-portable-vst3.broken-20260728-061407.zip` and must not be published.
8. A later VST3-only portable build completed and produced `dist\VocalProcess-portable-vst3.zip`; that ZIP passed portable runtime check, extracted startup smoke, model smoke, and bundled UVR worker smoke.
9. Native VST3 bridge probing is still blocked on this machine/session: `scripts\probe_vst3_bridge.ps1` fails during CMake compiler detection because MSBuild `Microsoft.Build.Utilities.FileTracker` raises `UnauthorizedAccessException` / `E_ACCESSDENIED`.

Current architectural limits:

1. Pinyin matching currently normalizes tone information for filename matching, so homophones such as `shi` still require acoustic/ASR/duration evidence or manual review.
2. Target duration splitting assumes the available reference segment duration should be distributed by recognized text/pronunciation unit spans. This is a useful bridge, but true character start/end accuracy still requires stronger forced alignment.
3. Third-party in-process ASR/Demucs calls cannot be killed mid-function safely. External FFmpeg and UVR worker processes can now be stopped promptly; future model workers should prefer subprocess or persistent-helper boundaries where cancellation must be hard.
4. Full VST3 portable packaging has passed package-level runtime/model/UVR smoke, but host/native VST3 probing still needs an MSBuild-permission-clean environment before Release asset replacement.

Next architecture direction:

1. Add a stronger character-timing backend or optional stage, likely CTC/forced-alignment style, for Chinese text-to-audio alignment.
2. Keep filename pronunciation as high-value human label evidence, but require corroborating acoustic evidence for high-confidence automatic decisions in ambiguous homophone cases.
3. Expand diagnostics so manual testers can inspect exact pinyin units, position matches, and duration splits before rendering.

### 2026-07-28 Real Test Follow-up

1. The project now has a persistent real-test harness under `tests_real/` with tracked docs/examples and ignored audio/output directories, so real samples can be kept in place without polluting source control.
2. `audio_processor.real_eval` now treats `origin_vocal` and `material_set` as a cross-product case source, while preserving CN/JP labeling from filenames and keeping caches under per-case output folders.
3. The cache redirection matters operationally: material analysis cache no longer has to live beside the source audio, which keeps the real test corpus cleaner and makes it easier to swap or refresh audio without incidental cache churn.
4. A real JP smoke case on `PlasticLove_JP__vmzJP` finished as `review_required`. That is a useful baseline rather than a defect: it shows the pipeline is conservative on live data and still expects review on many homophone-heavy material clips.
5. The latest verification pass reached 86 unit/integration tests plus one full real smoke run, so the pronunciation-ordering and timeline-splitting changes are now covered by both synthetic and real corpus checks.

### 2026-07-28 Cache Routing Follow-up

1. Normal model-assisted ordering now routes material analysis caches into the work cache tree instead of writing beside the source material directory. That keeps both the app flow and future real-test corpora cleaner.
2. The explicit cache-directory override remains in place for the `tests_real` harness and other controlled cases, so cache placement can still be forced when a caller wants it.
3. The user-facing copy was updated to match the new behavior, which reduces the risk that the GUI or README implies source-folder mutation where none should happen.
4. The current regression surface is now 87 tests, and the change stayed within the existing pronunciation-ordering/time-alignment architecture rather than adding a separate caching subsystem.

### 2026-07-28 Maintenance Session Runner

1. A single chat/API turn cannot be treated as a reliable 10-hour daemon because the model call has a bounded request lifecycle. Durable autonomy needs an external local process plus resumable state.
2. The repository now includes a maintenance-session runner that uses a JSON plan, heartbeat, state file, event log, task stdout/stderr logs, and a stop-file contract. This gives future long work a local process that can keep running after the chat turn ends.
3. `scripts/start_maintenance_session.ps1` launches that runner with `Start-Process -WindowStyle Hidden`, satisfying the local hidden-background-service requirement while keeping all state under ignored `.tmp\maintenance_sessions`.
4. This does not make the model itself reason unattended for 10 hours; it creates the missing process-supervision layer so long deterministic checks, real-test loops, and future resumable agent orchestration have a stable substrate.
5. The runner is covered by maintenance-plan and one-cycle execution tests, and the full regression suite now passes with 90 tests.

### 2026-07-29: Real-Test Evidence and Long-Run Plan

1. The user explicitly clarified that the relevant evidence is the project software's own real test outputs, not `.codex` maintenance logs.
2. The actual project evidence came from `tests_real/origin_vocal`, `tests_real/material_set`, and `tests_real/output/real-eval-20260728-170800/summary.json` plus `analysis.json` for `PlasticLove_JP__vmzJP`.
3. That real case remained `review_required`, with the main risk signals being ambiguous phonetic positions, low match scores, and excessive stretch ratios.
4. A reusable long-run plan template now exists at `%USERPROFILE%\.codex\tmp\demo-long-run.plan.json` and includes compile, unit test, audio check, a real-eval smoke, and git status.
5. This plan is suitable as the next autonomy entry point for `E:\Workplace\demo` because it keeps real-test feedback in the loop before each cycle.

### 2026-07-29: Partial Timeline Targets and Cancellation Boundary

1. Pronunciation-level timeline accuracy now handles mixed-confidence ordering better: when only some material decisions have text/phonetic positions, the known syllable targets keep their segment-derived durations and unresolved materials receive the remaining reference time.
2. The render planner now fits model target durations through a shared duration allocator and clamps them to Rubber Band tempo bounds. This prevents impossible sub-millisecond targets from producing invalid render commands while preserving the total reference duration when the requested bounds are feasible.
3. Stretch strategy labels now distinguish full-clip stretch, syllable-safe expansion with tail padding, max-compression floor, and max-expansion ceiling. These labels are useful diagnostics for real cases where pronunciation alignment would otherwise be hidden behind a generic extreme-stretch warning.
4. Cancellation coverage now includes a stdout-idle child-process regression. The progress runner polls cancellation independently from FFmpeg progress output and explicitly closes stdout/stderr handles after termination.
5. Verification for this continuation passed `compileall`, 94 unit tests, `audio_processor check`, and `git diff --check` with only CRLF conversion warnings.

### 2026-07-29: Rendered Audio Acceptance Metrics

1. Real-eval is no longer treated as a feasibility-only harness. The acceptance target is the exported concatenated material wav, not just whether the analysis stage can build an ordering plan.
2. The new real-eval scorecard separates planning quality from rendered-output quality:
   - `match_ordering_score` tracks material ordering confidence from per-decision match scores and review-required decisions;
   - `positioned_decision_ratio` tracks how many materials have text/phonetic positions suitable for pronunciation-level timeline placement;
   - `target_duration_alignment_score` tracks whether planned clip target durations sum to the original vocal duration;
   - `rendered_audio_alignment_score` adds actual rendered wav duration validation when `--render` is enabled.
3. `strict_render_pass` is intentionally conservative: it requires a rendered output, <=1% output-duration error, <=1% target-duration total error, no error warnings/review-required matches, minimum match score above the low-score threshold, and at least 95% positioned decisions.
4. Long rendered suites now flush `summary.json` and `summary.md` after each case, including `analysis_failed` records, so interrupted autonomous runs still leave reviewable scoring evidence.
5. The suite now reports `group_score_summary` by language, reference vocal, material set, and split. This matters because the current real corpus is finite: progress should be accepted only when overall score and worst groups improve or remain stable, not when one case improves by exploiting a narrow filename or song pattern.
6. Post-score improvement strategy for autonomous rounds:
   - pick recurring warning/failure classes and worst score groups first;
   - prefer general changes in phonetic normalization, candidate ranking, timing allocation, render bounds, diagnostics, or forced-alignment plumbing;
   - reject case-specific song/material hard-coding, threshold relaxation, or warning suppression unless the full rendered suite and worst groups do not regress.
7. This gives the autonomous loop numeric metrics to improve across repeated runs: aggregate score summary, group score summary, rendered audio score, strict pass/fail counts, render duration deltas, matching warnings, and timeline warnings.
8. Remaining architectural limit: duration matching and ordering scores are still proxy evidence unless reference analysis produces aligned unit timings. The next acceptance boundary must treat missing unit timing as a failure, not as a deferred enhancement.

### 2026-07-29: Forced Unit Timing Implementation

1. The project now has a concrete unit-timing path in the render pipeline:
   - WhisperX character alignment produces `VoiceUnitTiming` records;
   - `VoiceSegment.unit_timings` carries those records through reference analysis and cache reuse;
   - model runtime duration allocation uses aligned unit start/end for positioned material decisions;
   - rendered material stretch durations therefore consume original-vocal unit durations when coverage exists.
2. Strict acceptance was tightened accordingly. A rendered case cannot pass strict validation only because total wav duration matches; it must also have high `timed_target_duration_ratio` / `aligned_timing_score` and must avoid `missing_aligned_unit_timing`.
3. `missing_aligned_unit_timing` is intentionally an error, because proportional segment splitting is not sufficient for the user's goal of matching each character's actual duration.
4. The autonomous rendered full eval now runs through `scripts/run_real_eval_render_full.ps1`, forcing WhisperX rather than allowing the default ASR fallback to skip character alignment when the WhisperX model is not cached.
5. The current hard problem is no longer "add a forced-alignment hook"; that hook is now in the duration path. The next concrete failures to solve must come from real rendered runs: model download/runtime failures, language support gaps, incomplete char coverage, bad positioned matching, or stretch/render limits surfaced by `missing_aligned_unit_timing`, `timed_target_duration_ratio`, and group score summaries.

### 2026-07-29: Real-Eval Infrastructure Gate

The rendered real-eval gate now distinguishes quality evidence from infrastructure absence. Because strict pronunciation-level timing requires WhisperX character alignment, a run where WhisperX cannot load its model is not a failed ordering experiment; it is an environment blocker.

Current machine evidence:

1. `audio_processor check` shows WhisperX is installed, but `WhisperX model cached: False` and `Faster Whisper model cached: False`.
2. `scripts/run_real_eval_render_full.ps1` forces `VOCAL_PROCESS_ASR_BACKEND=whisperx` and `VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD=1`.
3. The current environment cannot download `Systran/faster-whisper-base` from Hugging Face because HTTPS certificate verification fails.
4. Latest generated report: `tests_real\output\real-eval-20260729-213550\summary.json`.
5. The report records `analysis_failed=1`, `analysis_blocked=27`, `asr_model_download_failed=28`, `infrastructure_blocker.blocked=true`, and `recommended_exit_code=2`.

Architecture result:

1. `audio_processor.real_eval` classifies shared ASR/model/tool failures as infrastructure warning kinds rather than generic case failures.
2. When every planned case is blocked before real pronunciation/timeline analysis, the real-eval CLI returns non-zero so autonomous runners stop treating the task as OK.
3. After the first shared infrastructure blocker, remaining cases are marked `analysis_blocked` instead of repeating the same model download failure. Group counts stay visible, but the suite no longer spends time producing duplicate non-evidence.
4. This preserves the stricter acceptance rule: no silent fallback to segment-only ASR and no lowering of timing thresholds. The next valid quality-improvement cycle must first restore WhisperX model availability or pre-populate the required cache, then rerun rendered real-eval.

Next technical target:

1. Repair the WhisperX/Faster-Whisper model-cache path or machine certificate trust for Hugging Face access.
2. Once rendered eval reaches real analysis again, compare `timed_target_duration_ratio`, `aligned_timing_score`, `missing_aligned_unit_timing`, match scores, and group summaries before changing ordering or timing algorithms.
3. Continue improving pinyin/romanized filename matching and pronunciation-level timeline allocation only against actual rendered-eval evidence, not against infrastructure-blocked summaries.

### 2026-07-29: CN/JP Language Compatibility Boundary

The matching pipeline must not treat Chinese and Japanese material sets as interchangeable evidence. CN/JP material-language compatibility is now a gating condition before expensive material analysis and before real-eval scoring.

Current behavior:

1. `build_model_ordering()` checks reference/material language compatibility before material-library analysis. If both sides are confidently identified as different CN/JP languages, it raises `AudioProcessorError` with a `Language mismatch` message.
2. Reference language evidence can come from explicit filename markers, lyrics text, ASR language notes, or transcript text. Material-set evidence can come from directory markers, kana/CJK filenames, pinyin tone markers, and distinctive Chinese/Japanese romanized filename patterns.
3. Unknown language remains allowed instead of being hard-failed, because false positives would block valid custom assets. The hard gate applies only when both sides have confident CN/JP evidence and disagree.
4. `real_eval` filters automatically discovered mismatches into `skipped_cases` and writes the skip table to the Markdown report, so skipped combinations remain auditable without spending ASR/render time.
5. Current real corpus discovery result: 13 executable compatible cases, 15 skipped `language_mismatch` cases. This prevents `PlasticLove_JP`, `kamippoina_JP`, `LAB=01_JP`, and `1000nenyikiteru_JP` from being evaluated against Chinese material sets.

Acceptance impact:

1. Future ordering/timing score improvements must be measured on language-compatible groups only.
2. A user-created mismatch during normal GUI/CLI/batch testing should fail fast with a language mismatch error instead of producing a misleading rendered output.
3. Long autonomous runs should include skipped-case counts in human-readable reports so reviewers can distinguish intentional filtering from missing test coverage.

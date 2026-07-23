# Project Analysis

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

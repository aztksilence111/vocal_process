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

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


class AudioProcessorError(RuntimeError):
    """Raised when a required tool or audio processing command fails."""


@dataclass(frozen=True)
class ToolInfo:
    name: str
    path: str
    version_line: str


@dataclass(frozen=True)
class ProcessOptions:
    input_path: Path
    output_path: Path
    overwrite: bool = False
    trim_start: str | None = None
    duration: str | None = None
    gain_db: float | None = None
    normalize: bool = False
    highpass_hz: float | None = None
    lowpass_hz: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None


ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


def run_command(args: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(arg) for arg in args],
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise AudioProcessorError(f"Required tool not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or "").strip()
        command = subprocess.list2cmdline([str(arg) for arg in args])
        message = f"Command failed with exit code {exc.returncode}: {command}"
        if output:
            message = f"{message}\n{output}"
        raise AudioProcessorError(message) from exc


def resolve_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise AudioProcessorError(
            f"{name} is not available on PATH. Install FFmpeg and reopen the terminal."
        )
    return path


def get_tool_info(name: str) -> ToolInfo:
    path = resolve_tool(name)
    result = run_command([name, "-version"], capture=True)
    version_line = result.stdout.splitlines()[0] if result.stdout else "unknown version"
    return ToolInfo(name=name, path=path, version_line=version_line)


def get_environment_report() -> list[str]:
    lines = [f"Python: {sys.version.split()[0]} ({sys.executable})"]
    for tool in ("ffmpeg", "ffprobe"):
        info = get_tool_info(tool)
        lines.append(f"{tool}: {info.path}")
        lines.append(f"  {info.version_line}")
    return lines


def probe_audio(input_path: Path) -> dict[str, Any]:
    path = input_path.expanduser()
    if not path.exists():
        raise AudioProcessorError(f"Input file does not exist: {path}")

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture=True,
    )
    return json.loads(result.stdout)


def summarize_probe(data: dict[str, Any]) -> list[tuple[str, str]]:
    streams = data.get("streams", [])
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    container = data.get("format", {})

    duration = audio_stream.get("duration") or container.get("duration") or "unknown"
    bitrate = audio_stream.get("bit_rate") or container.get("bit_rate") or "unknown"

    return [
        ("format", str(container.get("format_name", "unknown"))),
        ("duration", _format_duration(duration)),
        ("codec", str(audio_stream.get("codec_name", "unknown"))),
        ("sample_rate", str(audio_stream.get("sample_rate", "unknown"))),
        ("channels", str(audio_stream.get("channels", "unknown"))),
        ("bit_rate", str(bitrate)),
    ]


def build_process_args(options: ProcessOptions, *, progress: bool = False) -> list[str]:
    _validate_options(options)

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")

    if options.trim_start:
        args.extend(["-ss", options.trim_start])

    args.extend(["-i", str(options.input_path)])

    if options.duration:
        args.extend(["-t", options.duration])

    filters = _build_audio_filters(options)
    if filters:
        args.extend(["-af", filters])

    if options.sample_rate is not None:
        args.extend(["-ar", str(options.sample_rate)])

    if options.channels is not None:
        args.extend(["-ac", str(options.channels)])

    if options.codec:
        args.extend(["-codec:a", options.codec])

    args.append(str(options.output_path))
    return args


def process_audio(options: ProcessOptions) -> None:
    run_command(build_process_args(_normalize_options(options)))


def process_audio_with_progress(
    options: ProcessOptions,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> None:
    normalized_options = _normalize_options(options)
    duration_seconds = get_audio_duration_seconds(probe_audio(normalized_options.input_path))
    args = build_process_args(normalized_options, progress=True)
    process = _open_progress_process(args)
    stderr = ""

    try:
        last_progress = 0.0
        assert process.stdout is not None
        for line in process.stdout:
            if should_cancel is not None and should_cancel():
                process.terminate()
                raise AudioProcessorError("Processing cancelled")

            key, value = _split_progress_line(line)
            if key is None:
                continue

            if key in {"out_time_us", "out_time_ms", "out_time"}:
                current_seconds = _parse_progress_time(key, value, duration_seconds)
                if current_seconds is not None and duration_seconds > 0:
                    last_progress = min(max(current_seconds / duration_seconds, last_progress), 1.0)
                    _notify_progress(on_progress, last_progress, f"{last_progress * 100:.0f}%")

            if key == "progress" and value == "end":
                last_progress = 1.0
                _notify_progress(on_progress, last_progress, "Complete")

        return_code = process.wait()
        if process.stderr is not None:
            stderr = process.stderr.read().strip()
    finally:
        if process.poll() is None:
            process.kill()

    if return_code != 0:
        command = subprocess.list2cmdline(args)
        message = f"Command failed with exit code {return_code}: {command}"
        if stderr:
            message = f"{message}\n{stderr}"
        raise AudioProcessorError(message)


def get_audio_duration_seconds(data: dict[str, Any]) -> float:
    streams = data.get("streams", [])
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    container = data.get("format", {})
    duration = audio_stream.get("duration") or container.get("duration") or 0

    try:
        return max(float(duration), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_options(options: ProcessOptions) -> ProcessOptions:
    input_path = options.input_path.expanduser()
    if not input_path.exists():
        raise AudioProcessorError(f"Input file does not exist: {input_path}")

    output_path = options.output_path.expanduser()
    if output_path.parent != Path("."):
        output_path.parent.mkdir(parents=True, exist_ok=True)

    return ProcessOptions(
        input_path=input_path,
        output_path=output_path,
        overwrite=options.overwrite,
        trim_start=options.trim_start,
        duration=options.duration,
        gain_db=options.gain_db,
        normalize=options.normalize,
        highpass_hz=options.highpass_hz,
        lowpass_hz=options.lowpass_hz,
        sample_rate=options.sample_rate,
        channels=options.channels,
        codec=options.codec,
    )


def _build_audio_filters(options: ProcessOptions) -> str:
    filters: list[str] = []

    if options.highpass_hz is not None:
        filters.append(f"highpass=f={options.highpass_hz:g}")

    if options.lowpass_hz is not None:
        filters.append(f"lowpass=f={options.lowpass_hz:g}")

    if options.gain_db is not None:
        filters.append(f"volume={options.gain_db:g}dB")

    if options.normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    return ",".join(filters)


def _validate_options(options: ProcessOptions) -> None:
    _validate_positive_float(options.highpass_hz, "highpass_hz")
    _validate_positive_float(options.lowpass_hz, "lowpass_hz")

    if options.sample_rate is not None and options.sample_rate <= 0:
        raise AudioProcessorError("sample_rate must be greater than 0")

    if options.channels is not None and options.channels <= 0:
        raise AudioProcessorError("channels must be greater than 0")


def _validate_positive_float(value: float | None, name: str) -> None:
    if value is not None and value <= 0:
        raise AudioProcessorError(f"{name} must be greater than 0")


def _format_duration(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return str(value)

    total_milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _open_progress_process(args: Sequence[str]) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            [str(arg) for arg in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AudioProcessorError(f"Required tool not found: {args[0]}") from exc


def _split_progress_line(line: str) -> tuple[str | None, str]:
    text = line.strip()
    if "=" not in text:
        return None, text
    key, value = text.split("=", 1)
    return key, value


def _parse_progress_time(key: str, value: str, duration_seconds: float) -> float | None:
    if key == "out_time":
        return _parse_timestamp(value)

    try:
        numeric_value = float(value)
    except ValueError:
        return None

    if key == "out_time_us":
        return numeric_value / 1_000_000

    if duration_seconds > 0 and numeric_value > duration_seconds * 10_000:
        return numeric_value / 1_000_000

    return numeric_value / 1000


def _parse_timestamp(value: str) -> float | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None

    return hours * 3600 + minutes * 60 + seconds


def _notify_progress(callback: ProgressCallback | None, progress: float, status: str) -> None:
    if callback is not None:
        callback(progress, status)

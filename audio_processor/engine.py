from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import hashlib
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO


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


@dataclass(frozen=True)
class MaterialStretchClip:
    index: int
    source_path: Path
    source_duration_seconds: float
    target_duration_seconds: float
    tempo: float
    quality_warning: str = ""
    requested_tempo: float | None = None
    stretch_strategy: str = "rubberband_full_clip"
    text_hint: str = ""
    fade_seconds: float = 0.0
    stretch_naturalness_score: float = 1.0
    continuity_warning: str = ""


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
RUBBERBAND_MIN_TEMPO = 0.01
RUBBERBAND_MAX_TEMPO = 100.0
RUBBERBAND_SHORT_TEXT_MIN_TEMPO = 0.35
DAW_WAV_CODEC = "pcm_s24le"
MIN_MATERIAL_TARGET_DURATION_SECONDS = 0.001
RENDERED_CLIP_CONCAT_LIST_THRESHOLD = 64
MATERIAL_RENDER_FADE_SECONDS = 0.010
MATERIAL_RENDER_MIN_FADE_SECONDS = 0.002
MATERIAL_RENDER_MIN_FADE_TARGET_SECONDS = 0.050
MATERIAL_RENDER_FADE_TARGET_FRACTION = 0.08
MATERIAL_RENDER_TINY_TARGET_SECONDS = 0.030
MATERIAL_RENDER_DURATION_TOLERANCE_SECONDS = 0.025
MATERIAL_RENDER_DURATION_TOLERANCE_RATIO = 0.001
MATERIAL_RENDER_LOOP_FILL_SIZE_SAMPLES = 2_147_483_647
MATERIAL_RENDER_FILTER_FORMAT = "material_render_filter_v6_short_text_loop_fill"

ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


def run_command(args: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    ensure_runtime_tool_paths()
    command_args = _resolve_command_args(args)
    try:
        return subprocess.run(
            [str(arg) for arg in command_args],
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            creationflags=_subprocess_creationflags(),
        )
    except FileNotFoundError as exc:
        raise _tool_launch_error(args, command_args, exc) from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or "").strip()
        command = subprocess.list2cmdline([str(arg) for arg in command_args])
        message = f"Command failed with exit code {exc.returncode}: {command}"
        if output:
            message = f"{message}\n{output}"
        raise AudioProcessorError(message) from exc


def resolve_tool(name: str) -> str:
    ensure_runtime_tool_paths()
    for path in _candidate_tool_paths(name):
        if path.is_file():
            return str(path)

    path = shutil.which(name)
    if path is None:
        raise AudioProcessorError(
            f"{name} is not available beside the application or on PATH. "
            "Install FFmpeg or use the portable package."
        )
    return path


def get_tool_info(name: str) -> ToolInfo:
    path = resolve_tool(name)
    result = run_command([path, "-version"], capture=True)
    version_line = result.stdout.splitlines()[0] if result.stdout else "unknown version"
    return ToolInfo(name=name, path=path, version_line=version_line)


def get_environment_report() -> list[str]:
    ensure_runtime_tool_paths()
    lines = [f"Python: {sys.version.split()[0]} ({sys.executable})"]
    for tool in ("ffmpeg", "ffprobe"):
        info = get_tool_info(tool)
        lines.append(f"{tool}: {info.path}")
        lines.append(f"  {info.version_line}")
    return lines


def ensure_runtime_tool_paths() -> list[Path]:
    """Expose FFmpeg tools to child libraries that invoke ffmpeg by name."""

    tool_dirs = _bundled_runtime_tool_directories()
    if not tool_dirs:
        tool_dirs = _system_runtime_tool_directories()

    if not tool_dirs:
        return []

    current_path = os.environ.get("PATH", "")
    existing_parts = [part for part in current_path.split(os.pathsep) if part]
    existing_normalized = {_normalize_env_path(part) for part in existing_parts}
    prepend = [str(path) for path in tool_dirs if _normalize_env_path(path) not in existing_normalized]
    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + existing_parts)
    return tool_dirs


def list_audio_files(directory: Path) -> list[Path]:
    material_dir = directory.expanduser()
    if not material_dir.is_dir():
        raise AudioProcessorError(f"Material directory does not exist: {material_dir}")

    return sorted(
        [
            path
            for path in material_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        ],
        key=lambda path: path.name.lower(),
    )


def probe_audio(input_path: Path) -> dict[str, Any]:
    path = input_path.expanduser()
    if not path.exists():
        raise AudioProcessorError(f"Input file does not exist: {path}")

    try:
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
    except AudioProcessorError as exc:
        fallback = _probe_audio_fallback(path)
        if fallback is not None:
            fallback["probe_warning"] = "ffprobe command failed; metadata came from fallback"
            fallback["probe_warning_details"] = str(exc)
            return fallback
        raise

    output = result.stdout
    if output is None or not output.strip():
        details = (result.stderr or "").strip()
        fallback = _probe_audio_fallback(path)
        if fallback is not None:
            fallback["probe_warning"] = "ffprobe returned no JSON metadata; metadata came from fallback"
            if details:
                fallback["probe_warning_details"] = details
            return fallback
        message = f"FFprobe returned no JSON metadata for: {path}"
        if details:
            message = f"{message}\n{details}"
        raise AudioProcessorError(message)

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        fallback = _probe_audio_fallback(path)
        if fallback is not None:
            fallback["probe_warning"] = "ffprobe returned invalid JSON metadata; metadata came from fallback"
            fallback["probe_warning_details"] = output.strip().splitlines()[0][:200] if output.strip() else ""
            return fallback
        preview = output.strip().splitlines()[0][:200]
        raise AudioProcessorError(
            f"FFprobe returned invalid JSON metadata for: {path}\n{preview}"
        ) from exc

    if not isinstance(data, dict):
        raise AudioProcessorError(f"FFprobe returned unexpected JSON metadata for: {path}")

    return data


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

    codec = options.codec or _default_audio_codec(options.output_path)
    if codec:
        args.extend(["-codec:a", codec])

    args.append(str(options.output_path))
    return args


def build_material_assembly_args(
    reference_path: Path,
    material_paths: Sequence[Path],
    output_path: Path,
    options: ProcessOptions,
    *,
    material_target_durations: Sequence[float | None] | None = None,
    material_text_hints: Sequence[str] | None = None,
    progress: bool = False,
) -> list[str]:
    _validate_options(options)

    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    clip_plan = plan_material_stretch_clips(
        reference_path,
        material_paths,
        target_durations=material_target_durations,
        material_text_hints=material_text_hints,
    )
    filters = _build_material_plan_filter_graph(clip_plan, options)

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")
    for path in material_paths:
        args.extend(["-i", str(path)])
    args.extend(["-filter_complex", filters, "-map", "[outa]"])

    if options.sample_rate is not None:
        args.extend(["-ar", str(options.sample_rate)])

    if options.channels is not None:
        args.extend(["-ac", str(options.channels)])

    codec = options.codec or _default_audio_codec(output_path)
    if codec:
        args.extend(["-codec:a", codec])

    args.append(str(output_path))
    return args


def build_material_clip_args(
    input_path: Path,
    output_path: Path,
    tempo: float,
    options: ProcessOptions,
    *,
    target_duration: float | None = None,
    text_hint: str = "",
    progress: bool = False,
) -> list[str]:
    _validate_options(options)
    resolved_tempo, _ = _resolve_stretch_strategy(tempo, text_hint, target_duration)
    _validate_rubberband_tempo(resolved_tempo)

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")
    args.extend(["-i", str(input_path)])
    args.extend([
        "-af",
        _build_material_clip_filter(
            resolved_tempo,
            options,
            target_duration=target_duration,
            text_hint=text_hint,
        ),
    ])

    if options.sample_rate is not None:
        args.extend(["-ar", str(options.sample_rate)])

    if options.channels is not None:
        args.extend(["-ac", str(options.channels)])

    codec = options.codec or _default_audio_codec(output_path)
    if codec:
        args.extend(["-codec:a", codec])

    args.append(str(output_path))
    return args


def _build_rendered_clip_concat_args(
    clip_paths: Sequence[Path],
    output_path: Path,
    options: ProcessOptions,
    *,
    concat_list_path: Path | None = None,
    progress: bool = False,
) -> list[str]:
    if not clip_paths:
        raise AudioProcessorError("Rendered material clip list is empty")

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")
    if concat_list_path is not None:
        args.extend(["-f", "concat", "-safe", "0", "-i", str(concat_list_path), "-map", "0:a"])
    elif len(clip_paths) == 1:
        args.extend(["-i", str(clip_paths[0])])
        args.extend(["-map", "0:a"])
    else:
        for path in clip_paths:
            args.extend(["-i", str(path)])
        labels = "".join(f"[{index}:a]" for index in range(len(clip_paths)))
        args.extend(["-filter_complex", f"{labels}concat=n={len(clip_paths)}:v=0:a=1[outa]", "-map", "[outa]"])

    if options.sample_rate is not None:
        args.extend(["-ar", str(options.sample_rate)])

    if options.channels is not None:
        args.extend(["-ac", str(options.channels)])

    codec = options.codec or _default_audio_codec(output_path)
    if codec:
        args.extend(["-codec:a", codec])

    args.append(str(output_path))
    return args


def _build_duration_correction_args(
    input_path: Path,
    output_path: Path,
    target_duration: float,
    options: ProcessOptions,
    *,
    fade: bool = False,
    progress: bool = False,
) -> list[str]:
    _validate_options(options)
    if target_duration <= 0:
        raise AudioProcessorError("target_duration must be greater than 0")

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.extend(
        [
            "-y",
            "-i",
            str(input_path),
            "-af",
            ",".join(_exact_duration_filters(target_duration, fade=fade)),
        ]
    )

    if options.sample_rate is not None:
        args.extend(["-ar", str(options.sample_rate)])

    if options.channels is not None:
        args.extend(["-ac", str(options.channels)])

    codec = options.codec or _default_audio_codec(output_path)
    if codec:
        args.extend(["-codec:a", codec])

    args.append(str(output_path))
    return args


def _ensure_audio_duration(
    path: Path,
    target_duration: float,
    options: ProcessOptions,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
    progress_message: str = "Correcting audio duration",
    fade: bool = False,
) -> float:
    if target_duration <= 0:
        raise AudioProcessorError("target_duration must be greater than 0")

    actual_duration = _probe_audio_duration_seconds(path)
    if _duration_matches_target(actual_duration, target_duration):
        return float(actual_duration or 0.0)
    if not path.exists():
        raise AudioProcessorError(f"Rendered audio was not created: {path}")

    temp_path = _duration_correction_temp_path(path)
    correction_options = ProcessOptions(
        input_path=path,
        output_path=temp_path,
        overwrite=True,
        sample_rate=options.sample_rate,
        channels=options.channels,
        codec=options.codec,
    )
    args = _build_duration_correction_args(
        path,
        temp_path,
        target_duration,
        correction_options,
        fade=fade,
        progress=True,
    )

    def correction_progress(progress: float, message: str) -> None:
        _notify_progress(on_progress, progress, f"{progress_message}: {message}")

    try:
        _run_progress_process(
            args,
            duration_seconds=target_duration,
            on_progress=correction_progress,
            should_cancel=should_cancel,
        )
        corrected_duration = _probe_audio_duration_seconds(temp_path)
        if not _duration_matches_target(corrected_duration, target_duration):
            actual_text = "unknown" if corrected_duration is None else f"{corrected_duration:.6f}s"
            raise AudioProcessorError(
                "Duration correction failed for "
                f"{path}: target={target_duration:.6f}s actual={actual_text}"
            )
        temp_path.replace(path)
        return float(corrected_duration or target_duration)
    finally:
        _remove_file_if_exists(temp_path)


def _duration_correction_temp_path(path: Path) -> Path:
    suffix = path.suffix or ".wav"
    return path.with_name(f"{path.stem}.duration-fix-{os.getpid()}-{time.time_ns()}{suffix}")


def _probe_audio_duration_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        duration = get_audio_duration_seconds(probe_audio(path))
    except AudioProcessorError:
        return None
    if duration <= 0:
        return None
    return duration


def _duration_matches_target(actual_duration: float | None, target_duration: float) -> bool:
    if actual_duration is None or target_duration <= 0:
        return False
    tolerance = _duration_tolerance_seconds(target_duration)
    return abs(actual_duration - target_duration) <= tolerance


def _duration_tolerance_seconds(target_duration: float) -> float:
    return max(
        MATERIAL_RENDER_DURATION_TOLERANCE_SECONDS,
        abs(target_duration) * MATERIAL_RENDER_DURATION_TOLERANCE_RATIO,
    )


def _remove_file_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


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
    _run_progress_process(
        args,
        duration_seconds=duration_seconds,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )


def process_material_clip_with_progress(
    input_path: Path,
    output_path: Path,
    tempo: float,
    options: ProcessOptions,
    *,
    target_duration: float | None = None,
    text_hint: str = "",
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> None:
    normalized_input = input_path.expanduser()
    if not normalized_input.exists():
        raise AudioProcessorError(f"Input file does not exist: {normalized_input}")

    normalized_output = output_path.expanduser()
    if normalized_output.parent != Path("."):
        normalized_output.parent.mkdir(parents=True, exist_ok=True)

    input_duration = get_audio_duration_seconds(probe_audio(normalized_input))
    resolved_tempo, _ = _resolve_stretch_strategy(tempo, text_hint, target_duration)
    expected_duration = target_duration or (input_duration / resolved_tempo if resolved_tempo > 0 else input_duration)
    args = build_material_clip_args(
        normalized_input,
        normalized_output,
        resolved_tempo,
        ProcessOptions(
            input_path=normalized_input,
            output_path=normalized_output,
            overwrite=options.overwrite,
            trim_start=None,
            duration=None,
            gain_db=options.gain_db,
            normalize=options.normalize,
            highpass_hz=options.highpass_hz,
            lowpass_hz=options.lowpass_hz,
            sample_rate=options.sample_rate,
            channels=options.channels,
            codec=options.codec,
        ),
        target_duration=target_duration,
        text_hint=text_hint,
        progress=True,
    )
    _run_progress_process(
        args,
        duration_seconds=expected_duration,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    if target_duration is not None:
        _ensure_audio_duration(
            normalized_output,
            target_duration,
            options,
            on_progress=on_progress,
            should_cancel=should_cancel,
            progress_message="Correcting material clip duration",
            fade=False,
        )


def assemble_material_to_reference_with_progress(
    reference_path: Path,
    material_directory: Path,
    output_path: Path,
    options: ProcessOptions,
    *,
    material_paths: Sequence[Path] | None = None,
    material_target_durations: Sequence[float | None] | None = None,
    material_text_hints: Sequence[str] | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> None:
    normalized_reference = reference_path.expanduser()
    if not normalized_reference.exists():
        raise AudioProcessorError(f"Reference audio does not exist: {normalized_reference}")

    normalized_output = output_path.expanduser()
    if normalized_output.parent != Path("."):
        normalized_output.parent.mkdir(parents=True, exist_ok=True)

    ordered_material_paths = list(material_paths) if material_paths is not None else list_audio_files(material_directory)
    reference_duration = get_audio_duration_seconds(probe_audio(normalized_reference))
    if len(ordered_material_paths) > 1 or material_target_durations is not None or material_text_hints is not None:
        _assemble_material_clips_with_render_cache(
            normalized_reference,
            ordered_material_paths,
            normalized_output,
            options,
            material_target_durations=material_target_durations,
            material_text_hints=material_text_hints,
            reference_duration=reference_duration,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        return

    args = build_material_assembly_args(
        normalized_reference,
        ordered_material_paths,
        normalized_output,
        ProcessOptions(
            input_path=normalized_reference,
            output_path=normalized_output,
            overwrite=options.overwrite,
            trim_start=None,
            duration=None,
            gain_db=options.gain_db,
            normalize=options.normalize,
            highpass_hz=options.highpass_hz,
            lowpass_hz=options.lowpass_hz,
            sample_rate=options.sample_rate,
            channels=options.channels,
            codec=options.codec,
        ),
        material_target_durations=material_target_durations,
        material_text_hints=material_text_hints,
        progress=True,
    )
    _run_progress_process(
        args,
        duration_seconds=reference_duration,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    _ensure_audio_duration(
        normalized_output,
        reference_duration,
        options,
        on_progress=on_progress,
        should_cancel=should_cancel,
        progress_message="Correcting assembled audio duration",
        fade=False,
    )


def _assemble_material_clips_with_render_cache(
    reference_path: Path,
    material_paths: Sequence[Path],
    output_path: Path,
    options: ProcessOptions,
    *,
    material_target_durations: Sequence[float | None] | None,
    material_text_hints: Sequence[str] | None,
    reference_duration: float,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCallback | None,
) -> None:
    if output_path.exists() and not options.overwrite:
        raise AudioProcessorError(f"Output file already exists: {output_path}")

    clips = plan_material_stretch_clips(
        reference_path,
        material_paths,
        target_durations=material_target_durations,
        material_text_hints=material_text_hints,
    )
    cache_root = output_path.parent / ".vocalprocess_render_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    total_steps = len(clips) + 1

    for clip in clips:
        if should_cancel is not None and should_cancel():
            raise AudioProcessorError("Processing cancelled")

        cache_path = cache_root / f"{_material_render_cache_key(clip, options)}.wav"
        if cache_path.exists():
            cached_duration = _probe_audio_duration_seconds(cache_path)
            if _duration_matches_target(cached_duration, clip.target_duration_seconds):
                rendered_paths.append(cache_path)
                _notify_progress(
                    on_progress,
                    clip.index / total_steps,
                    f"Reused rendered material clip {clip.index}/{len(clips)}",
                )
                continue
            _remove_file_if_exists(cache_path)

        cache_options = ProcessOptions(
            input_path=clip.source_path,
            output_path=cache_path,
            overwrite=True,
            gain_db=options.gain_db,
            normalize=options.normalize,
            highpass_hz=options.highpass_hz,
            lowpass_hz=options.lowpass_hz,
            sample_rate=options.sample_rate,
            channels=options.channels,
            codec=DAW_WAV_CODEC,
        )

        def clip_progress(progress: float, message: str) -> None:
            _notify_progress(
                on_progress,
                ((clip.index - 1) + progress) / total_steps,
                f"Rendering material clip {clip.index}/{len(clips)}: {message}",
            )

        process_material_clip_with_progress(
            clip.source_path,
            cache_path,
            clip.tempo,
            cache_options,
            target_duration=clip.target_duration_seconds,
            text_hint=clip.text_hint,
            on_progress=clip_progress,
            should_cancel=should_cancel,
        )
        _ensure_audio_duration(
            cache_path,
            clip.target_duration_seconds,
            cache_options,
            on_progress=clip_progress,
            should_cancel=should_cancel,
            progress_message="Correcting rendered material clip duration",
            fade=False,
        )
        rendered_paths.append(cache_path)

    concat_list_path = None
    if len(rendered_paths) > RENDERED_CLIP_CONCAT_LIST_THRESHOLD:
        concat_list_path = _write_rendered_clip_concat_list(rendered_paths, cache_root, output_path)
    args = _build_rendered_clip_concat_args(
        rendered_paths,
        output_path,
        options,
        concat_list_path=concat_list_path,
        progress=True,
    )

    def concat_progress(progress: float, message: str) -> None:
        _notify_progress(
            on_progress,
            (len(clips) + progress) / total_steps,
            f"Concatenating rendered material clips: {message}",
        )

    _run_progress_process(
        args,
        duration_seconds=reference_duration,
        on_progress=concat_progress,
        should_cancel=should_cancel,
    )
    _ensure_audio_duration(
        output_path,
        reference_duration,
        options,
        on_progress=concat_progress,
        should_cancel=should_cancel,
        progress_message="Correcting concatenated material audio duration",
        fade=False,
    )


def _write_rendered_clip_concat_list(
    clip_paths: Sequence[Path],
    cache_root: Path,
    output_path: Path,
) -> Path:
    digest = hashlib.sha256(
        "\n".join(str(path.resolve()) for path in clip_paths).encode("utf-8")
    ).hexdigest()[:16]
    list_path = cache_root / f"{output_path.stem}-{digest}.concat.txt"
    list_path.write_text(
        "\n".join(f"file '{_ffmpeg_concat_list_path(path)}'" for path in clip_paths) + "\n",
        encoding="utf-8",
    )
    return list_path


def _ffmpeg_concat_list_path(path: Path) -> str:
    resolved = str(path.resolve()).replace("\\", "/")
    return resolved.replace("'", "'\\''")


def plan_material_stretch_clips(
    reference_path: Path,
    material_paths: Sequence[Path],
    *,
    target_durations: Sequence[float | None] | None = None,
    material_text_hints: Sequence[str] | None = None,
) -> list[MaterialStretchClip]:
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    normalized_reference = reference_path.expanduser()
    reference_duration = get_audio_duration_seconds(probe_audio(normalized_reference))
    if reference_duration <= 0:
        raise AudioProcessorError(f"Could not read reference audio duration: {normalized_reference}")

    normalized_materials = [path.expanduser() for path in material_paths]
    source_durations = [get_audio_duration_seconds(probe_audio(path)) for path in normalized_materials]
    if any(duration <= 0 for duration in source_durations):
        bad_paths = [
            str(path)
            for path, duration in zip(normalized_materials, source_durations)
            if duration <= 0
        ]
        raise AudioProcessorError(f"Could not read material audio duration: {', '.join(bad_paths)}")

    resolved_targets = _resolve_material_target_durations(
        reference_duration,
        source_durations,
        target_durations=target_durations,
    )

    clips: list[MaterialStretchClip] = []
    resolved_text_hints = _resolve_material_text_hints(len(normalized_materials), material_text_hints)
    for index, (path, source_duration, target_duration, text_hint) in enumerate(
        zip(normalized_materials, source_durations, resolved_targets, resolved_text_hints),
        start=1,
    ):
        requested_tempo = source_duration / target_duration
        tempo, strategy = _resolve_stretch_strategy(requested_tempo, text_hint, target_duration)
        _validate_rubberband_tempo(tempo)
        clips.append(
            MaterialStretchClip(
                index=index,
                source_path=path,
                source_duration_seconds=source_duration,
                target_duration_seconds=target_duration,
                tempo=tempo,
                quality_warning=_stretch_quality_warning(requested_tempo),
                requested_tempo=requested_tempo,
                stretch_strategy=strategy,
                text_hint=text_hint,
                fade_seconds=_material_clip_fade_seconds(target_duration),
                stretch_naturalness_score=_stretch_naturalness_score_with_fill_mode(
                    requested_tempo,
                    text_hint,
                    loop_fill=strategy == "syllable_formant_expand_with_loop_fill",
                ),
                continuity_warning=_stretch_continuity_warning(requested_tempo, text_hint),
            )
        )
    return clips


def render_material_stretch_plan(clips: Sequence[MaterialStretchClip]) -> list[dict[str, Any]]:
    return [
        {
            "index": clip.index,
            "source_path": clip.source_path,
            "source_duration_seconds": clip.source_duration_seconds,
            "target_duration_seconds": clip.target_duration_seconds,
            "rubberband_tempo": clip.tempo,
            "requested_rubberband_tempo": clip.requested_tempo if clip.requested_tempo is not None else clip.tempo,
            "stretch_strategy": clip.stretch_strategy,
            "text_hint": clip.text_hint,
            "quality_warning": clip.quality_warning,
            "fade_seconds": clip.fade_seconds,
            "boundary_conditioning": (
                "loop_fill+fade_in_out"
                if clip.stretch_strategy == "syllable_formant_expand_with_loop_fill" and clip.fade_seconds > 0
                else "loop_fill"
                if clip.stretch_strategy == "syllable_formant_expand_with_loop_fill"
                else "fade_in_out" if clip.fade_seconds > 0 else ""
            ),
            "formant_preservation": (
                "direct_trim_no_pitch_shift"
                if clip.stretch_strategy == "tiny_target_direct_trim"
                else "rubberband_formant_preserved"
            ),
            "stretch_naturalness_score": clip.stretch_naturalness_score,
            "continuity_warning": clip.continuity_warning,
        }
        for clip in clips
    ]


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


def _build_material_filter_graph(
    material_count: int,
    tempo: float,
    options: ProcessOptions,
    *,
    target_duration: float | None = None,
) -> str:
    if material_count <= 0:
        raise AudioProcessorError("material_count must be greater than 0")
    if target_duration is not None and target_duration <= 0:
        raise AudioProcessorError("target_duration must be greater than 0")

    filters: list[str] = []
    source_label = "[0:a]"
    if material_count > 1:
        concat_inputs = "".join(f"[{index}:a]" for index in range(material_count))
        filters.append(f"{concat_inputs}concat=n={material_count}:v=0:a=1[cat]")
        source_label = "[cat]"

    post_filters = _material_post_filters(tempo, options, target_duration=target_duration)
    filters.append(f"{source_label}{','.join(post_filters)}[outa]")
    return ";".join(filters)


def _build_material_plan_filter_graph(
    clips: Sequence[MaterialStretchClip],
    options: ProcessOptions,
) -> str:
    if not clips:
        raise AudioProcessorError("Material stretch plan is empty")

    audio_filters = _build_audio_filters(options)
    filters: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        output_label = "outa" if len(clips) == 1 else f"clip{index}"
        post_filters = _material_post_filters(
            clip.tempo,
            options,
            target_duration=clip.target_duration_seconds,
            text_hint=clip.text_hint,
        )
        filters.append(f"[{index}:a]{','.join(post_filters)}[{output_label}]")
        if len(clips) > 1:
            labels.append(f"[{output_label}]")

    if len(clips) > 1:
        filters.append(f"{''.join(labels)}concat=n={len(clips)}:v=0:a=1[outa]")
    return ";".join(filters)


def _build_material_clip_filter(
    tempo: float,
    options: ProcessOptions,
    *,
    target_duration: float | None = None,
    text_hint: str = "",
) -> str:
    if target_duration is not None and target_duration <= 0:
        raise AudioProcessorError("target_duration must be greater than 0")

    audio_filters = _build_audio_filters(options)
    filters = []
    if not _should_direct_trim_tiny_target(target_duration):
        filters.append(_rubberband_filter(tempo))
    if audio_filters:
        filters.append(audio_filters)
    if target_duration is not None:
        filters.extend(
            _target_duration_filters(
                target_duration,
                loop_fill=_should_loop_fill_short_material(tempo, text_hint, target_duration),
            )
        )
    return ",".join(filters)


def _material_post_filters(
    tempo: float,
    options: ProcessOptions,
    *,
    target_duration: float | None,
    text_hint: str = "",
) -> list[str]:
    audio_filters = _build_audio_filters(options)
    filters: list[str] = []
    if not _should_direct_trim_tiny_target(target_duration):
        filters.append(_rubberband_filter(tempo))
    if audio_filters:
        filters.append(audio_filters)
    if target_duration is not None:
        filters.extend(
            _target_duration_filters(
                target_duration,
                loop_fill=_should_loop_fill_short_material(tempo, text_hint, target_duration),
            )
        )
    if not filters:
        filters.append("anull")
    return filters


def _should_direct_trim_tiny_target(target_duration: float | None) -> bool:
    return target_duration is not None and target_duration <= MATERIAL_RENDER_TINY_TARGET_SECONDS


def _target_duration_filters(target_duration: float, *, loop_fill: bool = False) -> list[str]:
    return _exact_duration_filters(target_duration, fade=True, loop_fill=loop_fill)


def _exact_duration_filters(target_duration: float, *, fade: bool, loop_fill: bool = False) -> list[str]:
    if loop_fill:
        filters = [
            f"aloop=loop=-1:size={MATERIAL_RENDER_LOOP_FILL_SIZE_SAMPLES}:start=0",
            f"atrim=duration={target_duration:.6f}",
        ]
    else:
        filters = [
            f"apad=whole_dur={target_duration:.6f}",
            f"atrim=duration={target_duration:.6f}",
        ]
    fade_seconds = _material_clip_fade_seconds(target_duration) if fade else 0.0
    if fade_seconds > 0:
        filters.extend(
            [
                f"afade=t=in:st=0:d={fade_seconds:.6f}",
                f"afade=t=out:st={max(target_duration - fade_seconds, 0.0):.6f}:d={fade_seconds:.6f}",
            ]
        )
    filters.append("asetpts=N/SR/TB")
    return filters


def _rubberband_filter(tempo: float) -> str:
    return f"rubberband=tempo={tempo:.8f}:pitch=1:formant=preserved:transients=crisp:phase=laminar"


def _resolve_material_target_durations(
    reference_duration: float,
    source_durations: Sequence[float],
    *,
    target_durations: Sequence[float | None] | None,
) -> list[float]:
    if reference_duration <= 0:
        raise AudioProcessorError("reference_duration must be greater than 0")
    if not source_durations:
        raise AudioProcessorError("source_durations must not be empty")

    if target_durations is not None and len(target_durations) == len(source_durations):
        explicit_targets = [_positive_optional_float(target) for target in target_durations]
        if any(target is not None for target in explicit_targets):
            all_targets_explicit = all(target is not None for target in explicit_targets)
            resolved = _resolve_explicit_material_target_durations(
                reference_duration,
                source_durations,
                explicit_targets,
            )
            if all_targets_explicit:
                return _clamp_durations_to_render_bounds(resolved, source_durations)
            return _fit_durations_to_render_bounds(resolved, source_durations, reference_duration)

    resolved = _fit_durations_to_total(source_durations, reference_duration)
    return _fit_durations_to_render_bounds(resolved, source_durations, reference_duration)


def _resolve_explicit_material_target_durations(
    reference_duration: float,
    source_durations: Sequence[float],
    explicit_targets: Sequence[float | None],
) -> list[float]:
    explicit_indices = [index for index, target in enumerate(explicit_targets) if target is not None]
    unresolved_indices = [index for index, target in enumerate(explicit_targets) if target is None]
    if not explicit_indices:
        return _fit_durations_to_total(source_durations, reference_duration)

    if not unresolved_indices:
        return [float(target or 0.0) for target in explicit_targets]

    targets: list[float | None] = [None for _ in explicit_targets]
    explicit_sum = sum(float(explicit_targets[index] or 0.0) for index in explicit_indices)
    unresolved_reserve = _minimum_duration_budget(reference_duration, len(unresolved_indices))
    explicit_budget = max(reference_duration - unresolved_reserve, 0.0)

    if explicit_sum > explicit_budget:
        explicit_values = _fit_durations_to_total(
            [float(explicit_targets[index] or 0.0) for index in explicit_indices],
            explicit_budget,
        )
    else:
        explicit_values = [float(explicit_targets[index] or 0.0) for index in explicit_indices]

    for index, value in zip(explicit_indices, explicit_values):
        targets[index] = value

    remaining_duration = max(reference_duration - sum(explicit_values), 0.0)
    unresolved_values = _fit_durations_to_total(
        [float(source_durations[index]) for index in unresolved_indices],
        remaining_duration,
    )
    for index, value in zip(unresolved_indices, unresolved_values):
        targets[index] = value

    return _fit_durations_to_total([float(value or 0.0) for value in targets], reference_duration)


def _positive_optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _minimum_duration_budget(total_duration: float, count: int) -> float:
    if count <= 0 or total_duration <= 0:
        return 0.0
    return min(MIN_MATERIAL_TARGET_DURATION_SECONDS * count, total_duration)


def _fit_durations_to_total(durations: Sequence[float], total_duration: float) -> list[float]:
    if not durations:
        return []
    if total_duration <= 0:
        return [0.0 for _ in durations]
    if len(durations) == 1:
        return [total_duration]

    minimum = min(MIN_MATERIAL_TARGET_DURATION_SECONDS, total_duration / len(durations))
    raw_values = [max(float(duration), 0.0) for duration in durations]
    raw_total = sum(raw_values)
    if raw_total <= 0:
        raw_values = [1.0 for _ in durations]
        raw_total = float(len(raw_values))

    scaled = [total_duration * (duration / raw_total) for duration in raw_values]
    adjusted: list[float] = []
    remaining = total_duration
    remaining_count = len(scaled)
    for duration in scaled[:-1]:
        max_duration = max(remaining - (minimum * (remaining_count - 1)), minimum)
        adjusted_duration = min(max(duration, minimum), max_duration)
        adjusted.append(adjusted_duration)
        remaining -= adjusted_duration
        remaining_count -= 1
    adjusted.append(max(remaining, minimum))
    return adjusted


def _fit_durations_to_render_bounds(
    durations: Sequence[float],
    source_durations: Sequence[float],
    total_duration: float,
) -> list[float]:
    if len(durations) != len(source_durations):
        return _fit_durations_to_total(durations, total_duration)
    if not durations:
        return []

    lower_bounds = [
        max(MIN_MATERIAL_TARGET_DURATION_SECONDS, float(source) / RUBBERBAND_MAX_TEMPO)
        for source in source_durations
    ]
    upper_bounds = [
        max(lower, float(source) / RUBBERBAND_MIN_TEMPO)
        for lower, source in zip(lower_bounds, source_durations)
    ]
    lower_total = sum(lower_bounds)
    upper_total = sum(upper_bounds)
    if lower_total >= total_duration:
        return lower_bounds
    if upper_total <= total_duration:
        return upper_bounds

    resolved: list[float | None] = [None for _ in durations]
    active = set(range(len(durations)))
    desired = [max(float(duration), 0.0) for duration in durations]

    while active:
        active_indices = sorted(active)
        fixed_total = sum(float(value or 0.0) for value in resolved)
        remaining_total = max(total_duration - fixed_total, 0.0)
        fitted_values = _fit_durations_to_total(
            [desired[index] for index in active_indices],
            remaining_total,
        )

        clamped: list[int] = []
        for index, value in zip(active_indices, fitted_values):
            if value < lower_bounds[index]:
                resolved[index] = lower_bounds[index]
                clamped.append(index)
            elif value > upper_bounds[index]:
                resolved[index] = upper_bounds[index]
                clamped.append(index)

        if not clamped:
            for index, value in zip(active_indices, fitted_values):
                resolved[index] = value
            break

        for index in clamped:
            active.remove(index)

    return [float(value or lower_bounds[index]) for index, value in enumerate(resolved)]


def _clamp_durations_to_render_bounds(
    durations: Sequence[float],
    source_durations: Sequence[float],
) -> list[float]:
    if len(durations) != len(source_durations):
        return [max(float(duration), MIN_MATERIAL_TARGET_DURATION_SECONDS) for duration in durations]
    resolved: list[float] = []
    for duration, source in zip(durations, source_durations):
        lower = max(MIN_MATERIAL_TARGET_DURATION_SECONDS, float(source) / RUBBERBAND_MAX_TEMPO)
        upper = max(lower, float(source) / RUBBERBAND_MIN_TEMPO)
        resolved.append(min(max(float(duration), lower), upper))
    return resolved


def _resolve_material_text_hints(count: int, hints: Sequence[str] | None) -> list[str]:
    if hints is None or len(hints) != count:
        return ["" for _ in range(count)]
    return [str(hint or "") for hint in hints]


def _resolve_stretch_strategy(
    requested_tempo: float,
    text_hint: str,
    target_duration: float | None = None,
) -> tuple[float, str]:
    if requested_tempo > RUBBERBAND_MAX_TEMPO:
        return RUBBERBAND_MAX_TEMPO, "rubberband_max_compression_floor"
    if _should_direct_trim_tiny_target(target_duration):
        return max(min(requested_tempo, RUBBERBAND_MAX_TEMPO), RUBBERBAND_MIN_TEMPO), "tiny_target_direct_trim"
    if _is_short_material_text(text_hint) and requested_tempo < RUBBERBAND_SHORT_TEXT_MIN_TEMPO:
        return RUBBERBAND_SHORT_TEXT_MIN_TEMPO, "syllable_formant_expand_with_loop_fill"
    if requested_tempo < RUBBERBAND_MIN_TEMPO:
        return RUBBERBAND_MIN_TEMPO, "rubberband_max_expansion_ceiling"
    return requested_tempo, "rubberband_full_clip"


def _is_short_material_text(text: str) -> bool:
    units = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())
    compact = "".join(units)
    return 0 < len(compact) <= 4


def _should_loop_fill_short_material(tempo: float, text_hint: str, target_duration: float | None) -> bool:
    return (
        target_duration is not None
        and not _should_direct_trim_tiny_target(target_duration)
        and _is_short_material_text(text_hint)
        and tempo <= RUBBERBAND_SHORT_TEXT_MIN_TEMPO
    )


def _material_clip_fade_seconds(target_duration: float) -> float:
    if target_duration < MATERIAL_RENDER_MIN_FADE_TARGET_SECONDS:
        return 0.0
    fade = min(MATERIAL_RENDER_FADE_SECONDS, target_duration * MATERIAL_RENDER_FADE_TARGET_FRACTION)
    return max(fade, MATERIAL_RENDER_MIN_FADE_SECONDS)


def _stretch_naturalness_score(requested_tempo: float, text_hint: str = "") -> float:
    return _stretch_naturalness_score_with_fill_mode(requested_tempo, text_hint, loop_fill=False)


def _stretch_naturalness_score_with_fill_mode(
    requested_tempo: float,
    text_hint: str = "",
    *,
    loop_fill: bool,
) -> float:
    if requested_tempo <= 0:
        return 0.0
    ratio = max(requested_tempo, 1.0 / requested_tempo)
    if ratio <= 1.0:
        score = 1.0
    else:
        score = max(1.0 - (math.log(ratio, 2) / 2.5), 0.0)
    if _is_short_material_text(text_hint):
        if ratio >= 2.0:
            score *= 0.8 if loop_fill else 0.65
        elif ratio >= 1.5:
            score *= 0.92 if loop_fill else 0.85
    return max(min(score, 1.0), 0.0)


def _stretch_continuity_warning(requested_tempo: float, text_hint: str = "") -> str:
    if requested_tempo <= 0:
        return "invalid_stretch_ratio"
    ratio = max(requested_tempo, 1.0 / requested_tempo)
    if _is_short_material_text(text_hint) and ratio >= 2.0:
        return "single_syllable_boundary_risk"
    if ratio >= 3.0:
        return "extreme_boundary_risk"
    if ratio >= 1.75:
        return "moderate_boundary_risk"
    return ""


def _material_render_cache_key(clip: MaterialStretchClip, options: ProcessOptions) -> str:
    stat = clip.source_path.stat()
    payload = {
        "format": "vocal_process_render_cache_key_v1",
        "source_path": str(clip.source_path),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "source_duration_seconds": round(clip.source_duration_seconds, 9),
        "target_duration_seconds": round(clip.target_duration_seconds, 9),
        "rubberband_tempo": round(clip.tempo, 10),
        "requested_rubberband_tempo": round(clip.requested_tempo if clip.requested_tempo is not None else clip.tempo, 10),
        "stretch_strategy": clip.stretch_strategy,
        "filter_format": MATERIAL_RENDER_FILTER_FORMAT,
        "text_hint": clip.text_hint,
        "options": {
            "gain_db": options.gain_db,
            "normalize": options.normalize,
            "highpass_hz": options.highpass_hz,
            "lowpass_hz": options.lowpass_hz,
            "sample_rate": options.sample_rate,
            "channels": options.channels,
            "codec": DAW_WAV_CODEC,
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stretch_quality_warning(tempo: float) -> str:
    if tempo < 0.5 or tempo > 2.0:
        return "extreme_stretch_ratio"
    if tempo < 0.75 or tempo > 1.5:
        return "moderate_stretch_ratio"
    return ""


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


def _validate_rubberband_tempo(tempo: float) -> None:
    if tempo < RUBBERBAND_MIN_TEMPO or tempo > RUBBERBAND_MAX_TEMPO:
        raise AudioProcessorError(
            "Material/reference duration ratio is outside FFmpeg rubberband limits "
            f"({RUBBERBAND_MIN_TEMPO:g} to {RUBBERBAND_MAX_TEMPO:g}): {tempo:.4g}"
        )


def _default_audio_codec(output_path: Path) -> str | None:
    if output_path.suffix.lower() == ".wav":
        return DAW_WAV_CODEC
    return None


def _resolve_command_args(args: Sequence[str]) -> list[str]:
    resolved_args = [str(arg) for arg in args]
    if not resolved_args:
        return resolved_args

    command = Path(resolved_args[0])
    tool_name = _normal_tool_name(command.name)
    if tool_name in {"ffmpeg", "ffprobe"} and command.parent == Path("."):
        resolved_args[0] = resolve_tool(tool_name)

    return resolved_args


def _candidate_tool_paths(name: str) -> list[Path]:
    executable_name = _tool_executable_name(name)
    candidates: list[Path] = []
    for root in _runtime_tool_roots():
        candidates.append(root / executable_name)
        candidates.append(root / "bin" / executable_name)
    return candidates


def _bundled_runtime_tool_directories() -> list[Path]:
    tool_dirs: list[Path] = []
    for root in _runtime_tool_roots():
        for candidate in (root / "bin", root):
            _append_runtime_tool_directory(tool_dirs, candidate)
    return tool_dirs


def _system_runtime_tool_directories() -> list[Path]:
    tool_dirs: list[Path] = []
    for candidate in _configured_runtime_tool_directories():
        _append_runtime_tool_directory(tool_dirs, candidate)
    for tool_name in ("ffmpeg", "ffprobe"):
        resolved = shutil.which(tool_name)
        if resolved:
            _append_runtime_tool_directory(tool_dirs, Path(resolved).parent)
    if sys.platform.startswith("win"):
        for candidate in _windows_common_runtime_tool_directories():
            _append_runtime_tool_directory(tool_dirs, candidate)
    return tool_dirs


def _append_runtime_tool_directory(tool_dirs: list[Path], candidate: Path) -> None:
    if not _contains_runtime_tool(candidate):
        return
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    if resolved not in tool_dirs:
        tool_dirs.append(resolved)


def _configured_runtime_tool_directories() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("VOCAL_PROCESS_FFMPEG_DIR", "VOCAL_PROCESS_RUNTIME_TOOL_DIRS"):
        value = os.environ.get(env_name, "")
        if not value.strip():
            continue
        for part in value.split(os.pathsep):
            path_text = part.strip()
            if path_text:
                candidates.append(_runtime_tool_directory_from_value(path_text))
    return candidates


def _runtime_tool_directory_from_value(value: str) -> Path:
    path = Path(value).expanduser()
    if _normal_tool_name(path.name) in {"ffmpeg", "ffprobe"}:
        return path.parent
    return path


def _windows_common_runtime_tool_directories() -> list[Path]:
    candidates: list[Path] = []
    program_data = os.environ.get("ProgramData")
    if program_data:
        base = Path(program_data)
    else:
        base = Path(os.environ.get("SystemDrive", "C:") + "\\ProgramData")
    candidates.append(base / "chocolatey" / "bin")
    candidates.append(base / "chocolatey" / "lib" / "ffmpeg" / "tools" / "ffmpeg" / "bin")

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / "scoop" / "shims")

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Links")
    return candidates


def _contains_runtime_tool(path: Path) -> bool:
    return any((path / _tool_executable_name(name)).is_file() for name in ("ffmpeg", "ffprobe"))


def _normalize_env_path(path: str | Path) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _runtime_tool_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(str(bundle_root)).resolve())

    roots.append(Path(__file__).resolve().parent)
    roots.append(Path.cwd())

    unique_roots: list[Path] = []
    for root in roots:
        if root not in unique_roots:
            unique_roots.append(root)
    return unique_roots


def _tool_executable_name(name: str) -> str:
    if sys.platform.startswith("win") and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def _normal_tool_name(name: str) -> str:
    normalized = name.lower()
    if normalized.endswith(".exe"):
        return normalized[:-4]
    return normalized


def _probe_audio_fallback(path: Path) -> dict[str, Any] | None:
    wav_data = _probe_wav_with_stdlib(path)
    if wav_data is not None:
        return wav_data
    return _probe_audio_with_ffmpeg_stderr(path)


def _probe_wav_with_stdlib(path: Path) -> dict[str, Any] | None:
    if path.suffix.lower() != ".wav":
        return None

    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
            sample_width = handle.getsampwidth()
    except (EOFError, OSError, wave.Error):
        return None

    duration = frames / sample_rate if sample_rate > 0 else 0.0
    bits_per_sample = sample_width * 8
    codec_name = "pcm_u8" if bits_per_sample == 8 else f"pcm_s{bits_per_sample}le"
    duration_text = f"{duration:.6f}"
    return {
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": codec_name,
                "sample_rate": str(sample_rate),
                "channels": channels,
                "duration": duration_text,
            }
        ],
        "format": {"format_name": "wav", "duration": duration_text},
        "probe_fallback": "python_wave",
    }


def _probe_audio_with_ffmpeg_stderr(path: Path) -> dict[str, Any] | None:
    try:
        command_args = _resolve_command_args(["ffmpeg", "-hide_banner", "-i", str(path)])
        result = subprocess.run(
            [str(arg) for arg in command_args],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_subprocess_creationflags(),
        )
    except (FileNotFoundError, OSError, AudioProcessorError):
        return None

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    duration = _parse_ffmpeg_duration(output)
    if duration is None:
        return None

    audio_line = _first_ffmpeg_audio_line(output)
    stream: dict[str, Any] = {"codec_type": "audio", "duration": f"{duration:.6f}"}
    codec_name = _parse_ffmpeg_audio_codec(audio_line)
    sample_rate = _parse_ffmpeg_sample_rate(audio_line)
    channels = _parse_ffmpeg_channels(audio_line)
    if codec_name:
        stream["codec_name"] = codec_name
    if sample_rate:
        stream["sample_rate"] = str(sample_rate)
    if channels:
        stream["channels"] = channels

    return {
        "streams": [stream],
        "format": {
            "format_name": path.suffix.lower().lstrip(".") or "unknown",
            "duration": f"{duration:.6f}",
        },
        "probe_fallback": "ffmpeg_stderr",
    }


def _parse_ffmpeg_duration(output: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return None
    return max(
        (int(match.group(1)) * 3600) + (int(match.group(2)) * 60) + float(match.group(3)),
        0.0,
    )


def _first_ffmpeg_audio_line(output: str) -> str:
    for line in output.splitlines():
        if "Audio:" in line:
            return line.strip()
    return ""


def _parse_ffmpeg_audio_codec(audio_line: str) -> str | None:
    match = re.search(r"Audio:\s*([^,\s]+)", audio_line)
    return match.group(1) if match else None


def _parse_ffmpeg_sample_rate(audio_line: str) -> int | None:
    match = re.search(r"(\d+)\s*Hz", audio_line)
    return int(match.group(1)) if match else None


def _parse_ffmpeg_channels(audio_line: str) -> int | None:
    if re.search(r"\bmono\b", audio_line, re.IGNORECASE):
        return 1
    if re.search(r"\bstereo\b", audio_line, re.IGNORECASE):
        return 2
    match = re.search(r"(\d+)\s+channels?", audio_line, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _subprocess_creationflags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


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
    command_args = _resolve_command_args(args)
    try:
        return subprocess.Popen(
            [str(arg) for arg in command_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            creationflags=_subprocess_creationflags(),
        )
    except FileNotFoundError as exc:
        raise _tool_launch_error(args, command_args, exc) from exc


def _tool_launch_error(
    args: Sequence[str],
    resolved_args: Sequence[str],
    exc: FileNotFoundError,
) -> AudioProcessorError:
    if getattr(exc, "winerror", None) == 206:
        return AudioProcessorError(
            "Command line is too long for Windows process creation while launching "
            f"{args[0]}. Use concat-list rendering or reduce the number of direct input arguments."
        )
    command = subprocess.list2cmdline([str(arg) for arg in resolved_args[:4]])
    return AudioProcessorError(f"Required tool not found: {args[0]} (resolved command starts with: {command})")


def _run_progress_process(
    args: Sequence[str],
    *,
    duration_seconds: float,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> None:
    process = _open_progress_process(args)
    stderr_parts: list[str] = []
    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stdout_done = False
    return_code: int | None = None

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=_read_stream_lines,
        args=(process.stdout, stdout_queue),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_collect_stream_text,
        args=(process.stderr, stderr_parts),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        last_progress = 0.0
        while True:
            if should_cancel is not None and should_cancel():
                _stop_progress_process(process)
                raise AudioProcessorError("Processing cancelled")

            try:
                line = stdout_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and stdout_done:
                    break
                continue

            if line is None:
                stdout_done = True
                if process.poll() is not None:
                    break
                continue

            key, value = _split_progress_line(line)
            if key is None:
                continue

            if key in {"out_time_us", "out_time_ms", "out_time"}:
                current_seconds = _parse_progress_time(key, value, duration_seconds)
                if current_seconds is not None and duration_seconds > 0:
                    last_progress = min(max(current_seconds / duration_seconds, last_progress), 1.0)
                    _notify_progress(on_progress, last_progress, f"{last_progress * 100:.0f}%")

            if key == "progress" and value == "end":
                _notify_progress(on_progress, 1.0, "Complete")

        return_code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        _close_process_streams(process)

    if return_code is None:
        return_code = process.returncode

    if return_code != 0:
        command = subprocess.list2cmdline([str(arg) for arg in args])
        message = f"Command failed with exit code {return_code}: {command}"
        stderr = "".join(stderr_parts).strip()
        if stderr:
            message = f"{message}\n{stderr}"
        raise AudioProcessorError(message)


def _read_stream_lines(stream: TextIO, output: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            output.put(line)
    finally:
        output.put(None)


def _collect_stream_text(stream: TextIO, output: list[str]) -> None:
    for line in stream:
        output.append(line)


def _stop_progress_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    deadline = time.monotonic() + 2.0
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()


def _close_process_streams(process: subprocess.Popen[str]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


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

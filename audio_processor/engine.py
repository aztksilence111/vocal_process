from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import hashlib
import wave
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
DAW_WAV_CODEC = "pcm_s24le"

ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


def run_command(args: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
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
        raise AudioProcessorError(f"Required tool not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or "").strip()
        command = subprocess.list2cmdline([str(arg) for arg in command_args])
        message = f"Command failed with exit code {exc.returncode}: {command}"
        if output:
            message = f"{message}\n{output}"
        raise AudioProcessorError(message) from exc


def resolve_tool(name: str) -> str:
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
    lines = [f"Python: {sys.version.split()[0]} ({sys.executable})"]
    for tool in ("ffmpeg", "ffprobe"):
        info = get_tool_info(tool)
        lines.append(f"{tool}: {info.path}")
        lines.append(f"  {info.version_line}")
    return lines


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
    progress: bool = False,
) -> list[str]:
    _validate_options(options)
    _validate_rubberband_tempo(tempo)

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")
    args.extend(["-i", str(input_path)])
    args.extend(["-af", _build_material_clip_filter(tempo, options, target_duration=target_duration)])

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
    progress: bool = False,
) -> list[str]:
    if not clip_paths:
        raise AudioProcessorError("Rendered material clip list is empty")

    args = ["ffmpeg", "-hide_banner"]
    if progress:
        args.extend(["-loglevel", "error", "-nostats", "-progress", "pipe:1"])

    args.append("-y" if options.overwrite else "-n")
    for path in clip_paths:
        args.extend(["-i", str(path)])

    if len(clip_paths) == 1:
        args.extend(["-map", "0:a"])
    else:
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
    expected_duration = target_duration or (input_duration / tempo if tempo > 0 else input_duration)
    args = build_material_clip_args(
        normalized_input,
        normalized_output,
        tempo,
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
        progress=True,
    )
    _run_progress_process(
        args,
        duration_seconds=expected_duration,
        on_progress=on_progress,
        should_cancel=should_cancel,
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
            rendered_paths.append(cache_path)
            _notify_progress(
                on_progress,
                clip.index / total_steps,
                f"Reused rendered material clip {clip.index}/{len(clips)}",
            )
            continue

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
            on_progress=clip_progress,
            should_cancel=should_cancel,
        )
        rendered_paths.append(cache_path)

    args = _build_rendered_clip_concat_args(rendered_paths, output_path, options, progress=True)

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
        tempo, strategy = _resolve_stretch_strategy(requested_tempo, text_hint)
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

    audio_filters = _build_audio_filters(options)
    stretch_filter = f"rubberband=tempo={tempo:.8f}:pitch=1:formant=preserved:transients=crisp:phase=laminar"
    post_filters = [stretch_filter]
    if audio_filters:
        post_filters.append(audio_filters)
    if target_duration is not None:
        post_filters.append(f"apad=whole_dur={target_duration:.6f}")
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
        post_filters = [
            _rubberband_filter(clip.tempo),
        ]
        if audio_filters:
            post_filters.append(audio_filters)
        post_filters.append(f"apad=whole_dur={clip.target_duration_seconds:.6f}")
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
) -> str:
    if target_duration is not None and target_duration <= 0:
        raise AudioProcessorError("target_duration must be greater than 0")

    audio_filters = _build_audio_filters(options)
    filters = [
        _rubberband_filter(tempo)
    ]
    if audio_filters:
        filters.append(audio_filters)
    if target_duration is not None:
        filters.append(f"apad=whole_dur={target_duration:.6f}")
    return ",".join(filters)


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
        base = [
            float(target) if target is not None and float(target) > 0 else float(source)
            for source, target in zip(source_durations, target_durations)
        ]
    else:
        base = [float(source) for source in source_durations]

    total = sum(base)
    if total <= 0:
        raise AudioProcessorError("Could not resolve material target durations")

    scale = reference_duration / total
    targets = [max(duration * scale, 0.001) for duration in base]
    if len(targets) == 1:
        return [reference_duration]

    accumulated = 0.0
    adjusted: list[float] = []
    for duration in targets[:-1]:
        adjusted_duration = min(duration, max(reference_duration - accumulated - 0.001, 0.001))
        adjusted.append(adjusted_duration)
        accumulated += adjusted_duration
    adjusted.append(max(reference_duration - accumulated, 0.001))
    return adjusted


def _resolve_material_text_hints(count: int, hints: Sequence[str] | None) -> list[str]:
    if hints is None or len(hints) != count:
        return ["" for _ in range(count)]
    return [str(hint or "") for hint in hints]


def _resolve_stretch_strategy(requested_tempo: float, text_hint: str) -> tuple[float, str]:
    if _is_short_material_text(text_hint) and requested_tempo < 0.75:
        return 0.75, "syllable_safe_expand_with_tail_padding"
    return requested_tempo, "rubberband_full_clip"


def _is_short_material_text(text: str) -> bool:
    units = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())
    compact = "".join(units)
    return 0 < len(compact) <= 4


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
        raise AudioProcessorError(f"Required tool not found: {args[0]}") from exc


def _run_progress_process(
    args: Sequence[str],
    *,
    duration_seconds: float,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> None:
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
                _notify_progress(on_progress, 1.0, "Complete")

        return_code = process.wait()
        if process.stderr is not None:
            stderr = process.stderr.read().strip()
    finally:
        if process.poll() is None:
            process.kill()

    if return_code != 0:
        command = subprocess.list2cmdline([str(arg) for arg in args])
        message = f"Command failed with exit code {return_code}: {command}"
        if stderr:
            message = f"{message}\n{stderr}"
        raise AudioProcessorError(message)


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

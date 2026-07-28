from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .daw import DawExportResult, DawTimelineClip, export_daw_timeline_with_progress
from .engine import (
    DAW_WAV_CODEC,
    AudioProcessorError,
    ProcessOptions,
    get_audio_duration_seconds,
    probe_audio,
    run_command,
)

ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class TimelineHandoffLane:
    index: int
    source_path: Path
    rendered_clip_path: Path
    lane_path: Path
    start_seconds: float
    target_duration_seconds: float
    timeline_duration_seconds: float
    text_hint: str | None
    stretch_strategy: str


@dataclass(frozen=True)
class TimelineHandoffBwfClip:
    index: int
    source_path: Path
    rendered_clip_path: Path
    bwf_path: Path
    start_seconds: float
    time_reference_samples: int
    sample_rate: int


@dataclass(frozen=True)
class TimelineHandoffResult:
    target: str
    output_directory: Path
    full_mix_path: Path
    manifest_path: Path
    csv_path: Path
    daw_project_path: Path
    daw_manifest_path: Path
    daw_csv_path: Path
    audio_directory: Path
    lanes_directory: Path
    lanes: tuple[TimelineHandoffLane, ...]
    bwf_directory: Path | None = None
    bwf_clips: tuple[TimelineHandoffBwfClip, ...] = ()


DEFAULT_MELODYNE_EXE_CANDIDATES: tuple[Path, ...] = (
    Path(r"E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe"),
    Path(r"D:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe"),
    Path(r"E:\Program Files (x86)\Celemony\Melodyne Studio 3.2\Melodyne.exe"),
    Path(r"D:\Program Files (x86)\Celemony\Melodyne Studio 3.2\Melodyne.exe"),
    Path(r"E:\Program Files\Celemony\Melodyne 5\Melodyne.exe"),
    Path(r"D:\Program Files\Celemony\Melodyne 5\Melodyne.exe"),
    Path(r"C:\Program Files\Celemony\Melodyne 5\Melodyne.exe"),
)


def export_melodyne_handoff_with_progress(
    reference_path: Path,
    material_directory: Path,
    output_directory: Path,
    options: ProcessOptions,
    *,
    material_paths: Sequence[Path] | None = None,
    target_durations: Sequence[float] | None = None,
    material_text_hints: Sequence[str | None] | None = None,
    on_progress: ProgressCallback | None = None,
) -> TimelineHandoffResult:
    """Export Melodyne-friendly audio with timeline position preserved in WAV lanes."""

    return export_timeline_handoff_with_progress(
        "melodyne",
        reference_path,
        material_directory,
        output_directory,
        options,
        material_paths=material_paths,
        target_durations=target_durations,
        material_text_hints=material_text_hints,
        include_bwf_timestamps=False,
        on_progress=on_progress,
    )


def export_vegas_handoff_with_progress(
    reference_path: Path,
    material_directory: Path,
    output_directory: Path,
    options: ProcessOptions,
    *,
    material_paths: Sequence[Path] | None = None,
    target_durations: Sequence[float] | None = None,
    material_text_hints: Sequence[str | None] | None = None,
    on_progress: ProgressCallback | None = None,
) -> TimelineHandoffResult:
    """Export VEGAS-friendly audio, including Broadcast Wave timestamp clips."""

    return export_timeline_handoff_with_progress(
        "vegas",
        reference_path,
        material_directory,
        output_directory,
        options,
        material_paths=material_paths,
        target_durations=target_durations,
        material_text_hints=material_text_hints,
        include_bwf_timestamps=True,
        on_progress=on_progress,
    )


def export_timeline_handoff_with_progress(
    target: str,
    reference_path: Path,
    material_directory: Path,
    output_directory: Path,
    options: ProcessOptions,
    *,
    material_paths: Sequence[Path] | None = None,
    target_durations: Sequence[float] | None = None,
    material_text_hints: Sequence[str | None] | None = None,
    include_bwf_timestamps: bool,
    on_progress: ProgressCallback | None = None,
) -> TimelineHandoffResult:
    """Render a DAW timeline plus handoff audio that keeps clip timing explicit."""

    target_slug = _target_slug(target)
    reference_path = reference_path.expanduser().resolve()
    material_directory = material_directory.expanduser().resolve()
    output_directory = output_directory.expanduser().resolve()
    full_mix_path = output_directory / f"{target_slug}_full.wav"
    manifest_path = output_directory / f"{target_slug}_handoff.json"
    csv_path = output_directory / f"{target_slug}_handoff.csv"
    daw_project_path = output_directory / "timeline.rpp"
    lanes_directory = output_directory / f"{target_slug}_lanes"
    bwf_directory = output_directory / f"{target_slug}_bwf" if include_bwf_timestamps else None

    _assert_outputs_can_be_written(
        output_directory,
        [full_mix_path, manifest_path, csv_path, daw_project_path],
        options.overwrite,
    )

    reference_probe = probe_audio(reference_path)
    reference_duration = get_audio_duration_seconds(reference_probe)
    sample_rate = int(_audio_stream_value(reference_probe, "sample_rate", options.sample_rate or 44100))
    channels = int(_audio_stream_value(reference_probe, "channels", options.channels or 2))

    if on_progress:
        on_progress(0.01, f"正在生成{_target_label(target_slug)}基础时间轴")

    daw_options = _replace_output_path(options, daw_project_path)
    daw_result = export_daw_timeline_with_progress(
        reference_path,
        material_directory,
        daw_project_path,
        daw_options,
        material_paths=material_paths,
        target_durations=target_durations,
        material_text_hints=material_text_hints,
        on_progress=_scaled_progress(on_progress, 0.03, 0.54),
    )

    lane_paths: list[Path] = []
    lanes: list[TimelineHandoffLane] = []
    lanes_directory.mkdir(parents=True, exist_ok=True)
    if bwf_directory is not None:
        bwf_directory.mkdir(parents=True, exist_ok=True)
    total_clip_count = max(1, len(daw_result.clips))
    render_span = 0.27 if bwf_directory is not None else 0.36
    bwf_span = 0.12 if bwf_directory is not None else 0.0

    for zero_index, clip in enumerate(daw_result.clips):
        lane_path = lanes_directory / _lane_file_name(clip, target_slug)
        clip_progress = zero_index / total_clip_count
        if on_progress:
            on_progress(0.58 + render_span * clip_progress, f"正在渲染{_target_label(target_slug)}时间轴音轨 {clip.index}/{len(daw_result.clips)}")
        _render_timeline_lane(
            clip.rendered_path,
            lane_path,
            clip.start_seconds,
            reference_duration,
            sample_rate,
            channels,
            options.overwrite,
        )
        lane_paths.append(lane_path)
        lanes.append(
            TimelineHandoffLane(
                index=clip.index,
                source_path=clip.source_path,
                rendered_clip_path=clip.rendered_path,
                lane_path=lane_path,
                start_seconds=clip.start_seconds,
                target_duration_seconds=clip.target_duration_seconds,
                timeline_duration_seconds=reference_duration,
                text_hint=clip.text_hint,
                stretch_strategy=clip.stretch_strategy,
            )
        )

    bwf_clips: list[TimelineHandoffBwfClip] = []
    if bwf_directory is not None:
        for zero_index, clip in enumerate(daw_result.clips):
            bwf_path = bwf_directory / _bwf_file_name(clip, target_slug)
            time_reference_samples = max(0, int(round(clip.start_seconds * sample_rate)))
            if on_progress:
                on_progress(0.58 + render_span + bwf_span * (zero_index / total_clip_count), f"正在写入VEGAS BWF时间戳 {clip.index}/{len(daw_result.clips)}")
            _render_bwf_clip(
                clip,
                bwf_path,
                time_reference_samples,
                sample_rate,
                channels,
                options.overwrite,
            )
            bwf_clips.append(
                TimelineHandoffBwfClip(
                    index=clip.index,
                    source_path=clip.source_path,
                    rendered_clip_path=clip.rendered_path,
                    bwf_path=bwf_path,
                    start_seconds=clip.start_seconds,
                    time_reference_samples=time_reference_samples,
                    sample_rate=sample_rate,
                )
            )

    if on_progress:
        on_progress(0.88, f"正在渲染{_target_label(target_slug)}完整时间轴参考音频")
    _render_timeline_mix(
        lane_paths,
        full_mix_path,
        reference_duration,
        sample_rate,
        channels,
        options.overwrite,
    )

    result = TimelineHandoffResult(
        target=target_slug,
        output_directory=output_directory,
        full_mix_path=full_mix_path,
        manifest_path=manifest_path,
        csv_path=csv_path,
        daw_project_path=daw_result.project_path,
        daw_manifest_path=daw_result.manifest_path,
        daw_csv_path=daw_result.csv_path,
        audio_directory=daw_result.audio_directory,
        lanes_directory=lanes_directory,
        lanes=tuple(lanes),
        bwf_directory=bwf_directory,
        bwf_clips=tuple(bwf_clips),
    )
    _write_timeline_handoff_manifest(result, reference_path, sample_rate, channels, reference_duration)
    _write_timeline_handoff_csv(result)
    if on_progress:
        on_progress(1.0, f"{_target_label(target_slug)}时间轴交接文件已生成")
    return result


def open_melodyne_handoff(wav_path: Path, melodyne_exe: Path | None = None) -> subprocess.Popen[bytes]:
    """Open the full-timeline WAV in Melodyne 3.x/5.x for manual pitch editing."""

    executable = _resolve_melodyne_exe(melodyne_exe)
    wav_path = wav_path.expanduser().resolve()
    if not wav_path.exists():
        raise AudioProcessorError(f"Melodyne handoff WAV not found: {wav_path}")
    return subprocess.Popen(
        [str(executable), str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_subprocess_creationflags(),
    )


def render_timeline_handoff_manifest(
    result: TimelineHandoffResult,
    reference_path: Path,
    sample_rate: int,
    channels: int,
    reference_duration: float,
) -> dict[str, object]:
    return {
        "format": "vocal_process_timeline_handoff_v1",
        "target": result.target,
        "reference": str(reference_path),
        "sample_rate": sample_rate,
        "channels": channels,
        "timeline_duration_seconds": reference_duration,
        "outputs": {
            "full_mix_wav": str(result.full_mix_path),
            "lanes_directory": str(result.lanes_directory),
            "bwf_directory": str(result.bwf_directory) if result.bwf_directory else None,
            "daw_reaper_project": str(result.daw_project_path),
            "daw_manifest": str(result.daw_manifest_path),
            "daw_csv": str(result.daw_csv_path),
            "clip_audio_directory": str(result.audio_directory),
        },
        "import_guidance": _import_guidance(result.target),
        "lanes": [
            {
                "index": lane.index,
                "source": str(lane.source_path),
                "rendered_clip": str(lane.rendered_clip_path),
                "timeline_lane": str(lane.lane_path),
                "start_seconds": lane.start_seconds,
                "target_duration_seconds": lane.target_duration_seconds,
                "timeline_duration_seconds": lane.timeline_duration_seconds,
                "text_hint": lane.text_hint,
                "stretch_strategy": lane.stretch_strategy,
            }
            for lane in result.lanes
        ],
        "bwf_clips": [
            {
                "index": clip.index,
                "source": str(clip.source_path),
                "rendered_clip": str(clip.rendered_clip_path),
                "bwf_clip": str(clip.bwf_path),
                "start_seconds": clip.start_seconds,
                "time_reference_samples": clip.time_reference_samples,
                "sample_rate": clip.sample_rate,
            }
            for clip in result.bwf_clips
        ],
    }


def _write_timeline_handoff_manifest(
    result: TimelineHandoffResult,
    reference_path: Path,
    sample_rate: int,
    channels: int,
    reference_duration: float,
) -> None:
    manifest = render_timeline_handoff_manifest(result, reference_path, sample_rate, channels, reference_duration)
    result.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    result.manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_timeline_handoff_csv(result: TimelineHandoffResult) -> None:
    result.csv_path.parent.mkdir(parents=True, exist_ok=True)
    bwf_by_index = {clip.index: clip for clip in result.bwf_clips}
    with result.csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "start_seconds",
                "target_duration_seconds",
                "timeline_duration_seconds",
                "timeline_lane",
                "bwf_clip",
                "time_reference_samples",
                "source",
                "rendered_clip",
                "text_hint",
                "stretch_strategy",
            ],
        )
        writer.writeheader()
        for lane in result.lanes:
            bwf_clip = bwf_by_index.get(lane.index)
            writer.writerow(
                {
                    "index": lane.index,
                    "start_seconds": f"{lane.start_seconds:.6f}",
                    "target_duration_seconds": f"{lane.target_duration_seconds:.6f}",
                    "timeline_duration_seconds": f"{lane.timeline_duration_seconds:.6f}",
                    "timeline_lane": str(lane.lane_path),
                    "bwf_clip": str(bwf_clip.bwf_path) if bwf_clip else "",
                    "time_reference_samples": bwf_clip.time_reference_samples if bwf_clip else "",
                    "source": str(lane.source_path),
                    "rendered_clip": str(lane.rendered_clip_path),
                    "text_hint": lane.text_hint or "",
                    "stretch_strategy": lane.stretch_strategy,
                }
            )


def _render_timeline_lane(
    rendered_clip_path: Path,
    output_path: Path,
    start_seconds: float,
    timeline_duration_seconds: float,
    sample_rate: int,
    channels: int,
    overwrite: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(rendered_clip_path),
        "-filter_complex",
        _timeline_position_filter(start_seconds, timeline_duration_seconds),
        "-map",
        "[outa]",
        "-c:a",
        DAW_WAV_CODEC,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        str(output_path),
    ]
    run_command(args)


def _render_bwf_clip(
    clip: DawTimelineClip,
    output_path: Path,
    time_reference_samples: int,
    sample_rate: int,
    channels: int,
    overwrite: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(clip.rendered_path),
        "-map",
        "0:a:0",
        "-c:a",
        DAW_WAV_CODEC,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-write_bext",
        "1",
        "-metadata",
        f"time_reference={time_reference_samples}",
        "-metadata",
        "originator=VocalProcess",
        "-metadata",
        f"description=VocalProcess {clip.index:04d} {clip.source_path.name}",
        str(output_path),
    ]
    run_command(args)


def _render_timeline_mix(
    lane_paths: Sequence[Path],
    output_path: Path,
    timeline_duration_seconds: float,
    sample_rate: int,
    channels: int,
    overwrite: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not lane_paths:
        args = [
            "ffmpeg",
            "-hide_banner",
            "-y" if overwrite else "-n",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl={'mono' if channels == 1 else 'stereo'}",
            "-t",
            f"{timeline_duration_seconds:.6f}",
            "-c:a",
            DAW_WAV_CODEC,
            str(output_path),
        ]
        run_command(args)
        return

    args = ["ffmpeg", "-hide_banner", "-y" if overwrite else "-n"]
    for lane_path in lane_paths:
        args.extend(["-i", str(lane_path)])
    args.extend(
        [
            "-filter_complex",
            _timeline_mix_filter(len(lane_paths), timeline_duration_seconds),
            "-map",
            "[outa]",
            "-c:a",
            DAW_WAV_CODEC,
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            str(output_path),
        ]
    )
    run_command(args)


def _timeline_position_filter(start_seconds: float, timeline_duration_seconds: float) -> str:
    delay_ms = max(0, int(round(start_seconds * 1000)))
    duration = max(0.001, timeline_duration_seconds)
    return f"[0:a]adelay=delays={delay_ms}:all=1,apad=whole_dur={duration:.6f},atrim=duration={duration:.6f},asetpts=N/SR/TB[outa]"


def _timeline_mix_filter(input_count: int, timeline_duration_seconds: float) -> str:
    duration = max(0.001, timeline_duration_seconds)
    labels = "".join(f"[{index}:a]" for index in range(input_count))
    return f"{labels}amix=inputs={input_count}:duration=longest:normalize=0,atrim=duration={duration:.6f},asetpts=N/SR/TB[outa]"


def _assert_outputs_can_be_written(output_directory: Path, paths: Sequence[Path], overwrite: bool) -> None:
    if output_directory.exists() and not output_directory.is_dir():
        raise AudioProcessorError(f"Output path is not a directory: {output_directory}")
    if overwrite:
        return
    existing = [path for path in paths if path.exists()]
    if existing:
        names = ", ".join(str(path) for path in existing)
        raise AudioProcessorError(f"Refusing to overwrite existing handoff output(s): {names}. Use --overwrite.")


def _lane_file_name(clip: DawTimelineClip, target_slug: str) -> str:
    return f"{clip.index:04d}_{_safe_stem(clip.source_path)}_{target_slug}_timeline.wav"


def _bwf_file_name(clip: DawTimelineClip, target_slug: str) -> str:
    return f"{clip.index:04d}_{_safe_stem(clip.source_path)}_{target_slug}_timestamp.wav"


def _safe_stem(path: Path) -> str:
    stem = path.stem.strip() or "clip"
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in stem)
    return cleaned[:80] or "clip"


def _target_slug(target: str) -> str:
    slug = target.strip().lower().replace("_", "-")
    if slug in {"melodyne", "vegas"}:
        return slug
    raise AudioProcessorError(f"Unsupported timeline handoff target: {target}")


def _target_label(target_slug: str) -> str:
    if target_slug == "melodyne":
        return "Melodyne"
    if target_slug == "vegas":
        return "VEGAS"
    return target_slug


def _import_guidance(target_slug: str) -> dict[str, str]:
    if target_slug == "melodyne":
        return {
            "full_mix_wav": "Open this file in Melodyne when one continuous timeline is preferred.",
            "timeline_lanes": "Each lane starts at 0 seconds and contains silence before its clip, so importing lane WAVs keeps clip placement audible even when the host does not read external clip offsets.",
            "daw_project": "Open timeline.rpp in REAPER when separate movable clips are needed before manual transfer to Melodyne.",
        }
    if target_slug == "vegas":
        return {
            "bwf_timestamp_clips": "Import these Broadcast Wave files in VEGAS with timestamp placement enabled to recover clip start positions.",
            "timeline_lanes": "Fallback lane WAVs preserve timing as full-length audio if timestamp placement is unavailable.",
            "full_mix_wav": "Use this continuous reference file to verify the rendered timeline by ear.",
        }
    return {}


def _replace_output_path(options: ProcessOptions, output_path: Path) -> ProcessOptions:
    return ProcessOptions(
        input_path=options.input_path,
        output_path=output_path,
        trim_start=options.trim_start,
        duration=options.duration,
        gain_db=options.gain_db,
        normalize=options.normalize,
        highpass_hz=options.highpass_hz,
        lowpass_hz=options.lowpass_hz,
        sample_rate=options.sample_rate,
        channels=options.channels,
        overwrite=options.overwrite,
        codec=options.codec,
    )


def _audio_stream_value(probe_data: dict[str, object], key: str, default: int) -> int | str:
    streams = probe_data.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if isinstance(stream, dict) and stream.get("codec_type") == "audio":
                value = stream.get(key)
                if value not in (None, ""):
                    return value
    return default


def _scaled_progress(on_progress: ProgressCallback | None, start: float, end: float) -> ProgressCallback | None:
    if on_progress is None:
        return None

    def _callback(value: float, message: str) -> None:
        bounded = min(1.0, max(0.0, value))
        on_progress(start + (end - start) * bounded, message)

    return _callback


def _resolve_melodyne_exe(explicit_path: Path | None) -> Path:
    candidates = (explicit_path.expanduser().resolve(),) if explicit_path else DEFAULT_MELODYNE_EXE_CANDIDATES
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise AudioProcessorError(f"Melodyne executable not found. Searched: {searched}")


def _subprocess_creationflags() -> int:
    if sys.platform.startswith("win"):
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return 0

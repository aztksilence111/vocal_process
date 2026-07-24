from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from .engine import (
    AudioProcessorError,
    ProcessOptions,
    get_audio_duration_seconds,
    list_audio_files,
    probe_audio,
    process_material_clip_with_progress,
)


@dataclass(frozen=True)
class DawTimelineClip:
    index: int
    source_path: Path
    rendered_path: Path
    start_seconds: float
    source_duration_seconds: float
    target_duration_seconds: float
    actual_duration_seconds: float = 0.0


@dataclass(frozen=True)
class DawTimelinePlan:
    reference_path: Path
    reference_duration_seconds: float
    material_duration_seconds: float
    tempo: float
    clips: list[DawTimelineClip]


@dataclass(frozen=True)
class DawExportResult:
    project_path: Path
    manifest_path: Path
    csv_path: Path
    audio_directory: Path
    clips: list[DawTimelineClip]


ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


def plan_daw_timeline(
    reference_path: Path,
    material_paths: Sequence[Path],
    audio_directory: Path,
) -> DawTimelinePlan:
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    normalized_reference = reference_path.expanduser()
    reference_duration = get_audio_duration_seconds(probe_audio(normalized_reference))
    if reference_duration <= 0:
        raise AudioProcessorError(f"Could not read reference audio duration: {normalized_reference}")

    source_durations = [
        get_audio_duration_seconds(probe_audio(path.expanduser())) for path in material_paths
    ]
    material_duration = sum(source_durations)
    if material_duration <= 0:
        raise AudioProcessorError("Could not read material audio duration")

    tempo = material_duration / reference_duration
    scale = reference_duration / material_duration
    clips: list[DawTimelineClip] = []
    start = 0.0
    used_names: set[str] = set()
    for index, (path, source_duration) in enumerate(zip(material_paths, source_durations), start=1):
        if index == len(material_paths):
            target_duration = max(reference_duration - start, 0.0)
        else:
            target_duration = source_duration * scale

        rendered_path = audio_directory / _clip_file_name(index, path, used_names)
        clips.append(
            DawTimelineClip(
                index=index,
                source_path=path.expanduser(),
                rendered_path=rendered_path,
                start_seconds=start,
                source_duration_seconds=source_duration,
                target_duration_seconds=target_duration,
            )
        )
        start += target_duration

    return DawTimelinePlan(
        reference_path=normalized_reference,
        reference_duration_seconds=reference_duration,
        material_duration_seconds=material_duration,
        tempo=tempo,
        clips=clips,
    )


def export_daw_timeline_with_progress(
    reference_path: Path,
    material_directory: Path,
    project_path: Path,
    options: ProcessOptions,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> DawExportResult:
    normalized_project = project_path.expanduser()
    project_directory = normalized_project.parent
    audio_directory = project_directory / "audio"
    manifest_path = project_directory / "timeline.json"
    csv_path = project_directory / "timeline.csv"

    if not options.overwrite and normalized_project.exists():
        raise AudioProcessorError(f"DAW project already exists: {normalized_project}")

    project_directory.mkdir(parents=True, exist_ok=True)
    audio_directory.mkdir(parents=True, exist_ok=True)

    material_paths = list_audio_files(material_directory)
    plan = plan_daw_timeline(reference_path, material_paths, audio_directory)

    rendered_clips: list[DawTimelineClip] = []
    total_steps = len(plan.clips) + 1
    for clip in plan.clips:
        if should_cancel is not None and should_cancel():
            raise AudioProcessorError("Processing cancelled")

        clip_options = ProcessOptions(
            input_path=clip.source_path,
            output_path=clip.rendered_path,
            overwrite=options.overwrite,
            gain_db=options.gain_db,
            normalize=options.normalize,
            highpass_hz=options.highpass_hz,
            lowpass_hz=options.lowpass_hz,
            sample_rate=options.sample_rate,
            channels=options.channels,
            codec=None,
        )

        def clip_progress(progress: float, message: str) -> None:
            overall = ((clip.index - 1) + progress) / total_steps
            _notify_progress(
                on_progress,
                overall,
                f"Exporting DAW clip {clip.index}/{len(plan.clips)}: {message}",
            )

        process_material_clip_with_progress(
            clip.source_path,
            clip.rendered_path,
            plan.tempo,
            clip_options,
            target_duration=clip.target_duration_seconds,
            on_progress=clip_progress,
            should_cancel=should_cancel,
        )

        actual_duration = get_audio_duration_seconds(probe_audio(clip.rendered_path))
        rendered_clips.append(
            DawTimelineClip(
                index=clip.index,
                source_path=clip.source_path,
                rendered_path=clip.rendered_path,
                start_seconds=clip.start_seconds,
                source_duration_seconds=clip.source_duration_seconds,
                target_duration_seconds=clip.target_duration_seconds,
                actual_duration_seconds=actual_duration,
            )
        )

    result = DawExportResult(
        project_path=normalized_project,
        manifest_path=manifest_path,
        csv_path=csv_path,
        audio_directory=audio_directory,
        clips=rendered_clips,
    )
    _write_manifest(result, plan)
    _write_csv(result)
    _write_reaper_project(result, plan)
    _notify_progress(on_progress, 1.0, "Complete")
    return result


def render_manifest(result: DawExportResult, plan: DawTimelinePlan) -> dict[str, object]:
    return {
        "format": "vocal_process_timeline_v1",
        "reference": {
            "path": str(plan.reference_path),
            "duration_seconds": plan.reference_duration_seconds,
        },
        "material": {
            "total_duration_seconds": plan.material_duration_seconds,
            "tempo": plan.tempo,
            "note": "tempo is material_total_duration / reference_duration",
        },
        "outputs": {
            "project_path": str(result.project_path),
            "audio_directory": str(result.audio_directory),
        },
        "clips": [
            {
                **asdict(clip),
                "source_path": str(clip.source_path),
                "rendered_path": str(clip.rendered_path),
            }
            for clip in result.clips
        ],
    }


def render_reaper_project(result: DawExportResult, plan: DawTimelinePlan) -> str:
    lines = [
        '<REAPER_PROJECT 0.1 "VocalProcess" 0',
        "  RIPPLE 0",
        "  GROUPOVERRIDE 0 0 0",
        "  AUTOXFADE 1",
        "  TEMPO 120 4 4",
        _render_reference_track(result, plan),
        _render_material_track(result),
        ">",
        "",
    ]
    return "\n".join(lines)


def _write_manifest(result: DawExportResult, plan: DawTimelinePlan) -> None:
    result.manifest_path.write_text(
        json.dumps(render_manifest(result, plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_csv(result: DawExportResult) -> None:
    with result.csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "index",
                "source_path",
                "rendered_path",
                "start_seconds",
                "target_duration_seconds",
                "actual_duration_seconds",
            ]
        )
        for clip in result.clips:
            writer.writerow(
                [
                    clip.index,
                    str(clip.source_path),
                    str(clip.rendered_path),
                    f"{clip.start_seconds:.6f}",
                    f"{clip.target_duration_seconds:.6f}",
                    f"{clip.actual_duration_seconds:.6f}",
                ]
            )


def _write_reaper_project(result: DawExportResult, plan: DawTimelinePlan) -> None:
    result.project_path.write_text(render_reaper_project(result, plan), encoding="utf-8")


def _render_reference_track(result: DawExportResult, plan: DawTimelinePlan) -> str:
    source = _relative_rpp_path(plan.reference_path, result.project_path.parent)
    return "\n".join(
        [
            f"  <TRACK {{{uuid.uuid4()}}}",
            '    NAME "Original Reference"',
            "    MUTESOLO 1 0 0",
            "    VOLPAN 1 0 -1 -1 1",
            "    <ITEM",
            "      POSITION 0.000000",
            f"      LENGTH {plan.reference_duration_seconds:.6f}",
            '      NAME "Original Reference"',
            "      MUTE 0 0",
            f"      IGUID {{{uuid.uuid4()}}}",
            "      LOOP 0",
            "      ALLTAKES 0",
            "      <SOURCE WAVE",
            f'        FILE "{_escape_rpp(source)}"',
            "      >",
            "    >",
            "  >",
        ]
    )


def _render_material_track(result: DawExportResult) -> str:
    lines = [
        f"  <TRACK {{{uuid.uuid4()}}}",
        '    NAME "VocalProcess Stretched Clips"',
        "    MUTESOLO 0 0 0",
        "    VOLPAN 1 0 -1 -1 1",
    ]
    for clip in result.clips:
        source = _relative_rpp_path(clip.rendered_path, result.project_path.parent)
        lines.extend(
            [
                "    <ITEM",
                f"      POSITION {clip.start_seconds:.6f}",
                f"      LENGTH {clip.target_duration_seconds:.6f}",
                f'      NAME "{_escape_rpp(clip.source_path.stem)}"',
                "      MUTE 0 0",
                f"      IGUID {{{uuid.uuid4()}}}",
                "      LOOP 0",
                "      ALLTAKES 0",
                "      <SOURCE WAVE",
                f'        FILE "{_escape_rpp(source)}"',
                "      >",
                "    >",
            ]
        )
    lines.append("  >")
    return "\n".join(lines)


def _relative_rpp_path(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.resolve()).replace("\\", "/")
    return str(relative).replace("\\", "/")


def _clip_file_name(index: int, path: Path, used_names: set[str]) -> str:
    stem = _safe_stem(path.stem)
    name = f"{index:04d}_{stem}.wav"
    while name.lower() in used_names:
        stem = f"{stem}_{index}"
        name = f"{index:04d}_{stem}.wav"
    used_names.add(name.lower())
    return name


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return stem or "clip"


def _escape_rpp(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _notify_progress(callback: ProgressCallback | None, progress: float, status: str) -> None:
    if callback is not None:
        callback(progress, status)

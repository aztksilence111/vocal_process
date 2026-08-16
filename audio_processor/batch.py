from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .daw import export_daw_timeline_with_progress
from .diagnostics import DiagnosticLogger, diagnostic_log_path
from .engine import (
    AudioProcessorError,
    assemble_material_to_reference_with_progress,
    get_audio_duration_seconds,
    list_audio_files,
    plan_material_stretch_clips,
    probe_audio,
    process_audio_with_progress,
    render_material_stretch_plan,
)
from .model_runtime import (
    ReferenceChannelTopology,
    analyze_reference_channel_topology,
    build_model_ordering,
    prepare_reference_channel_lanes,
    speech_runtime_preflight_report,
)
from .settings import ProcessingSettings


@dataclass
class QueueItem:
    input_path: Path
    output_path: Path
    status: str = "Queued"
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class BatchSummary:
    total: int
    completed: int
    failed: int
    cancelled: int


ItemCallback = Callable[[int, QueueItem], None]
QueueCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


def create_queue(input_paths: Iterable[Path], settings: ProcessingSettings) -> list[QueueItem]:
    return [
        QueueItem(input_path=Path(input_path), output_path=settings.output_path_for(Path(input_path)))
        for input_path in input_paths
    ]


def run_batch_queue(
    items: list[QueueItem],
    settings: ProcessingSettings,
    *,
    on_item_update: ItemCallback | None = None,
    on_queue_progress: QueueCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> BatchSummary:
    completed = 0
    failed = 0
    cancelled = 0
    total = len(items)
    batch_started_at = time.monotonic()

    if total == 0:
        _notify_queue(on_queue_progress, 0.0, "No queued files")
        return BatchSummary(total=0, completed=0, failed=0, cancelled=0)

    for index, item in enumerate(items):
        if should_cancel is not None and should_cancel():
            cancelled += _mark_remaining_cancelled(items, index, on_item_update)
            break

        diagnostics_directory = (
            Path(settings.diagnostics_directory).expanduser()
            if settings.diagnostics_directory
            else None
        )
        diagnostics = DiagnosticLogger(diagnostic_log_path(item.output_path, diagnostics_directory))
        item_started_at = time.monotonic()
        item.status = "Processing"
        item.progress = 0.0
        item.elapsed_seconds = 0.0
        item.message = f"Diagnostics: {diagnostics.path}"
        _notify_item(on_item_update, index, item)
        diagnostics.event(
            "batch.item.started",
            "Queued item started",
            item_index=index,
            total_items=total,
            input_path=item.input_path,
            output_path=item.output_path,
            mode=_processing_mode(settings),
            settings=settings.to_dict(),
        )

        def progress_callback(progress: float, message: str) -> None:
            if should_cancel is not None and should_cancel():
                raise AudioProcessorError("Processing cancelled")
            item.progress = progress
            item.elapsed_seconds = _elapsed_since(item_started_at)
            item.message = message
            _notify_item(on_item_update, index, item)
            queue_progress = (index + progress) / total
            _notify_queue(on_queue_progress, queue_progress, f"{index + 1}/{total}: {message}")

        try:
            _log_input_diagnostics(diagnostics, item.input_path, settings)
            if should_cancel is not None and should_cancel():
                raise AudioProcessorError("Processing cancelled")
            split_channels, reference_channels, channel_topology = _should_split_reference_channels(
                settings,
                item.input_path,
            )
            if settings.split_reference_channels and not split_channels:
                diagnostics.event(
                    "reference.channels.split_skipped",
                    "Reference channel split was requested but the reference is not independent multichannel vocal content",
                    requested=True,
                    detected_channels=reference_channels,
                    reason=(
                        channel_topology.reason
                        if channel_topology is not None
                        else "channel_count_unavailable"
                    ),
                    channel_topology=(
                        channel_topology.to_dict()
                        if channel_topology is not None
                        else None
                    ),
                )
            if split_channels:
                lane_outputs = _run_split_reference_channel_item(
                    item,
                    settings,
                    diagnostics,
                    progress_callback,
                    should_cancel,
                )
                item.elapsed_seconds = _elapsed_since(item_started_at)
                diagnostics.event(
                    "batch.item.completed",
                    "Queued item completed",
                    elapsed_seconds=item.elapsed_seconds,
                    total_elapsed_seconds=_elapsed_since(batch_started_at),
                )
                item.status = "Done"
                item.progress = 1.0
                item.message = _message_with_diagnostics(
                    "Complete; channel outputs: "
                    + ", ".join(str(path.expanduser()) for path in lane_outputs),
                    diagnostics,
                )
                completed += 1
                _notify_item(on_item_update, index, item)
                _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: complete")
                continue
            options = settings.to_process_options(item.input_path, item.output_path)
            _remove_stale_output_before_overwrite(item.output_path, options.overwrite, diagnostics)
            if settings.material_directory:
                lyrics_file = settings.effective_lyrics_file()
                diagnostics.event(
                    "model.runtime.preflight",
                    "Speech recognition runtime checked before ordering",
                    report=speech_runtime_preflight_report(settings.compute_device),
                )
                ordering = build_model_ordering(
                    item.input_path,
                    Path(settings.material_directory),
                    lyrics_file=Path(lyrics_file) if lyrics_file else None,
                    work_dir=diagnostics.path.parent / "model_analysis_cache",
                    compute_device=settings.compute_device,
                    source_separation=settings.source_separation,
                    on_progress=progress_callback,
                    should_cancel=should_cancel,
                )
                ordered_material_paths = list(ordering.ordered_paths)
                ordered_material_texts = [decision.material_text for decision in ordering.decisions]
                diagnostics.event(
                    "model.ordering.completed",
                    "Model-assisted material ordering completed",
                    report=ordering.analysis_report,
                )
                stretch_plan = plan_material_stretch_clips(
                    item.input_path,
                    ordered_material_paths,
                    target_durations=ordering.target_durations,
                    audible_target_durations=ordering.target_audible_durations,
                    pre_silence_seconds=ordering.target_pre_silences,
                    material_text_hints=ordered_material_texts,
                )
                diagnostics.event(
                    "render.stretch_plan",
                    "Per-material stretch plan prepared",
                    render_strategy="per_clip_signalsmith_or_rubberband_then_concat",
                    quality_warning_count=sum(1 for clip in stretch_plan if clip.quality_warning),
                    clips=render_material_stretch_plan(stretch_plan),
                )
                lowest_score = min((decision.score for decision in ordering.decisions), default=0.0)
                review_required = (
                    lowest_score < 0.18
                    or any(decision.confidence_label == "review_required" for decision in ordering.decisions)
                    or any(clip.quality_warning for clip in stretch_plan)
                )
                if review_required:
                    diagnostics.event(
                        "model.ordering.review_required",
                        "Model-assisted ordering or stretch plan should be reviewed before trusting the output",
                        level="warning",
                        lowest_score=lowest_score,
                        review_required_decision_count=sum(
                            1 for decision in ordering.decisions if decision.confidence_label == "review_required"
                        ),
                        severe_warning_count=sum(1 for clip in stretch_plan if clip.quality_warning == "extreme_stretch_ratio"),
                        moderate_warning_count=sum(1 for clip in stretch_plan if clip.quality_warning == "moderate_stretch_ratio"),
                    )
                if settings.daw_timeline_export:
                    export_daw_timeline_with_progress(
                        item.input_path,
                        Path(settings.material_directory),
                        item.output_path,
                        options,
                        material_paths=ordered_material_paths,
                        target_durations=ordering.target_durations,
                        audible_target_durations=ordering.target_audible_durations,
                        pre_silence_seconds=ordering.target_pre_silences,
                        material_text_hints=ordered_material_texts,
                        on_progress=progress_callback,
                        should_cancel=should_cancel,
                    )
                else:
                    assemble_material_to_reference_with_progress(
                        item.input_path,
                        Path(settings.material_directory),
                        item.output_path,
                        options,
                        material_paths=ordered_material_paths,
                        material_target_durations=ordering.target_durations,
                        material_audible_target_durations=ordering.target_audible_durations,
                        material_pre_silence_seconds=ordering.target_pre_silences,
                        material_text_hints=ordered_material_texts,
                        render_cache_directory=(
                            Path(settings.render_cache_directory).expanduser()
                            if settings.render_cache_directory
                            else None
                        ),
                        on_progress=progress_callback,
                        should_cancel=should_cancel,
                    )
            else:
                process_audio_with_progress(
                    options,
                    on_progress=progress_callback,
                    should_cancel=should_cancel,
                )
        except AudioProcessorError as exc:
            item.elapsed_seconds = _elapsed_since(item_started_at)
            if str(exc) == "Processing cancelled":
                diagnostics.event(
                    "batch.item.cancelled",
                    "Processing cancelled by user",
                    elapsed_seconds=item.elapsed_seconds,
                    total_elapsed_seconds=_elapsed_since(batch_started_at),
                )
                item.status = "Cancelled"
                item.message = _message_with_diagnostics(str(exc), diagnostics)
                item.progress = 0.0
                cancelled += 1
                _notify_item(on_item_update, index, item)
                cancelled += _mark_remaining_cancelled(items, index + 1, on_item_update)
                break

            diagnostics.error(
                "batch.item.failed",
                exc,
                elapsed_seconds=item.elapsed_seconds,
                total_elapsed_seconds=_elapsed_since(batch_started_at),
            )
            item.status = "Failed"
            item.message = _message_with_diagnostics(str(exc), diagnostics)
            item.progress = 0.0
            failed += 1
            _notify_item(on_item_update, index, item)
            _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: failed")
            continue
        except Exception as exc:
            item.elapsed_seconds = _elapsed_since(item_started_at)
            diagnostics.error(
                "batch.item.unexpected_failed",
                exc,
                elapsed_seconds=item.elapsed_seconds,
                total_elapsed_seconds=_elapsed_since(batch_started_at),
            )
            item.status = "Failed"
            item.message = _message_with_diagnostics(f"Unexpected error: {exc}", diagnostics)
            item.progress = 0.0
            failed += 1
            _notify_item(on_item_update, index, item)
            _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: failed")
            continue

        item.elapsed_seconds = _elapsed_since(item_started_at)
        diagnostics.event(
            "batch.item.completed",
            "Queued item completed",
            elapsed_seconds=item.elapsed_seconds,
            total_elapsed_seconds=_elapsed_since(batch_started_at),
        )
        item.status = "Done"
        item.progress = 1.0
        item.message = _message_with_diagnostics("Complete", diagnostics)
        completed += 1
        _notify_item(on_item_update, index, item)
        _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: complete")

    return BatchSummary(total=total, completed=completed, failed=failed, cancelled=cancelled)


def _should_split_reference_channels(
    settings: ProcessingSettings,
    reference_path: Path,
) -> tuple[bool, int | None, ReferenceChannelTopology | None]:
    if not (
        settings.split_reference_channels
        and settings.material_directory
        and not settings.daw_timeline_export
    ):
        return False, None, None
    try:
        topology = analyze_reference_channel_topology(reference_path)
    except AudioProcessorError:
        channel_count = _audio_channel_count(reference_path)
        return False, channel_count, None
    return topology.split_recommended, topology.channel_count, topology


def _audio_channel_count(path: Path) -> int | None:
    try:
        data = probe_audio(path.expanduser())
    except AudioProcessorError:
        return None
    stream = next(
        (
            stream
            for stream in data.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        {},
    )
    try:
        parsed = int(stream.get("channels") or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _run_split_reference_channel_item(
    item: QueueItem,
    settings: ProcessingSettings,
    diagnostics: DiagnosticLogger,
    progress_callback: Callable[[float, str], None],
    should_cancel: CancelCallback | None,
) -> list[Path]:
    lane_work_dir = diagnostics.path.parent / "reference_channel_lanes"
    lanes = prepare_reference_channel_lanes(
        item.input_path,
        work_dir=lane_work_dir,
        compute_device=settings.compute_device,
        source_separation=settings.source_separation,
        should_cancel=should_cancel,
    )
    if not lanes:
        raise AudioProcessorError("Reference channel splitting produced no usable lanes")

    diagnostics.event(
        "reference.channels.split",
        "Reference channel lanes prepared for independent mono rendering",
        lane_count=len(lanes),
        lanes=[
            {
                "index": lane.index,
                "label": lane.label,
                "reference_path": lane.reference_path,
                "source_path": lane.source_path,
                "split_from_stereo": lane.split_from_stereo,
                "notes": list(lane.notes),
            }
            for lane in lanes
        ],
    )

    base_render_cache = (
        Path(settings.render_cache_directory).expanduser()
        if settings.render_cache_directory
        else None
    )
    lane_outputs: list[Path] = []
    for lane_index, lane in enumerate(lanes):
        if should_cancel is not None and should_cancel():
            raise AudioProcessorError("Processing cancelled")

        lane_output = (
            item.output_path
            if lane.label == "mono"
            else _channel_output_path(item.output_path, lane.label)
        )
        lane_cache = base_render_cache / lane.label if base_render_cache is not None else None
        lane_settings = replace(
            settings,
            split_reference_channels=False,
            source_separation="never",
            channels=1,
            render_cache_directory=str(lane_cache) if lane_cache is not None else "",
        )
        lane_item = QueueItem(input_path=lane.reference_path, output_path=lane_output)

        def lane_progress(
            progress: float,
            message: str,
            *,
            lane_index: int = lane_index,
            lane_label: str = lane.label,
        ) -> None:
            progress_callback(
                (lane_index + max(min(progress, 1.0), 0.0)) / len(lanes),
                f"{lane_label} channel: {message}",
            )

        lane_summary = run_batch_queue(
            [lane_item],
            lane_settings,
            on_queue_progress=lane_progress,
            should_cancel=should_cancel,
        )
        if lane_summary.cancelled:
            raise AudioProcessorError("Processing cancelled")
        if lane_summary.failed:
            raise AudioProcessorError(
                f"Reference channel lane {lane.label} failed: {lane_item.message}"
            )
        lane_outputs.append(lane_output)
        progress_callback(
            (lane_index + 1) / len(lanes),
            f"{lane.label} channel complete",
        )

    diagnostics.event(
        "reference.channels.rendered",
        "Reference channel lanes rendered as independent mono outputs",
        lane_count=len(lane_outputs),
        output_paths=lane_outputs,
        output_channels=1,
    )
    return lane_outputs


def _channel_output_path(output_path: Path, label: str) -> Path:
    suffix = output_path.suffix
    stem = output_path.stem if suffix else output_path.name
    return output_path.with_name(f"{stem}_{label}{suffix}")


def _remove_stale_output_before_overwrite(
    output_path: Path,
    overwrite: bool,
    diagnostics: DiagnosticLogger,
) -> None:
    if not overwrite or not output_path.exists() or not output_path.is_file():
        return
    try:
        output_path.unlink()
    except OSError as exc:
        raise AudioProcessorError(f"Could not remove existing output before overwrite: {output_path}") from exc
    diagnostics.event(
        "outputs.stale_removed",
        "Removed existing output before overwrite rendering",
        output_path=output_path,
    )


def _mark_remaining_cancelled(
    items: list[QueueItem],
    start_index: int,
    callback: ItemCallback | None,
) -> int:
    count = 0
    for index in range(start_index, len(items)):
        item = items[index]
        if item.status == "Queued":
            item.status = "Cancelled"
            item.message = "Cancelled before processing"
            item.progress = 0.0
            count += 1
            _notify_item(callback, index, item)
    return count


def _notify_item(callback: ItemCallback | None, index: int, item: QueueItem) -> None:
    if callback is not None:
        callback(index, item)


def _notify_queue(callback: QueueCallback | None, progress: float, message: str) -> None:
    if callback is not None:
        callback(progress, message)


def _elapsed_since(started_at: float) -> float:
    return round(max(time.monotonic() - started_at, 0.0), 3)


def _processing_mode(settings: ProcessingSettings) -> str:
    if settings.material_directory and settings.daw_timeline_export:
        return "model_assisted_daw_timeline_export"
    if settings.material_directory:
        return "model_assisted_material_stretch_assembly"
    return "direct_audio_processing"


def _log_input_diagnostics(
    diagnostics: DiagnosticLogger,
    reference_path: Path,
    settings: ProcessingSettings,
) -> None:
    reference_digest, reference_error = _safe_audio_metadata_digest(reference_path)
    if reference_digest is not None:
        diagnostics.event(
            "inputs.reference",
            "Reference audio metadata collected",
            reference=reference_digest,
        )
    else:
        diagnostics.event(
            "inputs.reference.metadata_failed",
            "Reference audio metadata could not be collected; rendering will continue to the processing stage",
            level="warning",
            reference_path=reference_path,
            error=reference_error,
        )

    if not settings.material_directory:
        return

    material_dir = Path(settings.material_directory)
    material_paths = list_audio_files(material_dir)
    material_digests: list[dict[str, Any]] = []
    material_failures: list[dict[str, str]] = []
    for path in material_paths:
        digest, error = _safe_audio_metadata_digest(path)
        if digest is not None:
            material_digests.append(digest)
        else:
            material_failures.append({"path": str(path.expanduser()), "error": error or "unknown error"})

    diagnostics.event(
        "inputs.materials",
        "Material audio metadata collected",
        material_directory=material_dir,
        material_count=len(material_paths),
        metadata_count=len(material_digests),
        metadata_failure_count=len(material_failures),
        total_duration_seconds=sum(
            float(digest.get("duration_seconds") or 0.0) for digest in material_digests
        ),
        materials=material_digests,
        metadata_failures=material_failures,
    )

    lyrics_file = settings.effective_lyrics_file()
    if lyrics_file:
        lyrics_path = Path(lyrics_file)
        diagnostics.event(
            "inputs.lyrics",
            "Lyrics file path recorded",
            lyrics_file=lyrics_path,
            exists=lyrics_path.expanduser().is_file(),
            suffix=lyrics_path.suffix.lower(),
        )


def _audio_metadata_digest(path: Path) -> dict[str, Any]:
    data = probe_audio(path.expanduser())
    streams = data.get("streams", [])
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    container = data.get("format", {})
    return {
        "path": path.expanduser(),
        "duration_seconds": get_audio_duration_seconds(data),
        "format_name": container.get("format_name"),
        "codec_name": audio_stream.get("codec_name"),
        "sample_rate": audio_stream.get("sample_rate"),
        "channels": audio_stream.get("channels"),
    }


def _safe_audio_metadata_digest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return _audio_metadata_digest(path), None
    except AudioProcessorError as exc:
        return None, str(exc)


def _message_with_diagnostics(message: str, diagnostics: DiagnosticLogger) -> str:
    return f"{message} | Diagnostics: {diagnostics.path}"

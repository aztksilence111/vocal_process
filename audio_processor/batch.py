from __future__ import annotations

import time
from dataclasses import dataclass
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
from .model_runtime import build_model_ordering
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

        diagnostics = DiagnosticLogger(diagnostic_log_path(item.output_path))
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
            item.progress = progress
            item.elapsed_seconds = _elapsed_since(item_started_at)
            item.message = message
            _notify_item(on_item_update, index, item)
            queue_progress = (index + progress) / total
            _notify_queue(on_queue_progress, queue_progress, f"{index + 1}/{total}: {message}")

        try:
            _log_input_diagnostics(diagnostics, item.input_path, settings)
            options = settings.to_process_options(item.input_path, item.output_path)
            if settings.material_directory:
                ordering = build_model_ordering(
                    item.input_path,
                    Path(settings.material_directory),
                    lyrics_file=Path(settings.lyrics_file) if settings.lyrics_file else None,
                    work_dir=diagnostics.path.parent / "model_analysis_cache",
                    compute_device=settings.compute_device,
                    source_separation=settings.source_separation,
                    on_progress=progress_callback,
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
                    material_text_hints=ordered_material_texts,
                )
                diagnostics.event(
                    "render.stretch_plan",
                    "Per-material stretch plan prepared",
                    render_strategy="per_clip_rubberband_then_concat",
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
                        material_text_hints=ordered_material_texts,
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

    if settings.lyrics_file:
        lyrics_path = Path(settings.lyrics_file)
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

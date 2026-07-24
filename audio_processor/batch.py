from __future__ import annotations

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
    probe_audio,
    process_audio_with_progress,
)
from .settings import ProcessingSettings


@dataclass
class QueueItem:
    input_path: Path
    output_path: Path
    status: str = "Queued"
    progress: float = 0.0
    message: str = ""


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

    if total == 0:
        _notify_queue(on_queue_progress, 0.0, "No queued files")
        return BatchSummary(total=0, completed=0, failed=0, cancelled=0)

    for index, item in enumerate(items):
        if should_cancel is not None and should_cancel():
            cancelled += _mark_remaining_cancelled(items, index, on_item_update)
            break

        diagnostics = DiagnosticLogger(diagnostic_log_path(item.output_path))
        item.status = "Processing"
        item.progress = 0.0
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
            item.message = message
            _notify_item(on_item_update, index, item)
            queue_progress = (index + progress) / total
            _notify_queue(on_queue_progress, queue_progress, f"{index + 1}/{total}: {message}")

        try:
            _log_input_diagnostics(diagnostics, item.input_path, settings)
            options = settings.to_process_options(item.input_path, item.output_path)
            if settings.material_directory:
                if settings.daw_timeline_export:
                    export_daw_timeline_with_progress(
                        item.input_path,
                        Path(settings.material_directory),
                        item.output_path,
                        options,
                        on_progress=progress_callback,
                        should_cancel=should_cancel,
                    )
                else:
                    assemble_material_to_reference_with_progress(
                        item.input_path,
                        Path(settings.material_directory),
                        item.output_path,
                        options,
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
            if str(exc) == "Processing cancelled":
                diagnostics.event("batch.item.cancelled", "Processing cancelled by user")
                item.status = "Cancelled"
                item.message = _message_with_diagnostics(str(exc), diagnostics)
                item.progress = 0.0
                cancelled += 1
                _notify_item(on_item_update, index, item)
                cancelled += _mark_remaining_cancelled(items, index + 1, on_item_update)
                break

            diagnostics.error("batch.item.failed", exc)
            item.status = "Failed"
            item.message = _message_with_diagnostics(str(exc), diagnostics)
            item.progress = 0.0
            failed += 1
            _notify_item(on_item_update, index, item)
            _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: failed")
            continue
        except Exception as exc:
            diagnostics.error("batch.item.unexpected_failed", exc)
            item.status = "Failed"
            item.message = _message_with_diagnostics(f"Unexpected error: {exc}", diagnostics)
            item.progress = 0.0
            failed += 1
            _notify_item(on_item_update, index, item)
            _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: failed")
            continue

        diagnostics.event("batch.item.completed", "Queued item completed")
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


def _processing_mode(settings: ProcessingSettings) -> str:
    if settings.material_directory and settings.daw_timeline_export:
        return "daw_timeline_export"
    if settings.material_directory:
        return "material_stretch_assembly"
    return "direct_audio_processing"


def _log_input_diagnostics(
    diagnostics: DiagnosticLogger,
    reference_path: Path,
    settings: ProcessingSettings,
) -> None:
    diagnostics.event(
        "inputs.reference",
        "Reference audio metadata collected",
        reference=_audio_metadata_digest(reference_path),
    )

    if not settings.material_directory:
        return

    material_dir = Path(settings.material_directory)
    material_paths = list_audio_files(material_dir)
    material_digests = [_audio_metadata_digest(path) for path in material_paths]
    diagnostics.event(
        "inputs.materials",
        "Material audio metadata collected",
        material_directory=material_dir,
        material_count=len(material_paths),
        total_duration_seconds=sum(
            float(digest.get("duration_seconds") or 0.0) for digest in material_digests
        ),
        materials=material_digests,
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


def _message_with_diagnostics(message: str, diagnostics: DiagnosticLogger) -> str:
    return f"{message} | Diagnostics: {diagnostics.path}"

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .daw import export_daw_timeline_with_progress
from .engine import AudioProcessorError, assemble_material_to_reference_with_progress, process_audio_with_progress
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

        item.status = "Processing"
        item.progress = 0.0
        item.message = ""
        _notify_item(on_item_update, index, item)

        def progress_callback(progress: float, message: str) -> None:
            item.progress = progress
            item.message = message
            _notify_item(on_item_update, index, item)
            queue_progress = (index + progress) / total
            _notify_queue(on_queue_progress, queue_progress, f"{index + 1}/{total}: {message}")

        try:
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
                item.status = "Cancelled"
                item.message = str(exc)
                item.progress = 0.0
                cancelled += 1
                _notify_item(on_item_update, index, item)
                cancelled += _mark_remaining_cancelled(items, index + 1, on_item_update)
                break

            item.status = "Failed"
            item.message = str(exc)
            item.progress = 0.0
            failed += 1
            _notify_item(on_item_update, index, item)
            _notify_queue(on_queue_progress, (index + 1) / total, f"{index + 1}/{total}: failed")
            continue

        item.status = "Done"
        item.progress = 1.0
        item.message = "Complete"
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

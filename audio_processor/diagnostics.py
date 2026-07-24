from __future__ import annotations

import json
import traceback
import uuid
from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_TEXT_LENGTH = 4000


@dataclass
class DiagnosticLogger:
    path: Path
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def event(self, stage: str, message: str, level: str = "info", **fields: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "level": level,
            "stage": stage,
            "message": message,
        }
        if fields:
            record["fields"] = _to_jsonable(fields)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def error(self, stage: str, exc: BaseException, **fields: Any) -> None:
        self.event(
            stage,
            str(exc) or exc.__class__.__name__,
            "error",
            exception_type=exc.__class__.__name__,
            exception_message=str(exc),
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            **fields,
        )


def diagnostic_log_path(output_path: Path) -> Path:
    output = output_path.expanduser()
    if output.suffix.lower() == ".rpp":
        return output.parent / "diagnostics.jsonl"
    return output.with_name(f"{output.stem}.diagnostics.jsonl")


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, str):
        return _truncate(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]

    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))

    return _truncate(str(value))


def _truncate(value: str) -> str:
    if len(value) <= MAX_TEXT_LENGTH:
        return value
    return f"{value[:MAX_TEXT_LENGTH]}...[truncated]"

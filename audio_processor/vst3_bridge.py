from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .batch import QueueItem, run_batch_queue
from .diagnostics import diagnostic_log_path
from .engine import AudioProcessorError
from .settings import ProcessingSettings, normalize_compute_device


BRIDGE_REQUEST_FORMAT = "vocal_process_vst3_bridge_request_v1"
BRIDGE_RESPONSE_FORMAT = "vocal_process_vst3_bridge_response_v1"


def run_bridge_request_file(request_path: Path, response_path: Path | None = None) -> dict[str, Any]:
    request = _load_request(request_path)
    response = run_bridge_request(request)
    output_path = response_path or Path(str(request_path)).with_suffix(".response.json")
    output_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    return response


def run_bridge_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("format") != BRIDGE_REQUEST_FORMAT:
        raise AudioProcessorError(f"Unsupported VST3 bridge request format: {request.get('format')}")

    command = str(request.get("command") or "render").strip().lower()
    if command not in {"render", "render_timeline"}:
        raise AudioProcessorError(f"Unsupported VST3 bridge command: {command}")

    reference_path = _required_path(request, "reference_path")
    material_directory = _required_path(request, "material_directory")
    output_path = _required_path(request, "output_path")
    lyrics_file = _optional_path(request, "lyrics_file")

    settings = ProcessingSettings(
        material_directory=str(material_directory),
        lyrics_file=str(lyrics_file or ""),
        daw_timeline_export=_optional_bool(
            request,
            "daw_timeline_export",
            default=output_path.suffix.lower() == ".rpp" or command == "render_timeline",
        ),
        compute_device=normalize_compute_device(request.get("compute_device")),
        output_directory=str(output_path.parent),
        output_extension=output_path.suffix or ".wav",
        overwrite=_optional_bool(request, "overwrite", default=True),
        normalize=_optional_bool(request, "normalize", default=False),
        gain_db=_optional_float(request.get("gain_db")),
        highpass_hz=_optional_float(request.get("highpass_hz")),
        lowpass_hz=_optional_float(request.get("lowpass_hz")),
        sample_rate=_optional_int(request.get("sample_rate")),
        channels=_optional_int(request.get("channels")),
        codec=_optional_string(request.get("codec")),
    )
    item = QueueItem(input_path=reference_path, output_path=output_path)
    summary = run_batch_queue([item], settings)
    ok = summary.failed == 0 and summary.cancelled == 0
    return {
        "format": BRIDGE_RESPONSE_FORMAT,
        "ok": ok,
        "command": command,
        "status": item.status,
        "message": item.message,
        "summary": {
            "total": summary.total,
            "completed": summary.completed,
            "failed": summary.failed,
            "cancelled": summary.cancelled,
        },
        "reference_path": str(reference_path),
        "material_directory": str(material_directory),
        "output_path": str(output_path),
        "diagnostics_path": str(diagnostic_log_path(output_path)),
    }


def bridge_request_template() -> dict[str, Any]:
    return {
        "format": BRIDGE_REQUEST_FORMAT,
        "command": "render_timeline",
        "reference_path": "C:/path/to/reference.wav",
        "material_directory": "C:/path/to/materials",
        "lyrics_file": "",
        "output_path": "C:/path/to/output/reference.rpp",
        "daw_timeline_export": True,
        "compute_device": "auto",
        "overwrite": True,
    }


def _load_request(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AudioProcessorError(f"Could not read VST3 bridge request: {path}") from exc

    if not isinstance(data, dict):
        raise AudioProcessorError(f"VST3 bridge request must be a JSON object: {path}")
    return data


def _required_path(request: dict[str, Any], key: str) -> Path:
    value = _optional_string(request.get(key))
    if not value:
        raise AudioProcessorError(f"VST3 bridge request missing required field: {key}")
    return Path(value).expanduser()


def _optional_path(request: dict[str, Any], key: str) -> Path | None:
    value = _optional_string(request.get(key))
    return Path(value).expanduser() if value else None


def _optional_bool(request: dict[str, Any], key: str, *, default: bool) -> bool:
    value = request.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_float(value: Any) -> float | None:
    text = _optional_string(value)
    if text is None:
        return None
    return float(text)


def _optional_int(value: Any) -> int | None:
    text = _optional_string(value)
    if text is None:
        return None
    return int(text)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from .batch import QueueItem, run_batch_queue
from .diagnostics import diagnostic_log_path
from .engine import AudioProcessorError
from .settings import ProcessingSettings, normalize_compute_device, normalize_source_separation


BRIDGE_REQUEST_FORMAT = "vocal_process_vst3_bridge_request_v1"
BRIDGE_RESPONSE_FORMAT = "vocal_process_vst3_bridge_response_v1"
BRIDGE_WATCH_HEARTBEAT = "vocal_process_vst3_bridge_heartbeat_v1"


def run_bridge_request_file(request_path: Path, response_path: Path | None = None) -> dict[str, Any]:
    output_path = response_path or request_path.parent / f"{_request_base_name(request_path)}.response.json"
    response = _run_bridge_request_path(request_path)
    _write_json_atomic(output_path, response)
    return response


def run_bridge_watch(
    request_directory: Path,
    *,
    response_directory: Path | None = None,
    poll_interval_seconds: float = 0.5,
    once: bool = False,
) -> int:
    requests = request_directory.expanduser()
    responses = (response_directory or request_directory).expanduser()
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    processed = 0

    while True:
        _write_heartbeat(requests, responses)
        processed += _process_pending_requests(requests, responses)
        if once:
            return processed
        time.sleep(max(poll_interval_seconds, 0.05))


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
    progress_path = _optional_path(request, "progress_path")

    settings = ProcessingSettings(
        material_directory=str(material_directory),
        lyrics_file=str(lyrics_file or ""),
        daw_timeline_export=_optional_bool(
            request,
            "daw_timeline_export",
            default=output_path.suffix.lower() == ".rpp" or command == "render_timeline",
        ),
        compute_device=normalize_compute_device(request.get("compute_device")),
        source_separation=normalize_source_separation(request.get("source_separation")),
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

    def item_progress(index: int, updated_item: QueueItem) -> None:
        _write_bridge_progress(
            progress_path,
            request,
            progress=updated_item.progress,
            status=updated_item.status,
            message=updated_item.message,
        )

    def queue_progress(progress: float, message: str) -> None:
        _write_bridge_progress(
            progress_path,
            request,
            progress=progress,
            status=item.status,
            message=message,
        )

    _write_bridge_progress(progress_path, request, progress=0.0, status="Queued", message="Bridge request queued")
    summary = run_batch_queue([item], settings, on_item_update=item_progress, on_queue_progress=queue_progress)
    ok = summary.failed == 0 and summary.cancelled == 0
    _write_bridge_progress(
        progress_path,
        request,
        progress=1.0 if ok else item.progress,
        status=item.status,
        message=item.message,
        done=True,
    )
    return {
        "format": BRIDGE_RESPONSE_FORMAT,
        "request_id": _optional_string(request.get("request_id")) or "",
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
        "progress_path": str(progress_path) if progress_path else "",
    }


def bridge_request_template() -> dict[str, Any]:
    return {
        "format": BRIDGE_REQUEST_FORMAT,
        "request_id": "example-render-001",
        "command": "render_timeline",
        "reference_path": "C:/path/to/reference.wav",
        "material_directory": "C:/path/to/materials",
        "lyrics_file": "",
        "output_path": "C:/path/to/output/reference.rpp",
        "progress_path": "C:/path/to/output/reference.progress.json",
        "daw_timeline_export": True,
        "compute_device": "auto",
        "source_separation": "auto",
        "overwrite": True,
    }


def bridge_watch_contract() -> dict[str, Any]:
    return {
        "format": "vocal_process_vst3_bridge_contract_v1",
        "request_glob": "*.request.json",
        "request_file_name": "<request_id>.request.json",
        "response_suffix": ".response.json",
        "done_suffix": ".done.json",
        "heartbeat_file": "bridge.heartbeat.json",
        "helper_command": "VocalProcess.exe vst3-bridge --watch <request_dir> --responses <response_dir>",
        "write_protocol": [
            "Write the request to a temporary file in the request directory.",
            "Atomically rename it to <request_id>.request.json when complete.",
            "Wait for <request_id>.response.json in the response directory.",
            "Read ok/status/output_path/diagnostics_path from the response.",
            "Optionally provide progress_path and poll that JSON file for progress/status while the helper runs.",
        ],
        "request_template": bridge_request_template(),
    }


def _process_pending_requests(requests: Path, responses: Path) -> int:
    processed = 0
    for request_path in sorted(requests.glob("*.request.json"), key=lambda path: path.name.lower()):
        request_name = _request_base_name(request_path)
        response_path = responses / f"{request_name}.response.json"
        processing_path = request_path.with_name(f"{request_name}.processing.json")
        done_path = request_path.with_name(f"{request_name}.done.json")
        try:
            request_path.replace(processing_path)
        except OSError:
            continue
        response = _run_bridge_request_path(processing_path)
        _write_json_atomic(response_path, response)
        try:
            processing_path.replace(done_path)
        except OSError:
            pass
        processed += 1
    return processed


def _run_bridge_request_path(path: Path) -> dict[str, Any]:
    try:
        request = _load_request(path)
        response = run_bridge_request(request)
        response["request_path"] = str(path)
        return response
    except Exception as exc:
        return {
            "format": BRIDGE_RESPONSE_FORMAT,
            "ok": False,
            "status": "Failed",
            "message": str(exc) or exc.__class__.__name__,
            "request_path": str(path),
            "exception_type": exc.__class__.__name__,
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }


def _write_heartbeat(requests: Path, responses: Path) -> None:
    heartbeat = {
        "format": BRIDGE_WATCH_HEARTBEAT,
        "process_id": os.getpid(),
        "request_directory": str(requests),
        "response_directory": str(responses),
        "timestamp": time.time(),
    }
    try:
        _write_json_atomic(responses / "bridge.heartbeat.json", heartbeat)
    except OSError:
        pass


def _write_bridge_progress(
    progress_path: Path | None,
    request: dict[str, Any],
    *,
    progress: float,
    status: str,
    message: str,
    done: bool = False,
) -> None:
    if progress_path is None:
        return
    payload = {
        "format": "vocal_process_vst3_bridge_progress_v1",
        "request_id": _optional_string(request.get("request_id")) or "",
        "process_id": os.getpid(),
        "timestamp": time.time(),
        "progress": max(min(float(progress), 1.0), 0.0),
        "status": status,
        "message": message,
        "done": done,
    }
    _write_json_atomic(progress_path, payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _request_base_name(path: Path) -> str:
    name = path.name
    suffix = ".request.json"
    if name.lower().endswith(suffix):
        return name[: -len(suffix)]
    return path.stem


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

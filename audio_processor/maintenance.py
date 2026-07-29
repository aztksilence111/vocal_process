from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import AudioProcessorError


MAINTENANCE_PLAN_FORMAT = "vocal_process_maintenance_plan_v1"
MAINTENANCE_SESSION_FORMAT = "vocal_process_maintenance_session_v1"
MAINTENANCE_STATE_FORMAT = "vocal_process_maintenance_state_v1"
MAINTENANCE_EVENT_FORMAT = "vocal_process_maintenance_event_v1"
MAINTENANCE_HEARTBEAT_FORMAT = "vocal_process_maintenance_heartbeat_v1"


@dataclass(frozen=True)
class MaintenanceTask:
    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    timeout_seconds: float | None = None
    continue_on_failure: bool = True
    max_retries: int = 0
    retry_delay_seconds: float = 0.0


@dataclass(frozen=True)
class MaintenancePlan:
    name: str
    repeat: bool = True
    cycle_pause_seconds: float = 300.0
    tasks: tuple[MaintenanceTask, ...] = ()


@dataclass(frozen=True)
class MaintenanceResult:
    status: str
    session_dir: Path
    state_path: Path
    heartbeat_path: Path
    events_path: Path
    started_at: float
    finished_at: float
    cycles_completed: int
    task_runs: int
    task_failures: int


def load_maintenance_plan(plan_path: Path) -> MaintenancePlan:
    plan_path = plan_path.expanduser()
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise AudioProcessorError(f"Maintenance plan does not exist: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise AudioProcessorError(f"Maintenance plan is not valid JSON: {plan_path}") from exc

    if not isinstance(raw, dict):
        raise AudioProcessorError("Maintenance plan must be a JSON object")
    if raw.get("format") != MAINTENANCE_PLAN_FORMAT:
        raise AudioProcessorError(f"Unsupported maintenance plan format: {raw.get('format')!r}")

    tasks_raw = raw.get("tasks", [])
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise AudioProcessorError("Maintenance plan must include at least one task")

    tasks = tuple(_load_task(entry) for entry in tasks_raw if isinstance(entry, dict))
    if not tasks:
        raise AudioProcessorError("Maintenance plan must include at least one valid task")

    return MaintenancePlan(
        name=str(raw.get("name") or plan_path.stem or "maintenance"),
        repeat=_optional_bool(raw.get("repeat"), default=True),
        cycle_pause_seconds=max(_optional_float(raw.get("cycle_pause_seconds"), default=300.0), 0.0),
        tasks=tasks,
    )


def maintenance_plan_template() -> dict[str, Any]:
    return {
        "format": MAINTENANCE_PLAN_FORMAT,
        "name": "development-maintenance",
        "repeat": True,
        "cycle_pause_seconds": 900.0,
        "tasks": [
            {
                "name": "compileall",
                "command": ".venv311\\Scripts\\python.exe",
                "args": ["-m", "compileall", "-q", "audio_processor", "tests"],
                "cwd": ".",
                "timeout_seconds": 1200.0,
                "continue_on_failure": True,
                "max_retries": 0,
                "retry_delay_seconds": 0.0,
            },
            {
                "name": "unit-tests",
                "command": ".venv311\\Scripts\\python.exe",
                "args": ["-m", "unittest", "discover"],
                "cwd": ".",
                "timeout_seconds": 5400.0,
                "continue_on_failure": True,
                "max_retries": 0,
                "retry_delay_seconds": 0.0,
            },
            {
                "name": "audio-check",
                "command": ".venv311\\Scripts\\python.exe",
                "args": ["-m", "audio_processor", "check"],
                "cwd": ".",
                "timeout_seconds": 1800.0,
                "continue_on_failure": True,
                "max_retries": 0,
                "retry_delay_seconds": 0.0,
            },
            {
                "name": "git-status",
                "command": "git",
                "args": ["status", "--short", "--branch"],
                "cwd": ".",
                "timeout_seconds": 120.0,
                "continue_on_failure": True,
                "max_retries": 0,
                "retry_delay_seconds": 0.0,
            },
        ],
    }


def write_maintenance_plan_template(destination: Path) -> Path:
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(maintenance_plan_template(), ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def run_maintenance_session(
    plan_path: Path,
    *,
    workspace_root: Path | None = None,
    session_dir: Path | None = None,
    duration_hours: float = 10.0,
    poll_interval_seconds: float = 5.0,
    once: bool = False,
    stop_file: Path | None = None,
) -> MaintenanceResult:
    workspace_root = (workspace_root or Path.cwd()).expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    plan = load_maintenance_plan(plan_path)
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    session_dir = (session_dir or _default_session_dir(workspace_root, plan.name)).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    task_log_dir = session_dir / "task-logs"
    task_log_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    state_path = session_dir / "state.json"
    heartbeat_path = session_dir / "heartbeat.json"
    stored_plan_path = session_dir / "session.plan.json"
    if not stored_plan_path.exists():
        _write_json_atomic(stored_plan_path, _serialize_plan(plan, plan_path=plan_path, plan_hash=plan_hash))

    stop_path = (stop_file or (session_dir / "stop")).expanduser().resolve()
    started_at = time.time()
    deadline = started_at + max(duration_hours, 0.0) * 3600.0
    cycles_completed = 0
    task_runs = 0
    task_failures = 0
    status = "running"
    last_event: dict[str, Any] | None = None
    current_cycle = 0
    current_task: str = ""

    def record_event(kind: str, **payload: Any) -> dict[str, Any]:
        nonlocal last_event
        event = {
            "format": MAINTENANCE_EVENT_FORMAT,
            "kind": kind,
            "timestamp": time.time(),
            "session_dir": str(session_dir),
            **payload,
        }
        _append_jsonl(events_path, event)
        last_event = event
        return event

    def update_state(*, next_wake_at: float | None = None) -> None:
        state = {
            "format": MAINTENANCE_STATE_FORMAT,
            "session_dir": str(session_dir),
            "workspace_root": str(workspace_root),
            "plan_path": str(plan_path),
            "plan_hash": plan_hash,
            "plan_name": plan.name,
            "status": status,
            "started_at": started_at,
            "finished_at": time.time() if status not in {"running", "sleeping"} else None,
            "deadline": deadline,
            "current_cycle": current_cycle,
            "current_task": current_task,
            "cycles_completed": cycles_completed,
            "task_runs": task_runs,
            "task_failures": task_failures,
            "next_wake_at": next_wake_at,
            "stop_file": str(stop_path),
            "last_event": last_event or {},
        }
        _write_json_atomic(state_path, state)
        _write_json_atomic(
            heartbeat_path,
            {
                "format": MAINTENANCE_HEARTBEAT_FORMAT,
                "session_dir": str(session_dir),
                "workspace_root": str(workspace_root),
                "plan_name": plan.name,
                "status": status,
                "timestamp": time.time(),
                "current_cycle": current_cycle,
                "current_task": current_task,
                "cycles_completed": cycles_completed,
                "task_runs": task_runs,
                "task_failures": task_failures,
                "deadline": deadline,
            },
        )

    record_event("session.started", plan=_plan_payload(plan), plan_path=str(plan_path), plan_hash=plan_hash)
    update_state()

    try:
        while time.time() < deadline:
            if stop_path.exists():
                status = "stopped"
                record_event("session.stop_requested", stop_file=str(stop_path))
                break

            current_cycle += 1
            record_event("cycle.started", cycle_index=current_cycle)
            update_state()

            for task_index, task in enumerate(plan.tasks, start=1):
                if stop_path.exists():
                    status = "stopped"
                    record_event("session.stop_requested", stop_file=str(stop_path))
                    break
                if time.time() >= deadline:
                    status = "completed"
                    break

                current_task = task.name
                record_event("task.started", cycle_index=current_cycle, task_index=task_index, task=_task_payload(task))
                update_state()

                task_result = _run_task(
                    task,
                    workspace_root=workspace_root,
                    session_dir=session_dir,
                    task_log_dir=task_log_dir,
                    cycle_index=current_cycle,
                    task_index=task_index,
                    stop_path=stop_path,
                    poll_interval_seconds=poll_interval_seconds,
                )
                task_runs += 1
                task_failures += 0 if task_result["ok"] else 1
                record_event("task.completed", cycle_index=current_cycle, task_index=task_index, result=task_result)
                update_state()

                if task_result.get("status") == "stopped":
                    status = "stopped"
                    record_event("session.stop_requested", stop_file=str(stop_path))
                    update_state()
                    break
                if not task_result["ok"] and not task.continue_on_failure:
                    status = "failed"
                    break

            if status in {"failed", "stopped"}:
                break

            cycles_completed += 1
            record_event("cycle.completed", cycle_index=current_cycle)
            update_state()

            if once or not plan.repeat:
                status = "completed"
                break

            pause_seconds = max(plan.cycle_pause_seconds, 0.0)
            if pause_seconds <= 0:
                continue
            next_wake_at = time.time() + pause_seconds
            status = "sleeping"
            record_event("cycle.sleeping", cycle_index=current_cycle, next_wake_at=next_wake_at)
            update_state(next_wake_at=next_wake_at)
            while time.time() < next_wake_at:
                if stop_path.exists() or time.time() >= deadline:
                    break
                time.sleep(min(poll_interval_seconds, max(next_wake_at - time.time(), 0.05)))
                update_state(next_wake_at=next_wake_at)
            if stop_path.exists():
                status = "stopped"
                record_event("session.stop_requested", stop_file=str(stop_path))
                break
            if time.time() >= deadline:
                status = "completed"
                break
            status = "running"
            update_state()

        if status == "running":
            status = "completed"
    finally:
        finished_at = time.time()
        record_event("session.finished", status=status, finished_at=finished_at)
        update_state()

    return MaintenanceResult(
        status=status,
        session_dir=session_dir,
        state_path=state_path,
        heartbeat_path=heartbeat_path,
        events_path=events_path,
        started_at=started_at,
        finished_at=finished_at,
        cycles_completed=cycles_completed,
        task_runs=task_runs,
        task_failures=task_failures,
    )


def maintenance_contract() -> dict[str, Any]:
    return {
        "format": MAINTENANCE_PLAN_FORMAT,
        "session_format": MAINTENANCE_SESSION_FORMAT,
        "state_format": MAINTENANCE_STATE_FORMAT,
        "event_format": MAINTENANCE_EVENT_FORMAT,
        "heartbeat_format": MAINTENANCE_HEARTBEAT_FORMAT,
        "session_files": [
            "session.plan.json",
            "state.json",
            "heartbeat.json",
            "events.jsonl",
            "task-logs/*",
            "stop",
        ],
        "plan_template": maintenance_plan_template(),
        "default_duration_hours": 10.0,
        "default_poll_interval_seconds": 5.0,
    }


def _load_task(entry: dict[str, Any]) -> MaintenanceTask:
    name = str(entry.get("name") or "").strip()
    command = str(entry.get("command") or "").strip()
    if not name or not command:
        raise AudioProcessorError("Maintenance task requires both name and command")
    args = tuple(str(value) for value in entry.get("args", []) if str(value).strip())
    cwd_value = entry.get("cwd")
    cwd = Path(str(cwd_value)) if str(cwd_value or "").strip() else None
    return MaintenanceTask(
        name=name,
        command=command,
        args=args,
        cwd=cwd,
        timeout_seconds=_optional_float_or_none(entry.get("timeout_seconds")),
        continue_on_failure=_optional_bool(entry.get("continue_on_failure"), default=True),
        max_retries=max(_optional_int(entry.get("max_retries"), default=0), 0),
        retry_delay_seconds=max(_optional_float(entry.get("retry_delay_seconds"), default=0.0), 0.0),
    )


def _run_task(
    task: MaintenanceTask,
    *,
    workspace_root: Path,
    session_dir: Path,
    task_log_dir: Path,
    cycle_index: int,
    task_index: int,
    stop_path: Path,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    cwd = _resolve_task_cwd(task.cwd, workspace_root)
    command = [_resolve_command(task.command, workspace_root), *task.args]
    attempt = 0
    while True:
        attempt += 1
        log_base = f"cycle-{cycle_index:04d}-task-{task_index:02d}-{_slugify(task.name)}-attempt-{attempt:02d}"
        stdout_path = task_log_dir / f"{log_base}.stdout.txt"
        stderr_path = task_log_dir / f"{log_base}.stderr.txt"
        started_at = time.time()
        try:
            result = _run_process_task(
                command=command,
                cwd=cwd,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=task.timeout_seconds,
                stop_path=stop_path,
                poll_interval_seconds=poll_interval_seconds,
                started_at=started_at,
            )
            return {
                "task_name": task.name,
                "attempt": attempt,
                **result,
                "command": command,
                "cwd": str(cwd),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "timeout_seconds": task.timeout_seconds,
                "max_retries": task.max_retries,
            }
        except Exception as exc:
            _write_text(stdout_path, "")
            _write_text(stderr_path, str(exc))
            result = {
                "task_name": task.name,
                "attempt": attempt,
                "ok": False,
                "status": "failed",
                "returncode": None,
                "command": command,
                "cwd": str(cwd),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "started_at": started_at,
                "finished_at": time.time(),
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
                "timeout_seconds": task.timeout_seconds,
                "max_retries": task.max_retries,
            }

        if result.get("status") == "stopped":
            return result
        if attempt > task.max_retries or not task.continue_on_failure:
            return result
        _sleep_with_stop(task.retry_delay_seconds, poll_interval_seconds, stop_path)


def _run_process_task(
    *,
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float | None,
    stop_path: Path,
    poll_interval_seconds: float,
    started_at: float,
) -> dict[str, Any]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("VOCAL_PROCESS_STOP_FILE", str(stop_path))
    deadline = started_at + timeout_seconds if timeout_seconds is not None and timeout_seconds > 0 else None

    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_handle:
        with stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=env,
            )
            termination = ""
            while True:
                returncode = process.poll()
                if returncode is not None:
                    return {
                        "ok": returncode == 0,
                        "status": "ok" if returncode == 0 else "failed",
                        "returncode": returncode,
                        "started_at": started_at,
                        "finished_at": time.time(),
                        "termination": termination,
                    }

                if stop_path.exists():
                    termination = "stop_file"
                    returncode = _terminate_process(process, kill_after_seconds=5.0)
                    return {
                        "ok": False,
                        "status": "stopped",
                        "returncode": returncode,
                        "started_at": started_at,
                        "finished_at": time.time(),
                        "termination": termination,
                        "stop_file": str(stop_path),
                    }

                if deadline is not None and time.time() >= deadline:
                    termination = "timeout"
                    returncode = _kill_process(process)
                    return {
                        "ok": False,
                        "status": "timeout",
                        "returncode": returncode,
                        "started_at": started_at,
                        "finished_at": time.time(),
                        "termination": termination,
                    }

                sleep_seconds = max(min(poll_interval_seconds, 0.5), 0.05)
                if deadline is not None:
                    sleep_seconds = min(sleep_seconds, max(deadline - time.time(), 0.05))
                time.sleep(sleep_seconds)


def _terminate_process(process: subprocess.Popen, *, kill_after_seconds: float) -> int | None:
    try:
        process.terminate()
    except OSError:
        return process.poll()
    try:
        return process.wait(timeout=kill_after_seconds)
    except subprocess.TimeoutExpired:
        return _kill_process(process)


def _kill_process(process: subprocess.Popen) -> int | None:
    try:
        process.kill()
    except OSError:
        return process.poll()
    try:
        return process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        return process.poll()


def _resolve_task_cwd(cwd: Path | None, workspace_root: Path) -> Path:
    if cwd is None:
        return workspace_root
    resolved = cwd.expanduser()
    return resolved if resolved.is_absolute() else (workspace_root / resolved).resolve()


def _resolve_command(command: str, workspace_root: Path) -> str:
    value = str(command).strip()
    if not value:
        raise AudioProcessorError("Maintenance task command cannot be empty")
    if any(sep in value for sep in ("\\", "/")) or value.startswith("."):
        path = Path(value)
        return str(path if path.is_absolute() else (workspace_root / path).resolve())
    return value


def _default_session_dir(workspace_root: Path, plan_name: str) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return workspace_root / ".tmp" / "maintenance_sessions" / f"{timestamp}-{_slugify(plan_name)}"


def _serialize_plan(plan: MaintenancePlan, *, plan_path: Path, plan_hash: str) -> dict[str, Any]:
    return {
        "format": MAINTENANCE_SESSION_FORMAT,
        "plan_path": str(plan_path),
        "plan_hash": plan_hash,
        "plan": _plan_payload(plan),
    }


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _sleep_with_stop(duration_seconds: float, poll_interval_seconds: float, stop_path: Path) -> None:
    deadline = time.time() + max(duration_seconds, 0.0)
    while time.time() < deadline:
        if stop_path.exists():
            return
        time.sleep(min(max(poll_interval_seconds, 0.05), max(deadline - time.time(), 0.05)))


def _slugify(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in {" ", "-", "_"}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    return slug or "session"


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _optional_float(value: Any, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise AudioProcessorError("Missing required numeric value")
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        if default is None:
            raise AudioProcessorError(f"Invalid numeric value: {value!r}") from exc
        return default


def _optional_float_or_none(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AudioProcessorError(f"Invalid numeric value: {value!r}") from exc


def _plan_payload(plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "name": plan.name,
        "repeat": plan.repeat,
        "cycle_pause_seconds": plan.cycle_pause_seconds,
        "tasks": [_task_payload(task) for task in plan.tasks],
    }


def _task_payload(task: MaintenanceTask) -> dict[str, Any]:
    return {
        "name": task.name,
        "command": task.command,
        "args": list(task.args),
        "cwd": str(task.cwd) if task.cwd else "",
        "timeout_seconds": task.timeout_seconds,
        "continue_on_failure": task.continue_on_failure,
        "max_retries": task.max_retries,
        "retry_delay_seconds": task.retry_delay_seconds,
    }


def _optional_int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AudioProcessorError(f"Invalid integer value: {value!r}") from exc

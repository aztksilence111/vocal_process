from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


DEFAULT_UVR_ARCH = "demucs"
DEFAULT_UVR_DEMUCS_MODEL = "htdemucs"
DEFAULT_UVR_WAV_TYPE = "PCM_24"
RUNNER_MODULES = {
    "demucs": "demucs_headless_runner",
    "mdx": "mdx_headless_runner",
    "vr": "vr_headless_runner",
}


@dataclass(frozen=True)
class UvrRunner:
    python_exe: Path
    architecture: str
    module_name: str
    script_path: Path


def uvr_cache_fingerprint() -> dict[str, str]:
    return {
        "source_separator_backend": os.environ.get("VOCAL_PROCESS_SOURCE_SEPARATOR", "auto").strip().lower(),
        "uvr_architecture": _uvr_architecture(),
        "uvr_model": _uvr_model(),
        "uvr_wav_type": os.environ.get("VOCAL_PROCESS_UVR_WAV_TYPE", DEFAULT_UVR_WAV_TYPE).strip(),
    }


@lru_cache(maxsize=1)
def uvr_worker_available() -> bool:
    python_exe = resolve_uvr_python()
    if python_exe is None:
        return False
    return locate_uvr_runner(python_exe, architecture=_uvr_architecture()) is not None


def resolve_uvr_python() -> Path | None:
    candidates: list[Path] = []
    env_python = os.environ.get("VOCAL_PROCESS_UVR_PYTHON", "").strip()
    if env_python:
        candidates.append(Path(env_python))

    project_root = Path(__file__).resolve().parents[1]
    for root in _runtime_roots(project_root):
        candidates.extend(
            [
                root / ".uvr-worker" / "Scripts" / "python.exe",
                root / ".uvr-worker" / "bin" / "python",
                root / "uvr-worker" / "Scripts" / "python.exe",
                root / "uvr-worker" / "bin" / "python",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def locate_uvr_runner(
    python_exe: Path | None = None,
    *,
    architecture: str | None = None,
) -> UvrRunner | None:
    python_path = python_exe or resolve_uvr_python()
    if python_path is None:
        return None

    arch = _normalize_architecture(architecture or _uvr_architecture())
    module_name = RUNNER_MODULES[arch]
    probe_code = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.find_spec({module_name!r})\n"
        "print(json.dumps({'origin': spec.origin if spec and spec.origin else None}))\n"
        "sys.exit(0 if spec and spec.origin else 1)\n"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", probe_code],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            creationflags=_subprocess_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None

    origin = payload.get("origin")
    if not origin:
        return None

    script_path = Path(str(origin))
    if not script_path.is_file():
        return None

    if not _runner_imports_cleanly(python_path, module_name):
        return None

    return UvrRunner(
        python_exe=python_path,
        architecture=arch,
        module_name=module_name,
        script_path=script_path,
    )


def separate_vocals_with_uvr(
    input_path: Path,
    output_dir: Path,
    *,
    compute_device: str = "cpu",
    notes: list[str] | None = None,
) -> Path | None:
    note_sink = notes if notes is not None else []
    runner = locate_uvr_runner()
    if runner is None:
        note_sink.append("uvr headless worker unavailable; install it with scripts\\bootstrap_uvr_worker.ps1")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    existing = find_uvr_vocal_output(output_dir)
    if existing is not None:
        note_sink.append(f"reference vocals reused from uvr cache: {existing}")
        return existing

    model = _uvr_model()
    wav_type = os.environ.get("VOCAL_PROCESS_UVR_WAV_TYPE", DEFAULT_UVR_WAV_TYPE).strip() or DEFAULT_UVR_WAV_TYPE
    command = _build_uvr_command(
        runner,
        input_path=input_path,
        output_dir=output_dir,
        model=model,
        wav_type=wav_type,
        compute_device=compute_device,
    )
    log_path = output_dir / "uvr-headless.log"
    timeout_seconds = _uvr_timeout_seconds()

    try:
        result = subprocess.run(
            [str(arg) for arg in command],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            creationflags=_subprocess_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        _write_uvr_log(log_path, _timeout_output(exc))
        note_sink.append(f"uvr headless runner timed out after {timeout_seconds}s; see {log_path}")
        return None
    except OSError as exc:
        note_sink.append(f"uvr headless runner failed to start: {exc}")
        return None

    _write_uvr_log(log_path, result.stdout or "")
    if result.returncode != 0:
        note_sink.append(f"uvr headless runner exited with code {result.returncode}; see {log_path}")
        return None

    vocal_path = find_uvr_vocal_output(output_dir)
    if vocal_path is None:
        note_sink.append(f"uvr headless runner completed but no vocal stem was found in {output_dir}")
        return None

    note_sink.append(f"reference vocals separated with uvr headless runner: {vocal_path}")
    return vocal_path


def find_uvr_vocal_output(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None

    candidates: list[Path] = []
    for path in output_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".wav", ".flac", ".mp3"}:
            name = path.name.lower()
            if "instrumental" in name or "no_vocals" in name:
                continue
            if "vocal" in name or name == "vocals.wav":
                candidates.append(path)

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.suffix.lower() == ".wav" else 1,
            len(candidate.parts),
            candidate.name.lower(),
        ),
    )[0]


def make_uvr_output_dir(work_root: Path, input_path: Path) -> Path:
    return work_root / "uvr" / _safe_cache_name(input_path)


def _build_uvr_command(
    runner: UvrRunner,
    *,
    input_path: Path,
    output_dir: Path,
    model: str,
    wav_type: str,
    compute_device: str,
) -> list[Path | str]:
    device_flag = _uvr_device_flag(compute_device)
    if runner.architecture == "demucs":
        return [
            runner.python_exe,
            runner.script_path,
            "--model",
            model,
            "--input",
            input_path,
            "--output",
            output_dir,
            device_flag,
            "--stem",
            "Vocals",
            "--primary-only",
            "--wav-type",
            wav_type,
            "--quiet",
        ]

    if runner.architecture == "mdx":
        return [
            runner.python_exe,
            runner.script_path,
            "--model",
            model,
            "--input",
            input_path,
            "--output",
            output_dir,
            device_flag,
            "--vocals-only",
            "--wav-type",
            wav_type,
            "--quiet",
        ]

    command: list[Path | str] = [
        runner.python_exe,
        runner.script_path,
        "--model",
        model,
        "--input",
        input_path,
        "--output",
        output_dir,
        device_flag,
        "--primary-only",
        "--primary-stem",
        "Vocals",
        "--wav-type",
        wav_type,
        "--quiet",
    ]
    vr_param = os.environ.get("VOCAL_PROCESS_UVR_VR_PARAM", "").strip()
    if vr_param:
        command.extend(["--param", vr_param])
    return command


def _runner_imports_cleanly(python_exe: Path, module_name: str) -> bool:
    probe_code = f"import {module_name}\n"
    try:
        result = subprocess.run(
            [str(python_exe), "-c", probe_code],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            creationflags=_subprocess_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "demucs4": "demucs",
        "uvr-demucs": "demucs",
        "mdx-net": "mdx",
        "mdxnet": "mdx",
        "vr-arch": "vr",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in RUNNER_MODULES:
        return DEFAULT_UVR_ARCH
    return normalized


def _uvr_architecture() -> str:
    return _normalize_architecture(os.environ.get("VOCAL_PROCESS_UVR_ARCH", DEFAULT_UVR_ARCH))


def _uvr_model() -> str:
    return os.environ.get("VOCAL_PROCESS_UVR_MODEL", DEFAULT_UVR_DEMUCS_MODEL).strip() or DEFAULT_UVR_DEMUCS_MODEL


def _uvr_device_flag(compute_device: str) -> str:
    requested = os.environ.get("VOCAL_PROCESS_UVR_DEVICE", compute_device).strip().lower()
    if requested in {"cuda", "gpu", "nvidia"} or requested.startswith("cuda"):
        return "--gpu"
    if requested in {"directml", "dml", "amd"}:
        return "--directml"
    return "--cpu"


def _uvr_timeout_seconds() -> int:
    raw = os.environ.get("VOCAL_PROCESS_UVR_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 7200
    try:
        return max(int(raw), 60)
    except ValueError:
        return 7200


def _safe_cache_name(path: Path) -> str:
    absolute = str(path.expanduser().resolve())
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("._") or "reference"
    digest = hashlib.sha256(absolute.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{stem}-{digest}"


def _write_uvr_log(log_path: Path, text: str) -> None:
    try:
        log_path.write_text(text, encoding="utf-8", errors="replace")
    except OSError:
        pass


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    parts: Iterable[str | bytes | None] = (exc.stdout, exc.stderr)
    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, bytes):
            text_parts.append(part.decode("utf-8", errors="replace"))
        elif part:
            text_parts.append(str(part))
    return "\n".join(text_parts)


def _subprocess_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _runtime_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(str(bundle_root)).resolve())
        roots.append(Path(str(bundle_root)).resolve().parent)

    roots.append(project_root)
    roots.append(Path.cwd())

    unique_roots: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique_roots:
            unique_roots.append(resolved)
    return unique_roots

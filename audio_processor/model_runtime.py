from __future__ import annotations

import importlib.util
import importlib
import contextlib
from collections import namedtuple
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from xml.etree import ElementTree

from .engine import AudioProcessorError, ensure_runtime_tool_paths, get_audio_duration_seconds, list_audio_files, probe_audio
from .model_assist import (
    MaterialAnalysis,
    MaterialOrderDecision,
    VoiceSegment,
    VoiceUnitTiming,
    _phonetic_units,
    list_model_candidates,
    plan_material_ordering,
    render_ordering_score_matrix,
)
from .settings import get_config_dir
from .uvr_worker import (
    find_uvr_vocal_output,
    make_uvr_output_dir,
    separate_vocals_with_uvr,
    uvr_cache_fingerprint,
    uvr_worker_available,
)


ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    speaker_id: str | None = None
    timing_source: str = ""
    unit_timings: tuple[VoiceUnitTiming, ...] = ()


@dataclass(frozen=True)
class AudioAnalysis:
    path: Path
    duration_seconds: float
    transcript: str
    segments: tuple[TranscriptSegment, ...]
    vad_segments: tuple[tuple[float, float], ...]
    speaker_embedding: tuple[float, ...] | None
    analysis_source: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceAnalysis:
    source_path: Path
    vocal_path: Path
    transcript: str
    segments: tuple[VoiceSegment, ...]
    speaker_embedding: tuple[float, ...] | None
    backend: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialLibraryAnalysis:
    material_directory: Path
    materials: tuple[AudioAnalysis, ...]
    backend_summary: dict[str, bool]
    notes: tuple[str, ...] = ()

    @property
    def material_paths(self) -> list[Path]:
        return [analysis.path for analysis in self.materials]


@dataclass(frozen=True)
class OrderingDecision:
    rank: int
    source_path: Path
    score: float
    transcript_score: float
    filename_score: float
    duration_score: float
    speaker_score: float
    vad_score: float
    phonetic_score: float
    evidence_count: int
    confidence_label: str
    reference_text: str
    material_text: str
    reason: str
    reference_segment_index: int | None = None
    text_position: int | None = None
    phonetic_position: int | None = None
    phonetic_position_count: int = 0
    phonetic_span_units: int = 0
    phonetic_tone_score: float = 0.0
    phonetic_tone_position: int | None = None
    phonetic_tone_position_count: int = 0
    target_duration_seconds: float | None = None


@dataclass(frozen=True)
class ModelOrderingResult:
    reference: ReferenceAnalysis
    library: MaterialLibraryAnalysis
    ordered_paths: tuple[Path, ...]
    target_durations: tuple[float | None, ...]
    decisions: tuple[OrderingDecision, ...]
    analysis_report: dict[str, Any]


DEFAULT_ASR_MODEL = os.environ.get("VOCAL_PROCESS_ASR_MODEL", "base")
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
MATERIAL_CACHE_FILE = ".vocalprocess_material_cache.json"
REFERENCE_CACHE_FORMAT = "vocal_process_reference_cache_v1"
PYANNOTE_DIA_MODEL = "pyannote/speaker-diarization-community-1"
SPEAKER_EMBEDDING_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
TORCH_NATIVE_RUNTIME_HINT = (
    "PyTorch native runtime is incomplete or not loadable. Use the full portable package, "
    "extract the whole ZIP directory, and verify _internal\\torch\\_C.cp311-win_amd64.pyd "
    "and _internal\\torch\\lib exist beside VocalProcess.exe."
)
IMPORT_PROBE_MODULES = {"torch", "torchaudio"}
_DLL_DIRECTORY_HANDLES: list[Any] = []


def build_model_ordering(
    reference_path: Path,
    material_directory: Path,
    *,
    lyrics_file: Path | None = None,
    work_dir: Path | None = None,
    material_cache_dir: Path | None = None,
    compute_device: str = "auto",
    source_separation: str = "auto",
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> ModelOrderingResult:
    _raise_if_cancelled(should_cancel)
    work_root = _prepare_work_root(work_dir)
    device = _resolve_compute_device(compute_device)
    material_directory = material_directory.expanduser()
    material_paths = list_audio_files(material_directory)
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    _raise_if_cancelled(should_cancel)
    resolved_material_cache_dir = material_cache_dir or _default_material_cache_dir(
        work_root,
        material_directory,
        device,
    )
    notes: list[str] = [f"compute device resolved: {device}"]
    backend_summary = backend_availability()
    _raise_if_cancelled(should_cancel)
    _notify_progress(on_progress, 0.02, "Preparing reference analysis")
    reference = analyze_reference(
        reference_path,
        lyrics_file=lyrics_file,
        work_dir=work_root,
        compute_device=device,
        source_separation=source_separation,
        on_progress=on_progress,
        notes=notes,
        should_cancel=should_cancel,
    )

    _raise_if_cancelled(should_cancel)
    _notify_progress(on_progress, 0.25, "Preparing material analysis")
    library = analyze_material_library(
        material_directory,
        work_dir=work_root,
        material_cache_dir=resolved_material_cache_dir,
        compute_device=device,
        on_progress=on_progress,
        notes=notes,
        should_cancel=should_cancel,
    )

    _raise_if_cancelled(should_cancel)
    reference_segments = reference.segments
    if not reference_segments and reference.transcript.strip():
        reference_segments = (
            VoiceSegment(
                start_seconds=0.0,
                end_seconds=max(reference_duration_for(reference.source_path), 0.0),
                text=reference.transcript,
            ),
        )

    material_analyses = [
        MaterialAnalysis(
            path=analysis.path,
            transcript=analysis.transcript,
            voice_segments=tuple(
                VoiceSegment(
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                    confidence=segment.confidence,
                    speaker_id=segment.speaker_id,
                    timing_source=segment.timing_source,
                )
                for segment in analysis.segments
            ),
            filename_text=_filename_text_hint(analysis.path),
            speaker_embedding=analysis.speaker_embedding,
            vad_coverage=_vad_coverage(analysis.vad_segments, analysis.duration_seconds),
            duration_seconds=analysis.duration_seconds,
            analysis_source=analysis.analysis_source,
        )
        for analysis in library.materials
    ]

    ordering_plan = plan_material_ordering(
        reference_segments,
        material_analyses,
        reference_embedding=reference.speaker_embedding,
    )
    _raise_if_cancelled(should_cancel)
    decisions = list(ordering_plan.decisions)
    ordered_paths = tuple(decision.material_path for decision in decisions)
    target_durations = _target_durations_for_decisions(
        reference_segments,
        decisions,
        reference_duration=reference_duration_for(reference.source_path),
    )

    report = {
        "format": "vocal_process_model_ordering_v1",
        "compute_device": device,
        "source_separation": source_separation,
        "reference": render_reference_analysis(reference),
        "materials": [render_material_analysis(analysis) for analysis in library.materials],
        "material_cache": {
            "path": str(_material_cache_path(material_directory, resolved_material_cache_dir)),
            "notes": [note for note in library.notes if "material analysis cache" in note],
        },
        "ordering_strategy": ordering_plan.strategy,
        "score_matrix": render_ordering_score_matrix(ordering_plan),
        "ordering": [
            {
                "rank": decision.rank,
                "material_path": str(decision.material_path),
                "score": decision.score,
                "transcript_score": decision.transcript_score,
                "filename_score": decision.filename_score,
                "phonetic_score": decision.phonetic_score,
                "duration_score": decision.duration_score,
                "speaker_score": decision.speaker_score,
                "vad_score": decision.vad_score,
                "evidence_count": decision.evidence_count,
                "confidence_label": decision.confidence_label,
                "reference_segment_index": decision.reference_segment_index,
                "text_position": decision.text_position,
                "phonetic_position": decision.phonetic_position,
                "phonetic_position_count": decision.phonetic_position_count,
                "phonetic_span_units": decision.phonetic_span_units,
                "phonetic_tone_score": decision.phonetic_tone_score,
                "phonetic_tone_position": decision.phonetic_tone_position,
                "phonetic_tone_position_count": decision.phonetic_tone_position_count,
                "reference_text": decision.reference_text,
                "material_text": decision.material_text,
                "reason": decision.reason,
                "target_duration_seconds": target_durations[index],
            }
            for index, decision in enumerate(decisions)
        ],
        "timeline_alignment": _render_timeline_alignment_summary(
            reference_segments,
            decisions,
            target_durations,
        ),
        "backend_summary": backend_summary,
        "notes": notes,
    }
    _notify_progress(on_progress, 1.0, "Model-assisted ordering complete")
    return ModelOrderingResult(
        reference=reference,
        library=library,
        ordered_paths=ordered_paths,
        target_durations=target_durations,
        decisions=tuple(
            OrderingDecision(
                rank=decision.rank,
                source_path=decision.material_path,
                score=decision.score,
                transcript_score=decision.transcript_score,
                filename_score=decision.filename_score,
                duration_score=decision.duration_score,
                speaker_score=decision.speaker_score,
                vad_score=decision.vad_score,
                phonetic_score=decision.phonetic_score,
                evidence_count=decision.evidence_count,
                confidence_label=decision.confidence_label,
                reference_segment_index=decision.reference_segment_index,
                text_position=decision.text_position,
                phonetic_position=decision.phonetic_position,
                phonetic_position_count=decision.phonetic_position_count,
                phonetic_span_units=decision.phonetic_span_units,
                phonetic_tone_score=decision.phonetic_tone_score,
                phonetic_tone_position=decision.phonetic_tone_position,
                phonetic_tone_position_count=decision.phonetic_tone_position_count,
                reference_text=decision.reference_text,
                material_text=decision.material_text,
                reason=decision.reason,
                target_duration_seconds=target_durations[index],
            )
            for index, decision in enumerate(decisions)
        ),
        analysis_report=report,
    )


def analyze_reference(
    reference_path: Path,
    *,
    lyrics_file: Path | None = None,
    work_dir: Path | None = None,
    compute_device: str = DEFAULT_DEVICE,
    source_separation: str = "auto",
    on_progress: ProgressCallback | None = None,
    notes: list[str] | None = None,
    should_cancel: CancelCallback | None = None,
) -> ReferenceAnalysis:
    _raise_if_cancelled(should_cancel)
    notes = notes if notes is not None else []
    normalized_reference = reference_path.expanduser()
    if not normalized_reference.exists():
        raise AudioProcessorError(f"Reference audio does not exist: {normalized_reference}")

    _raise_if_cancelled(should_cancel)
    work_root = _prepare_work_root(work_dir)
    cache_path = _reference_cache_path(work_root, normalized_reference, lyrics_file, compute_device, source_separation)
    cached = _load_reference_analysis_cache(cache_path, normalized_reference=normalized_reference)
    if cached is not None:
        _raise_if_cancelled(should_cancel)
        cache_note = f"reference analysis cache reused: {cache_path}"
        notes.append(cache_note)
        _notify_progress(on_progress, 0.2, "Reference analysis cache reused")
        return ReferenceAnalysis(
            source_path=cached.source_path,
            vocal_path=cached.vocal_path,
            transcript=cached.transcript,
            segments=cached.segments,
            speaker_embedding=cached.speaker_embedding,
            backend=cached.backend,
            notes=tuple([*cached.notes, cache_note]),
        )

    vocal_path = _maybe_separate_vocals(
        normalized_reference,
        work_dir=work_root,
        compute_device=compute_device,
        source_separation=source_separation,
        notes=notes,
        should_cancel=should_cancel,
    )
    _raise_if_cancelled(should_cancel)
    transcript_result = _transcribe_audio(
        vocal_path,
        work_dir=work_root,
        compute_device=compute_device,
        should_cancel=should_cancel,
    )
    _raise_if_cancelled(should_cancel)
    reference_embedding = _speaker_embedding(
        vocal_path,
        work_dir=work_root,
        compute_device=compute_device,
        should_cancel=should_cancel,
    )
    _raise_if_cancelled(should_cancel)
    notes.extend(_lyric_timing_notes(lyrics_file, transcript_result["segments"]))
    segments = _segments_from_transcript(transcript_result["segments"], lyrics_file=lyrics_file)
    if not segments:
        segments = (
            VoiceSegment(
                start_seconds=0.0,
                end_seconds=max(get_audio_duration_seconds(probe_audio(vocal_path)), 0.0),
                text=transcript_result["text"],
                confidence=0.0,
                timing_source="asr_full_text_fallback",
            ),
        )

    reference = ReferenceAnalysis(
        source_path=normalized_reference,
        vocal_path=vocal_path,
        transcript=transcript_result["text"],
        segments=segments,
        speaker_embedding=reference_embedding,
        backend=transcript_result["backend"],
        notes=tuple(notes),
    )
    _write_reference_analysis_cache(cache_path, reference)
    _raise_if_cancelled(should_cancel)
    _notify_progress(on_progress, 0.2, "Reference analysis complete")
    return reference


def analyze_material_library(
    material_directory: Path,
    *,
    work_dir: Path | None = None,
    material_cache_dir: Path | None = None,
    compute_device: str = DEFAULT_DEVICE,
    on_progress: ProgressCallback | None = None,
    notes: list[str] | None = None,
    should_cancel: CancelCallback | None = None,
) -> MaterialLibraryAnalysis:
    _raise_if_cancelled(should_cancel)
    notes = notes if notes is not None else []
    material_directory = material_directory.expanduser()
    material_paths = list_audio_files(material_directory)
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    _raise_if_cancelled(should_cancel)
    cache_path = _material_cache_path(material_directory, material_cache_dir)
    if material_cache_dir is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _material_snapshot(material_paths)
    cached_library = _load_material_library_cache(
        cache_path,
        material_directory=material_directory,
        snapshot=snapshot,
    )
    if cached_library is not None:
        _raise_if_cancelled(should_cancel)
        notes.append(f"material analysis cache reused: {cache_path}")
        _notify_progress(on_progress, 0.9, "Material analysis cache reused")
        return cached_library

    analyses: list[AudioAnalysis] = []
    total = len(material_paths)
    for index, path in enumerate(material_paths):
        _raise_if_cancelled(should_cancel)
        progress = 0.25 + ((index + 1) / max(total, 1)) * 0.65
        _notify_progress(on_progress, progress, f"Analyzing material {index + 1}/{total}: {path.name}")
        _raise_if_cancelled(should_cancel)
        transcript_result = _transcribe_audio(
            path,
            work_dir=work_dir,
            compute_device=compute_device,
            should_cancel=should_cancel,
        )
        _raise_if_cancelled(should_cancel)
        vad_segments = _detect_vad_segments(path, compute_device=compute_device, should_cancel=should_cancel)
        _raise_if_cancelled(should_cancel)
        speaker_embedding = _speaker_embedding(
            path,
            work_dir=work_dir,
            compute_device=compute_device,
            should_cancel=should_cancel,
        )
        _raise_if_cancelled(should_cancel)
        duration = get_audio_duration_seconds(probe_audio(path))
        analyses.append(
            AudioAnalysis(
                path=path,
                duration_seconds=duration,
                transcript=transcript_result["text"],
                segments=tuple(
                    TranscriptSegment(
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        text=segment.text,
                        confidence=segment.confidence,
                        speaker_id=segment.speaker_id,
                    )
                    for segment in transcript_result["segments"]
                ),
                vad_segments=vad_segments,
                speaker_embedding=speaker_embedding,
                analysis_source=transcript_result["backend"],
                notes=tuple(transcript_result.get("notes", ())),
            )
        )

    library = MaterialLibraryAnalysis(
        material_directory=material_directory.expanduser(),
        materials=tuple(analyses),
        backend_summary=backend_availability(),
        notes=tuple(notes),
    )
    cache_written = _write_material_library_cache(cache_path, library=library, snapshot=snapshot)
    updated_notes = list(notes)
    if cache_written:
        updated_notes.append(f"material analysis cache updated: {cache_path}")
    else:
        updated_notes.append(f"material analysis cache could not be updated: {cache_path}")
    return MaterialLibraryAnalysis(
        material_directory=library.material_directory,
        materials=library.materials,
        backend_summary=library.backend_summary,
        notes=tuple(updated_notes),
    )


def render_reference_analysis(reference: ReferenceAnalysis) -> dict[str, Any]:
    return {
        "source_path": str(reference.source_path),
        "vocal_path": str(reference.vocal_path),
        "backend": reference.backend,
        "transcript": reference.transcript,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "confidence": segment.confidence,
                "speaker_id": segment.speaker_id,
                "timing_source": segment.timing_source,
                "unit_timings": [
                    {
                        "position": timing.position,
                        "unit": timing.unit,
                        "start_seconds": timing.start_seconds,
                        "end_seconds": timing.end_seconds,
                        "confidence": timing.confidence,
                        "timing_source": timing.timing_source,
                    }
                    for timing in segment.unit_timings
                ],
            }
            for segment in reference.segments
        ],
        "speaker_embedding": list(reference.speaker_embedding) if reference.speaker_embedding else None,
        "notes": list(reference.notes),
    }


def render_material_analysis(analysis: AudioAnalysis) -> dict[str, Any]:
    return {
        "path": str(analysis.path),
        "filename_text": _filename_text_hint(analysis.path),
        "duration_seconds": analysis.duration_seconds,
        "backend": analysis.analysis_source,
        "transcript": analysis.transcript,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "confidence": segment.confidence,
                "speaker_id": segment.speaker_id,
                "timing_source": segment.timing_source,
            }
            for segment in analysis.segments
        ],
        "vad_segments": [{"start": start, "end": end} for start, end in analysis.vad_segments],
        "speaker_embedding": list(analysis.speaker_embedding) if analysis.speaker_embedding else None,
        "notes": list(analysis.notes),
    }


def backend_availability() -> dict[str, bool]:
    availability: dict[str, bool] = {}
    for candidate in list_model_candidates():
        name = candidate["name"]
        optional_dependency = candidate["optional_dependency"]
        if name == "UVR Headless Runner":
            availability[name] = uvr_worker_available()
        else:
            availability[name] = _module_available(optional_dependency)
    return availability


def speech_runtime_preflight_report(compute_device: str = "auto") -> dict[str, Any]:
    tool_dirs = ensure_runtime_tool_paths()
    ffmpeg_on_path = shutil.which("ffmpeg")
    preferred_backend = os.environ.get("VOCAL_PROCESS_ASR_BACKEND", "auto").strip().lower()
    allow_model_download = os.environ.get("VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD") == "1"
    issue = _speech_runtime_issue(preferred_backend, allow_model_download)
    return {
        "preferred_backend": preferred_backend,
        "allow_model_download": allow_model_download,
        "requested_compute_device": compute_device,
        "resolved_compute_device": _resolve_compute_device(compute_device),
        "available": not bool(issue),
        "issue": issue,
        "runtime_tool_directories": [str(path) for path in tool_dirs],
        "ffmpeg_on_path": ffmpeg_on_path or "",
        "torch_native_available": _module_available("torch"),
        "torch_native_detail": "" if _module_available("torch") else _module_unavailable_reason("torch"),
        "faster_whisper_module": _module_available("faster_whisper"),
        "faster_whisper_model_cached": _faster_whisper_model_cached(),
        "whisperx_module": _module_available("whisperx"),
        "whisperx_model_cached": _whisperx_model_cached(),
        "openai_whisper_module": _module_available("whisper"),
        "openai_whisper_model_cached": _whisper_model_cached(),
    }


def get_model_runtime_report(compute_device: str = "auto") -> list[str]:
    cache_root = _model_cache_root()
    availability = backend_availability()
    dependency_by_name = {candidate["name"]: candidate["optional_dependency"] for candidate in list_model_candidates()}
    resolved_device = _resolve_compute_device(compute_device)
    lines = [
        "Model runtime: local pretrained models",
        f"Model cache: {cache_root}",
        "Online inference billing: not used",
        f"Requested compute device: {compute_device}",
        f"Resolved compute device: {resolved_device}",
        f"CUDA available: {_torch_cuda_available()}",
    ]
    for name in (
        "Demucs",
        "UVR Headless Runner",
        "Faster Whisper",
        "OpenAI Whisper",
        "Silero VAD",
        "SpeechBrain",
        "WhisperX",
        "pyannote.audio",
        "whisper.cpp",
        "Librosa",
        "MSAF",
    ):
        status = "available" if availability.get(name) else "not installed"
        lines.append(f"{name}: {status}")
        if not availability.get(name):
            reason = _module_unavailable_reason(dependency_by_name.get(name, name))
            if reason:
                lines.append(f"  Runtime detail: {reason}")
    lines.append(f"Whisper model cached: {_whisper_model_cached()}")
    lines.append(f"Faster Whisper model cached: {_faster_whisper_model_cached()}")
    lines.append(f"WhisperX model cached: {_whisperx_model_cached()}")
    lines.append(f"Silero VAD cached: {_silero_model_cached()}")
    lines.append(f"SpeechBrain cached: {_speechbrain_model_cached(cache_root / 'speechbrain' / 'ecapa')}")
    return lines


def plan_reference_text_segments(
    reference_transcript: str,
    *,
    lyrics_file: Path | None = None,
) -> tuple[VoiceSegment, ...]:
    lyrics_segments = parse_lyrics_file(lyrics_file) if lyrics_file else []
    if lyrics_segments:
        return tuple(lyrics_segments)

    transcript = reference_transcript.strip()
    if not transcript:
        return ()

    sentences = [part.strip() for part in transcript.split("\n") if part.strip()]
    if not sentences:
        sentences = [transcript]

    duration = max(len(sentences), 1)
    segments: list[VoiceSegment] = []
    for index, sentence in enumerate(sentences):
        segments.append(
            VoiceSegment(
                start_seconds=float(index),
                end_seconds=float(index + 1),
                text=sentence,
                confidence=1.0,
                timing_source="transcript_sequence",
            )
        )
    return tuple(segments)


def parse_lyrics_file(path: Path | None) -> list[VoiceSegment]:
    if path is None:
        return []

    lyrics_path = path.expanduser()
    if not lyrics_path.exists():
        raise AudioProcessorError(f"Lyrics file does not exist: {lyrics_path}")

    suffix = lyrics_path.suffix.lower()
    if suffix in {".txt", ".lrc", ".srt"}:
        text = lyrics_path.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".docx":
        text = _read_docx_text(lyrics_path)
    elif suffix == ".doc":
        text = _read_legacy_doc_text(lyrics_path)
    else:
        raise AudioProcessorError(f"Unsupported lyrics file format: {lyrics_path}")

    segments = _lyrics_text_to_segments(text, suffix=suffix)
    if not segments:
        raise AudioProcessorError(f"Lyrics file did not contain usable text: {lyrics_path}")
    return segments


def _lyric_timing_notes(
    lyrics_file: Path | None,
    transcript_segments: Sequence[TranscriptSegment],
) -> list[str]:
    if lyrics_file is None:
        return []

    lyric_segments = parse_lyrics_file(lyrics_file)
    timed_lyrics = [
        segment
        for segment in lyric_segments
        if segment.timing_source in {"lrc_timestamp", "srt_timestamp"} and segment.duration_seconds > 0
    ]
    if not timed_lyrics:
        return ["lyric_timing_absent: lyrics provide text only; acoustic/model timing remains primary"]
    if not transcript_segments:
        return [
            "lyric_timing_unverified: timestamped lyrics exist but ASR segment timing is unavailable; "
            "lyrics may be used only as a fallback timing prior"
        ]

    comparable_count = min(len(timed_lyrics), len(transcript_segments))
    conflicts: list[str] = []
    for index in range(comparable_count):
        lyric = timed_lyrics[index]
        transcript = transcript_segments[index]
        tolerance = max(0.35, 0.25 * max(lyric.duration_seconds, transcript.end_seconds - transcript.start_seconds, 0.001))
        start_delta = abs(lyric.start_seconds - transcript.start_seconds)
        end_delta = abs(lyric.end_seconds - transcript.end_seconds)
        if start_delta > tolerance or end_delta > tolerance:
            conflicts.append(
                f"#{index + 1} start_delta={start_delta:.3f}s end_delta={end_delta:.3f}s tolerance={tolerance:.3f}s"
            )

    if conflicts:
        preview = "; ".join(conflicts[:5])
        more = f"; additional_conflicts={len(conflicts) - 5}" if len(conflicts) > 5 else ""
        return [
            "lyric_timing_conflict: timestamped lyrics disagree with ASR/acoustic timing; "
            f"acoustic segment timing retained; conflicts={len(conflicts)}/{comparable_count}; {preview}{more}"
        ]

    return [
        "lyric_timing_consistent: timestamped lyrics are consistent with ASR/acoustic timing within tolerance; "
        "timestamps remain a timing prior, not an absolute truth source"
    ]


def _lyrics_text_to_segments(text: str, *, suffix: str) -> list[VoiceSegment]:
    if suffix == ".lrc":
        timed_lines: list[tuple[float, str]] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip().lstrip("\ufeff")
            if not stripped:
                continue
            timestamps = re.findall(r"\[(\d{1,2}:\d{2}(?:[.:]\d{1,3})?)\]", stripped)
            cleaned = _strip_lrc_timestamp(stripped)
            if cleaned and timestamps:
                for timestamp in timestamps:
                    start = _parse_lrc_timestamp(timestamp)
                    if start is not None:
                        timed_lines.append((start, cleaned))
        if timed_lines:
            timed_lines.sort(key=lambda item: item[0])
            return [
                VoiceSegment(
                    start_seconds=start,
                    end_seconds=timed_lines[index + 1][0] if index + 1 < len(timed_lines) else start + 1.0,
                    text=line,
                    confidence=0.8,
                    timing_source="lrc_timestamp",
                )
                for index, (start, line) in enumerate(timed_lines)
                if line
            ]
        lines = [
            _strip_lrc_timestamp(raw_line.strip())
            for raw_line in text.splitlines()
            if _strip_lrc_timestamp(raw_line.strip())
        ]
    elif suffix == ".srt":
        timed_segments = _parse_srt_segments(text)
        if timed_segments:
            return timed_segments
        lines = _srt_text_lines_without_timing(text)
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]

    return [
        VoiceSegment(
            start_seconds=float(index),
            end_seconds=float(index + 1),
            text=line,
            confidence=1.0,
            timing_source="lyrics_sequence",
        )
        for index, line in enumerate(lines)
        if line
    ]


def _strip_lrc_timestamp(line: str) -> str:
    line = line.lstrip("\ufeff").strip()
    while line.startswith("[") and "]" in line:
        line = line.split("]", 1)[1].strip()
    return line


def _parse_lrc_timestamp(value: str) -> float | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?", value.strip())
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "0"
    fraction_seconds = int(fraction) / (10 ** len(fraction))
    return (minutes * 60.0) + seconds + fraction_seconds


def _parse_srt_segments(text: str) -> list[VoiceSegment]:
    segments: list[VoiceSegment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start, end = _parse_srt_time_range(lines[timing_index])
        if start is None or end is None or end <= start:
            continue
        subtitle_text = " ".join(lines[timing_index + 1 :]).strip()
        if not subtitle_text:
            continue
        segments.append(
            VoiceSegment(
                start_seconds=start,
                end_seconds=end,
                text=subtitle_text,
                confidence=0.8,
                timing_source="srt_timestamp",
            )
        )
    return segments


def _parse_srt_time_range(line: str) -> tuple[float | None, float | None]:
    parts = [part.strip() for part in line.split("-->", 1)]
    if len(parts) != 2:
        return None, None
    return _parse_srt_timestamp(parts[0]), _parse_srt_timestamp(parts[1])


def _parse_srt_timestamp(value: str) -> float | None:
    match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})", value)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4)
    return (hours * 3600.0) + (minutes * 60.0) + seconds + (int(fraction) / (10 ** len(fraction)))


def _srt_text_lines_without_timing(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return lines


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml_text = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise AudioProcessorError(f"Could not read DOCX lyrics file: {path}") from exc

    root = ElementTree.fromstring(xml_text)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
            if node.text
        ]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_legacy_doc_text(path: Path) -> str:
    word_text = _try_word_com_text(path)
    if word_text:
        return word_text

    antiword = shutil.which("antiword")
    if antiword:
        result = subprocess.run(
            [antiword, str(path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stdout.strip():
            return result.stdout

    raise AudioProcessorError(
        "DOC lyrics need either Microsoft Word, antiword, or a conversion to DOCX/TXT before analysis."
    )


def _try_word_com_text(path: Path) -> str:
    try:
        import win32com.client  # type: ignore
    except Exception:
        return ""

    try:
        word = win32com.client.Dispatch("Word.Application")
    except Exception:
        return ""

    document = None
    try:
        document = word.Documents.Open(str(path))
        text = document.Content.Text
        return text or ""
    except Exception:
        return ""
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:
            pass
        try:
            word.Quit()
        except Exception:
            pass


def _maybe_separate_vocals(
    path: Path,
    *,
    work_dir: Path | None = None,
    compute_device: str = DEFAULT_DEVICE,
    source_separation: str = "auto",
    notes: list[str],
    should_cancel: CancelCallback | None = None,
) -> Path:
    _raise_if_cancelled(should_cancel)
    mode = source_separation if source_separation in {"auto", "always", "never"} else "auto"
    if mode == "never":
        notes.append("source separation skipped by user setting; using reference audio as vocals")
        return path

    os.environ.setdefault("TORCH_HOME", str(_model_cache_root() / "torch"))
    work_root = _prepare_work_root(work_dir)
    separator_backend = _source_separator_backend()

    if separator_backend in {"auto", "uvr", "uvr-only"}:
        _raise_if_cancelled(should_cancel)
        uvr_root = make_uvr_output_dir(work_root, path)
        cached_uvr = find_uvr_vocal_output(uvr_root)
        if cached_uvr is not None:
            _raise_if_cancelled(should_cancel)
            notes.append(f"reference vocals reused from uvr cache: {cached_uvr}")
            return cached_uvr

        uvr_candidate = separate_vocals_with_uvr(
            path,
            uvr_root,
            compute_device=compute_device,
            notes=notes,
            should_cancel=should_cancel,
        )
        _raise_if_cancelled(should_cancel)
        if uvr_candidate is not None:
            return uvr_candidate
        if separator_backend == "uvr-only":
            notes.append("uvr-only source separation requested but no vocal stem was produced; using original reference audio")
            return path
        if separator_backend == "uvr":
            notes.append("uvr source separation requested but unavailable or failed; falling back to demucs when available")

    if separator_backend not in {"auto", "uvr", "demucs"}:
        notes.append(f"unknown source separator backend {separator_backend!r}; falling back to auto")

    if not _module_available("demucs"):
        notes.append("demucs unavailable; using original reference audio")
        return path

    _raise_if_cancelled(should_cancel)
    separated_root = work_root / "demucs"
    separated_root.mkdir(parents=True, exist_ok=True)
    candidate = separated_root / "htdemucs" / path.stem / "vocals.wav"
    if candidate.exists():
        _raise_if_cancelled(should_cancel)
        notes.append(f"reference vocals reused from demucs cache: {candidate}")
        return candidate

    _raise_if_cancelled(should_cancel)
    try:
        import demucs.separate  # type: ignore
    except Exception as exc:
        notes.append(f"demucs import failed: {exc}")
        return path

    cmd = [
        "--two-stems",
        "vocals",
        "-n",
        "htdemucs",
        "-d",
        compute_device,
        "--out",
        str(separated_root),
        str(path),
    ]
    _raise_if_cancelled(should_cancel)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            demucs.separate.main(cmd)
    except SystemExit as exc:
        if getattr(exc, "code", 0) not in (0, None):
            notes.append(f"demucs exited with code {exc.code}")
            return path
    except Exception as exc:
        notes.append(f"demucs separation failed: {exc}")
        return path

    _raise_if_cancelled(should_cancel)
    if candidate.exists():
        _raise_if_cancelled(should_cancel)
        notes.append(f"reference vocals separated with demucs: {candidate}")
        return candidate

    for found in separated_root.rglob("vocals.wav"):
        _raise_if_cancelled(should_cancel)
        notes.append(f"reference vocals separated with demucs: {found}")
        return found

    notes.append("demucs completed but no vocals stem was found")
    return path


def _transcribe_audio(
    path: Path,
    *,
    work_dir: Path | None = None,
    compute_device: str = DEFAULT_DEVICE,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(should_cancel)
    ensure_runtime_tool_paths()
    work_root = _prepare_work_root(work_dir)
    device = _resolve_compute_device(compute_device)
    preferred_backend = os.environ.get("VOCAL_PROCESS_ASR_BACKEND", "auto").strip().lower()
    fallback_notes: list[str] = []
    allow_model_download = os.environ.get("VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD") == "1"
    runtime_issue = _speech_runtime_issue(preferred_backend, allow_model_download)
    if runtime_issue:
        raise AudioProcessorError(runtime_issue)

    _raise_if_cancelled(should_cancel)
    if preferred_backend in {"auto", "faster-whisper", "faster_whisper"} and _module_available("faster_whisper"):
        if preferred_backend == "auto" and not allow_model_download and not _faster_whisper_model_cached():
            fallback_notes.append("faster-whisper skipped: local model cache not found and downloads are not enabled")
        else:
            try:
                return _transcribe_with_faster_whisper(
                    path,
                    compute_device=device,
                    should_cancel=should_cancel,
                )
            except Exception as exc:
                if preferred_backend in {"faster-whisper", "faster_whisper"}:
                    raise AudioProcessorError(f"Faster Whisper transcription failed for {path}: {exc}") from exc
                fallback_notes.append(f"faster-whisper failed: {exc}")

    _raise_if_cancelled(should_cancel)
    if preferred_backend in {"auto", "whisperx"} and _module_available("whisperx"):
        if preferred_backend == "auto" and not allow_model_download and not _whisperx_model_cached():
            fallback_notes.append("whisperx skipped: local model cache not found and downloads are not enabled")
        else:
            try:
                _raise_if_cancelled(should_cancel)
                _prepare_torchaudio_legacy_api()
                import whisperx  # type: ignore

                _raise_if_cancelled(should_cancel)
                model = _load_whisperx_model(
                    DEFAULT_ASR_MODEL,
                    device,
                )
                _raise_if_cancelled(should_cancel)
                audio = whisperx.load_audio(str(path))
                _raise_if_cancelled(should_cancel)
                result = model.transcribe(audio, batch_size=4)
                _raise_if_cancelled(should_cancel)
                align_model, metadata = whisperx.load_align_model(
                    language_code=result["language"], device=device
                )
                _raise_if_cancelled(should_cancel)
                aligned = whisperx.align(
                    result["segments"],
                    align_model,
                    metadata,
                    audio,
                    device,
                    return_char_alignments=True,
                )
                _raise_if_cancelled(should_cancel)
                segments: list[TranscriptSegment] = []
                for segment in aligned.get("segments", []):
                    _raise_if_cancelled(should_cancel)
                    if not str(segment.get("text", "")).strip():
                        continue
                    segments.append(
                        TranscriptSegment(
                            start_seconds=float(segment.get("start", 0.0) or 0.0),
                            end_seconds=float(segment.get("end", 0.0) or 0.0),
                            text=str(segment.get("text", "")).strip(),
                            confidence=_segment_confidence(segment),
                            speaker_id=segment.get("speaker"),
                            timing_source=(
                                "whisperx_char_alignment"
                                if _aligned_char_entries(segment)
                                else "whisperx_alignment"
                            ),
                            unit_timings=_unit_timings_from_aligned_chars(segment),
                        )
                    )
                text = " ".join(segment.text for segment in segments).strip()
                return {"backend": "whisperx", "text": text, "segments": segments, "notes": fallback_notes}
            except FileNotFoundError as exc:
                message = _ffmpeg_start_failure_message("WhisperX", path)
                if preferred_backend == "whisperx":
                    raise AudioProcessorError(message) from exc
                fallback_notes.append(message)
            except Exception as exc:
                if preferred_backend == "whisperx":
                    raise AudioProcessorError(f"WhisperX transcription failed for {path}: {exc}") from exc
                # Fall through to Whisper if WhisperX is unavailable or fails on a specific file.
                fallback_notes.append(f"whisperx failed: {exc}")

    _raise_if_cancelled(should_cancel)
    return _transcribe_with_whisper(
        path,
        work_dir=work_root,
        compute_device=device,
        fallback_note="; ".join(fallback_notes) if fallback_notes else "accelerated ASR backend unavailable",
        should_cancel=should_cancel,
    )


def _transcribe_with_faster_whisper(
    path: Path,
    *,
    compute_device: str,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(should_cancel)
    from faster_whisper import WhisperModel  # type: ignore

    device = _resolve_compute_device(compute_device)
    _raise_if_cancelled(should_cancel)
    model = _load_faster_whisper_model(DEFAULT_ASR_MODEL, device, _compute_type_for_device(device))
    _raise_if_cancelled(should_cancel)
    raw_segments, info = model.transcribe(
        str(path),
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
    )
    segments: list[TranscriptSegment] = []
    for segment in raw_segments:
        _raise_if_cancelled(should_cancel)
        if not str(getattr(segment, "text", "") or "").strip():
            continue
        segments.append(
            TranscriptSegment(
                start_seconds=float(getattr(segment, "start", 0.0) or 0.0),
                end_seconds=float(getattr(segment, "end", 0.0) or 0.0),
                text=str(getattr(segment, "text", "") or "").strip(),
                confidence=None,
                speaker_id=None,
                timing_source="faster_whisper_segment",
            )
        )
    text = " ".join(segment.text for segment in segments).strip()
    language = str(getattr(info, "language", "") or "")
    notes = [f"language={language}"] if language else []
    return {"backend": "faster-whisper", "text": text, "segments": segments, "notes": notes}


def _transcribe_with_whisper(
    path: Path,
    *,
    work_dir: Path,
    compute_device: str,
    fallback_note: str,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(should_cancel)
    if not _module_available("whisper"):
        reason = _module_unavailable_reason("whisper") or _module_unavailable_reason("torch")
        detail = f" Runtime detail: {reason}" if reason else ""
        raise AudioProcessorError(
            "Model-assisted ordering requires a speech recognition backend, but none is installed. "
            "Install openai-whisper or whisperx before running material assembly."
            f"{detail}"
        )

    try:
        _raise_if_cancelled(should_cancel)
        import whisper  # type: ignore

        device = _resolve_compute_device(compute_device)
        _raise_if_cancelled(should_cancel)
        model = _load_openai_whisper_model(DEFAULT_ASR_MODEL, device)
        _raise_if_cancelled(should_cancel)
        result = model.transcribe(str(path), fp16=device == "cuda", verbose=None)
        _raise_if_cancelled(should_cancel)
        segments: list[TranscriptSegment] = []
        for segment in result.get("segments", []):
            _raise_if_cancelled(should_cancel)
            if not str(segment.get("text", "")).strip():
                continue
            segments.append(
                TranscriptSegment(
                    start_seconds=float(segment.get("start", 0.0) or 0.0),
                    end_seconds=float(segment.get("end", 0.0) or 0.0),
                    text=str(segment.get("text", "")).strip(),
                    confidence=None,
                    speaker_id=None,
                    timing_source="whisper_segment",
                )
            )
        text = " ".join(segment.text for segment in segments).strip()
        notes = [fallback_note] if fallback_note else []
        return {"backend": "whisper", "text": text, "segments": segments, "notes": notes}
    except FileNotFoundError as exc:
        raise AudioProcessorError(_ffmpeg_start_failure_message("Whisper", path)) from exc
    except Exception as exc:
        raise AudioProcessorError(f"Whisper transcription failed for {path}: {exc}") from exc


def _ffmpeg_start_failure_message(backend_name: str, path: Path) -> str:
    tool_dirs = ", ".join(str(path) for path in ensure_runtime_tool_paths()) or "no bundled tool directory found"
    return (
        f"{backend_name} transcription failed for {path}: FFmpeg executable could not be started. "
        "For the portable package, keep the whole VocalProcess folder together and verify "
        f"`bin\\ffmpeg.exe` exists. Runtime tool directories: {tool_dirs}"
    )


def _detect_vad_segments(
    path: Path,
    *,
    compute_device: str = DEFAULT_DEVICE,
    should_cancel: CancelCallback | None = None,
) -> tuple[tuple[float, float], ...]:
    _raise_if_cancelled(should_cancel)
    pyannote_segments = _detect_pyannote_segments(path, should_cancel=should_cancel)
    if pyannote_segments:
        return pyannote_segments

    _raise_if_cancelled(should_cancel)
    if not _module_available("silero_vad"):
        torch_hub_segments = _detect_vad_segments_with_torch_hub(
            path,
            compute_device=compute_device,
            should_cancel=should_cancel,
        )
        if torch_hub_segments:
            return torch_hub_segments
        _raise_if_cancelled(should_cancel)
        return ((0.0, get_audio_duration_seconds(probe_audio(path))),)

    try:
        _raise_if_cancelled(should_cancel)
        from silero_vad import get_speech_timestamps, load_silero_vad, read_audio  # type: ignore

        device = _resolve_compute_device(compute_device)
        _raise_if_cancelled(should_cancel)
        model = _load_silero_vad_model(device)
        if device == "cuda" and hasattr(model, "to"):
            model = model.to(device)
        _raise_if_cancelled(should_cancel)
        wav = read_audio(str(path))
        if device == "cuda" and hasattr(wav, "to"):
            wav = wav.to(device)
        _raise_if_cancelled(should_cancel)
        timestamps = get_speech_timestamps(wav, model, return_seconds=True)
        segments = []
        for entry in timestamps:
            _raise_if_cancelled(should_cancel)
            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", 0.0) or 0.0)
            if end > start:
                segments.append((start, end))
        if segments:
            return tuple(segments)
    except Exception:
        pass

    _raise_if_cancelled(should_cancel)
    return ((0.0, get_audio_duration_seconds(probe_audio(path))),)


def _detect_pyannote_segments(
    path: Path,
    *,
    should_cancel: CancelCallback | None = None,
) -> tuple[tuple[float, float], ...]:
    _raise_if_cancelled(should_cancel)
    token = os.environ.get("PYANNOTE_AUTH_TOKEN") or os.environ.get("HF_TOKEN")
    if not token or not _module_available("pyannote.audio"):
        return ()

    try:
        _raise_if_cancelled(should_cancel)
        pipeline = _load_pyannote_pipeline(token)
        _raise_if_cancelled(should_cancel)
        diarization = pipeline(str(path))
        segments: list[tuple[float, float]] = []
        for turn, _, _speaker in diarization.itertracks(yield_label=True):
            _raise_if_cancelled(should_cancel)
            start = float(turn.start)
            end = float(turn.end)
            if end > start:
                segments.append((start, end))
        return tuple(segments)
    except Exception:
        return ()


def _detect_vad_segments_with_torch_hub(
    path: Path,
    *,
    compute_device: str = DEFAULT_DEVICE,
    should_cancel: CancelCallback | None = None,
) -> tuple[tuple[float, float], ...]:
    _raise_if_cancelled(should_cancel)
    if not _module_available("torch"):
        return ()

    try:
        _raise_if_cancelled(should_cancel)
        import torch  # type: ignore

        model_root = _model_cache_root() / "torch" / "hub"
        os.environ.setdefault("TORCH_HOME", str(_model_cache_root() / "torch"))
        torch.hub.set_dir(str(model_root))
        local_repo = model_root / "snakers4_silero-vad_master"
        if local_repo.exists():
            model, utils = _load_torch_hub_silero_vad(str(local_repo), "local")
        else:
            model, utils = _load_torch_hub_silero_vad("snakers4/silero-vad", "github")
        get_speech_timestamps, _, read_audio, _, _ = utils
        device = _resolve_compute_device(compute_device)
        if device == "cuda" and hasattr(model, "to"):
            model = model.to(device)
        _raise_if_cancelled(should_cancel)
        wav = read_audio(str(path))
        if device == "cuda" and hasattr(wav, "to"):
            wav = wav.to(device)
        _raise_if_cancelled(should_cancel)
        timestamps = get_speech_timestamps(wav, model, return_seconds=True)
        segments = []
        for entry in timestamps:
            _raise_if_cancelled(should_cancel)
            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", 0.0) or 0.0)
            if end > start:
                segments.append((start, end))
        return tuple(segments)
    except Exception:
        return ()


def _speaker_embedding(
    path: Path,
    *,
    work_dir: Path | None = None,
    compute_device: str = DEFAULT_DEVICE,
    should_cancel: CancelCallback | None = None,
) -> tuple[float, ...] | None:
    _raise_if_cancelled(should_cancel)
    if not _module_available("speechbrain"):
        return None

    try:
        _raise_if_cancelled(should_cancel)
        import torch  # type: ignore
        from speechbrain.dataio.dataio import read_audio  # type: ignore

        savedir = _model_cache_root() / "speechbrain" / "ecapa"
        if not _speechbrain_model_cached(savedir) and os.environ.get("VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD") != "1":
            return None

        device = _resolve_compute_device(compute_device)
        _raise_if_cancelled(should_cancel)
        classifier = _load_speechbrain_classifier(str(savedir), device)
        _raise_if_cancelled(should_cancel)
        waveform = read_audio(str(path))
        if waveform.dim() == 1:
            waveform = waveform.unsqueeze(0)
        waveform = waveform.to(device)
        _raise_if_cancelled(should_cancel)
        with torch.no_grad():
            embedding = classifier.encode_batch(waveform)
        _raise_if_cancelled(should_cancel)
        vector = embedding.squeeze().detach().cpu().flatten().tolist()
        return tuple(float(value) for value in vector)
    except Exception:
        return None


@lru_cache(maxsize=4)
def _load_faster_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel  # type: ignore

    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=str(_model_cache_root() / "faster-whisper"),
    )


@lru_cache(maxsize=4)
def _load_whisperx_model(model_name: str, device: str) -> Any:
    _prepare_torchaudio_legacy_api()
    import whisperx  # type: ignore

    return whisperx.load_model(
        model_name,
        device,
        compute_type=_compute_type_for_device(device),
        download_root=str(_model_cache_root() / "whisperx"),
    )


@lru_cache(maxsize=4)
def _load_openai_whisper_model(model_name: str, device: str) -> Any:
    import whisper  # type: ignore

    return whisper.load_model(
        model_name,
        device=device,
        download_root=str(_model_cache_root() / "whisper"),
    )


@lru_cache(maxsize=2)
def _load_silero_vad_model(device: str) -> Any:
    from silero_vad import load_silero_vad  # type: ignore

    model = load_silero_vad()
    if device == "cuda" and hasattr(model, "to"):
        model = model.to(device)
    return model


@lru_cache(maxsize=2)
def _load_torch_hub_silero_vad(repo_or_dir: str, source: str) -> tuple[Any, Any]:
    import torch  # type: ignore

    return torch.hub.load(
        repo_or_dir=repo_or_dir,
        model="silero_vad",
        source=source,
        trust_repo=True,
    )


@lru_cache(maxsize=2)
def _load_pyannote_pipeline(token: str) -> Any:
    _prepare_torchaudio_legacy_api()
    from pyannote.audio import Pipeline  # type: ignore

    return Pipeline.from_pretrained(PYANNOTE_DIA_MODEL, token=token)


@lru_cache(maxsize=2)
def _load_speechbrain_classifier(savedir: str, device: str) -> Any:
    from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

    return EncoderClassifier.from_hparams(
        source=SPEAKER_EMBEDDING_MODEL,
        savedir=savedir,
        run_opts={"device": device},
    )


def _material_snapshot(material_paths: Sequence[Path]) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for path in material_paths:
        expanded = path.expanduser()
        stat = expanded.stat()
        snapshot.append(
            {
                "path": str(expanded),
                "name": expanded.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "suffix": expanded.suffix.lower(),
            }
        )
    return snapshot


def _load_material_library_cache(
    cache_path: Path,
    *,
    material_directory: Path,
    snapshot: Sequence[dict[str, Any]],
) -> MaterialLibraryAnalysis | None:
    if not cache_path.exists():
        return None

    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    if raw.get("format") != "vocal_process_material_cache_v1":
        return None
    if raw.get("asr_model") != DEFAULT_ASR_MODEL:
        return None
    if raw.get("snapshot") != list(snapshot):
        return None

    materials = raw.get("materials")
    if not isinstance(materials, list) or len(materials) != len(snapshot):
        return None

    analyses = [_audio_analysis_from_cache(entry) for entry in materials if isinstance(entry, dict)]
    if len(analyses) != len(snapshot):
        return None

    return MaterialLibraryAnalysis(
        material_directory=material_directory,
        materials=tuple(analyses),
        backend_summary=backend_availability(),
        notes=(f"material analysis cache reused: {cache_path}",),
    )


def _write_material_library_cache(
    cache_path: Path,
    *,
    library: MaterialLibraryAnalysis,
    snapshot: Sequence[dict[str, Any]],
) -> bool:
    payload = {
        "format": "vocal_process_material_cache_v1",
        "asr_model": DEFAULT_ASR_MODEL,
        "snapshot": list(snapshot),
        "materials": [render_material_analysis(analysis) for analysis in library.materials],
    }
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def _material_cache_path(material_directory: Path, material_cache_dir: Path | None) -> Path:
    base = material_cache_dir.expanduser() if material_cache_dir is not None else material_directory.expanduser()
    return base / MATERIAL_CACHE_FILE


def _default_material_cache_dir(work_root: Path, material_directory: Path, compute_device: str) -> Path:
    payload = {
        "material_directory": str(material_directory.expanduser().resolve()),
        "asr_model": DEFAULT_ASR_MODEL,
        "asr_backend": os.environ.get("VOCAL_PROCESS_ASR_BACKEND", "auto").strip().lower(),
        "compute_device": compute_device,
    }
    key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return work_root / "material_analysis_cache" / key


def _reference_cache_path(
    work_root: Path,
    reference_path: Path,
    lyrics_file: Path | None,
    compute_device: str,
    source_separation: str,
) -> Path:
    payload = {
        "reference": _file_snapshot(reference_path),
        "lyrics": _optional_file_snapshot(lyrics_file.expanduser()) if lyrics_file else None,
        "asr_model": DEFAULT_ASR_MODEL,
        "asr_backend": os.environ.get("VOCAL_PROCESS_ASR_BACKEND", "auto").strip().lower(),
        "reference_unit_timing_format": "voice_unit_timing_v1",
        "whisperx_return_char_alignments": True,
        "speaker_model": SPEAKER_EMBEDDING_MODEL,
        "compute_device": compute_device,
        "source_separation": source_separation,
        "source_separator": uvr_cache_fingerprint(),
    }
    key = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_dir = work_root / "reference_analysis_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.json"


def _source_separator_backend() -> str:
    raw = os.environ.get("VOCAL_PROCESS_SOURCE_SEPARATOR", "auto").strip().lower().replace("_", "-")
    if raw in {"auto", "uvr", "uvr-only", "demucs"}:
        return raw
    return "auto"


def _load_reference_analysis_cache(
    cache_path: Path,
    *,
    normalized_reference: Path,
) -> ReferenceAnalysis | None:
    if not cache_path.exists():
        return None

    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict) or raw.get("format") != REFERENCE_CACHE_FORMAT:
        return None

    reference = raw.get("reference")
    if not isinstance(reference, dict):
        return None

    source_path = Path(str(reference.get("source_path") or ""))
    if source_path != normalized_reference:
        return None

    vocal_path = Path(str(reference.get("vocal_path") or ""))
    if vocal_path != normalized_reference and not vocal_path.exists():
        return None

    return ReferenceAnalysis(
        source_path=source_path,
        vocal_path=vocal_path,
        transcript=str(reference.get("transcript") or ""),
        segments=tuple(
            VoiceSegment(
                start_seconds=float(segment.get("start_seconds", 0.0) or 0.0),
                end_seconds=float(segment.get("end_seconds", 0.0) or 0.0),
                text=str(segment.get("text", "") or ""),
                confidence=_optional_float(segment.get("confidence")),
                speaker_id=_optional_string(segment.get("speaker_id")),
                timing_source=str(segment.get("timing_source") or ""),
                unit_timings=_voice_unit_timings_from_json(segment.get("unit_timings")),
            )
            for segment in reference.get("segments", [])
            if isinstance(segment, dict)
        ),
        speaker_embedding=(
            tuple(float(value) for value in reference.get("speaker_embedding"))
            if isinstance(reference.get("speaker_embedding"), list)
            else None
        ),
        backend=str(reference.get("backend") or "cache"),
        notes=tuple(str(note) for note in reference.get("notes", []) if isinstance(note, str)),
    )


def _write_reference_analysis_cache(cache_path: Path, reference: ReferenceAnalysis) -> bool:
    payload = {
        "format": REFERENCE_CACHE_FORMAT,
        "reference": render_reference_analysis(reference),
    }
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def _file_snapshot(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "suffix": path.suffix.lower(),
    }


def _optional_file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "missing": True,
            "suffix": path.suffix.lower(),
        }
    return _file_snapshot(path)


def _audio_analysis_from_cache(data: dict[str, Any]) -> AudioAnalysis:
    segments = tuple(
        TranscriptSegment(
            start_seconds=float(segment.get("start_seconds", 0.0) or 0.0),
            end_seconds=float(segment.get("end_seconds", 0.0) or 0.0),
            text=str(segment.get("text", "") or ""),
            confidence=_optional_float(segment.get("confidence")),
            speaker_id=_optional_string(segment.get("speaker_id")),
            timing_source=str(segment.get("timing_source") or ""),
        )
        for segment in data.get("segments", [])
        if isinstance(segment, dict)
    )
    vad_segments = tuple(
        (
            float(segment.get("start", 0.0) or 0.0),
            float(segment.get("end", 0.0) or 0.0),
        )
        for segment in data.get("vad_segments", [])
        if isinstance(segment, dict)
    )
    embedding_raw = data.get("speaker_embedding")
    speaker_embedding = (
        tuple(float(value) for value in embedding_raw)
        if isinstance(embedding_raw, list)
        else None
    )
    return AudioAnalysis(
        path=Path(str(data.get("path") or "")),
        duration_seconds=float(data.get("duration_seconds", 0.0) or 0.0),
        transcript=str(data.get("transcript") or ""),
        segments=segments,
        vad_segments=vad_segments,
        speaker_embedding=speaker_embedding,
        analysis_source=str(data.get("backend") or "cache"),
        notes=tuple(str(note) for note in data.get("notes", []) if isinstance(note, str)),
    )


def _speechbrain_model_cached(savedir: Path) -> bool:
    return (savedir / "hyperparams.yaml").exists()


def _segments_from_transcript(
    segments: Sequence[TranscriptSegment],
    *,
    lyrics_file: Path | None = None,
) -> tuple[VoiceSegment, ...]:
    if lyrics_file is not None:
        lyric_segments = parse_lyrics_file(lyrics_file)
        if lyric_segments:
            if segments:
                max_count = min(len(lyric_segments), len(segments))
                paired: list[VoiceSegment] = []
                for index in range(max_count):
                    lyric = lyric_segments[index]
                    transcript_segment = segments[index]
                    paired.append(
                        VoiceSegment(
                            start_seconds=transcript_segment.start_seconds,
                            end_seconds=transcript_segment.end_seconds,
                            text=lyric.text,
                            confidence=transcript_segment.confidence,
                            speaker_id=transcript_segment.speaker_id,
                            timing_source="asr_segment_with_lyric_text",
                            unit_timings=_retarget_unit_timings_to_text(
                                transcript_segment.unit_timings,
                                lyric.text,
                            ),
                        )
                    )
                if len(lyric_segments) > max_count and segments:
                    last_end = paired[-1].end_seconds if paired else segments[-1].end_seconds
                    for index in range(max_count, len(lyric_segments)):
                        lyric = lyric_segments[index]
                        if lyric.timing_source in {"lrc_timestamp", "srt_timestamp"} and lyric.duration_seconds > 0:
                            start_seconds = lyric.start_seconds
                            end_seconds = lyric.end_seconds
                            timing_source = f"{lyric.timing_source}_without_asr_segment"
                        else:
                            start_seconds = last_end + float(index - max_count)
                            end_seconds = last_end + float(index - max_count + 1)
                            timing_source = "lyrics_sequence_without_asr_segment"
                        paired.append(
                            VoiceSegment(
                                start_seconds=start_seconds,
                                end_seconds=end_seconds,
                                text=lyric.text,
                                confidence=1.0,
                                timing_source=timing_source,
                            )
                        )
                return tuple(paired)
            return tuple(lyric_segments)

    return tuple(
        VoiceSegment(
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text,
            confidence=segment.confidence,
            speaker_id=segment.speaker_id,
            timing_source=(
                "asr_segment_with_unit_timing"
                if segment.unit_timings
                else "asr_segment"
            ),
            unit_timings=segment.unit_timings,
        )
        for segment in segments
    )


def _retarget_unit_timings_to_text(
    timings: Sequence[VoiceUnitTiming],
    text: str,
) -> tuple[VoiceUnitTiming, ...]:
    if not timings:
        return ()
    units = _timeline_units(text)
    if len(units) != len(timings):
        return ()
    return tuple(
        VoiceUnitTiming(
            position=index,
            unit=unit,
            start_seconds=timing.start_seconds,
            end_seconds=timing.end_seconds,
            confidence=timing.confidence,
            timing_source=f"{timing.timing_source}_retargeted_to_lyrics",
        )
        for index, (unit, timing) in enumerate(zip(units, timings))
    )


def reference_duration_for(path: Path) -> float:
    return get_audio_duration_seconds(probe_audio(path))


@dataclass(frozen=True)
class _TimedUnitSpan:
    start_seconds: float
    end_seconds: float
    aligned_unit_count: int
    expected_unit_count: int
    timing_source: str

    @property
    def duration_seconds(self) -> float:
        return max(self.end_seconds - self.start_seconds, 0.0)


def _target_durations_for_decisions(
    reference_segments: Sequence[VoiceSegment],
    decisions: Sequence[MaterialOrderDecision],
    *,
    reference_duration: float,
) -> tuple[float | None, ...]:
    if not decisions:
        return ()
    if reference_duration <= 0:
        return tuple(None for _ in decisions)

    positioned_targets = _positioned_target_durations(reference_segments, decisions)
    if positioned_targets and any(target is not None for target in positioned_targets):
        return _fill_unresolved_target_durations(
            positioned_targets,
            decisions,
            reference_duration=reference_duration,
        )

    normalized_reference_texts = {
        _compact_bridge_text(decision.reference_text)
        for decision in decisions
        if decision.reference_text.strip()
    }
    has_segment_indices = any(decision.reference_segment_index is not None for decision in decisions)
    if len(decisions) > 1 and len(normalized_reference_texts) <= 1 and not has_segment_indices:
        return _weighted_target_durations(decisions, reference_duration=reference_duration)

    targets: list[float | None] = list(positioned_targets) if positioned_targets else [None for _ in decisions]
    used_segments: set[int] = {
        decision.reference_segment_index
        for index, decision in enumerate(decisions)
        if targets[index] is not None and decision.reference_segment_index is not None
    }
    for decision_index, decision in enumerate(decisions):
        if targets[decision_index] is not None:
            continue
        if decision.reference_segment_index is not None:
            segment_index = decision.reference_segment_index
            if segment_index in used_segments:
                continue
            if 0 <= segment_index < len(reference_segments):
                segment = reference_segments[segment_index]
                if segment.duration_seconds > 0:
                    targets[decision_index] = segment.duration_seconds
                    used_segments.add(segment_index)
                    continue

        decision_text = _compact_bridge_text(decision.reference_text)
        if not decision_text:
            continue
        for segment_index, segment in enumerate(reference_segments):
            if segment_index in used_segments:
                continue
            if segment.duration_seconds <= 0:
                continue
            if _compact_bridge_text(segment.text) != decision_text:
                continue
            targets[decision_index] = segment.duration_seconds
            used_segments.add(segment_index)
            break

    if any(target is not None for target in targets):
        return _fill_unresolved_target_durations(
            targets,
            decisions,
            reference_duration=reference_duration,
        )
    return _weighted_target_durations(decisions, reference_duration=reference_duration)


def _render_timeline_alignment_summary(
    reference_segments: Sequence[VoiceSegment],
    decisions: Sequence[MaterialOrderDecision],
    target_durations: Sequence[float | None],
) -> dict[str, Any]:
    positioned_decisions = [
        decision
        for decision in decisions
        if decision.reference_segment_index is not None and _decision_position(decision) is not None
    ]
    split_segment_indices = sorted(
        segment_index
        for segment_index, count in _positioned_decision_count_by_segment(decisions).items()
        if count > 1
    )
    decision_details = _render_timeline_alignment_details(reference_segments, decisions, target_durations)
    timed_target_duration_count = sum(
        1 for detail in decision_details if detail.get("target_duration_source") == "aligned_unit_timing"
    )
    return {
        "format": "vocal_process_timeline_alignment_summary_v1",
        "reference_segment_count": len(reference_segments),
        "reference_unit_timing_count": sum(len(segment.unit_timings) for segment in reference_segments),
        "decision_count": len(decisions),
        "positioned_decision_count": len(positioned_decisions),
        "decision_details": decision_details,
        "split_reference_segment_indices": split_segment_indices,
        "resolved_target_duration_count": sum(1 for duration in target_durations if duration is not None),
        "timed_target_duration_count": timed_target_duration_count,
        "target_duration_total_seconds": sum(float(duration or 0.0) for duration in target_durations),
        "phonetic_positioned_decision_count": sum(
            1 for decision in decisions if decision.phonetic_position is not None
        ),
        "tone_disambiguated_decision_count": sum(
            1 for decision in decisions if decision.phonetic_tone_score > 0 and decision.phonetic_tone_position_count == 1
        ),
        "ambiguous_phonetic_decision_count": sum(
            1 for decision in decisions if decision.phonetic_position_count > 1
        ),
        "mode": (
            "aligned_unit_timing"
            if timed_target_duration_count
            else "phonetic_or_text_position_split" if split_segment_indices else "segment_or_weighted_duration"
        ),
    }


def _render_timeline_alignment_details(
    reference_segments: Sequence[VoiceSegment],
    decisions: Sequence[MaterialOrderDecision],
    target_durations: Sequence[float | None],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any] | None] = [None for _ in decisions]
    groups: dict[int, list[tuple[int, MaterialOrderDecision]]] = {}
    for decision_index, decision in enumerate(decisions):
        segment_index = decision.reference_segment_index
        position = _decision_position(decision)
        if segment_index is None or position is None:
            continue
        if not 0 <= segment_index < len(reference_segments):
            continue
        groups.setdefault(segment_index, []).append((decision_index, decision))

    for segment_index, group in groups.items():
        segment = reference_segments[segment_index]
        if segment.duration_seconds <= 0:
            continue

        unit_count = _positioned_group_unit_count(segment, group)
        complete_position_cover = _positioned_group_covers_segment(group, unit_count)
        ordered_group = sorted(group, key=lambda item: (_decision_position(item[1]) or 0, item[1].rank))
        for group_index, (decision_index, decision) in enumerate(ordered_group):
            decision_path = _decision_path(decision)
            position = max(_decision_position(decision) or 0, 0)
            start_unit = min(position, unit_count - 1)
            next_start = None
            if group_index + 1 < len(ordered_group):
                next_position = _decision_position(ordered_group[group_index + 1][1])
                if next_position is not None and next_position > start_unit:
                    next_start = min(next_position, unit_count)

            material_units = _decision_timeline_unit_count(decision)
            if next_start is not None:
                end_unit = next_start
            elif complete_position_cover and group_index == len(ordered_group) - 1:
                end_unit = unit_count
            else:
                end_unit = start_unit + material_units

            span_units = max(min(end_unit, unit_count) - start_unit, 1)
            timed_span = _timed_unit_span(segment, start_unit, end_unit)
            target_duration = target_durations[decision_index]
            reference_text_units = list(_timeline_units(segment.text))
            reference_phonetic_units = list(_phonetic_units(segment.text))
            material_text = decision.material_text or decision_path.stem
            material_text_units = list(_timeline_units(material_text))
            material_phonetic_units = list(_phonetic_units(material_text))
            details[decision_index] = {
                "rank": decision.rank,
                "source_path": str(decision_path),
                "source_filename": decision_path.name,
                "reference_segment_index": segment_index,
                "reference_segment_text": segment.text,
                "reference_text_units": reference_text_units,
                "reference_phonetic_units": reference_phonetic_units,
                "source_filename_units": list(_timeline_units(decision_path.stem)),
                "source_filename_phonetic_units": list(_phonetic_units(decision_path.stem)),
                "material_text": decision.material_text,
                "material_text_units": material_text_units,
                "material_phonetic_units": material_phonetic_units,
                "position_mode": "text_position" if decision.text_position is not None else "phonetic_position",
                "text_position": decision.text_position,
                "phonetic_position": decision.phonetic_position,
                "phonetic_position_count": decision.phonetic_position_count,
                "phonetic_tone_position": decision.phonetic_tone_position,
                "phonetic_tone_position_count": decision.phonetic_tone_position_count,
                "phonetic_span_units": decision.phonetic_span_units,
                "reference_segment_unit_count": unit_count,
                "position_unit_start": start_unit,
                "position_unit_end": end_unit,
                "position_unit_span": span_units,
                "complete_position_cover": complete_position_cover,
                "segment_duration_seconds": segment.duration_seconds,
                "target_start_seconds": (
                    timed_span.start_seconds if timed_span is not None else None
                ),
                "target_end_seconds": (
                    timed_span.end_seconds if timed_span is not None else None
                ),
                "target_duration_seconds": target_duration,
                "target_duration_source": (
                    timed_span.timing_source if timed_span is not None else "proportional_segment_split"
                ),
                "aligned_unit_count": timed_span.aligned_unit_count if timed_span is not None else 0,
                "expected_unit_count": timed_span.expected_unit_count if timed_span is not None else span_units,
                "target_duration_ratio": (
                    float(target_duration) / segment.duration_seconds
                    if target_duration is not None and segment.duration_seconds > 0
                    else None
                ),
                "score": decision.score,
                "confidence_label": decision.confidence_label,
                "reason": decision.reason,
            }

    for decision_index, decision in enumerate(decisions):
        if details[decision_index] is not None:
            continue
        decision_path = _decision_path(decision)
        segment = (
            reference_segments[decision.reference_segment_index]
            if decision.reference_segment_index is not None
            and 0 <= decision.reference_segment_index < len(reference_segments)
            else None
        )
        material_text = decision.material_text or decision_path.stem
        details[decision_index] = {
            "rank": decision.rank,
            "source_path": str(decision_path),
            "source_filename": decision_path.name,
            "reference_segment_index": decision.reference_segment_index,
            "reference_segment_text": segment.text if segment is not None else "",
            "reference_text_units": list(_timeline_units(segment.text)) if segment is not None else [],
            "reference_phonetic_units": list(_phonetic_units(segment.text)) if segment is not None else [],
            "source_filename_units": list(_timeline_units(decision_path.stem)),
            "source_filename_phonetic_units": list(_phonetic_units(decision_path.stem)),
            "material_text": decision.material_text,
            "material_text_units": list(_timeline_units(material_text)),
            "material_phonetic_units": list(_phonetic_units(material_text)),
            "position_mode": "weighted_duration",
            "text_position": decision.text_position,
            "phonetic_position": decision.phonetic_position,
            "phonetic_position_count": decision.phonetic_position_count,
            "phonetic_tone_position": decision.phonetic_tone_position,
            "phonetic_tone_position_count": decision.phonetic_tone_position_count,
            "phonetic_span_units": decision.phonetic_span_units,
            "reference_segment_unit_count": len(_timeline_units(segment.text)) if segment is not None else 0,
            "position_unit_start": None,
            "position_unit_end": None,
            "position_unit_span": None,
            "complete_position_cover": False,
            "segment_duration_seconds": segment.duration_seconds if segment is not None else None,
            "target_start_seconds": None,
            "target_end_seconds": None,
            "target_duration_seconds": target_durations[decision_index],
            "target_duration_source": "weighted_duration",
            "aligned_unit_count": 0,
            "expected_unit_count": 0,
            "target_duration_ratio": None,
            "score": decision.score,
            "confidence_label": decision.confidence_label,
            "reason": decision.reason,
        }

    return [detail for detail in details if detail is not None]


def _decision_path(decision: Any) -> Path:
    path = getattr(decision, "source_path", None)
    if path is None:
        path = getattr(decision, "material_path", None)
    if path is None:
        raise AttributeError("Decision is missing a source or material path")
    return Path(path)


def _positioned_decision_count_by_segment(
    decisions: Sequence[MaterialOrderDecision],
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for decision in decisions:
        if decision.reference_segment_index is None or _decision_position(decision) is None:
            continue
        counts[decision.reference_segment_index] = counts.get(decision.reference_segment_index, 0) + 1
    return counts


def _positioned_target_durations(
    reference_segments: Sequence[VoiceSegment],
    decisions: Sequence[MaterialOrderDecision],
) -> tuple[float | None, ...]:
    if len(decisions) <= 1:
        return tuple(None for _ in decisions)

    targets: list[float | None] = [None for _ in decisions]
    groups: dict[int, list[tuple[int, MaterialOrderDecision]]] = {}
    for decision_index, decision in enumerate(decisions):
        segment_index = decision.reference_segment_index
        if segment_index is None or _decision_position(decision) is None:
            continue
        if not 0 <= segment_index < len(reference_segments):
            continue
        groups.setdefault(segment_index, []).append((decision_index, decision))

    for segment_index, group in groups.items():
        if len(group) <= 1:
            continue
        segment = reference_segments[segment_index]
        if segment.duration_seconds <= 0:
            continue

        unit_count = _positioned_group_unit_count(segment, group)
        complete_position_cover = _positioned_group_covers_segment(group, unit_count)
        ordered_group = sorted(group, key=lambda item: (_decision_position(item[1]) or 0, item[1].rank))
        for group_index, (decision_index, decision) in enumerate(ordered_group):
            start_unit = min(max(_decision_position(decision) or 0, 0), unit_count - 1)
            next_start = None
            if group_index + 1 < len(ordered_group):
                next_position = _decision_position(ordered_group[group_index + 1][1])
                if next_position is not None and next_position > start_unit:
                    next_start = min(next_position, unit_count)

            material_units = _decision_timeline_unit_count(decision)
            if next_start is not None:
                end_unit = next_start
            elif complete_position_cover and group_index == len(ordered_group) - 1:
                end_unit = unit_count
            else:
                end_unit = start_unit + material_units

            span_units = max(min(end_unit, unit_count) - start_unit, 1)
            timed_span = _timed_unit_span(segment, start_unit, end_unit)
            if timed_span is not None and timed_span.duration_seconds > 0:
                targets[decision_index] = timed_span.duration_seconds
            else:
                targets[decision_index] = segment.duration_seconds * (span_units / unit_count)

    return tuple(targets)


def _timed_unit_span(segment: VoiceSegment, start_unit: int, end_unit: int) -> _TimedUnitSpan | None:
    expected_unit_count = max(end_unit - start_unit, 1)
    if not segment.unit_timings or expected_unit_count <= 0:
        return None

    by_position = {
        timing.position: timing
        for timing in segment.unit_timings
        if timing.duration_seconds > 0
    }
    timings: list[VoiceUnitTiming] = []
    for position in range(start_unit, end_unit):
        timing = by_position.get(position)
        if timing is None:
            return None
        timings.append(timing)
    if len(timings) != expected_unit_count:
        return None

    start_seconds = min(timing.start_seconds for timing in timings)
    end_seconds = max(timing.end_seconds for timing in timings)
    if end_seconds <= start_seconds:
        return None
    return _TimedUnitSpan(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        aligned_unit_count=len(timings),
        expected_unit_count=expected_unit_count,
        timing_source="aligned_unit_timing",
    )


def _positioned_group_unit_count(
    segment: VoiceSegment,
    group: Sequence[tuple[int, MaterialOrderDecision]],
) -> int:
    max_end = 0
    for _, decision in group:
        position = _decision_position(decision)
        if position is None:
            continue
        max_end = max(max_end, max(int(position), 0) + _decision_timeline_unit_count(decision))
    return max(len(_timeline_units(segment.text)), max_end, 1)


def _positioned_group_covers_segment(
    group: Sequence[tuple[int, MaterialOrderDecision]],
    unit_count: int,
) -> bool:
    if unit_count <= 0:
        return False
    covered: set[int] = set()
    for _, decision in group:
        position = _decision_position(decision)
        if position is None:
            continue
        start = min(max(int(position), 0), unit_count - 1)
        span = max(_decision_timeline_unit_count(decision), 1)
        covered.update(range(start, min(start + span, unit_count)))
    return len(covered) >= unit_count


def _decision_position(decision: MaterialOrderDecision) -> int | None:
    if decision.text_position is not None:
        return decision.text_position
    return decision.phonetic_position


def _weighted_target_durations(
    decisions: Sequence[MaterialOrderDecision],
    *,
    reference_duration: float,
) -> tuple[float | None, ...]:
    weights = [_decision_text_weight(decision) for decision in decisions]
    return tuple(_fit_target_durations_to_total(weights, reference_duration))


def _fill_unresolved_target_durations(
    targets: Sequence[float | None],
    decisions: Sequence[MaterialOrderDecision],
    *,
    reference_duration: float,
) -> tuple[float | None, ...]:
    if len(targets) != len(decisions):
        return _weighted_target_durations(decisions, reference_duration=reference_duration)

    explicit_indices = [
        index
        for index, target in enumerate(targets)
        if target is not None and float(target) > 0
    ]
    if not explicit_indices:
        return _weighted_target_durations(decisions, reference_duration=reference_duration)

    explicit_index_set = set(explicit_indices)
    unresolved_indices = [index for index in range(len(targets)) if index not in explicit_index_set]
    if not unresolved_indices:
        return tuple(
            _fit_target_durations_to_total(
                [float(targets[index] or 0.0) for index in explicit_indices],
                reference_duration,
            )
        )

    resolved: list[float | None] = [None for _ in targets]
    explicit_sum = sum(float(targets[index] or 0.0) for index in explicit_indices)
    unresolved_reserve = _minimum_target_duration_budget(reference_duration, len(unresolved_indices))
    explicit_budget = max(reference_duration - unresolved_reserve, 0.0)

    if explicit_sum > explicit_budget:
        explicit_values = _fit_target_durations_to_total(
            [float(targets[index] or 0.0) for index in explicit_indices],
            explicit_budget,
        )
    else:
        explicit_values = [float(targets[index] or 0.0) for index in explicit_indices]

    for index, value in zip(explicit_indices, explicit_values):
        resolved[index] = value

    remaining_duration = max(reference_duration - sum(explicit_values), 0.0)
    unresolved_values = _fit_target_durations_to_total(
        [_decision_text_weight(decisions[index]) for index in unresolved_indices],
        remaining_duration,
    )
    for index, value in zip(unresolved_indices, unresolved_values):
        resolved[index] = value

    return tuple(_fit_target_durations_to_total([float(value or 0.0) for value in resolved], reference_duration))


def _minimum_target_duration_budget(total_duration: float, count: int) -> float:
    if count <= 0 or total_duration <= 0:
        return 0.0
    return min(0.001 * count, total_duration)


def _fit_target_durations_to_total(durations: Sequence[float], total_duration: float) -> list[float]:
    if not durations:
        return []
    if total_duration <= 0:
        return [0.0 for _ in durations]
    if len(durations) == 1:
        return [total_duration]

    minimum = min(0.001, total_duration / len(durations))
    raw_values = [max(float(duration), 0.0) for duration in durations]
    raw_total = sum(raw_values)
    if raw_total <= 0:
        raw_values = [1.0 for _ in durations]
        raw_total = float(len(raw_values))

    scaled = [total_duration * (duration / raw_total) for duration in raw_values]
    adjusted: list[float] = []
    remaining = total_duration
    remaining_count = len(scaled)
    for duration in scaled[:-1]:
        max_duration = max(remaining - (minimum * (remaining_count - 1)), minimum)
        adjusted_duration = min(max(duration, minimum), max_duration)
        adjusted.append(adjusted_duration)
        remaining -= adjusted_duration
        remaining_count -= 1
    adjusted.append(max(remaining, minimum))
    return adjusted


def _decision_text_weight(decision: MaterialOrderDecision) -> float:
    return float(_decision_timeline_unit_count(decision))


def _decision_timeline_unit_count(decision: MaterialOrderDecision) -> int:
    if decision.phonetic_span_units > 0:
        return decision.phonetic_span_units
    counts = [
        len(units)
        for units in (
            _timeline_units(decision.material_path.stem),
            _timeline_units(decision.material_text),
        )
        if units
    ]
    return max(min(counts), 1) if counts else 1


def _timeline_units(text: str) -> list[str]:
    return [unit for unit, _, _ in _timeline_unit_spans(text)]


def _timeline_unit_spans(text: str) -> list[tuple[str, int, int]]:
    normalized = text.replace("_", " ").replace("-", " ").lower()
    return [
        (match.group(0), match.start(), match.end())
        for match in re.finditer(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", normalized)
    ]


def _compact_bridge_text(text: str) -> str:
    units = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())
    return "".join(units)


def _prepare_work_root(work_dir: Path | None) -> Path:
    root = (work_dir or Path(tempfile.gettempdir()) / "vocal_process_model_cache").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_compute_device(requested: str | None) -> str:
    normalized = str(requested or "auto").strip().lower()
    if normalized == "cuda":
        return "cuda" if _torch_cuda_available() else DEFAULT_DEVICE
    if normalized == "cpu":
        return DEFAULT_DEVICE
    if normalized == "auto":
        return "cuda" if _torch_cuda_available() else DEFAULT_DEVICE
    return DEFAULT_DEVICE


def _compute_type_for_device(device: str) -> str:
    return "float16" if device == "cuda" else DEFAULT_COMPUTE_TYPE


def _torch_cuda_available() -> bool:
    if not _module_available("torch"):
        return False
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _filename_text_hint(path: Path) -> str:
    text = path.stem
    text = text.replace("_", " ").replace("-", " ")
    text = " ".join(part for part in text.split() if not part.isdigit())
    return text.strip()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _voice_unit_timings_from_json(value: Any) -> tuple[VoiceUnitTiming, ...]:
    if not isinstance(value, list):
        return ()
    timings: list[VoiceUnitTiming] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        start_seconds = _optional_float(entry.get("start_seconds"))
        end_seconds = _optional_float(entry.get("end_seconds"))
        if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
            continue
        try:
            position = int(entry.get("position", len(timings)))
        except (TypeError, ValueError):
            position = len(timings)
        timings.append(
            VoiceUnitTiming(
                position=max(position, 0),
                unit=str(entry.get("unit") or ""),
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                confidence=_optional_float(entry.get("confidence")),
                timing_source=str(entry.get("timing_source") or "cache"),
            )
        )
    return tuple(timings)


def _model_cache_root() -> Path:
    for candidate in _model_cache_candidates():
        root = _ensure_model_cache_root(candidate)
        if root is not None:
            return root

    fallback = Path(tempfile.gettempdir()) / "vocal_process_models"
    root = _ensure_model_cache_root(fallback)
    if root is not None:
        return root
    return fallback


def _model_cache_candidates() -> list[Path]:
    configured = os.environ.get("VOCAL_PROCESS_MODEL_CACHE")
    if configured:
        return [Path(configured).expanduser()]

    if getattr(sys, "frozen", False):
        portable_root = Path(sys.executable).resolve().parent / "models"
        return [portable_root, get_config_dir() / "models"]

    project_cache = Path(__file__).resolve().parents[1] / ".tmp" / "model-cache"
    if project_cache.exists():
        return [project_cache, get_config_dir() / "models"]
    return [get_config_dir() / "models", project_cache]


def _ensure_model_cache_root(path: Path) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def _whisper_model_cached() -> bool:
    model_name = DEFAULT_ASR_MODEL
    return (_model_cache_root() / "whisper" / f"{model_name}.pt").exists()


def _faster_whisper_model_cached() -> bool:
    root = _model_cache_root() / "faster-whisper"
    if not root.exists():
        return False
    return any(root.rglob("model.bin")) or any(root.rglob("config.json"))


def _whisperx_model_cached() -> bool:
    root = _model_cache_root() / "whisperx"
    if not root.exists():
        return False
    return any(root.rglob("model.bin")) or any(root.rglob("config.json"))


def _silero_model_cached() -> bool:
    root = _model_cache_root() / "torch" / "hub"
    return (root / "snakers4_silero-vad_master").exists() or any((root / "checkpoints").glob("*.th"))


def _speech_runtime_issue(preferred_backend: str, allow_model_download: bool) -> str:
    attempted: list[str] = []

    if preferred_backend in {"auto", "faster-whisper", "faster_whisper"}:
        if _module_available("faster_whisper"):
            if preferred_backend != "auto" or allow_model_download or _faster_whisper_model_cached():
                return ""
            attempted.append("faster-whisper skipped: local model cache not found and downloads are not enabled")
        else:
            attempted.append(f"faster-whisper not loadable: {_module_unavailable_reason('faster_whisper')}")

    if preferred_backend in {"auto", "whisperx"}:
        if _module_available("whisperx"):
            torch_issue = _torch_dependent_backend_issue("whisperx")
            if torch_issue:
                attempted.append(torch_issue)
            elif preferred_backend != "auto" or allow_model_download or _whisperx_model_cached():
                return ""
            else:
                attempted.append("whisperx skipped: local model cache not found and downloads are not enabled")
        else:
            attempted.append(f"whisperx not loadable: {_module_unavailable_reason('whisperx')}")

    if preferred_backend in {"auto", "whisper", "openai-whisper", "openai_whisper"}:
        if _module_available("whisper"):
            torch_issue = _torch_dependent_backend_issue("openai-whisper")
            if torch_issue:
                attempted.append(torch_issue)
            else:
                return ""
        else:
            attempted.append(f"openai-whisper not loadable: {_module_unavailable_reason('whisper')}")

    if preferred_backend not in {"auto", "faster-whisper", "faster_whisper", "whisperx", "whisper", "openai-whisper", "openai_whisper"}:
        attempted.append(f"unsupported ASR backend setting: {preferred_backend}")

    reason = " | ".join(part for part in attempted if part)
    hint = f" {TORCH_NATIVE_RUNTIME_HINT}" if "torch._C" in reason or "PyTorch native" in reason else ""
    return f"Speech recognition runtime is unavailable before rendering. {reason}.{hint}".strip()


def _torch_dependent_backend_issue(backend_name: str) -> str:
    if not _module_available("torch"):
        return f"{backend_name} not loadable: {_module_unavailable_reason('torch')}"
    if backend_name == "whisperx" and not _module_available("torchaudio"):
        return f"{backend_name} not loadable: {_module_unavailable_reason('torchaudio')}"
    return ""


def _maybe_module_available(module_name: str) -> bool:
    return _module_status(module_name)[0]


def _module_unavailable_reason(module_name: str) -> str:
    return _module_status(module_name)[1]


@lru_cache(maxsize=None)
def _module_status(module_name: str) -> tuple[bool, str]:
    try:
        _prepare_native_dependency_paths()
        if module_name == "torch":
            importlib.import_module("torch")
            importlib.import_module("torch._C")
            return True, ""
        if module_name == "torchaudio":
            _prepare_torchaudio_legacy_api()
            importlib.import_module("torchaudio")
            return True, ""
        if module_name in IMPORT_PROBE_MODULES:
            importlib.import_module(module_name)
            return True, ""
        available = importlib.util.find_spec(module_name) is not None
        return available, "" if available else "module spec not found"
    except (ModuleNotFoundError, ValueError):
        return False, _format_module_import_error(sys.exc_info()[1])
    except Exception as exc:
        return False, _format_module_import_error(exc)


def _module_available(module_name: str) -> bool:
    if module_name == "silero_vad":
        return _maybe_module_available(module_name)
    return _maybe_module_available(module_name)


@lru_cache(maxsize=1)
def _prepare_native_dependency_paths() -> None:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(Path(meipass))
        roots.append(Path(sys.executable).resolve().parent / "_internal")
    roots.append(Path(sys.prefix))

    add_dll_directory = getattr(os, "add_dll_directory", None)
    for root in roots:
        for dll_directory in (root / "torch" / "lib", root / "torchaudio" / "lib", root):
            if dll_directory.exists() and add_dll_directory is not None:
                with contextlib.suppress(OSError):
                    _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(dll_directory)))


def _format_module_import_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown import error"
    text = f"{type(exc).__name__}: {exc}"
    if "torch._C" in text:
        return f"{text}. {TORCH_NATIVE_RUNTIME_HINT}"
    return text


@lru_cache(maxsize=1)
def _prepare_torchaudio_legacy_api() -> None:
    try:
        os.environ.setdefault("MPLCONFIGDIR", str(_model_cache_root() / "matplotlib"))
        Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
        import soundfile as sf  # type: ignore
        import torch  # type: ignore
        import torchaudio  # type: ignore
    except Exception:
        return

    if not hasattr(torchaudio, "AudioMetaData"):
        torchaudio.AudioMetaData = namedtuple(  # type: ignore[attr-defined]
            "AudioMetaData",
            ["sample_rate", "num_frames", "num_channels", "bits_per_sample", "encoding"],
        )

    def info(uri: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        meta = sf.info(uri)
        return torchaudio.AudioMetaData(  # type: ignore[attr-defined]
            sample_rate=int(meta.samplerate),
            num_frames=int(meta.frames),
            num_channels=int(meta.channels),
            bits_per_sample=0,
            encoding=str(meta.subtype or meta.format or "UNKNOWN"),
        )

    def load(
        uri: Any,
        frame_offset: int = 0,
        num_frames: int = -1,
        normalize: bool = True,
        channels_first: bool = True,
        format: str | None = None,
        buffer_size: int = 4096,
        backend: str | None = None,
    ) -> tuple[Any, int]:
        del normalize, format, buffer_size, backend
        stop = -1 if num_frames is None or num_frames < 0 else int(frame_offset) + int(num_frames)
        data, sample_rate = sf.read(
            uri,
            start=int(frame_offset or 0),
            stop=stop,
            dtype="float32",
            always_2d=True,
        )
        tensor = torch.from_numpy(data)
        if channels_first:
            tensor = tensor.transpose(0, 1)
        return tensor, int(sample_rate)

    def save(
        uri: Any,
        src: Any,
        sample_rate: int,
        channels_first: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        del args, kwargs
        data = src.detach().cpu()
        if channels_first and data.ndim == 2:
            data = data.transpose(0, 1)
        sf.write(uri, data.numpy(), int(sample_rate))

    if not hasattr(torchaudio, "info"):
        torchaudio.info = info  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "get_audio_backend"):
        torchaudio.get_audio_backend = lambda: "soundfile"  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "set_audio_backend"):
        torchaudio.set_audio_backend = lambda backend=None: None  # type: ignore[attr-defined]

    try:
        import torchcodec  # type: ignore  # noqa: F401
    except Exception:
        torchaudio.load = load  # type: ignore[assignment]
        torchaudio.save = save  # type: ignore[assignment]


def _vad_coverage(vad_segments: Sequence[tuple[float, float]], duration_seconds: float) -> float | None:
    if duration_seconds <= 0:
        return None

    covered = sum(max(end - start, 0.0) for start, end in vad_segments)
    return min(max(covered / duration_seconds, 0.0), 1.0)


def _unit_timings_from_aligned_chars(segment: dict[str, Any]) -> tuple[VoiceUnitTiming, ...]:
    text = str(segment.get("text", "") or "")
    char_entries = _aligned_char_entries(segment)
    if not text.strip() or not char_entries:
        return ()

    timings: list[VoiceUnitTiming] = []
    for position, (unit, start_index, end_index) in enumerate(_timeline_unit_spans(text)):
        entries = char_entries[start_index:end_index]
        aligned_entries = [
            entry
            for entry in entries
            if _optional_float(entry.get("start")) is not None and _optional_float(entry.get("end")) is not None
        ]
        if not aligned_entries:
            continue
        start_seconds = min(float(entry["start"]) for entry in aligned_entries)
        end_seconds = max(float(entry["end"]) for entry in aligned_entries)
        if end_seconds <= start_seconds:
            continue
        scores = [_optional_float(entry.get("score")) for entry in aligned_entries]
        scores = [score for score in scores if score is not None]
        timings.append(
            VoiceUnitTiming(
                position=position,
                unit=unit,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                confidence=(sum(scores) / len(scores)) if scores else None,
                timing_source="whisperx_char_alignment",
            )
        )
    return tuple(timings)


def _aligned_char_entries(segment: dict[str, Any]) -> list[dict[str, Any]]:
    raw_chars = segment.get("chars", [])
    if not isinstance(raw_chars, list):
        return []
    return [entry for entry in raw_chars if isinstance(entry, dict)]


def _segment_confidence(segment: dict[str, Any]) -> float | None:
    confidence = segment.get("confidence")
    try:
        return None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        return None


def _notify_progress(callback: ProgressCallback | None, progress: float, message: str) -> None:
    if callback is not None:
        callback(progress, message)


def _raise_if_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise AudioProcessorError("Processing cancelled")

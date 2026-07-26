from __future__ import annotations

import importlib.util
import math
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    repository_url: str
    pipeline_stage: str
    role: str
    optional_dependency: str
    install_hint: str
    notes: str


@dataclass(frozen=True)
class VoiceSegment:
    start_seconds: float
    end_seconds: float
    text: str = ""
    speaker_id: str | None = None
    confidence: float | None = None

    @property
    def duration_seconds(self) -> float:
        return max(self.end_seconds - self.start_seconds, 0.0)


@dataclass(frozen=True)
class MaterialAnalysis:
    path: Path
    transcript: str = ""
    filename_text: str = ""
    voice_segments: tuple[VoiceSegment, ...] = ()
    speaker_label: str | None = None
    embedding_ref: str | None = None
    duration_seconds: float | None = None
    speaker_embedding: tuple[float, ...] | None = None
    vad_coverage: float | None = None
    analysis_source: str = ""


@dataclass(frozen=True)
class MaterialOrderDecision:
    rank: int
    material_path: Path
    score: float
    reference_text: str
    material_text: str
    reason: str
    transcript_score: float = 0.0
    filename_score: float = 0.0
    duration_score: float = 0.0
    speaker_score: float = 0.0
    vad_score: float = 0.0


OPEN_SOURCE_MODEL_CANDIDATES: tuple[ModelCandidate, ...] = (
    ModelCandidate(
        name="Demucs",
        repository_url="https://github.com/facebookresearch/demucs",
        pipeline_stage="source_separation",
        role="Separate vocals from full-mix reference songs before alignment.",
        optional_dependency="demucs",
        install_hint="pip install demucs",
        notes="Useful when the original audio is a mixed song instead of isolated vocals.",
    ),
    ModelCandidate(
        name="UVR Headless Runner",
        repository_url="https://github.com/chyinan/uvr-headless-runner",
        pipeline_stage="source_separation",
        role="Run UVR-style MDX, Demucs, or VR separation models in an isolated Python 3.10 worker.",
        optional_dependency="uvr-headless-runner",
        install_hint="powershell -ExecutionPolicy Bypass -File scripts\\bootstrap_uvr_worker.ps1",
        notes="Kept outside the main Python 3.11 environment because the package targets Python versions below 3.11.",
    ),
    ModelCandidate(
        name="Silero VAD",
        repository_url="https://github.com/snakers4/silero-vad",
        pipeline_stage="voice_activity_detection",
        role="Detect speech/vocal regions quickly before ASR and matching.",
        optional_dependency="torch",
        install_hint="pip install torch torchaudio onnxruntime",
        notes="Lightweight VAD candidate for CPU-first local analysis.",
    ),
    ModelCandidate(
        name="pyannote.audio",
        repository_url="https://github.com/pyannote/pyannote-audio",
        pipeline_stage="diarization_and_speaker_embedding",
        role="Find who speaks when and extract speaker-related features.",
        optional_dependency="pyannote.audio",
        install_hint="pip install pyannote.audio",
        notes="May require accepting model terms and providing a Hugging Face token for pretrained pipelines.",
    ),
    ModelCandidate(
        name="WhisperX",
        repository_url="https://github.com/m-bain/whisperX",
        pipeline_stage="asr_alignment",
        role="Transcribe vocals and produce word-level timestamps for timeline matching.",
        optional_dependency="whisperx",
        install_hint="pip install whisperx",
        notes="Best fit for matching material text to reference vocal timing.",
    ),
    ModelCandidate(
        name="Faster Whisper",
        repository_url="https://github.com/SYSTRAN/faster-whisper",
        pipeline_stage="asr",
        role="Run Whisper-style transcription through CTranslate2 for faster local ASR.",
        optional_dependency="faster_whisper",
        install_hint="pip install faster-whisper",
        notes="Preferred accelerated ASR backend when installed; falls back to OpenAI Whisper if unavailable.",
    ),
    ModelCandidate(
        name="OpenAI Whisper",
        repository_url="https://github.com/openai/whisper",
        pipeline_stage="asr",
        role="Fallback ASR for transcription when word-level forced alignment is not required.",
        optional_dependency="whisper",
        install_hint="pip install -U openai-whisper",
        notes="General-purpose multilingual speech recognition model.",
    ),
    ModelCandidate(
        name="whisper.cpp",
        repository_url="https://github.com/ggerganov/whisper.cpp",
        pipeline_stage="asr",
        role="Potential future native/offline ASR runtime for smaller CPU-first packages.",
        optional_dependency="whisper_cpp",
        install_hint="install a whisper.cpp binary or Python binding",
        notes="Not called by the current Python pipeline; tracked as a candidate for smaller non-Python ASR builds.",
    ),
    ModelCandidate(
        name="SpeechBrain",
        repository_url="https://github.com/speechbrain/speechbrain",
        pipeline_stage="speaker_similarity",
        role="Score material clips by voice/speaker similarity using speaker-recognition models.",
        optional_dependency="speechbrain",
        install_hint="pip install speechbrain",
        notes="Candidate backend for ECAPA-TDNN style speaker embeddings.",
    ),
    ModelCandidate(
        name="Librosa",
        repository_url="https://github.com/librosa/librosa",
        pipeline_stage="music_structure",
        role="Estimate repeated sections and self-similar regions for future safe reuse planning.",
        optional_dependency="librosa",
        install_hint="pip install librosa",
        notes="Useful for non-destructive structure analysis; not a replacement for ASR matching.",
    ),
    ModelCandidate(
        name="MSAF",
        repository_url="https://github.com/urinieto/msaf",
        pipeline_stage="music_structure",
        role="Candidate framework for music structural segmentation such as verse/chorus boundaries.",
        optional_dependency="msaf",
        install_hint="pip install msaf",
        notes="Potential future section detector; kept optional because it increases dependency weight.",
    ),
)


def list_model_candidates() -> list[dict[str, str]]:
    return [asdict(candidate) for candidate in OPEN_SOURCE_MODEL_CANDIDATES]


def build_model_assisted_pipeline_plan() -> dict[str, object]:
    return {
        "format": "vocal_process_model_pipeline_plan_v1",
        "goal": "Use open-source speech/audio models to make vocal recognition, ordering, and DAW timeline assembly meaningful.",
        "stages": [
            {
                "stage": "source_separation",
                "input": "original/reference audio",
                "output": "isolated reference vocal stem when needed",
                "candidate_models": ["UVR Headless Runner", "Demucs"],
            },
            {
                "stage": "voice_activity_detection",
                "input": "reference vocals and material clips",
                "output": "speech/vocal time ranges",
                "candidate_models": ["Silero VAD", "pyannote.audio"],
            },
            {
                "stage": "asr_alignment",
                "input": "vocal ranges",
                "output": "transcript segments with timestamps",
                "candidate_models": ["Faster Whisper", "WhisperX", "OpenAI Whisper", "whisper.cpp"],
            },
            {
                "stage": "speaker_similarity",
                "input": "reference vocal segments and material vocal segments",
                "output": "voice similarity scores",
                "candidate_models": ["SpeechBrain", "pyannote.audio"],
            },
            {
                "stage": "ordering_and_timeline",
                "input": "transcript, timing, and similarity scores",
                "output": "ordered material clips with start times for WAV/RPP export",
                "candidate_models": ["VocalProcess internal planner"],
            },
            {
                "stage": "music_structure",
                "input": "reference audio and transcript timings",
                "output": "safe repeated-section hints for caching and manual review",
                "candidate_models": ["Librosa", "MSAF", "VocalProcess internal repetition planner"],
            },
        ],
        "candidates": list_model_candidates(),
    }


def check_optional_backend(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def backend_availability() -> dict[str, bool]:
    return {
        candidate.name: check_optional_backend(candidate.optional_dependency)
        for candidate in OPEN_SOURCE_MODEL_CANDIDATES
    }


def order_materials_for_reference(
    reference_segments: Sequence[VoiceSegment],
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None = None,
) -> list[MaterialOrderDecision]:
    if not materials:
        return []

    usable_reference = [segment for segment in reference_segments if segment.text.strip()]
    usable_materials = [material for material in materials if _material_search_text(material).strip()]
    if not usable_reference or not usable_materials:
        return _filename_order_decisions(materials, reason="filename_fallback")

    reference_duration_hint = _reference_duration_hint(usable_reference)
    if len(usable_reference) < len(usable_materials):
        return _order_materials_by_reference_text(
            " ".join(segment.text for segment in usable_reference),
            materials,
            reference_embedding=reference_embedding,
            reference_duration_hint=reference_duration_hint,
        )

    remaining = list(materials)
    decisions: list[MaterialOrderDecision] = []
    for reference_segment in usable_reference:
        if not remaining:
            break

        best_index = 0
        best_score = -1.0
        best_transcript_score = 0.0
        best_filename_score = 0.0
        best_duration_score = 0.0
        best_speaker_score = 0.0
        best_vad_score = 0.0
        for index, material in enumerate(remaining):
            transcript_score = text_similarity(reference_segment.text, material.transcript)
            filename_score = text_similarity(reference_segment.text, material.filename_text)
            duration_score = _duration_similarity(
                reference_segment.duration_seconds or reference_duration_hint,
                material.duration_seconds,
            )
            speaker_score = _speaker_similarity(reference_segment, material, reference_embedding=reference_embedding)
            vad_score = material.vad_coverage or 0.0
            score = _candidate_score(
                transcript_score=transcript_score,
                filename_score=filename_score,
                duration_score=duration_score,
                speaker_score=speaker_score,
                vad_score=vad_score,
                reference_text=reference_segment.text,
            )
            if score > best_score:
                best_index = index
                best_score = score
                best_transcript_score = transcript_score
                best_filename_score = filename_score
                best_duration_score = duration_score
                best_speaker_score = speaker_score
                best_vad_score = vad_score

        selected = remaining.pop(best_index)
        decisions.append(
            MaterialOrderDecision(
                rank=len(decisions) + 1,
                material_path=selected.path,
                score=max(best_score, 0.0),
                reference_text=reference_segment.text,
                material_text=_material_display_text(selected),
                reason=_reason_for_scores(
                    reference_segment.text,
                    transcript_score=best_transcript_score,
                    filename_score=best_filename_score,
                    duration_score=best_duration_score,
                ),
                transcript_score=best_transcript_score,
                filename_score=best_filename_score,
                duration_score=best_duration_score,
                speaker_score=best_speaker_score,
                vad_score=best_vad_score,
            )
        )

    for material in sorted(remaining, key=lambda item: item.path.name.lower()):
        decisions.append(
            MaterialOrderDecision(
                rank=len(decisions) + 1,
                material_path=material.path,
                score=0.0,
                reference_text="",
                material_text=_material_display_text(material),
                reason="unmatched_filename_fallback",
            )
        )

    return decisions


def text_similarity(left: str, right: str) -> float:
    left_features = _text_features(left)
    right_features = _text_features(right)
    if not left_features or not right_features:
        return 0.0

    overlap = len(left_features & right_features)
    union = len(left_features | right_features)
    jaccard = overlap / union if union else 0.0
    left_compact = _compact_text(left)
    right_compact = _compact_text(right)
    if not left_compact or not right_compact:
        return jaccard
    if left_compact == right_compact:
        return 1.0
    sequence_score = SequenceMatcher(None, left_compact, right_compact).ratio()
    return max(jaccard, (0.6 * sequence_score) + (0.4 * jaccard))


def _order_materials_by_reference_text(
    reference_text: str,
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None,
    reference_duration_hint: float,
) -> list[MaterialOrderDecision]:
    scored: list[tuple[int, float, str, MaterialOrderDecision]] = []
    for material in materials:
        transcript_score = text_similarity(reference_text, material.transcript)
        filename_score = text_similarity(reference_text, material.filename_text)
        duration_score = _duration_similarity(reference_duration_hint, material.duration_seconds)
        speaker_score = _speaker_similarity(
            VoiceSegment(0.0, 0.0, reference_text),
            material,
            reference_embedding=reference_embedding,
        )
        vad_score = material.vad_coverage or 0.0
        score = _candidate_score(
            transcript_score=transcript_score,
            filename_score=filename_score,
            duration_score=duration_score,
            speaker_score=speaker_score,
            vad_score=vad_score,
            reference_text=reference_text,
        )
        position = _material_text_position(reference_text, material)
        fallback_position = 10**9
        scored.append(
            (
                position if position is not None else fallback_position,
                -score,
                material.path.name.lower(),
                MaterialOrderDecision(
                    rank=0,
                    material_path=material.path,
                    score=max(score, 0.0),
                    reference_text=reference_text,
                    material_text=_material_display_text(material),
                    reason=_reason_for_scores(
                        reference_text,
                        transcript_score=transcript_score,
                        filename_score=filename_score,
                        duration_score=duration_score,
                        position=position,
                    ),
                    transcript_score=transcript_score,
                    filename_score=filename_score,
                    duration_score=duration_score,
                    speaker_score=speaker_score,
                    vad_score=vad_score,
                ),
            )
        )

    decisions: list[MaterialOrderDecision] = []
    for rank, (_, _, _, decision) in enumerate(sorted(scored, key=lambda item: item[:3]), start=1):
        decisions.append(
            MaterialOrderDecision(
                rank=rank,
                material_path=decision.material_path,
                score=decision.score,
                reference_text=decision.reference_text,
                material_text=decision.material_text,
                reason=decision.reason,
                transcript_score=decision.transcript_score,
                filename_score=decision.filename_score,
                duration_score=decision.duration_score,
                speaker_score=decision.speaker_score,
                vad_score=decision.vad_score,
            )
        )
    return decisions


def _text_position(reference_text: str, material_text: str) -> int | None:
    reference = _compact_text(reference_text)
    material = _compact_text(material_text)
    if not reference or not material:
        return None

    exact = reference.find(material)
    if exact >= 0:
        return exact

    positions = [
        reference.find(unit)
        for unit in _text_units(material_text)
        if unit and reference.find(unit) >= 0
    ]
    return min(positions) if positions else None


def _material_text_similarity(reference_text: str, material: MaterialAnalysis) -> float:
    transcript_score = text_similarity(reference_text, material.transcript)
    filename_score = text_similarity(reference_text, material.filename_text)
    if transcript_score <= 0:
        return filename_score * 0.95
    if filename_score <= 0:
        return transcript_score
    blended = (0.72 * transcript_score) + (0.28 * filename_score)
    return max(transcript_score, blended, filename_score * 0.95)


def _material_text_position(reference_text: str, material: MaterialAnalysis) -> int | None:
    positions = [
        position
        for position in (
            _text_position(reference_text, material.transcript),
            _text_position(reference_text, material.filename_text),
        )
        if position is not None
    ]
    return min(positions) if positions else None


def _material_search_text(material: MaterialAnalysis) -> str:
    return " ".join(part for part in (material.transcript, material.filename_text) if part.strip())


def _material_display_text(material: MaterialAnalysis) -> str:
    if material.transcript.strip() and material.filename_text.strip():
        return f"{material.transcript} | filename: {material.filename_text}"
    return _material_search_text(material)


def _filename_order_decisions(
    materials: Sequence[MaterialAnalysis],
    *,
    reason: str,
) -> list[MaterialOrderDecision]:
    decisions: list[MaterialOrderDecision] = []
    for material in sorted(materials, key=lambda item: item.path.name.lower()):
        decisions.append(
            MaterialOrderDecision(
                rank=len(decisions) + 1,
                material_path=material.path,
                score=0.0,
                reference_text="",
                material_text=_material_display_text(material),
                reason=reason,
                transcript_score=0.0,
                filename_score=0.0,
                duration_score=0.0,
                speaker_score=0.0,
                vad_score=0.0,
            )
        )
    return decisions


def _text_features(text: str) -> set[str]:
    units = _text_units(text)
    if not units:
        return set()

    features = set(units)
    compact = "".join(units)
    if len(compact) > 1:
        features.update(compact[index : index + 2] for index in range(len(compact) - 1))
    return features


def _text_units(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())


def _compact_text(text: str) -> str:
    return "".join(_text_units(text))


def _speaker_similarity(
    reference_segment: VoiceSegment,
    material: MaterialAnalysis,
    *,
    reference_embedding: tuple[float, ...] | None = None,
) -> float:
    if material.speaker_embedding is None:
        return 0.0

    reference_hint = reference_segment.speaker_id
    if reference_hint is not None and material.speaker_label is not None and reference_hint == material.speaker_label:
        return 1.0

    if reference_embedding is None:
        return 0.0

    if reference_segment.confidence is not None and reference_segment.confidence <= 0:
        return 0.0

    return _cosine_similarity(reference_embedding, material.speaker_embedding)


def _reference_duration_hint(reference_segments: Sequence[VoiceSegment]) -> float:
    durations = [segment.duration_seconds for segment in reference_segments if segment.duration_seconds > 0]
    if not durations:
        return 0.0
    return sum(durations) / len(durations)


def _duration_similarity(reference_seconds: float, material_seconds: float | None) -> float:
    if reference_seconds <= 0 or material_seconds is None or material_seconds <= 0:
        return 0.0

    ratio = material_seconds / reference_seconds
    if ratio <= 0:
        return 0.0

    distance = abs(math.log(ratio))
    return max(min(math.exp(-distance), 1.0), 0.0)


def _candidate_score(
    *,
    transcript_score: float,
    filename_score: float,
    duration_score: float,
    speaker_score: float,
    vad_score: float,
    reference_text: str,
) -> float:
    compact_reference = _compact_text(reference_text)
    if len(compact_reference) <= 4:
        weights = {
            "transcript": 0.38,
            "filename": 0.24,
            "duration": 0.22,
            "speaker": 0.08,
            "vad": 0.08,
        }
    else:
        weights = {
            "transcript": 0.52,
            "filename": 0.18,
            "duration": 0.12,
            "speaker": 0.08,
            "vad": 0.10,
        }

    return (
        (weights["transcript"] * transcript_score)
        + (weights["filename"] * filename_score)
        + (weights["duration"] * duration_score)
        + (weights["speaker"] * speaker_score)
        + (weights["vad"] * vad_score)
    )


def _reason_for_scores(
    reference_text: str,
    *,
    transcript_score: float,
    filename_score: float,
    duration_score: float,
    position: int | None = None,
) -> str:
    if position is not None:
        return "reference_text_position"

    compact_reference = _compact_text(reference_text)
    if len(compact_reference) <= 4 and duration_score >= max(transcript_score, filename_score):
        return "duration_similarity"
    if filename_score > transcript_score:
        return "filename_similarity"
    return "transcript_similarity"


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0

    dot_product = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = sum(float(a) * float(a) for a in left) ** 0.5
    right_norm = sum(float(b) * float(b) for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    score = dot_product / (left_norm * right_norm)
    return max(min(score, 1.0), -1.0)

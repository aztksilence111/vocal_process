from __future__ import annotations

import importlib.util
import re
from dataclasses import asdict, dataclass
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
    voice_segments: tuple[VoiceSegment, ...] = ()
    speaker_label: str | None = None
    embedding_ref: str | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class MaterialOrderDecision:
    rank: int
    material_path: Path
    score: float
    reference_text: str
    material_text: str
    reason: str


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
        name="OpenAI Whisper",
        repository_url="https://github.com/openai/whisper",
        pipeline_stage="asr",
        role="Fallback ASR for transcription when word-level forced alignment is not required.",
        optional_dependency="whisper",
        install_hint="pip install -U openai-whisper",
        notes="General-purpose multilingual speech recognition model.",
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
                "candidate_models": ["Demucs"],
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
                "candidate_models": ["WhisperX", "OpenAI Whisper"],
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
) -> list[MaterialOrderDecision]:
    if not materials:
        return []

    usable_reference = [segment for segment in reference_segments if segment.text.strip()]
    usable_materials = [material for material in materials if material.transcript.strip()]
    if not usable_reference or not usable_materials:
        return _filename_order_decisions(materials, reason="filename_fallback")

    remaining = list(materials)
    decisions: list[MaterialOrderDecision] = []
    for reference_segment in usable_reference:
        if not remaining:
            break

        best_index = 0
        best_score = -1.0
        for index, material in enumerate(remaining):
            score = text_similarity(reference_segment.text, material.transcript)
            if score > best_score:
                best_index = index
                best_score = score

        selected = remaining.pop(best_index)
        decisions.append(
            MaterialOrderDecision(
                rank=len(decisions) + 1,
                material_path=selected.path,
                score=max(best_score, 0.0),
                reference_text=reference_segment.text,
                material_text=selected.transcript,
                reason="transcript_similarity",
            )
        )

    for material in sorted(remaining, key=lambda item: item.path.name.lower()):
        decisions.append(
            MaterialOrderDecision(
                rank=len(decisions) + 1,
                material_path=material.path,
                score=0.0,
                reference_text="",
                material_text=material.transcript,
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
    return overlap / union if union else 0.0


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
                material_text=material.transcript,
                reason=reason,
            )
        )
    return decisions


def _text_features(text: str) -> set[str]:
    units = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
    if not units:
        return set()

    features = set(units)
    compact = "".join(units)
    if len(compact) > 1:
        features.update(compact[index : index + 2] for index in range(len(compact) - 1))
    return features

from __future__ import annotations

import importlib.util
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence


KANA_PATTERN = re.compile(r"^[\u3040-\u30ff\u31f0-\u31ff]+$")


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
class VoiceUnitTiming:
    position: int
    unit: str
    start_seconds: float
    end_seconds: float
    confidence: float | None = None
    timing_source: str = ""

    @property
    def duration_seconds(self) -> float:
        return max(self.end_seconds - self.start_seconds, 0.0)


@dataclass(frozen=True)
class VoiceSegment:
    start_seconds: float
    end_seconds: float
    text: str = ""
    speaker_id: str | None = None
    confidence: float | None = None
    timing_source: str = ""
    unit_timings: tuple[VoiceUnitTiming, ...] = ()

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
    phonetic_score: float = 0.0
    evidence_count: int = 0
    confidence_label: str = "low"
    reference_segment_index: int | None = None
    text_position: int | None = None
    phonetic_position: int | None = None
    phonetic_position_count: int = 0
    phonetic_span_units: int = 0
    phonetic_tone_score: float = 0.0
    phonetic_tone_position: int | None = None
    phonetic_tone_position_count: int = 0


@dataclass(frozen=True)
class MaterialScoreBreakdown:
    reference_index: int
    material_index: int
    material_path: Path
    reference_text: str
    material_text: str
    score: float
    transcript_score: float
    filename_score: float
    phonetic_score: float
    duration_score: float
    speaker_score: float
    vad_score: float
    evidence_count: int
    confidence_label: str
    reason: str
    short_reference: bool
    text_position: int | None = None
    phonetic_position: int | None = None
    phonetic_position_count: int = 0
    reference_phonetic_units: tuple[str, ...] = ()
    material_phonetic_units: tuple[str, ...] = ()
    reference_phonetic_tone_units: tuple[str, ...] = ()
    material_phonetic_tone_units: tuple[str, ...] = ()
    phonetic_span_units: int = 0
    phonetic_tone_score: float = 0.0
    phonetic_tone_position: int | None = None
    phonetic_tone_position_count: int = 0


@dataclass(frozen=True)
class _PhoneticMatch:
    position: int | None = None
    position_count: int = 0
    span_units: int = 0
    tone_position: int | None = None
    tone_position_count: int = 0
    tone_span_units: int = 0
    tone_score: float = 0.0


@dataclass(frozen=True)
class _RawPhoneticMatch:
    positions: tuple[int, ...] = ()
    span_units: int = 0
    tone_positions: tuple[int, ...] = ()
    tone_span_units: int = 0


@dataclass(frozen=True)
class MaterialOrderingPlan:
    decisions: tuple[MaterialOrderDecision, ...]
    score_matrix: tuple[tuple[MaterialScoreBreakdown, ...], ...]
    strategy: str


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
    return list(
        plan_material_ordering(
            reference_segments,
            materials,
            reference_embedding=reference_embedding,
        ).decisions
    )


def plan_material_ordering(
    reference_segments: Sequence[VoiceSegment],
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None = None,
) -> MaterialOrderingPlan:
    if not materials:
        return MaterialOrderingPlan(decisions=(), score_matrix=(), strategy="empty")

    usable_reference = [segment for segment in reference_segments if segment.text.strip()]
    usable_materials = [material for material in materials if _material_search_text(material).strip()]
    if not usable_reference or not usable_materials:
        return MaterialOrderingPlan(
            decisions=tuple(_filename_order_decisions(materials, reason="filename_fallback")),
            score_matrix=(),
            strategy="filename_fallback",
        )

    reference_duration_hint = _reference_duration_hint(usable_reference)
    score_matrix = _build_score_matrix(
        usable_reference,
        materials,
        reference_embedding=reference_embedding,
        reference_duration_hint=reference_duration_hint,
    )
    if len(usable_reference) < len(usable_materials):
        return MaterialOrderingPlan(
            decisions=tuple(
                _order_materials_by_reference_text(
                    usable_reference,
                    materials,
                    reference_embedding=reference_embedding,
                    reference_duration_hint=reference_duration_hint,
                )
            ),
            score_matrix=score_matrix,
            strategy="reference_text_position",
        )

    return MaterialOrderingPlan(
        decisions=tuple(_global_assignment_decisions(usable_reference, materials, score_matrix)),
        score_matrix=score_matrix,
        strategy="global_assignment",
    )


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


def phonetic_similarity(left: str, right: str) -> float:
    left_phonetic = _phonetic_text(left)
    right_phonetic = _phonetic_text(right)
    if not left_phonetic or not right_phonetic:
        return 0.0
    return text_similarity(left_phonetic, right_phonetic)


def _order_materials_by_reference_text(
    reference_segments: Sequence[VoiceSegment],
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None,
    reference_duration_hint: float,
) -> list[MaterialOrderDecision]:
    reference_text = " ".join(segment.text for segment in reference_segments)
    aggregate_reference = VoiceSegment(0.0, reference_duration_hint, reference_text)
    scored: list[tuple[int, float, str, MaterialScoreBreakdown]] = []
    for material_index, material in enumerate(materials):
        score = _score_material_against_reference(
            0,
            material_index,
            aggregate_reference,
            material,
            reference_embedding=reference_embedding,
            reference_duration_hint=reference_duration_hint,
            allow_position_reason=True,
        )
        position = _score_reference_position(score)
        fallback_position = 10**9
        scored.append(
            (
                position if position is not None else fallback_position,
                -score.score,
                material.path.name.lower(),
                score,
            )
        )

    decisions: list[MaterialOrderDecision] = []
    for rank, (_, _, _, score) in enumerate(sorted(scored, key=lambda item: item[:3]), start=1):
        decisions.append(_localize_aggregate_decision(_decision_from_score(rank, score), reference_segments))
    return decisions


def render_ordering_score_matrix(plan: MaterialOrderingPlan) -> list[list[dict[str, object]]]:
    return [
        [
            {
                "reference_index": score.reference_index,
                "material_index": score.material_index,
                "material_path": str(score.material_path),
                "reference_text": score.reference_text,
                "material_text": score.material_text,
                "score": score.score,
                "transcript_score": score.transcript_score,
                "filename_score": score.filename_score,
                "phonetic_score": score.phonetic_score,
                "duration_score": score.duration_score,
                "speaker_score": score.speaker_score,
                "vad_score": score.vad_score,
                "evidence_count": score.evidence_count,
                "confidence_label": score.confidence_label,
                "reason": score.reason,
                "short_reference": score.short_reference,
                "text_position": score.text_position,
                "phonetic_position": score.phonetic_position,
                "phonetic_position_count": score.phonetic_position_count,
                "reference_phonetic_units": list(score.reference_phonetic_units),
                "material_phonetic_units": list(score.material_phonetic_units),
                "reference_phonetic_tone_units": list(score.reference_phonetic_tone_units),
                "material_phonetic_tone_units": list(score.material_phonetic_tone_units),
                "phonetic_span_units": score.phonetic_span_units,
                "phonetic_tone_score": score.phonetic_tone_score,
                "phonetic_tone_position": score.phonetic_tone_position,
                "phonetic_tone_position_count": score.phonetic_tone_position_count,
            }
            for score in row
        ]
        for row in plan.score_matrix
    ]


def _build_score_matrix(
    reference_segments: Sequence[VoiceSegment],
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None,
    reference_duration_hint: float,
) -> tuple[tuple[MaterialScoreBreakdown, ...], ...]:
    return tuple(
        tuple(
            _score_material_against_reference(
                reference_index,
                material_index,
                reference_segment,
                material,
                reference_embedding=reference_embedding,
                reference_duration_hint=reference_duration_hint,
            )
            for material_index, material in enumerate(materials)
        )
        for reference_index, reference_segment in enumerate(reference_segments)
    )


def _score_material_against_reference(
    reference_index: int,
    material_index: int,
    reference_segment: VoiceSegment,
    material: MaterialAnalysis,
    *,
    reference_embedding: tuple[float, ...] | None,
    reference_duration_hint: float,
    allow_position_reason: bool = False,
) -> MaterialScoreBreakdown:
    reference_duration = reference_segment.duration_seconds or reference_duration_hint
    transcript_score = text_similarity(reference_segment.text, material.transcript)
    filename_score = text_similarity(reference_segment.text, material.filename_text)
    phonetic_match = _material_phonetic_match(reference_segment.text, material)
    phonetic_score = max(
        phonetic_similarity(reference_segment.text, _material_search_text(material)),
        _material_phonetic_position_score(reference_segment.text, material, phonetic_match=phonetic_match),
    )
    if phonetic_match.tone_score > 0:
        phonetic_score = max(phonetic_score, phonetic_match.tone_score)
    duration_score = _duration_similarity(reference_duration, material.duration_seconds)
    speaker_score = max(_speaker_similarity(reference_segment, material, reference_embedding=reference_embedding), 0.0)
    vad_score = material.vad_coverage or 0.0
    text_position = _material_text_position(reference_segment.text, material)
    phonetic_position = phonetic_match.position
    phonetic_position_count = phonetic_match.position_count
    reference_position = text_position if text_position is not None else phonetic_position
    score = _candidate_score(
        transcript_score=transcript_score,
        filename_score=filename_score,
        phonetic_score=phonetic_score,
        duration_score=duration_score,
        speaker_score=speaker_score,
        vad_score=vad_score,
        reference_text=reference_segment.text,
    )
    evidence_count = _evidence_count(
        transcript_score=transcript_score,
        filename_score=filename_score,
        phonetic_score=phonetic_score,
        duration_score=duration_score,
        speaker_score=speaker_score,
        vad_score=vad_score,
    )
    short_reference = _is_short_reference_text(reference_segment.text)
    confidence_label = _confidence_label(score, evidence_count=evidence_count, short_reference=short_reference)
    reason = _reason_for_scores(
        reference_segment.text,
        transcript_score=transcript_score,
        filename_score=filename_score,
        phonetic_score=phonetic_score,
        duration_score=duration_score,
        position=reference_position if allow_position_reason else None,
    )
    return MaterialScoreBreakdown(
        reference_index=reference_index,
        material_index=material_index,
        material_path=material.path,
        reference_text=reference_segment.text,
        material_text=_material_display_text(material),
        score=max(score, 0.0),
        transcript_score=transcript_score,
        filename_score=filename_score,
        phonetic_score=phonetic_score,
        duration_score=duration_score,
        speaker_score=speaker_score,
        vad_score=vad_score,
        evidence_count=evidence_count,
        confidence_label=confidence_label,
        reason=reason,
        short_reference=short_reference,
        text_position=text_position,
        phonetic_position=phonetic_position,
        phonetic_position_count=phonetic_position_count,
        reference_phonetic_units=tuple(_phonetic_units(reference_segment.text)),
        material_phonetic_units=tuple(_phonetic_units(_material_search_text(material))),
        reference_phonetic_tone_units=tuple(_phonetic_tone_units(reference_segment.text)),
        material_phonetic_tone_units=tuple(_phonetic_tone_units(_material_search_text(material))),
        phonetic_span_units=phonetic_match.span_units,
        phonetic_tone_score=phonetic_match.tone_score,
        phonetic_tone_position=phonetic_match.tone_position,
        phonetic_tone_position_count=phonetic_match.tone_position_count,
    )


def _global_assignment_decisions(
    reference_segments: Sequence[VoiceSegment],
    materials: Sequence[MaterialAnalysis],
    score_matrix: Sequence[Sequence[MaterialScoreBreakdown]],
) -> list[MaterialOrderDecision]:
    if not score_matrix:
        return _filename_order_decisions(materials, reason="filename_fallback")

    material_count = len(materials)
    reference_count = len(reference_segments)
    if material_count > reference_count:
        return _order_materials_by_reference_text(
            reference_segments,
            materials,
            reference_embedding=None,
            reference_duration_hint=_reference_duration_hint(reference_segments),
        )

    assignment_scores = [
        [score_matrix[reference_index][material_index].score for reference_index in range(reference_count)]
        for material_index in range(material_count)
    ]
    assigned_reference_by_material = _max_weight_assignment(assignment_scores)
    scored_pairs: list[tuple[int, int, MaterialScoreBreakdown]] = []
    for material_index, reference_index in enumerate(assigned_reference_by_material):
        if reference_index < 0:
            continue
        scored_pairs.append((reference_index, material_index, score_matrix[reference_index][material_index]))

    decisions: list[MaterialOrderDecision] = []
    for rank, (_, _, score) in enumerate(sorted(scored_pairs, key=lambda item: (item[0], item[1])), start=1):
        decisions.append(_decision_from_score(rank, score))

    assigned_materials = {material_index for _, material_index, _ in scored_pairs}
    for material_index, material in enumerate(materials):
        if material_index in assigned_materials:
            continue
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


def _decision_from_score(rank: int, score: MaterialScoreBreakdown) -> MaterialOrderDecision:
    return MaterialOrderDecision(
        rank=rank,
        material_path=score.material_path,
        score=score.score,
        reference_text=score.reference_text,
        material_text=score.material_text,
        reason=score.reason,
        transcript_score=score.transcript_score,
        filename_score=score.filename_score,
        duration_score=score.duration_score,
        speaker_score=score.speaker_score,
        vad_score=score.vad_score,
        phonetic_score=score.phonetic_score,
        evidence_count=score.evidence_count,
        confidence_label=score.confidence_label,
        reference_segment_index=score.reference_index,
        text_position=score.text_position,
        phonetic_position=score.phonetic_position,
        phonetic_position_count=score.phonetic_position_count,
        phonetic_span_units=score.phonetic_span_units,
        phonetic_tone_score=score.phonetic_tone_score,
        phonetic_tone_position=score.phonetic_tone_position,
        phonetic_tone_position_count=score.phonetic_tone_position_count,
    )


def _score_reference_position(score: MaterialScoreBreakdown) -> int | None:
    if score.text_position is not None:
        return score.text_position
    return score.phonetic_position


@dataclass(frozen=True)
class _SegmentPosition:
    segment_index: int
    local_position: int


def _localize_aggregate_decision(
    decision: MaterialOrderDecision,
    reference_segments: Sequence[VoiceSegment],
) -> MaterialOrderDecision:
    text_position = (
        _map_compact_position_to_segment_unit(reference_segments, decision.text_position)
        if decision.text_position is not None
        else None
    )
    phonetic_position = (
        _map_phonetic_position_to_segment_unit(reference_segments, decision.phonetic_position)
        if decision.phonetic_position is not None
        else None
    )
    primary = text_position or phonetic_position
    if primary is None or not 0 <= primary.segment_index < len(reference_segments):
        return replace(
            decision,
            reference_segment_index=None,
            text_position=None,
            phonetic_position=None,
        )

    segment = reference_segments[primary.segment_index]
    return replace(
        decision,
        reference_text=segment.text,
        reference_segment_index=primary.segment_index,
        text_position=(
            text_position.local_position
            if text_position is not None and text_position.segment_index == primary.segment_index
            else None
        ),
        phonetic_position=(
            phonetic_position.local_position
            if phonetic_position is not None and phonetic_position.segment_index == primary.segment_index
            else None
        ),
    )


def _map_compact_position_to_segment_unit(
    reference_segments: Sequence[VoiceSegment],
    position: int | None,
) -> _SegmentPosition | None:
    if position is None or position < 0:
        return None

    cursor = 0
    for segment_index, segment in enumerate(reference_segments):
        compact_length = len(_compact_text(segment.text))
        if compact_length <= 0:
            continue
        if cursor <= position < cursor + compact_length:
            local_offset = position - cursor
            return _SegmentPosition(
                segment_index=segment_index,
                local_position=_unit_index_at_compact_offset(segment.text, local_offset),
            )
        cursor += compact_length
    return None


def _map_phonetic_position_to_segment_unit(
    reference_segments: Sequence[VoiceSegment],
    position: int | None,
) -> _SegmentPosition | None:
    if position is None or position < 0:
        return None

    cursor = 0
    for segment_index, segment in enumerate(reference_segments):
        unit_count = len(_phonetic_units(segment.text))
        if unit_count <= 0:
            continue
        if cursor <= position < cursor + unit_count:
            return _SegmentPosition(segment_index=segment_index, local_position=position - cursor)
        cursor += unit_count
    return None


def _unit_index_at_compact_offset(text: str, offset: int) -> int:
    cursor = 0
    for unit_index, unit in enumerate(_text_units(text)):
        compact_unit = _compact_text(unit)
        if not compact_unit:
            continue
        next_cursor = cursor + len(compact_unit)
        if cursor <= offset < next_cursor:
            return unit_index
        cursor = next_cursor
    return 0


def _max_weight_assignment(scores: Sequence[Sequence[float]]) -> list[int]:
    if not scores:
        return []
    row_count = len(scores)
    column_count = len(scores[0])
    if column_count == 0:
        return [-1 for _ in scores]
    if any(len(row) != column_count for row in scores):
        raise ValueError("assignment score matrix must be rectangular")
    if row_count > column_count:
        raise ValueError("assignment requires row_count <= column_count")

    max_score = max((score for row in scores for score in row), default=0.0)
    costs = [[max_score - score for score in row] for row in scores]
    potentials_row = [0.0 for _ in range(row_count + 1)]
    potentials_col = [0.0 for _ in range(column_count + 1)]
    matching = [0 for _ in range(column_count + 1)]
    previous = [0 for _ in range(column_count + 1)]

    for row in range(1, row_count + 1):
        matching[0] = row
        current_col = 0
        min_values = [math.inf for _ in range(column_count + 1)]
        used = [False for _ in range(column_count + 1)]
        while True:
            used[current_col] = True
            current_row = matching[current_col]
            delta = math.inf
            next_col = 0
            for col in range(1, column_count + 1):
                if used[col]:
                    continue
                cost = costs[current_row - 1][col - 1] - potentials_row[current_row] - potentials_col[col]
                if cost < min_values[col]:
                    min_values[col] = cost
                    previous[col] = current_col
                if min_values[col] < delta:
                    delta = min_values[col]
                    next_col = col
            for col in range(0, column_count + 1):
                if used[col]:
                    potentials_row[matching[col]] += delta
                    potentials_col[col] -= delta
                else:
                    min_values[col] -= delta
            current_col = next_col
            if matching[current_col] == 0:
                break
        while True:
            next_col = previous[current_col]
            matching[current_col] = matching[next_col]
            current_col = next_col
            if current_col == 0:
                break

    assignment = [-1 for _ in range(row_count)]
    for col in range(1, column_count + 1):
        if matching[col] > 0:
            assignment[matching[col] - 1] = col - 1
    return assignment


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


def _material_phonetic_position(reference_text: str, material: MaterialAnalysis) -> int | None:
    return _material_phonetic_match(reference_text, material).position


def _material_phonetic_position_count(reference_text: str, material: MaterialAnalysis) -> int:
    return _material_phonetic_match(reference_text, material).position_count


def _material_phonetic_position_score(
    reference_text: str,
    material: MaterialAnalysis,
    *,
    phonetic_match: _PhoneticMatch | None = None,
) -> float:
    reference_units = _phonetic_units(reference_text)
    if not reference_units:
        return 0.0

    match = phonetic_match or _material_phonetic_match(reference_text, material)
    if match.position_count <= 0:
        return 0.0

    ambiguity_count = match.tone_position_count if match.tone_position_count else match.position_count
    ambiguity_penalty = 0.18 if ambiguity_count > 1 else 0.0
    span_units = max(match.span_units, match.tone_span_units, 1)
    if len(reference_units) <= span_units:
        base = 1.0
    else:
        base = 0.92
    if span_units > 1:
        base = min(base + 0.03, 1.0)
    return max(base - ambiguity_penalty, match.tone_score)


def _material_phonetic_match(reference_text: str, material: MaterialAnalysis) -> _PhoneticMatch:
    positions: set[int] = set()
    tone_positions: set[int] = set()
    span_units = 0
    tone_span_units = 0
    for text in (material.transcript, material.filename_text):
        match = _phonetic_match(reference_text, text)
        positions.update(match.positions)
        tone_positions.update(match.tone_positions)
        span_units = max(span_units, match.span_units)
        tone_span_units = max(tone_span_units, match.tone_span_units)

    tone_position_count = len(tone_positions)
    tone_score = 0.0
    if tone_position_count > 0:
        tone_score = 0.98 if tone_position_count == 1 else 0.80

    resolved_position = None
    if tone_position_count == 1:
        resolved_position = min(tone_positions)
    elif positions:
        resolved_position = min(positions)

    return _PhoneticMatch(
        position=resolved_position,
        position_count=len(positions),
        span_units=span_units,
        tone_position=min(tone_positions) if tone_positions else None,
        tone_position_count=tone_position_count,
        tone_span_units=tone_span_units,
        tone_score=tone_score,
    )


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


def _group_kana_units(units: Sequence[str]) -> list[str]:
    grouped: list[str] = []
    buffer: list[str] = []
    for unit in units:
        if KANA_PATTERN.fullmatch(unit):
            buffer.append(unit)
            continue
        if buffer:
            grouped.append("".join(buffer))
            buffer.clear()
        grouped.append(unit)
    if buffer:
        grouped.append("".join(buffer))
    return grouped


def _compact_text(text: str) -> str:
    return "".join(_text_units(text))


def _phonetic_text(text: str) -> str:
    return " ".join(_phonetic_units(text))


def _phonetic_units(text: str) -> list[str]:
    units = _group_kana_units(_text_units(_strip_accents(text)))
    if not units:
        return []

    result: list[str] = []
    for unit in units:
        if unit.isdigit():
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", unit):
            result.extend(_pinyin_units(unit))
        elif KANA_PATTERN.fullmatch(unit):
            result.extend(_kana_romaji_units(unit))
        else:
            result.append(_normalize_phonetic_unit(unit))
    return [unit for unit in result if unit]


def _phonetic_tone_units(text: str) -> list[str]:
    units = _group_kana_units(_text_units(_strip_accents(text)))
    if not units:
        return []

    result: list[str] = []
    for unit in units:
        if unit.isdigit():
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", unit):
            result.extend(_pinyin_tone_units(unit))
        elif KANA_PATTERN.fullmatch(unit):
            result.extend(_kana_romaji_units(unit))
        else:
            result.append(_normalize_phonetic_tone_unit(unit))
    return [unit for unit in result if unit]


def _phonetic_position(reference_text: str, material_text: str) -> int | None:
    positions = _phonetic_positions(reference_text, material_text)
    return positions[0] if positions else None


def _phonetic_positions(reference_text: str, material_text: str) -> list[int]:
    return list(_phonetic_match(reference_text, material_text).positions)


def _phonetic_match(reference_text: str, material_text: str) -> _RawPhoneticMatch:
    reference_units = _phonetic_units(reference_text)
    material_units = _phonetic_units(material_text)
    if not reference_units or not material_units:
        return _RawPhoneticMatch()

    positions, span_units = _phonetic_positions_for_units(reference_units, material_units)
    tone_positions: list[int] = []
    tone_span_units = 0
    reference_tone_units = _phonetic_tone_units(reference_text)
    material_tone_units = _phonetic_tone_units(material_text)
    if _has_tone_evidence(material_tone_units):
        tone_positions, tone_span_units = _phonetic_positions_for_units(reference_tone_units, material_tone_units)

    return _RawPhoneticMatch(
        positions=tuple(positions),
        span_units=span_units,
        tone_positions=tuple(tone_positions),
        tone_span_units=tone_span_units,
    )


def _phonetic_positions_for_units(
    reference_units: Sequence[str],
    material_units: Sequence[str],
) -> tuple[list[int], int]:
    material_count = len(material_units)
    positions: list[int] = []
    for start in range(0, len(reference_units) - material_count + 1):
        if reference_units[start : start + material_count] == material_units:
            positions.append(start)
    if positions:
        return positions, material_count

    reference_compact = "".join(reference_units)
    material_compact = "".join(material_units)
    if not reference_compact or not material_compact:
        return [], 0

    exact_positions: list[int] = []
    search_start = 0
    while True:
        exact = reference_compact.find(material_compact, search_start)
        if exact < 0:
            break
        exact_positions.append(exact)
        search_start = exact + 1
    if not exact_positions:
        return [], 0

    resolved_positions: list[int] = []
    resolved_spans: list[int] = []
    offset = 0
    for index, unit in enumerate(reference_units):
        next_offset = offset + len(unit)
        starts_here = [exact for exact in exact_positions if offset <= exact < next_offset]
        if starts_here:
            resolved_positions.append(index)
            start_offset = starts_here[0]
            end_offset = start_offset + len(material_compact)
            end_index = index
            running_end = next_offset
            while end_index + 1 < len(reference_units) and running_end < end_offset:
                end_index += 1
                running_end += len(reference_units[end_index])
            resolved_spans.append(max(end_index - index + 1, 1))
        offset = next_offset
    return resolved_positions, max(resolved_spans, default=0)


def _pinyin_units(text: str) -> list[str]:
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore
    except Exception:
        return list(text)

    return [
        _normalize_phonetic_unit(unit)
        for unit in lazy_pinyin(text, style=Style.NORMAL, errors="default")
        if unit
    ]


def _pinyin_tone_units(text: str) -> list[str]:
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore
    except Exception:
        return list(text)

    return [
        _normalize_phonetic_tone_unit(unit)
        for unit in lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True, errors="default")
        if unit
    ]


def _kana_romaji_units(text: str) -> list[str]:
    normalized = _strip_accents(text)
    units: list[str] = []
    index = 0
    while index < len(normalized):
        pair = normalized[index : index + 2]
        triple = normalized[index : index + 3]
        if triple in _KANA_ROMAJI_MAP:
            units.append(_KANA_ROMAJI_MAP[triple])
            index += 3
            continue
        if pair in _KANA_ROMAJI_MAP:
            units.append(_KANA_ROMAJI_MAP[pair])
            index += 2
            continue
        char = normalized[index]
        units.append(_KANA_ROMAJI_MAP.get(char, char))
        index += 1
    return [unit for unit in units if unit]


_KANA_ROMAJI_MAP: dict[str, str] = {
    "きゃ": "kya",
    "きゅ": "kyu",
    "きょ": "kyo",
    "しゃ": "sha",
    "しゅ": "shu",
    "しょ": "sho",
    "ちゃ": "cha",
    "ちゅ": "chu",
    "ちょ": "cho",
    "にゃ": "nya",
    "にゅ": "nyu",
    "にょ": "nyo",
    "ひゃ": "hya",
    "ひゅ": "hyu",
    "ひょ": "hyo",
    "みゃ": "mya",
    "みゅ": "myu",
    "みょ": "myo",
    "りゃ": "rya",
    "りゅ": "ryu",
    "りょ": "ryo",
    "ぎゃ": "gya",
    "ぎゅ": "gyu",
    "ぎょ": "gyo",
    "じゃ": "ja",
    "じゅ": "ju",
    "じょ": "jo",
    "びゃ": "bya",
    "びゅ": "byu",
    "びょ": "byo",
    "ぴゃ": "pya",
    "ぴゅ": "pyu",
    "ぴょ": "pyo",
    "キャ": "kya",
    "キュ": "kyu",
    "キョ": "kyo",
    "シャ": "sha",
    "シュ": "shu",
    "ショ": "sho",
    "チャ": "cha",
    "チュ": "chu",
    "チョ": "cho",
    "ニャ": "nya",
    "ニュ": "nyu",
    "ニョ": "nyo",
    "ヒャ": "hya",
    "ヒュ": "hyu",
    "ヒョ": "hyo",
    "ミャ": "mya",
    "ミュ": "myu",
    "ミョ": "myo",
    "リャ": "rya",
    "リュ": "ryu",
    "リョ": "ryo",
    "ギャ": "gya",
    "ギュ": "gyu",
    "ギョ": "gyo",
    "ジャ": "ja",
    "ジュ": "ju",
    "ジョ": "jo",
    "ビャ": "bya",
    "ビュ": "byu",
    "ビョ": "byo",
    "ピャ": "pya",
    "ピュ": "pyu",
    "ピョ": "pyo",
    "あ": "a",
    "い": "i",
    "う": "u",
    "え": "e",
    "お": "o",
    "か": "ka",
    "き": "ki",
    "く": "ku",
    "け": "ke",
    "こ": "ko",
    "さ": "sa",
    "し": "shi",
    "す": "su",
    "せ": "se",
    "そ": "so",
    "た": "ta",
    "ち": "chi",
    "つ": "tsu",
    "て": "te",
    "と": "to",
    "な": "na",
    "に": "ni",
    "ぬ": "nu",
    "ね": "ne",
    "の": "no",
    "は": "ha",
    "ひ": "hi",
    "ふ": "fu",
    "へ": "he",
    "ほ": "ho",
    "ま": "ma",
    "み": "mi",
    "む": "mu",
    "め": "me",
    "も": "mo",
    "や": "ya",
    "ゆ": "yu",
    "よ": "yo",
    "ら": "ra",
    "り": "ri",
    "る": "ru",
    "れ": "re",
    "ろ": "ro",
    "わ": "wa",
    "を": "wo",
    "ん": "n",
    "が": "ga",
    "ぎ": "gi",
    "ぐ": "gu",
    "げ": "ge",
    "ご": "go",
    "ざ": "za",
    "じ": "ji",
    "ず": "zu",
    "ぜ": "ze",
    "ぞ": "zo",
    "だ": "da",
    "ぢ": "ji",
    "づ": "zu",
    "で": "de",
    "ど": "do",
    "ば": "ba",
    "び": "bi",
    "ぶ": "bu",
    "べ": "be",
    "ぼ": "bo",
    "ぱ": "pa",
    "ぴ": "pi",
    "ぷ": "pu",
    "ぺ": "pe",
    "ぽ": "po",
    "ゃ": "ya",
    "ゅ": "yu",
    "ょ": "yo",
    "ぁ": "a",
    "ぃ": "i",
    "ぅ": "u",
    "ぇ": "e",
    "ぉ": "o",
    "っ": "",
    "ー": "",
    "ア": "a",
    "イ": "i",
    "ウ": "u",
    "エ": "e",
    "オ": "o",
    "カ": "ka",
    "キ": "ki",
    "ク": "ku",
    "ケ": "ke",
    "コ": "ko",
    "サ": "sa",
    "シ": "shi",
    "ス": "su",
    "セ": "se",
    "ソ": "so",
    "タ": "ta",
    "チ": "chi",
    "ツ": "tsu",
    "テ": "te",
    "ト": "to",
    "ナ": "na",
    "ニ": "ni",
    "ヌ": "nu",
    "ネ": "ne",
    "ノ": "no",
    "ハ": "ha",
    "ヒ": "hi",
    "フ": "fu",
    "ヘ": "he",
    "ホ": "ho",
    "マ": "ma",
    "ミ": "mi",
    "ム": "mu",
    "メ": "me",
    "モ": "mo",
    "ヤ": "ya",
    "ユ": "yu",
    "ヨ": "yo",
    "ラ": "ra",
    "リ": "ri",
    "ル": "ru",
    "レ": "re",
    "ロ": "ro",
    "ワ": "wa",
    "ヲ": "wo",
    "ン": "n",
    "ガ": "ga",
    "ギ": "gi",
    "グ": "gu",
    "ゲ": "ge",
    "ゴ": "go",
    "ザ": "za",
    "ジ": "ji",
    "ズ": "zu",
    "ゼ": "ze",
    "ゾ": "zo",
    "ダ": "da",
    "ヂ": "ji",
    "ヅ": "zu",
    "デ": "de",
    "ド": "do",
    "バ": "ba",
    "ビ": "bi",
    "ブ": "bu",
    "ベ": "be",
    "ボ": "bo",
    "パ": "pa",
    "ピ": "pi",
    "プ": "pu",
    "ペ": "pe",
    "ポ": "po",
    "ャ": "ya",
    "ュ": "yu",
    "ョ": "yo",
    "ァ": "a",
    "ィ": "i",
    "ゥ": "u",
    "ェ": "e",
    "ォ": "o",
    "ッ": "",
    "ー": "",
}


_ROMAJI_VARIANTS: dict[str, str] = {
    "jya": "ja",
    "jyu": "ju",
    "jyo": "jo",
    "sya": "sha",
    "syu": "shu",
    "syo": "sho",
    "tya": "cha",
    "tyu": "chu",
    "tyo": "cho",
    "zya": "ja",
    "zyu": "ju",
    "zyo": "jo",
    "thi": "shi",
    "ti": "chi",
    "tu": "tsu",
    "du": "zu",
}


def _normalize_phonetic_unit(unit: str) -> str:
    normalized = _strip_accents(unit)
    normalized = re.sub(r"[1-5]", "", normalized)
    normalized = normalized.replace("u:", "u").replace("v", "u")
    return _ROMAJI_VARIANTS.get(normalized, normalized)


def _normalize_phonetic_tone_unit(unit: str) -> str:
    normalized = _strip_accents(unit)
    normalized = normalized.replace("u:", "u").replace("v", "u")
    match = re.fullmatch(r"([a-z]+)([1-5])", normalized)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return _normalize_phonetic_unit(normalized)


def _has_tone_evidence(units: Sequence[str]) -> bool:
    return any(re.search(r"[1-5]$", unit) for unit in units)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


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
    phonetic_score: float,
    duration_score: float,
    speaker_score: float,
    vad_score: float,
    reference_text: str,
) -> float:
    compact_reference = _compact_text(reference_text)
    if len(compact_reference) <= 4:
        weights = {
            "transcript": 0.25,
            "filename": 0.18,
            "phonetic": 0.22,
            "duration": 0.22,
            "speaker": 0.06,
            "vad": 0.07,
        }
    else:
        weights = {
            "transcript": 0.42,
            "filename": 0.14,
            "phonetic": 0.14,
            "duration": 0.12,
            "speaker": 0.08,
            "vad": 0.10,
        }

    return (
        (weights["transcript"] * transcript_score)
        + (weights["filename"] * filename_score)
        + (weights["phonetic"] * phonetic_score)
        + (weights["duration"] * duration_score)
        + (weights["speaker"] * speaker_score)
        + (weights["vad"] * vad_score)
    )


def _reason_for_scores(
    reference_text: str,
    *,
    transcript_score: float,
    filename_score: float,
    phonetic_score: float,
    duration_score: float,
    position: int | None = None,
) -> str:
    if position is not None:
        return "reference_text_position"

    compact_reference = _compact_text(reference_text)
    if transcript_score >= max(filename_score, phonetic_score, duration_score):
        return "transcript_similarity"
    if filename_score >= max(transcript_score, phonetic_score, duration_score):
        return "filename_similarity"
    if phonetic_score >= max(transcript_score, filename_score, duration_score):
        return "phonetic_similarity"
    if len(compact_reference) <= 4 and duration_score >= max(transcript_score, filename_score, phonetic_score):
        return "duration_similarity"
    return "transcript_similarity"


def _evidence_count(
    *,
    transcript_score: float,
    filename_score: float,
    phonetic_score: float,
    duration_score: float,
    speaker_score: float,
    vad_score: float,
) -> int:
    thresholds = (
        transcript_score >= 0.18,
        filename_score >= 0.18,
        phonetic_score >= 0.18,
        duration_score >= 0.70,
        speaker_score >= 0.45,
        vad_score >= 0.35,
    )
    return sum(1 for passed in thresholds if passed)


def _confidence_label(score: float, *, evidence_count: int, short_reference: bool) -> str:
    if score < 0.18 or (short_reference and evidence_count < 2):
        return "review_required"
    if score >= 0.65 and evidence_count >= 2:
        return "strong"
    if score >= 0.35:
        return "medium"
    return "weak"


def _is_short_reference_text(text: str) -> bool:
    compact = _compact_text(text)
    return 0 < len(compact) <= 4


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

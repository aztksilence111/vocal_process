from __future__ import annotations

import importlib.util
import math
import re
import unicodedata
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
    timing_source: str = ""

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
                    " ".join(segment.text for segment in usable_reference),
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
    reference_text: str,
    materials: Sequence[MaterialAnalysis],
    *,
    reference_embedding: tuple[float, ...] | None,
    reference_duration_hint: float,
) -> list[MaterialOrderDecision]:
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
        position = _material_text_position(reference_text, material)
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
        decisions.append(_decision_from_score(rank, score))
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
    phonetic_score = phonetic_similarity(reference_segment.text, _material_search_text(material))
    duration_score = _duration_similarity(reference_duration, material.duration_seconds)
    speaker_score = max(_speaker_similarity(reference_segment, material, reference_embedding=reference_embedding), 0.0)
    vad_score = material.vad_coverage or 0.0
    text_position = _material_text_position(reference_segment.text, material)
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
        position=text_position if allow_position_reason else None,
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
            " ".join(segment.text for segment in reference_segments),
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
    )


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


def _phonetic_text(text: str) -> str:
    units = _text_units(_strip_accents(text))
    if not units:
        return ""

    result: list[str] = []
    for unit in units:
        if re.fullmatch(r"[\u4e00-\u9fff]+", unit):
            result.extend(_pinyin_units(unit))
        else:
            result.append(unit)
    return " ".join(result)


def _pinyin_units(text: str) -> list[str]:
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore
    except Exception:
        return list(text)

    return [
        _strip_accents(unit).lower()
        for unit in lazy_pinyin(text, style=Style.NORMAL, errors="default")
        if unit
    ]


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

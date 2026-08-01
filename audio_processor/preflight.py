from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Sequence

from .engine import MaterialStretchClip, plan_material_stretch_clips, render_material_stretch_plan
from .model_runtime import ModelOrderingResult, build_model_ordering


PREFLIGHT_REPORT_FORMAT = "vocal_process_preflight_analysis_v1"
LOW_MATCH_SCORE = 0.18
WEAK_TEXT_SCORE = 0.12
CancelCallback = Callable[[], bool]


def build_preflight_report(
    reference_path: Path,
    material_directory: Path,
    *,
    lyrics_file: Path | None = None,
    work_dir: Path | None = None,
    material_cache_dir: Path | None = None,
    compute_device: str = "auto",
    source_separation: str = "auto",
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    ordering = build_model_ordering(
        reference_path,
        material_directory,
        lyrics_file=lyrics_file,
        work_dir=work_dir,
        material_cache_dir=material_cache_dir,
        compute_device=compute_device,
        source_separation=source_separation,
        should_cancel=should_cancel,
    )
    stretch_plan = plan_material_stretch_clips(
        reference_path,
        ordering.ordered_paths,
        target_durations=ordering.target_durations,
        material_text_hints=[decision.material_text for decision in ordering.decisions],
    )
    warnings = _preflight_warnings(ordering, stretch_plan)
    lyric_conflict_count = sum(1 for warning in warnings if warning.get("kind") == "lyric_timing_conflict")
    return {
        "format": PREFLIGHT_REPORT_FORMAT,
        "reference_path": str(reference_path.expanduser()),
        "material_directory": str(material_directory.expanduser()),
        "lyrics_file": str(lyrics_file.expanduser()) if lyrics_file else "",
        "compute_device": ordering.analysis_report.get("compute_device", compute_device),
        "source_separation": ordering.analysis_report.get("source_separation", source_separation),
        "status": "review_required" if any(warning["severity"] == "error" for warning in warnings) else "ok",
        "summary": {
            "material_count": len(ordering.ordered_paths),
            "warning_count": len(warnings),
            "error_warning_count": sum(1 for warning in warnings if warning["severity"] == "error"),
            "review_required_match_count": sum(
                1 for decision in ordering.decisions if decision.confidence_label == "review_required"
            ),
            "lyric_timing_conflict_count": lyric_conflict_count,
            "minimum_match_score": _minimum_score(ordering),
            "extreme_stretch_count": sum(
                1 for clip in stretch_plan if clip.quality_warning == "extreme_stretch_ratio"
            ),
            "moderate_stretch_count": sum(
                1 for clip in stretch_plan if clip.quality_warning == "moderate_stretch_ratio"
            ),
            "continuity_warning_count": sum(1 for clip in stretch_plan if clip.continuity_warning),
            "fade_applied_clip_count": sum(1 for clip in stretch_plan if clip.fade_seconds > 0),
            "stretch_naturalness_score_mean": _mean(
                [clip.stretch_naturalness_score for clip in stretch_plan]
            ),
        },
        "warnings": warnings,
        "optimization": _optimization_report(ordering, stretch_plan),
        "ordering": ordering.analysis_report,
        "stretch_plan": _jsonable(render_material_stretch_plan(stretch_plan)),
    }


def _preflight_warnings(
    ordering: ModelOrderingResult,
    stretch_plan: Sequence[MaterialStretchClip],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    if _reference_text_requires_verification(ordering):
        warnings.append(
            {
                "severity": "error",
                "kind": "reference_asr_unverified",
                "reference_path": str(ordering.reference.source_path),
                "backend": ordering.reference.backend,
                "message": (
                    "Reference text comes from ASR without lyrics or a verified transcript. "
                    "Zero-recognition-error acceptance requires a lyrics_file or other ground-truth transcript."
                ),
            }
        )

    for decision in ordering.decisions:
        if decision.score < LOW_MATCH_SCORE:
            warnings.append(
                {
                    "severity": "error",
                    "kind": "low_match_score",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "message": "Material match score is too low for reliable automatic ordering.",
                }
            )
        elif decision.confidence_label == "review_required":
            warnings.append(
                {
                    "severity": "error",
                    "kind": "short_clip_insufficient_evidence",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "evidence_count": decision.evidence_count,
                    "phonetic_score": decision.phonetic_score,
                    "message": "Short material requires at least two reliable evidence signals before automatic ordering is trusted.",
                }
            )
        elif (
            decision.transcript_score < WEAK_TEXT_SCORE
            and decision.filename_score < WEAK_TEXT_SCORE
            and decision.phonetic_score < WEAK_TEXT_SCORE
        ):
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "weak_text_signal",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "transcript_score": decision.transcript_score,
                    "filename_score": decision.filename_score,
                    "message": "ASR transcript, filename hint, and pronunciation evidence provide weak ordering signal.",
                }
            )
        elif _is_short_text(decision.reference_text) and decision.phonetic_score < WEAK_TEXT_SCORE:
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "weak_phonetic_signal",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "phonetic_score": decision.phonetic_score,
                    "message": "Short material has weak pronunciation evidence; verify the one-to-one order before rendering.",
                }
            )

        if decision.phonetic_position_count > 1 and decision.phonetic_tone_position_count == 1:
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "tone_disambiguated_phonetic_position",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "phonetic_score": decision.phonetic_score,
                    "phonetic_tone_score": decision.phonetic_tone_score,
                    "phonetic_tone_position": decision.phonetic_tone_position,
                    "phonetic_position_count": decision.phonetic_position_count,
                    "message": "Tone-marked filename narrows an otherwise ambiguous pronunciation match; still verify if acoustic evidence is weak.",
                }
            )
        elif decision.phonetic_position_count > 1:
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "ambiguous_phonetic_position",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "score": decision.score,
                    "phonetic_score": decision.phonetic_score,
                    "phonetic_position_count": decision.phonetic_position_count,
                    "message": "Filename pronunciation matches multiple positions in the reference; verify the selected character timing before rendering.",
                }
            )

        if decision.reason.endswith("filename_fallback"):
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "filename_fallback",
                    "rank": decision.rank,
                    "material_path": str(decision.source_path),
                    "message": "Automatic recognition could not match this clip; filename order was used.",
                }
            )

    notes = ordering.analysis_report.get("notes", [])
    for note in (notes if isinstance(notes, list) else []):
        if isinstance(note, str) and note.startswith("lyric_timing_conflict:"):
            warnings.append(
                {
                    "severity": "warning",
                    "kind": "lyric_timing_conflict",
                    "message": note,
                }
            )

    timeline_alignment = ordering.analysis_report.get("timeline_alignment", {})
    if isinstance(timeline_alignment, dict):
        positioned_count = _positive_int(timeline_alignment.get("positioned_decision_count"))
        timed_count = _positive_int(timeline_alignment.get("timed_target_duration_count"))
        if positioned_count > 0 and timed_count < positioned_count:
            warnings.append(
                {
                    "severity": "error",
                    "kind": "missing_aligned_unit_timing",
                    "positioned_decision_count": positioned_count,
                    "timed_target_duration_count": timed_count,
                    "message": (
                        "Reference alignment does not provide actual start/end timing for every positioned "
                        "word or pronunciation unit; proportional duration fallback is not strict enough for "
                        "automatic rendered-audio acceptance."
                    ),
                }
            )

    for clip in stretch_plan:
        if not clip.quality_warning:
            continue
        is_short_text = _is_short_text(_decision_material_text(ordering, clip.index))
        severity = "error" if clip.quality_warning == "extreme_stretch_ratio" else "warning"
        kind = clip.quality_warning
        if is_short_text and clip.quality_warning == "extreme_stretch_ratio":
            kind = "single_syllable_extreme_stretch"
        warnings.append(
            {
                "severity": severity,
                "kind": kind,
                "rank": clip.index,
                "material_path": str(clip.source_path),
                "source_duration_seconds": clip.source_duration_seconds,
                "target_duration_seconds": clip.target_duration_seconds,
                "rubberband_tempo": clip.tempo,
                "requested_rubberband_tempo": clip.requested_tempo,
                "stretch_naturalness_score": clip.stretch_naturalness_score,
                "fade_seconds": clip.fade_seconds,
                "message": "Stretch ratio may damage pronunciation intelligibility.",
            }
        )

    for clip in stretch_plan:
        if not clip.continuity_warning:
            continue
        warnings.append(
            {
                "severity": "warning",
                "kind": clip.continuity_warning,
                "rank": clip.index,
                "material_path": str(clip.source_path),
                "source_duration_seconds": clip.source_duration_seconds,
                "target_duration_seconds": clip.target_duration_seconds,
                "rubberband_tempo": clip.tempo,
                "requested_rubberband_tempo": clip.requested_tempo,
                "stretch_naturalness_score": clip.stretch_naturalness_score,
                "fade_seconds": clip.fade_seconds,
                "message": (
                    "Rendered clip uses exact target duration with boundary conditioning, but this stretch ratio "
                    "can still sound discontinuous at syllable boundaries."
                ),
            }
        )

    return warnings


def _decision_material_text(ordering: ModelOrderingResult, rank: int) -> str:
    for decision in ordering.decisions:
        if decision.rank == rank:
            return decision.material_text
    return ""


def _reference_text_requires_verification(ordering: ModelOrderingResult) -> bool:
    if not ordering.reference.transcript.strip():
        return False
    if any("lyric" in segment.timing_source for segment in ordering.reference.segments):
        return False
    return any(segment.timing_source.startswith("asr") for segment in ordering.reference.segments)


def _is_short_text(text: str) -> bool:
    units = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())
    return 0 < len("".join(units)) <= 4


def _minimum_score(ordering: ModelOrderingResult) -> float:
    if not ordering.decisions:
        return 0.0
    return min(decision.score for decision in ordering.decisions)


def _positive_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _optimization_report(
    ordering: ModelOrderingResult,
    stretch_plan: Sequence[MaterialStretchClip],
) -> dict[str, Any]:
    duplicate_render_groups = _duplicate_render_groups(stretch_plan)
    repeated_text_groups = _repeated_reference_text_groups(ordering)
    backend_summary = ordering.analysis_report.get("backend_summary", {})
    continuity_warning_groups = _continuity_warning_groups(stretch_plan)
    return {
        "format": "vocal_process_optimization_report_v1",
        "safe_duplicate_clip_render_reuse_count": sum(len(group["ranks"]) - 1 for group in duplicate_render_groups),
        "duplicate_clip_render_groups": duplicate_render_groups,
        "repeated_reference_text_groups": repeated_text_groups,
        "render_continuity": {
            "formant_preservation": "rubberband_formant_preserved",
            "boundary_conditioning": _render_boundary_conditioning(stretch_plan),
            "fade_applied_clip_count": sum(1 for clip in stretch_plan if clip.fade_seconds > 0),
            "continuity_warning_count": sum(1 for clip in stretch_plan if clip.continuity_warning),
            "stretch_naturalness_score_mean": _mean(
                [clip.stretch_naturalness_score for clip in stretch_plan]
            ),
            "continuity_warning_groups": continuity_warning_groups,
        },
        "available_acceleration": {
            "faster_whisper": bool(backend_summary.get("Faster Whisper")),
            "cuda_requested_or_available": ordering.analysis_report.get("compute_device") == "cuda",
        },
        "source_separation": {
            "mode": ordering.analysis_report.get("source_separation", "auto"),
            "skip_when_reference_is_already_vocal": "Use source_separation=never only when the original audio is already an isolated vocal stem.",
        },
        "notes": [
            "Rendered audio is reused only when source file, target duration, tempo, and render options match exactly.",
            "Verse/chorus similarity is reported as a planning hint; it is not used to skip ASR or change words automatically.",
            "Formant-preserved Rubber Band stretching is used before exact-duration trim; extreme short-text expansion uses loop fill instead of silent tail padding.",
        ],
    }


def _duplicate_render_groups(stretch_plan: Sequence[MaterialStretchClip]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float, float], list[MaterialStretchClip]] = {}
    for clip in stretch_plan:
        key = (
            str(clip.source_path),
            round(clip.target_duration_seconds, 6),
            round(clip.tempo, 8),
        )
        groups.setdefault(key, []).append(clip)

    return [
        {
            "source_path": str(clips[0].source_path),
            "target_duration_seconds": clips[0].target_duration_seconds,
            "rubberband_tempo": clips[0].tempo,
            "ranks": [clip.index for clip in clips],
        }
        for clips in groups.values()
        if len(clips) > 1
    ]


def _render_boundary_conditioning(stretch_plan: Sequence[MaterialStretchClip]) -> str:
    has_fades = any(clip.fade_seconds > 0 for clip in stretch_plan)
    has_loop_fill = any(
        clip.stretch_strategy == "syllable_formant_expand_with_loop_fill"
        for clip in stretch_plan
    )
    if has_loop_fill and has_fades:
        return "short_text_loop_fill+per_clip_fade_in_out"
    if has_loop_fill:
        return "short_text_loop_fill"
    if has_fades:
        return "per_clip_fade_in_out"
    return ""


def _continuity_warning_groups(stretch_plan: Sequence[MaterialStretchClip]) -> list[dict[str, Any]]:
    groups: dict[str, list[MaterialStretchClip]] = {}
    for clip in stretch_plan:
        if clip.continuity_warning:
            groups.setdefault(clip.continuity_warning, []).append(clip)

    return [
        {
            "kind": kind,
            "count": len(clips),
            "ranks": [clip.index for clip in clips[:20]],
            "worst_stretch_naturalness_score": min(clip.stretch_naturalness_score for clip in clips),
        }
        for kind, clips in sorted(groups.items())
    ]


def _repeated_reference_text_groups(ordering: ModelOrderingResult) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = {}
    for decision in ordering.decisions:
        key = _compact_text(decision.reference_text)
        if not key:
            continue
        groups.setdefault(key, []).append(decision)

    return [
        {
            "reference_text": decisions[0].reference_text,
            "ranks": [decision.rank for decision in decisions],
            "target_duration_seconds": [
                decision.target_duration_seconds for decision in decisions
            ],
            "message": "Repeated text detected; matching and render reuse still require clip-level validation.",
        }
        for decisions in groups.values()
        if len(decisions) > 1
    ]


def _compact_text(text: str) -> str:
    units = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff]|[\u4e00-\u9fff]", text.lower())
    return "".join(units)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value

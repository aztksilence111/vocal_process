from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .batch import QueueItem, run_batch_queue
from .engine import get_audio_duration_seconds, list_audio_files, probe_audio
from .diagnostics import diagnostic_log_path
from .model_runtime import (
    infer_cn_jp_language_from_name,
    reference_material_language_compatibility,
    speech_runtime_preflight_report,
)
from .preflight import LOW_MATCH_SCORE, build_preflight_report
from .settings import ProcessingSettings


REAL_CASE_FORMAT = "vocal_process_real_cases_v1"
REAL_REPORT_FORMAT = "vocal_process_real_suite_report_v1"
STRICT_DURATION_TOLERANCE_RATIO = 0.01
INFRASTRUCTURE_WARNING_KINDS = {
    "asr_model_download_failed",
    "asr_model_cache_missing",
    "speech_runtime_unavailable",
    "runtime_tool_unavailable",
}
INFRASTRUCTURE_BLOCKED_EXIT_CODE = 2


@dataclass(frozen=True)
class RealCase:
    name: str
    reference_path: Path
    material_directory: Path
    lyrics_file: Path | None
    output_directory: Path
    split: str = "regression"
    language: str = ""
    expected_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class RealCaseResult:
    case: RealCase
    status: str
    analysis_report_path: Path
    output_path: Path | None
    render_report_path: Path | None
    summary: dict[str, Any]
    warnings: list[dict[str, Any]]


@dataclass(frozen=True)
class SkippedRealCase:
    name: str
    reference_path: Path
    material_directory: Path
    lyrics_file: Path | None
    output_directory: Path
    split: str
    language: str
    reason: str
    language_compatibility: dict[str, Any]


@dataclass(frozen=True)
class RealSuiteResult:
    root: Path
    report_directory: Path
    manifest_path: Path | None
    cases: tuple[RealCaseResult, ...]
    summary_path: Path
    markdown_path: Path
    skipped_cases: tuple[SkippedRealCase, ...] = ()


def discover_real_cases(root: Path, *, manifest_path: Path | None = None) -> list[RealCase]:
    cases, _skipped_cases = discover_real_cases_with_skips(root, manifest_path=manifest_path)
    return cases


def discover_real_cases_with_skips(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> tuple[list[RealCase], list[SkippedRealCase]]:
    root = root.expanduser()
    if manifest_path is not None and manifest_path.exists():
        return _filter_language_compatible_cases(_load_cases_from_manifest(root, manifest_path))

    origin_root = root / "origin_vocal"
    material_root = root / "material_set"
    output_root = root / "output"
    references = list_audio_files(origin_root) if origin_root.exists() else []
    material_dirs = [path for path in sorted(material_root.iterdir()) if path.is_dir()] if material_root.exists() else []
    if material_root.exists() and not material_dirs and list_audio_files(material_root):
        material_dirs = [material_root]

    cases: list[RealCase] = []
    for reference in references:
        language = _infer_language(reference.stem)
        lyrics_file = _find_lyrics_file(root / "lyrics", reference.stem)
        for material_directory in material_dirs:
            case_name = f"{reference.stem}__{material_directory.name}"
            cases.append(
                RealCase(
                    name=case_name,
                    reference_path=reference,
                    material_directory=material_directory,
                    lyrics_file=lyrics_file,
                    output_directory=output_root / reference.stem / material_directory.name,
                    split=language or "regression",
                    language=language,
                )
            )
    return _filter_language_compatible_cases(cases)


def run_real_suite(
    root: Path,
    *,
    manifest_path: Path | None = None,
    render: bool = False,
    compute_device: str = "auto",
    source_separation: str = "never",
    max_cases: int | None = None,
    case_filter: str | None = None,
    split_filter: str | None = None,
    output_root: Path | None = None,
) -> RealSuiteResult:
    root = root.expanduser()
    cases, skipped_cases = discover_real_cases_with_skips(root, manifest_path=manifest_path)
    if case_filter:
        lowered = case_filter.lower()
        cases = [case for case in cases if lowered in case.name.lower()]
        skipped_cases = [case for case in skipped_cases if lowered in case.name.lower()]
    if split_filter:
        lowered_split = split_filter.lower()
        cases = [case for case in cases if case.split.lower() == lowered_split]
        skipped_cases = [case for case in skipped_cases if case.split.lower() == lowered_split]
    if max_cases is not None:
        cases = cases[: max(0, max_cases)]

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    report_directory = (output_root or (root / "output")).expanduser() / f"real-eval-{timestamp}"
    report_directory.mkdir(parents=True, exist_ok=True)
    summary_path = report_directory / "summary.json"
    markdown_path = report_directory / "summary.md"

    runtime_preflight = _runtime_preflight_report(compute_device)
    results: list[RealCaseResult] = []
    planned_case_count = len(cases)
    suite_summary = _suite_summary(
        root=root,
        manifest_path=manifest_path,
        render=render,
        results=results,
        planned_case_count=planned_case_count,
        runtime_preflight=runtime_preflight,
        skipped_cases=skipped_cases,
    )
    _write_suite_outputs(summary_path, markdown_path, suite_summary, results)
    for case_index, case in enumerate(cases):
        case.output_directory.mkdir(parents=True, exist_ok=True)
        analysis_path = report_directory / case.name / "analysis.json"
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            report = build_preflight_report(
                case.reference_path,
                case.material_directory,
                lyrics_file=case.lyrics_file,
                work_dir=case.output_directory / "_work",
                material_cache_dir=case.output_directory / "_analysis_cache",
                compute_device=compute_device,
                source_separation=source_separation,
            )
        except Exception as exc:
            report = _case_failure_report(case, exc)
            analysis_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            result = RealCaseResult(
                case=case,
                status="analysis_failed",
                analysis_report_path=analysis_path,
                output_path=None,
                render_report_path=None,
                summary=_case_summary(
                    report,
                    {},
                    reference_path=case.reference_path,
                    output_path=None,
                    status="analysis_failed",
                ),
                warnings=list(report.get("warnings", [])),
            )
            results.append(result)
            suite_summary = _suite_summary(
                root=root,
                manifest_path=manifest_path,
                render=render,
                results=results,
                planned_case_count=planned_case_count,
                runtime_preflight=runtime_preflight,
                skipped_cases=skipped_cases,
            )
            _write_suite_outputs(summary_path, markdown_path, suite_summary, results)
            if _report_has_infrastructure_blocker(report):
                for blocked_case in cases[case_index + 1 :]:
                    blocked_case.output_directory.mkdir(parents=True, exist_ok=True)
                    blocked_analysis_path = report_directory / blocked_case.name / "analysis.json"
                    blocked_analysis_path.parent.mkdir(parents=True, exist_ok=True)
                    blocked_report = _case_blocked_report(
                        blocked_case,
                        report,
                        blocked_by_case=case.name,
                    )
                    blocked_analysis_path.write_text(
                        json.dumps(blocked_report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    results.append(
                        RealCaseResult(
                            case=blocked_case,
                            status="analysis_blocked",
                            analysis_report_path=blocked_analysis_path,
                            output_path=None,
                            render_report_path=None,
                            summary=_case_summary(
                                blocked_report,
                                {},
                                reference_path=blocked_case.reference_path,
                                output_path=None,
                                status="analysis_blocked",
                            ),
                            warnings=list(blocked_report.get("warnings", [])),
                        )
                    )
                suite_summary = _suite_summary(
                    root=root,
                    manifest_path=manifest_path,
                    render=render,
                    results=results,
                    planned_case_count=planned_case_count,
                    runtime_preflight=runtime_preflight,
                    skipped_cases=skipped_cases,
                )
                _write_suite_outputs(summary_path, markdown_path, suite_summary, results)
                break
            continue
        analysis_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        warnings = list(report.get("warnings", []))
        render_output_path: Path | None = None
        render_report_path: Path | None = None
        status = report.get("status", "unknown")

        if render:
            settings = ProcessingSettings(
                material_directory=str(case.material_directory),
                lyrics_file=str(case.lyrics_file or ""),
                output_directory=str(case.output_directory),
                output_extension=".wav",
                overwrite=True,
                compute_device=compute_device,
                source_separation=source_separation,
            )
            render_output_path = settings.output_path_for(case.reference_path)
            render_report_path = diagnostic_log_path(render_output_path)
            try:
                summary = run_batch_queue([QueueItem(case.reference_path, render_output_path)], settings)
                status = "rendered" if summary.failed == 0 and summary.cancelled == 0 else "render_failed"
                if not render_output_path.exists():
                    status = "render_failed"
                render_summary = {"batch_summary": summary.__dict__}
            except Exception as exc:
                status = "render_failed"
                render_summary = {"error": str(exc)}
                warnings.append(
                    {
                        "severity": "error",
                        "kind": "render_exception",
                        "message": str(exc),
                    }
                )
                report = _report_with_extra_warnings(report, warnings)
        else:
            render_summary = {}

        result = RealCaseResult(
            case=case,
            status=status,
            analysis_report_path=analysis_path,
            output_path=render_output_path,
            render_report_path=render_report_path,
            summary=_case_summary(
                report,
                render_summary,
                reference_path=case.reference_path,
                output_path=render_output_path,
                status=status,
            ),
            warnings=warnings,
        )
        results.append(result)
        suite_summary = _suite_summary(
            root=root,
            manifest_path=manifest_path,
            render=render,
            results=results,
            planned_case_count=planned_case_count,
            runtime_preflight=runtime_preflight,
            skipped_cases=skipped_cases,
        )
        _write_suite_outputs(summary_path, markdown_path, suite_summary, results)

    suite_summary = _suite_summary(
        root=root,
        manifest_path=manifest_path,
        render=render,
        results=results,
        planned_case_count=planned_case_count,
        runtime_preflight=runtime_preflight,
        skipped_cases=skipped_cases,
    )
    _write_suite_outputs(summary_path, markdown_path, suite_summary, results)
    return RealSuiteResult(
        root=root,
        report_directory=report_directory,
        manifest_path=manifest_path,
        cases=tuple(results),
        summary_path=summary_path,
        markdown_path=markdown_path,
        skipped_cases=tuple(skipped_cases),
    )


def write_manifest_template(root: Path, destination: Path) -> Path:
    root = root.expanduser()
    destination = destination.expanduser()
    cases, skipped_cases = discover_real_cases_with_skips(root)
    payload = {
        "format": REAL_CASE_FORMAT,
        "cases": [
            {
                "name": case.name,
                "split": case.split,
                "language": case.language,
                "reference": str(case.reference_path.relative_to(root)),
                "material_directory": str(case.material_directory.relative_to(root)),
                "lyrics_file": str(case.lyrics_file.relative_to(root)) if case.lyrics_file else "",
                "output_directory": str(case.output_directory.relative_to(root)),
                "expected_order": list(case.expected_order),
            }
            for case in cases
        ],
        "skipped_cases": [_skipped_case_payload(case) for case in skipped_cases],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate real audio cases stored under tests_real.")
    parser.add_argument("--root", type=Path, default=Path("tests_real"), help="real test root directory")
    parser.add_argument("--manifest", type=Path, help="optional manifest JSON file")
    parser.add_argument("--render", action="store_true", help="render audio outputs in addition to analysis")
    parser.add_argument("--compute-device", default="auto", help="model runtime device: auto, cpu, or cuda")
    parser.add_argument("--source-separation", default="never", help="reference separation mode")
    parser.add_argument("--max-cases", type=int, help="limit the number of evaluated cases")
    parser.add_argument("--case", dest="case_filter", help="substring filter for case names")
    parser.add_argument("--split", dest="split_filter", help="filter by split name")
    parser.add_argument("--output-root", type=Path, help="override report output root")
    parser.add_argument(
        "--write-template",
        type=Path,
        help="write a discovered manifest template and exit",
    )
    args = parser.parse_args(argv)

    if args.write_template:
        path = write_manifest_template(args.root, args.write_template)
        print(path)
        return 0

    result = run_real_suite(
        args.root,
        manifest_path=args.manifest,
        render=args.render,
        compute_device=args.compute_device,
        source_separation=args.source_separation,
        max_cases=args.max_cases,
        case_filter=args.case_filter,
        split_filter=args.split_filter,
        output_root=args.output_root,
    )
    print(result.summary_path)
    print(result.markdown_path)
    return _result_exit_code(result)


def _load_cases_from_manifest(root: Path, manifest_path: Path) -> list[RealCase]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("format") != REAL_CASE_FORMAT:
        raise ValueError(f"Unexpected real test manifest format: {data.get('format')!r}")

    cases: list[RealCase] = []
    for entry in data.get("cases", []):
        reference = _resolve_manifest_path(root, entry["reference"])
        material_directory = _resolve_manifest_path(root, entry["material_directory"])
        lyrics_value = str(entry.get("lyrics_file", "") or "").strip()
        lyrics_file = _resolve_manifest_path(root, lyrics_value) if lyrics_value else None
        output_directory = _resolve_manifest_path(root, entry["output_directory"])
        cases.append(
            RealCase(
                name=str(entry["name"]),
                reference_path=reference,
                material_directory=material_directory,
                lyrics_file=lyrics_file,
                output_directory=output_directory,
                split=str(entry.get("split", "regression") or "regression"),
                language=str(entry.get("language", "") or _infer_language(reference.stem)),
                expected_order=tuple(str(value) for value in entry.get("expected_order", []) if str(value).strip()),
            )
        )
    return cases


def _filter_language_compatible_cases(cases: Sequence[RealCase]) -> tuple[list[RealCase], list[SkippedRealCase]]:
    compatible_cases: list[RealCase] = []
    skipped_cases: list[SkippedRealCase] = []
    material_paths_by_directory: dict[Path, list[Path]] = {}
    for case in cases:
        material_key = case.material_directory.expanduser()
        if material_key not in material_paths_by_directory:
            try:
                material_paths_by_directory[material_key] = list_audio_files(case.material_directory)
            except Exception:
                material_paths_by_directory[material_key] = []
        material_paths = material_paths_by_directory[material_key]
        compatibility = reference_material_language_compatibility(
            case.reference_path,
            case.material_directory,
            lyrics_file=case.lyrics_file,
            material_paths=material_paths,
            reference_language=case.language,
        )
        if compatibility.get("status") == "mismatch":
            skipped_cases.append(
                SkippedRealCase(
                    name=case.name,
                    reference_path=case.reference_path,
                    material_directory=case.material_directory,
                    lyrics_file=case.lyrics_file,
                    output_directory=case.output_directory,
                    split=case.split,
                    language=case.language,
                    reason="language_mismatch",
                    language_compatibility=compatibility,
                )
            )
            continue
        compatible_cases.append(case)
    return compatible_cases, skipped_cases


def _skipped_case_payload(case: SkippedRealCase) -> dict[str, Any]:
    return {
        "name": case.name,
        "split": case.split,
        "language": case.language,
        "reference": str(case.reference_path),
        "material_directory": str(case.material_directory),
        "lyrics_file": str(case.lyrics_file) if case.lyrics_file else "",
        "output_directory": str(case.output_directory),
        "reason": case.reason,
        "language_compatibility": case.language_compatibility,
    }


def _resolve_manifest_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _find_lyrics_file(lyrics_root: Path, stem: str) -> Path | None:
    if not lyrics_root.exists():
        return None

    candidates: list[Path] = []
    search_stems = [stem]
    language = _infer_language(stem)
    if language:
        search_stems.append(stem[: -(len(language) + 1)])

    for file in lyrics_root.rglob("*"):
        if not file.is_file():
            continue
        if file.suffix.lower() not in {".txt", ".doc", ".docx", ".lrc", ".srt"}:
            continue
        if file.stem in search_stems or file.name in {f"{value}{file.suffix}" for value in search_stems}:
            candidates.append(file)
    return sorted(candidates)[0] if candidates else None


def _infer_language(stem: str) -> str:
    return infer_cn_jp_language_from_name(stem)


def _suite_summary(
    *,
    root: Path,
    manifest_path: Path | None,
    render: bool,
    results: Sequence[RealCaseResult],
    planned_case_count: int,
    runtime_preflight: dict[str, Any],
    skipped_cases: Sequence[SkippedRealCase],
) -> dict[str, Any]:
    infrastructure_blocker = _suite_infrastructure_blocker(
        results,
        planned_case_count=planned_case_count,
        runtime_preflight=runtime_preflight,
    )
    return {
        "format": REAL_REPORT_FORMAT,
        "root": str(root),
        "manifest_path": str(manifest_path) if manifest_path else "",
        "render": render,
        "case_count": len(results),
        "completed_case_count": len(results),
        "planned_case_count": planned_case_count,
        "discovered_case_count": planned_case_count + len(skipped_cases),
        "skipped_case_count": len(skipped_cases),
        "skipped_case_counts": _count_by((case.reason for case in skipped_cases)),
        "skipped_cases": [_skipped_case_payload(case) for case in skipped_cases],
        "split_counts": _count_by((result.case.split for result in results)),
        "language_counts": _count_by((result.case.language or "unknown" for result in results)),
        "status_counts": _count_by((result.status for result in results)),
        "warning_counts": _count_by(
            warning.get("kind", "unknown") for result in results for warning in result.warnings
        ),
        "score_summary": _suite_score_summary(results),
        "group_score_summary": _suite_group_score_summary(results),
        "runtime_preflight": runtime_preflight,
        "infrastructure_blocker": infrastructure_blocker,
        "recommended_exit_code": INFRASTRUCTURE_BLOCKED_EXIT_CODE
        if infrastructure_blocker["blocked"]
        else 0,
    }


def _write_suite_outputs(
    summary_path: Path,
    markdown_path: Path,
    suite_summary: dict[str, Any],
    results: Sequence[RealCaseResult],
) -> None:
    skipped_cases = suite_summary.get("skipped_cases", [])
    summary_path.write_text(
        json.dumps(
            {
                "format": REAL_REPORT_FORMAT,
                "suite": suite_summary,
                "cases": [_case_result_payload(result) for result in results],
                "skipped_cases": skipped_cases if isinstance(skipped_cases, list) else [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown_report(suite_summary, results), encoding="utf-8")


def _case_result_payload(result: RealCaseResult) -> dict[str, Any]:
    return {
        "name": result.case.name,
        "split": result.case.split,
        "language": result.case.language,
        "reference": str(result.case.reference_path),
        "material_directory": str(result.case.material_directory),
        "lyrics_file": str(result.case.lyrics_file) if result.case.lyrics_file else "",
        "output_directory": str(result.case.output_directory),
        "status": result.status,
        "analysis_report_path": str(result.analysis_report_path),
        "output_path": str(result.output_path) if result.output_path else "",
        "render_report_path": str(result.render_report_path) if result.render_report_path else "",
        "summary": result.summary,
        "warnings": result.warnings,
    }


def _case_failure_report(case: RealCase, exc: Exception) -> dict[str, Any]:
    warning = _analysis_failure_warning(exc)
    return {
        "format": "vocal_process_preflight_analysis_v1",
        "reference_path": str(case.reference_path.expanduser()),
        "material_directory": str(case.material_directory.expanduser()),
        "lyrics_file": str(case.lyrics_file.expanduser()) if case.lyrics_file else "",
        "status": "analysis_failed",
        "summary": {
            "material_count": 0,
            "warning_count": 1,
            "error_warning_count": 1,
            "review_required_match_count": 0,
            "minimum_match_score": 0.0,
            "extreme_stretch_count": 0,
            "moderate_stretch_count": 0,
        },
        "warnings": [warning],
        "optimization": {},
        "ordering": {"ordering": [], "timeline_alignment": {}},
        "stretch_plan": [],
    }


def _case_blocked_report(
    case: RealCase,
    source_report: dict[str, Any],
    *,
    blocked_by_case: str,
) -> dict[str, Any]:
    source_warnings = source_report.get("warnings", []) if isinstance(source_report, dict) else []
    source_warning = next(
        (
            warning
            for warning in source_warnings
            if isinstance(warning, dict) and warning.get("kind") in INFRASTRUCTURE_WARNING_KINDS
        ),
        {},
    )
    kind = str(source_warning.get("kind") or "analysis_exception")
    warning = {
        "severity": "error",
        "kind": kind,
        "infrastructure_blocker": kind in INFRASTRUCTURE_WARNING_KINDS,
        "blocked_by_case": blocked_by_case,
        "message": (
            f"Skipped after shared real-eval infrastructure blocker was detected in {blocked_by_case}: "
            f"{source_warning.get('message', '')}"
        ),
    }
    if source_warning.get("hint"):
        warning["hint"] = source_warning["hint"]
    return {
        "format": "vocal_process_preflight_analysis_v1",
        "reference_path": str(case.reference_path.expanduser()),
        "material_directory": str(case.material_directory.expanduser()),
        "lyrics_file": str(case.lyrics_file.expanduser()) if case.lyrics_file else "",
        "status": "analysis_blocked",
        "summary": {
            "material_count": 0,
            "warning_count": 1,
            "error_warning_count": 1,
            "review_required_match_count": 0,
            "minimum_match_score": 0.0,
            "extreme_stretch_count": 0,
            "moderate_stretch_count": 0,
        },
        "warnings": [warning],
        "optimization": {},
        "ordering": {"ordering": [], "timeline_alignment": {}},
        "stretch_plan": [],
    }


def _report_has_infrastructure_blocker(report: dict[str, Any]) -> bool:
    warnings = report.get("warnings", []) if isinstance(report, dict) else []
    return any(
        isinstance(warning, dict) and warning.get("kind") in INFRASTRUCTURE_WARNING_KINDS
        for warning in warnings
    )


def _runtime_preflight_report(compute_device: str) -> dict[str, Any]:
    try:
        return speech_runtime_preflight_report(compute_device)
    except Exception as exc:
        return {
            "preferred_backend": "",
            "allow_model_download": False,
            "requested_compute_device": compute_device,
            "resolved_compute_device": compute_device,
            "available": False,
            "issue": f"Speech runtime preflight failed: {exc}",
        }


def _analysis_failure_warning(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    kind = _analysis_failure_kind(message)
    warning: dict[str, Any] = {
        "severity": "error",
        "kind": kind,
        "message": message,
    }
    if kind in INFRASTRUCTURE_WARNING_KINDS:
        warning["infrastructure_blocker"] = True
        hint = _infrastructure_failure_hint(kind)
        if hint:
            warning["hint"] = hint
    return warning


def _analysis_failure_kind(message: str) -> str:
    lowered = message.lower()
    if "language mismatch" in lowered:
        return "language_mismatch"
    if "local model cache not found and downloads are not enabled" in lowered:
        return "asr_model_cache_missing"
    if (
        "huggingface.co" in lowered
        or "hugging face" in lowered
        or "maxretryerror" in lowered
        or "sslcertverificationerror" in lowered
        or "certificate_verify_failed" in lowered
    ) and ("whisper" in lowered or "model" in lowered or "download" in lowered):
        return "asr_model_download_failed"
    if (
        "speech recognition runtime is unavailable" in lowered
        or "torch._c" in lowered
        or "pytorch native" in lowered
        or "not loadable" in lowered
    ):
        return "speech_runtime_unavailable"
    if "ffmpeg executable could not be started" in lowered or "required tool not found" in lowered:
        return "runtime_tool_unavailable"
    return "analysis_exception"


def _infrastructure_failure_hint(kind: str) -> str:
    if kind == "asr_model_download_failed":
        return (
            "WhisperX character alignment is required for strict rendered evaluation. "
            "Fix network/certificate access to Hugging Face or pre-populate the local model cache, then rerun."
        )
    if kind == "asr_model_cache_missing":
        return "Enable model downloads for setup or pre-populate the local model cache before rendered evaluation."
    if kind == "speech_runtime_unavailable":
        return "Repair the local ASR/PyTorch runtime before evaluating pronunciation-level timing."
    if kind == "runtime_tool_unavailable":
        return "Repair FFmpeg/FFprobe resolution before rendering or ASR transcription."
    return ""


def _report_with_extra_warnings(report: dict[str, Any], warnings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(report)
    updated["warnings"] = list(warnings)
    summary = dict(updated.get("summary", {})) if isinstance(updated.get("summary", {}), dict) else {}
    summary["warning_count"] = len(warnings)
    summary["error_warning_count"] = sum(1 for warning in warnings if warning.get("severity") == "error")
    updated["summary"] = summary
    return updated


def _case_summary(
    report: dict[str, Any],
    render_summary: dict[str, Any],
    *,
    reference_path: Path | None = None,
    output_path: Path | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    summary = dict(report.get("summary", {}))
    ordering = report.get("ordering", {})
    ordering_entries = ordering.get("ordering", []) if isinstance(ordering, dict) else []
    timeline_alignment = ordering.get("timeline_alignment", {}) if isinstance(ordering, dict) else {}
    scorecard = _alignment_scorecard(
        report,
        render_summary,
        reference_path=reference_path,
        output_path=output_path,
        ordering_entries=ordering_entries if isinstance(ordering_entries, list) else [],
        timeline_alignment=timeline_alignment if isinstance(timeline_alignment, dict) else {},
    )
    summary.update(
        {
            "status": status or report.get("status", "unknown"),
            "analysis_status": report.get("status", "unknown"),
            "warning_count": len(report.get("warnings", [])),
            "ordered_material_count": len(ordering_entries) if isinstance(ordering_entries, list) else 0,
            "render_summary": render_summary,
            "timeline_alignment": timeline_alignment,
            "scorecard": scorecard,
            "match_score_mean": scorecard["match_score_mean"],
            "match_ordering_score": scorecard["match_ordering_score"],
            "positioned_decision_ratio": scorecard["positioned_decision_ratio"],
            "timed_target_duration_ratio": scorecard["timed_target_duration_ratio"],
            "aligned_timing_score": scorecard["aligned_timing_score"],
            "target_duration_alignment_score": scorecard["target_duration_alignment_score"],
            "planning_alignment_score": scorecard["planning_alignment_score"],
            "rendered_audio_alignment_score": scorecard["rendered_audio_alignment_score"],
            "strict_render_pass": scorecard["strict_render_pass"],
            "render_validation": scorecard["render_validation"],
        }
    )
    return summary


def _alignment_scorecard(
    report: dict[str, Any],
    render_summary: dict[str, Any],
    *,
    reference_path: Path | None,
    output_path: Path | None,
    ordering_entries: Sequence[dict[str, Any]],
    timeline_alignment: dict[str, Any],
) -> dict[str, Any]:
    report_summary = report.get("summary", {}) if isinstance(report.get("summary", {}), dict) else {}
    material_count = _positive_int(
        report_summary.get("material_count"),
        fallback=len(ordering_entries),
    )
    decision_count = _positive_int(timeline_alignment.get("decision_count"), fallback=len(ordering_entries))
    scores = [_safe_float(entry.get("score")) for entry in ordering_entries]
    scores = [score for score in scores if score is not None]
    match_score_mean = _mean(scores)
    minimum_match_score = _safe_float(report_summary.get("minimum_match_score"))
    if minimum_match_score is None and scores:
        minimum_match_score = min(scores)
    review_required_count = _positive_int(report_summary.get("review_required_match_count"))
    error_warning_count = _positive_int(report_summary.get("error_warning_count"))
    extreme_stretch_count = _positive_int(report_summary.get("extreme_stretch_count"))
    moderate_stretch_count = _positive_int(report_summary.get("moderate_stretch_count"))

    review_ratio = _ratio(review_required_count, material_count)
    match_ordering_score = _clamp01((match_score_mean or 0.0) * (1.0 - review_ratio))

    positioned_decision_count = _positive_int(timeline_alignment.get("positioned_decision_count"))
    resolved_target_count = _positive_int(timeline_alignment.get("resolved_target_duration_count"))
    timed_target_count = _positive_int(timeline_alignment.get("timed_target_duration_count"))
    positioned_decision_ratio = _ratio(positioned_decision_count, decision_count)
    resolved_target_duration_ratio = _ratio(resolved_target_count, decision_count)
    timed_target_duration_ratio = _ratio(timed_target_count, decision_count)
    timeline_position_score = _clamp01(0.7 * positioned_decision_ratio + 0.3 * resolved_target_duration_ratio)
    aligned_timing_score = timed_target_duration_ratio

    stretch_penalty = _ratio(extreme_stretch_count + 0.5 * moderate_stretch_count, material_count)
    stretch_quality_score = _clamp01(1.0 - stretch_penalty)

    reference_duration_seconds = _probe_duration(reference_path)
    target_duration_total_seconds = _safe_float(timeline_alignment.get("target_duration_total_seconds"))
    if target_duration_total_seconds is None:
        target_duration_total_seconds = _stretch_target_total(report.get("stretch_plan", []))
    target_duration_delta_seconds = _duration_delta(target_duration_total_seconds, reference_duration_seconds)
    target_duration_delta_ratio = _duration_delta_ratio(
        target_duration_total_seconds,
        reference_duration_seconds,
    )
    target_duration_alignment_score = _duration_alignment_score(target_duration_delta_ratio)

    render_validation = _render_validation(render_summary, reference_duration_seconds, output_path)
    rendered_audio_alignment_score = None
    render_duration_score = render_validation.get("duration_alignment_score")
    planning_alignment_score = _weighted_score(
        [
            (match_ordering_score, 0.35),
            (timeline_position_score, 0.15),
            (aligned_timing_score, 0.25),
            (target_duration_alignment_score, 0.15),
            (stretch_quality_score, 0.1),
        ]
    )
    if render_duration_score is not None:
        rendered_audio_alignment_score = _weighted_score(
            [
                (planning_alignment_score, 0.75),
                (render_duration_score, 0.25),
            ]
        )

    strict_render_pass = (
        render_validation.get("status") == "ok"
        and (minimum_match_score or 0.0) >= LOW_MATCH_SCORE
        and review_required_count == 0
        and error_warning_count == 0
        and target_duration_delta_ratio is not None
        and target_duration_delta_ratio <= STRICT_DURATION_TOLERANCE_RATIO
        and positioned_decision_ratio >= 0.95
        and timed_target_duration_ratio >= 0.95
    )

    return {
        "format": "vocal_process_real_eval_scorecard_v1",
        "metric_basis": (
            "material ordering match scores, pronunciation/text timeline positioning, "
            "planned target duration total, and rendered output duration when --render is enabled"
        ),
        "material_count": material_count,
        "decision_count": decision_count,
        "match_score_mean": _round_float(match_score_mean),
        "minimum_match_score": _round_float(minimum_match_score),
        "review_required_match_count": review_required_count,
        "review_required_match_ratio": _round_float(review_ratio),
        "match_ordering_score": _round_float(match_ordering_score),
        "positioned_decision_count": positioned_decision_count,
        "positioned_decision_ratio": _round_float(positioned_decision_ratio),
        "resolved_target_duration_count": resolved_target_count,
        "resolved_target_duration_ratio": _round_float(resolved_target_duration_ratio),
        "timed_target_duration_count": timed_target_count,
        "timed_target_duration_ratio": _round_float(timed_target_duration_ratio),
        "aligned_timing_score": _round_float(aligned_timing_score),
        "timeline_position_score": _round_float(timeline_position_score),
        "reference_duration_seconds": _round_float(reference_duration_seconds),
        "target_duration_total_seconds": _round_float(target_duration_total_seconds),
        "target_duration_delta_seconds": _round_float(target_duration_delta_seconds),
        "target_duration_delta_ratio": _round_float(target_duration_delta_ratio),
        "target_duration_alignment_score": _round_float(target_duration_alignment_score),
        "extreme_stretch_count": extreme_stretch_count,
        "moderate_stretch_count": moderate_stretch_count,
        "stretch_quality_score": _round_float(stretch_quality_score),
        "planning_alignment_score": _round_float(planning_alignment_score),
        "render_validation": render_validation,
        "rendered_audio_alignment_score": _round_float(rendered_audio_alignment_score),
        "strict_duration_tolerance_ratio": STRICT_DURATION_TOLERANCE_RATIO,
        "strict_render_pass": strict_render_pass,
    }


def _render_validation(
    render_summary: dict[str, Any],
    reference_duration_seconds: float | None,
    output_path: Path | None,
) -> dict[str, Any]:
    render_requested = bool(render_summary)
    output_exists = bool(output_path and output_path.exists())
    output_duration_seconds = _probe_duration(output_path) if output_exists else None
    delta_seconds = _duration_delta(output_duration_seconds, reference_duration_seconds)
    delta_ratio = _duration_delta_ratio(output_duration_seconds, reference_duration_seconds)
    duration_alignment_score = _duration_alignment_score(delta_ratio)
    if not render_requested:
        status = "not_requested"
    elif not output_exists:
        status = "missing_output"
    elif output_duration_seconds is None or reference_duration_seconds is None:
        status = "duration_unavailable"
    elif delta_ratio is not None and delta_ratio <= STRICT_DURATION_TOLERANCE_RATIO:
        status = "ok"
    else:
        status = "duration_mismatch"
    return {
        "format": "vocal_process_render_validation_v1",
        "status": status,
        "render_requested": render_requested,
        "output_exists": output_exists,
        "output_path": str(output_path) if output_path else "",
        "output_duration_seconds": _round_float(output_duration_seconds),
        "reference_duration_seconds": _round_float(reference_duration_seconds),
        "duration_delta_seconds": _round_float(delta_seconds),
        "duration_delta_ratio": _round_float(delta_ratio),
        "duration_alignment_score": _round_float(duration_alignment_score),
    }


def _suite_score_summary(results: Sequence[RealCaseResult]) -> dict[str, Any]:
    planning_scores = _summary_values(results, "planning_alignment_score")
    render_scores = _summary_values(results, "rendered_audio_alignment_score")
    match_scores = _summary_values(results, "match_ordering_score")
    render_duration_ratios = [
        value
        for value in (
            _safe_float(result.summary.get("render_validation", {}).get("duration_delta_ratio"))
            for result in results
            if isinstance(result.summary.get("render_validation"), dict)
        )
        if value is not None
    ]
    strict_pass_count = sum(1 for result in results if result.summary.get("strict_render_pass") is True)
    return {
        "format": "vocal_process_real_suite_score_summary_v1",
        "planning_alignment_score": _stats(planning_scores),
        "rendered_audio_alignment_score": _stats(render_scores),
        "match_ordering_score": _stats(match_scores),
        "render_duration_delta_ratio": _stats(render_duration_ratios),
        "strict_render_pass_count": strict_pass_count,
        "strict_render_fail_count": len(results) - strict_pass_count,
    }


def _suite_group_score_summary(results: Sequence[RealCaseResult]) -> dict[str, Any]:
    return {
        "format": "vocal_process_real_suite_group_score_summary_v1",
        "by_split": _group_score_summary(results, lambda result: result.case.split or "unknown"),
        "by_language": _group_score_summary(results, lambda result: result.case.language or "unknown"),
        "by_reference": _group_score_summary(results, lambda result: result.case.reference_path.stem),
        "by_material_set": _group_score_summary(results, lambda result: result.case.material_directory.name),
    }


def _group_score_summary(
    results: Sequence[RealCaseResult],
    key_for_result: Callable[[RealCaseResult], str],
) -> dict[str, Any]:
    groups: dict[str, list[RealCaseResult]] = {}
    for result in results:
        key = str(key_for_result(result) or "unknown")
        groups.setdefault(key, []).append(result)
    return {
        key: {
            "case_count": len(group_results),
            "status_counts": _count_by((result.status for result in group_results)),
            "warning_counts": _count_by(
                warning.get("kind", "unknown") for result in group_results for warning in result.warnings
            ),
            "score_summary": _suite_score_summary(group_results),
        }
        for key, group_results in sorted(groups.items())
    }


def _suite_infrastructure_blocker(
    results: Sequence[RealCaseResult],
    *,
    planned_case_count: int,
    runtime_preflight: dict[str, Any],
) -> dict[str, Any]:
    failed_results = [result for result in results if result.status in {"analysis_failed", "analysis_blocked"}]
    infrastructure_failed_results = [
        result for result in failed_results if _result_has_infrastructure_blocker(result)
    ]
    all_planned_cases_failed_on_infrastructure = (
        planned_case_count > 0
        and len(results) == planned_case_count
        and len(infrastructure_failed_results) == planned_case_count
    )
    preflight_issue = str(runtime_preflight.get("issue") or "")
    case_failure_messages = _unique_case_failure_messages(infrastructure_failed_results)
    message = ""
    if all_planned_cases_failed_on_infrastructure:
        message = (
            "Every planned real-eval case was blocked before pronunciation/timeline analysis could run. "
            "Treat this as a shared model/runtime environment problem, not as ordering-score evidence."
        )
    elif preflight_issue:
        message = "Speech runtime preflight reported an issue; inspect runtime_preflight before accepting scores."

    return {
        "format": "vocal_process_real_suite_infrastructure_blocker_v1",
        "blocked": all_planned_cases_failed_on_infrastructure,
        "runtime_preflight_available": bool(runtime_preflight.get("available")),
        "runtime_preflight_issue": preflight_issue,
        "planned_case_count": planned_case_count,
        "completed_case_count": len(results),
        "analysis_failed_case_count": sum(1 for result in results if result.status == "analysis_failed"),
        "analysis_blocked_case_count": sum(1 for result in results if result.status == "analysis_blocked"),
        "infrastructure_failed_case_count": len(infrastructure_failed_results),
        "case_failure_kinds": _count_by(
            warning.get("kind", "unknown")
            for result in infrastructure_failed_results
            for warning in result.warnings
            if warning.get("kind") in INFRASTRUCTURE_WARNING_KINDS
        ),
        "case_failure_messages": case_failure_messages,
        "message": message,
    }


def _result_has_infrastructure_blocker(result: RealCaseResult) -> bool:
    return any(warning.get("kind") in INFRASTRUCTURE_WARNING_KINDS for warning in result.warnings)


def _unique_case_failure_messages(results: Sequence[RealCaseResult]) -> list[str]:
    messages: list[str] = []
    seen: set[str] = set()
    for result in results:
        for warning in result.warnings:
            if warning.get("kind") not in INFRASTRUCTURE_WARNING_KINDS:
                continue
            if warning.get("blocked_by_case"):
                continue
            message = str(warning.get("message") or "")
            normalized = _failure_message_signature(message)
            if normalized in seen:
                continue
            seen.add(normalized)
            messages.append(message[:500])
            if len(messages) >= 5:
                return messages
    return messages


def _failure_message_signature(message: str) -> str:
    normalized = re.sub(r"Request ID: [0-9A-Fa-f-]+", "Request ID: <id>", message)
    normalized = re.sub(
        r"tests_real\\origin_vocal\\[^:|\"')]+",
        r"tests_real\\origin_vocal\\<reference>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[A-Za-z]:\\[^:|\"')]+", "<path>", normalized)
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _result_exit_code(result: RealSuiteResult) -> int:
    try:
        payload = json.loads(result.summary_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    suite = payload.get("suite", {}) if isinstance(payload, dict) else {}
    try:
        return max(int(suite.get("recommended_exit_code", 0)), 0)
    except (TypeError, ValueError):
        return 0


def _summary_values(results: Sequence[RealCaseResult], key: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = _safe_float(result.summary.get(key))
        if value is not None:
            values.append(value)
    return values


def _stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "min": _round_float(min(values)),
        "max": _round_float(max(values)),
        "mean": _round_float(sum(values) / len(values)),
    }


def _probe_duration(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        duration = get_audio_duration_seconds(probe_audio(path))
    except Exception:
        return None
    if duration <= 0:
        return None
    return duration


def _stretch_target_total(stretch_plan: Any) -> float | None:
    if not isinstance(stretch_plan, list):
        return None
    values = [_safe_float(entry.get("target_duration_seconds")) for entry in stretch_plan if isinstance(entry, dict)]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values)


def _duration_delta(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None:
        return None
    return value - reference


def _duration_delta_ratio(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference <= 0:
        return None
    return abs(value - reference) / reference


def _duration_alignment_score(delta_ratio: float | None) -> float | None:
    if delta_ratio is None:
        return None
    return _clamp01(1.0 - delta_ratio)


def _weighted_score(parts: Sequence[tuple[float | None, float]]) -> float | None:
    total_weight = 0.0
    total = 0.0
    for value, weight in parts:
        if value is None:
            continue
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return _clamp01(total / total_weight)


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp01(float(numerator) / float(denominator))


def _positive_int(value: Any, *, fallback: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return max(int(fallback), 0)


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _count_by(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _render_markdown_report(summary: dict[str, Any], results: Sequence[RealCaseResult]) -> str:
    lines = [
        "# Real Audio Evaluation Report",
        "",
        f"- Root: `{summary['root']}`",
        f"- Render: `{summary['render']}`",
        f"- Completed cases: `{summary.get('completed_case_count', summary['case_count'])}` / "
        f"`{summary.get('planned_case_count', summary['case_count'])}`",
        f"- Skipped cases: `{summary.get('skipped_case_count', 0)}` / "
        f"`{summary.get('discovered_case_count', summary.get('planned_case_count', summary['case_count']))}`",
        "",
        "## Counts",
        "",
        f"- Split counts: `{json.dumps(summary['split_counts'], ensure_ascii=False)}`",
        f"- Language counts: `{json.dumps(summary['language_counts'], ensure_ascii=False)}`",
        f"- Status counts: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- Warning counts: `{json.dumps(summary['warning_counts'], ensure_ascii=False)}`",
        f"- Skipped counts: `{json.dumps(summary.get('skipped_case_counts', {}), ensure_ascii=False)}`",
        f"- Score summary: `{json.dumps(summary.get('score_summary', {}), ensure_ascii=False)}`",
        f"- Infrastructure blocker: `{json.dumps(summary.get('infrastructure_blocker', {}), ensure_ascii=False)}`",
        "",
        *_render_group_score_markdown(summary),
        "",
        *_render_skipped_cases_markdown(summary),
        "",
        "## Cases",
        "",
        (
            "| Case | Split | Language | Status | Plan Score | Render Score | Min Match | "
            "Mean Match | Positioned | Timed | Target Delta | Render Delta | Strict Pass | Review Needed |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        case_summary = result.summary
        scorecard = case_summary.get("scorecard", {})
        render_validation = case_summary.get("render_validation", {})
        min_score = case_summary.get("minimum_match_score", "")
        mean_score = case_summary.get("match_score_mean", "")
        plan_score = case_summary.get("planning_alignment_score", "")
        render_score = case_summary.get("rendered_audio_alignment_score", "")
        positioned_ratio = case_summary.get("positioned_decision_ratio", "")
        timed_ratio = case_summary.get("timed_target_duration_ratio", "")
        target_delta = scorecard.get("target_duration_delta_seconds", "") if isinstance(scorecard, dict) else ""
        render_delta = (
            render_validation.get("duration_delta_seconds", "") if isinstance(render_validation, dict) else ""
        )
        review_count = case_summary.get("review_required_match_count", "")
        lines.append(
            f"| {result.case.name} | {result.case.split} | {result.case.language or 'unknown'} | "
            f"{result.status} | {_markdown_value(plan_score)} | {_markdown_value(render_score)} | "
            f"{_markdown_value(min_score)} | {_markdown_value(mean_score)} | {_markdown_value(positioned_ratio)} | "
            f"{_markdown_value(timed_ratio)} | {_markdown_value(target_delta)} | {_markdown_value(render_delta)} | "
            f"{case_summary.get('strict_render_pass', False)} | {review_count} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_skipped_cases_markdown(summary: dict[str, Any]) -> list[str]:
    skipped_cases = summary.get("skipped_cases", [])
    if not isinstance(skipped_cases, list) or not skipped_cases:
        return []
    lines = [
        "## Skipped Cases",
        "",
        "| Case | Split | Reference Lang | Material Lang | Reason | Message |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in skipped_cases:
        if not isinstance(case, dict):
            continue
        compatibility = case.get("language_compatibility", {})
        if not isinstance(compatibility, dict):
            compatibility = {}
        lines.append(
            f"| {case.get('name', '')} | {case.get('split', '')} | "
            f"{compatibility.get('reference_language') or 'unknown'} | "
            f"{compatibility.get('material_set_language') or 'unknown'} | "
            f"{case.get('reason', '')} | {_markdown_value(compatibility.get('message', ''))} |"
        )
    return lines


def _render_group_score_markdown(summary: dict[str, Any]) -> list[str]:
    group_summary = summary.get("group_score_summary", {})
    if not isinstance(group_summary, dict):
        return []
    lines = [
        "## Group Scores",
        "",
        (
            "| Group | Name | Cases | Plan Mean | Render Mean | Match Mean | "
            "Strict Pass/Fail | Status Counts |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    row_count = 0
    for group_name in ("by_language", "by_reference", "by_material_set", "by_split"):
        entries = group_summary.get(group_name, {})
        if not isinstance(entries, dict):
            continue
        for name, payload in entries.items():
            if not isinstance(payload, dict):
                continue
            score_summary = payload.get("score_summary", {})
            if not isinstance(score_summary, dict):
                score_summary = {}
            strict_pass = score_summary.get("strict_render_pass_count", 0)
            strict_fail = score_summary.get("strict_render_fail_count", 0)
            lines.append(
                f"| {group_name} | {name} | {payload.get('case_count', 0)} | "
                f"{_markdown_value(_score_stat_mean(score_summary, 'planning_alignment_score'))} | "
                f"{_markdown_value(_score_stat_mean(score_summary, 'rendered_audio_alignment_score'))} | "
                f"{_markdown_value(_score_stat_mean(score_summary, 'match_ordering_score'))} | "
                f"{strict_pass}/{strict_fail} | "
                f"{_markdown_value(json.dumps(payload.get('status_counts', {}), ensure_ascii=False))} |"
            )
            row_count += 1
    if row_count == 0:
        return []
    return lines


def _score_stat_mean(score_summary: dict[str, Any], key: str) -> Any:
    stat = score_summary.get(key, {})
    if not isinstance(stat, dict):
        return None
    return stat.get("mean")


def _markdown_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())

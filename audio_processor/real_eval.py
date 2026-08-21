from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .batch import QueueItem, run_batch_queue
from .engine import AudioProcessorError, get_audio_duration_seconds, list_audio_files, probe_audio
from .diagnostics import diagnostic_log_path
from .model_runtime import (
    analyze_reference_channel_topology,
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
REAL_EVAL_FAILED_EXIT_CODE = 1
INFRASTRUCTURE_BLOCKED_EXIT_CODE = 2
CANCELLED_EXIT_CODE = 130
FAILED_EXECUTION_STATUSES = {"analysis_failed", "analysis_blocked", "render_failed", "render_blocked"}
RENDERED_EVAL_DEFAULT_ASR_BACKEND = "whisperx"
CancelCallback = Callable[[], bool]


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
    output_root = root / "output" / "audio"
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


def _real_eval_suite_root(root: Path, output_root: Path | None) -> Path:
    if output_root is None:
        return root.expanduser() / "output"
    resolved = output_root.expanduser()
    if resolved.name.lower() == "reports":
        return resolved.parent
    return resolved


def _real_eval_report_root(root: Path, output_root: Path | None) -> Path:
    if output_root is not None and output_root.expanduser().name.lower() == "reports":
        return output_root.expanduser()
    return _real_eval_suite_root(root, output_root) / "reports"


def _real_eval_audio_root(root: Path, output_root: Path | None) -> Path:
    return _real_eval_suite_root(root, output_root) / "audio"


def _real_eval_cache_root(root: Path, output_root: Path | None) -> Path:
    return _real_eval_suite_root(root, output_root) / "cache"


def _case_report_directory(report_directory: Path, case: RealCase | SkippedRealCase) -> Path:
    return report_directory / "cases" / case.name


def _real_eval_case_cache_directory(root: Path, output_root: Path | None, case: RealCase) -> Path:
    return _real_eval_cache_root(root, output_root) / case.reference_path.stem / case.material_directory.name


def _rebase_case_output_directories(
    cases: Sequence[RealCase],
    audio_root: Path,
) -> list[RealCase]:
    return [
        RealCase(
            name=case.name,
            reference_path=case.reference_path,
            material_directory=case.material_directory,
            lyrics_file=case.lyrics_file,
            output_directory=audio_root / case.reference_path.stem / case.material_directory.name,
            split=case.split,
            language=case.language,
            expected_order=case.expected_order,
        )
        for case in cases
    ]


def _rebase_skipped_case_output_directories(
    cases: Sequence[SkippedRealCase],
    audio_root: Path,
) -> list[SkippedRealCase]:
    return [
        SkippedRealCase(
            name=case.name,
            reference_path=case.reference_path,
            material_directory=case.material_directory,
            lyrics_file=case.lyrics_file,
            output_directory=audio_root / case.reference_path.stem / case.material_directory.name,
            split=case.split,
            language=case.language,
            reason=case.reason,
            language_compatibility=case.language_compatibility,
        )
        for case in cases
    ]


def run_real_suite(
    root: Path,
    *,
    manifest_path: Path | None = None,
    render: bool = False,
    allow_unverified_reference_render: bool = False,
    compute_device: str = "auto",
    source_separation: str = "never",
    max_cases: int | None = None,
    case_filter: str | None = None,
    split_filter: str | None = None,
    output_root: Path | None = None,
    stop_file: Path | None = None,
    should_cancel: CancelCallback | None = None,
) -> RealSuiteResult:
    root = root.expanduser()
    cancel_checker = _combined_cancel_checker(should_cancel, stop_file)
    cases, skipped_cases = discover_real_cases_with_skips(root, manifest_path=manifest_path)
    if manifest_path is None:
        audio_root = _real_eval_audio_root(root, output_root)
        cases = _rebase_case_output_directories(cases, audio_root)
        skipped_cases = _rebase_skipped_case_output_directories(skipped_cases, audio_root)
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
    report_directory = _real_eval_report_root(root, output_root) / f"real-eval-{timestamp}"
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
        if _should_cancel(cancel_checker):
            _append_cancelled_case_results(results, cases[case_index:], report_directory)
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

        case.output_directory.mkdir(parents=True, exist_ok=True)
        case_report_directory = _case_report_directory(report_directory, case)
        analysis_path = case_report_directory / "analysis.json"
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            render
            and not allow_unverified_reference_render
            and not _case_has_verified_reference_text(case)
        ):
            warning = _missing_verified_reference_text_render_warning(case)
            report = _case_render_blocked_report(case, warning)
            _write_json_atomic(analysis_path, report)
            render_summary = {
                "blocked": True,
                "blocker_kind": warning["kind"],
                "message": warning["message"],
            }
            result = RealCaseResult(
                case=case,
                status="render_blocked",
                analysis_report_path=analysis_path,
                output_path=None,
                render_report_path=None,
                summary=_case_summary(
                    report,
                    render_summary,
                    reference_path=case.reference_path,
                    output_path=None,
                    status="render_blocked",
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
            continue
        try:
            report = build_preflight_report(
                case.reference_path,
                case.material_directory,
                lyrics_file=case.lyrics_file,
                work_dir=case_report_directory / "work",
                material_cache_dir=case_report_directory / "material-analysis-cache",
                compute_device=compute_device,
                source_separation=source_separation,
                should_cancel=cancel_checker,
            )
        except Exception as exc:
            if _is_cancellation_exception(exc):
                report = _case_cancelled_report(case, "Processing cancelled")
                _write_json_atomic(analysis_path, report)
                results.append(
                    RealCaseResult(
                        case=case,
                        status="cancelled",
                        analysis_report_path=analysis_path,
                        output_path=None,
                        render_report_path=None,
                        summary=_case_summary(
                            report,
                            {},
                            reference_path=case.reference_path,
                            output_path=None,
                            status="cancelled",
                        ),
                        warnings=list(report.get("warnings", [])),
                    )
                )
                _append_cancelled_case_results(results, cases[case_index + 1 :], report_directory)
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

            report = _case_failure_report(case, exc)
            _write_json_atomic(analysis_path, report)
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
                    blocked_analysis_path = _case_report_directory(report_directory, blocked_case) / "analysis.json"
                    blocked_analysis_path.parent.mkdir(parents=True, exist_ok=True)
                    blocked_report = _case_blocked_report(
                        blocked_case,
                        report,
                        blocked_by_case=case.name,
                    )
                    _write_json_atomic(blocked_analysis_path, blocked_report)
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
        _write_json_atomic(analysis_path, report)

        warnings = list(report.get("warnings", []))
        render_output_path: Path | None = None
        render_report_path: Path | None = None
        status = report.get("status", "unknown")

        if render:
            render_blocker = _render_blocker_warning(
                report,
                allow_unverified_reference_render=allow_unverified_reference_render,
            )
            if render_blocker is not None:
                status = "render_blocked"
                warnings.append(render_blocker)
                report = _report_with_extra_warnings(report, warnings)
                render_summary = {
                    "blocked": True,
                    "blocker_kind": render_blocker["kind"],
                    "message": render_blocker["message"],
                }
            else:
                settings = ProcessingSettings(
                    material_directory=str(case.material_directory),
                    manual_lyrics_enabled=bool(case.lyrics_file),
                    lyrics_file=str(case.lyrics_file or ""),
                    split_reference_channels=True,
                    output_directory=str(case.output_directory),
                    output_extension=".wav",
                    overwrite=True,
                    compute_device=compute_device,
                    source_separation=source_separation,
                    render_cache_directory=str(_real_eval_case_cache_directory(root, output_root, case)),
                    diagnostics_directory=str(case_report_directory / "render"),
                )
                base_render_output_path = settings.output_path_for(case.reference_path)
                expected_output_paths = _expected_channel_output_paths(
                    case.reference_path,
                    base_render_output_path,
                )
                render_output_path = expected_output_paths[0]
                render_report_path = diagnostic_log_path(
                    base_render_output_path,
                    case_report_directory / "render",
                )
                try:
                    if _should_cancel(cancel_checker):
                        raise AudioProcessorError("Processing cancelled")
                    summary = run_batch_queue(
                        [QueueItem(case.reference_path, base_render_output_path)],
                        settings,
                        should_cancel=cancel_checker,
                    )
                    if summary.cancelled:
                        status = "cancelled"
                        warnings.append(_cancelled_warning("Real-eval rendering was cancelled."))
                    else:
                        status = "rendered" if summary.failed == 0 else "render_failed"
                    if not all(path.exists() for path in expected_output_paths):
                        status = "cancelled" if status == "cancelled" else "render_failed"
                    render_summary = {
                        "batch_summary": summary.__dict__,
                        "channel_output_paths": [str(path) for path in expected_output_paths],
                        "render_boundary_measurements": _read_render_boundary_measurements(
                            case_report_directory / "render",
                            expected_output_paths,
                        ),
                        "render_acoustic_drift_measurements": _read_render_acoustic_drift_measurements(
                            case_report_directory / "render",
                            expected_output_paths,
                        ),
                    }
                except Exception as exc:
                    status = "cancelled" if _is_cancellation_exception(exc) else "render_failed"
                    render_summary = {"error": str(exc)}
                    if status == "cancelled":
                        warnings.append(_cancelled_warning("Real-eval rendering was cancelled."))
                    else:
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
        if status == "cancelled":
            _append_cancelled_case_results(results, cases[case_index + 1 :], report_directory)
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
    parser.add_argument(
        "--allow-unverified-reference-render",
        action="store_true",
        help=(
            "Allow rendered output even when reference text comes only from unverified ASR. "
            "By default rendered real-eval blocks this because content matching cannot be trusted."
        ),
    )
    parser.add_argument(
        "--asr-backend",
        help=(
            "ASR backend for this run. Rendered eval defaults to whisperx when the environment "
            "does not already request a backend, because strict output review requires character timing."
        ),
    )
    parser.add_argument("--compute-device", default="auto", help="model runtime device: auto, cpu, or cuda")
    parser.add_argument("--source-separation", default="never", help="reference separation mode")
    parser.add_argument("--max-cases", type=int, help="limit the number of evaluated cases")
    parser.add_argument("--case", dest="case_filter", help="substring filter for case names")
    parser.add_argument("--split", dest="split_filter", help="filter by split name")
    parser.add_argument("--output-root", type=Path, help="override report output root")
    parser.add_argument("--stop-file", type=Path, help="stop-file path for graceful cancellation")
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

    asr_restore_state = _apply_rendered_eval_asr_backend(args.render, args.asr_backend)
    try:
        result = run_real_suite(
            args.root,
            manifest_path=args.manifest,
            render=args.render,
            allow_unverified_reference_render=args.allow_unverified_reference_render,
            compute_device=args.compute_device,
            source_separation=args.source_separation,
            max_cases=args.max_cases,
            case_filter=args.case_filter,
            split_filter=args.split_filter,
            output_root=args.output_root,
            stop_file=args.stop_file,
        )
    finally:
        _restore_asr_backend(asr_restore_state)
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


def _expected_channel_output_paths(reference_path: Path, base_output_path: Path) -> list[Path]:
    try:
        topology = analyze_reference_channel_topology(reference_path)
    except Exception:
        return [base_output_path]

    channels = topology.channel_count
    if not topology.split_recommended or channels <= 1:
        return [base_output_path]

    labels = ["left", "right"] if channels == 2 else [f"ch{index + 1}" for index in range(channels)]
    suffix = base_output_path.suffix
    stem = base_output_path.stem if suffix else base_output_path.name
    return [
        base_output_path.with_name(f"{stem}_{label}{suffix}")
        for label in labels
    ]


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
        "recommended_exit_code": _recommended_exit_code(results, infrastructure_blocker),
    }


def _recommended_exit_code(
    results: Sequence[RealCaseResult],
    infrastructure_blocker: dict[str, Any],
) -> int:
    if infrastructure_blocker["blocked"]:
        return INFRASTRUCTURE_BLOCKED_EXIT_CODE
    if any(result.status == "cancelled" for result in results):
        return CANCELLED_EXIT_CODE
    if any(result.status in FAILED_EXECUTION_STATUSES for result in results):
        return REAL_EVAL_FAILED_EXIT_CODE
    return 0


def _write_suite_outputs(
    summary_path: Path,
    markdown_path: Path,
    suite_summary: dict[str, Any],
    results: Sequence[RealCaseResult],
) -> None:
    skipped_cases = suite_summary.get("skipped_cases", [])
    _write_json_atomic(
        summary_path,
        {
            "format": REAL_REPORT_FORMAT,
            "suite": suite_summary,
            "cases": [_case_result_payload(result) for result in results],
            "skipped_cases": skipped_cases if isinstance(skipped_cases, list) else [],
        },
    )
    _write_text_atomic(markdown_path, _render_markdown_report(suite_summary, results))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


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


def _append_cancelled_case_results(
    results: list[RealCaseResult],
    cases: Sequence[RealCase],
    report_directory: Path,
) -> None:
    for case in cases:
        case.output_directory.mkdir(parents=True, exist_ok=True)
        analysis_path = _case_report_directory(report_directory, case) / "analysis.json"
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        report = _case_cancelled_report(case, "Real-eval cancellation requested before this case started.")
        _write_json_atomic(analysis_path, report)
        results.append(
            RealCaseResult(
                case=case,
                status="cancelled",
                analysis_report_path=analysis_path,
                output_path=None,
                render_report_path=None,
                summary=_case_summary(
                    report,
                    {},
                    reference_path=case.reference_path,
                    output_path=None,
                    status="cancelled",
                ),
                warnings=list(report.get("warnings", [])),
            )
        )


def _case_has_verified_reference_text(case: RealCase) -> bool:
    return bool(case.lyrics_file is not None and case.lyrics_file.expanduser().exists())


def _missing_verified_reference_text_render_warning(case: RealCase) -> dict[str, Any]:
    return {
        "severity": "error",
        "kind": "render_blocked_unverified_reference_text",
        "reference_path": str(case.reference_path.expanduser()),
        "message": (
            "Rendered real-eval requires a lyrics_file or another verified reference transcript. "
            "No verified reference text was discovered for this case, so producing review audio "
            "would rely on ASR-only content matching."
        ),
    }


def _case_render_blocked_report(case: RealCase, warning: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "vocal_process_preflight_analysis_v1",
        "reference_path": str(case.reference_path.expanduser()),
        "material_directory": str(case.material_directory.expanduser()),
        "lyrics_file": str(case.lyrics_file.expanduser()) if case.lyrics_file else "",
        "status": "render_blocked",
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


def _case_cancelled_report(case: RealCase, message: str) -> dict[str, Any]:
    warning = _cancelled_warning(message)
    return {
        "format": "vocal_process_preflight_analysis_v1",
        "reference_path": str(case.reference_path.expanduser()),
        "material_directory": str(case.material_directory.expanduser()),
        "lyrics_file": str(case.lyrics_file.expanduser()) if case.lyrics_file else "",
        "status": "cancelled",
        "summary": {
            "material_count": 0,
            "warning_count": 1,
            "error_warning_count": 0,
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


def _cancelled_warning(message: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "kind": "cancelled",
        "message": message,
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


def _combined_cancel_checker(
    should_cancel: CancelCallback | None,
    stop_file: Path | None,
) -> CancelCallback | None:
    resolved_stop_file = stop_file or _stop_file_from_environment()
    if resolved_stop_file is None and should_cancel is None:
        return None
    resolved_stop_file = resolved_stop_file.expanduser().resolve() if resolved_stop_file is not None else None

    def check() -> bool:
        if should_cancel is not None and should_cancel():
            return True
        return bool(resolved_stop_file is not None and resolved_stop_file.exists())

    return check


def _stop_file_from_environment() -> Path | None:
    for name in ("VOCAL_PROCESS_STOP_FILE", "CODEX_AGENT_STOP_FILE"):
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value.strip())
    return None


def _should_cancel(should_cancel: CancelCallback | None) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _is_cancellation_exception(exc: Exception) -> bool:
    return isinstance(exc, AudioProcessorError) and str(exc) == "Processing cancelled"


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
        or "localentrynotfounderror" in lowered
        or "filemetadataerror" in lowered
        or "locate the file on the hub" in lowered
        or "requested files in the local cache" in lowered
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


def _render_blocker_warning(
    report: dict[str, Any],
    *,
    allow_unverified_reference_render: bool = False,
) -> dict[str, Any] | None:
    if not allow_unverified_reference_render:
        content_blocker = _unverified_reference_render_blocker_warning(report)
        if content_blocker is not None:
            return content_blocker
    return _render_timing_blocker_warning(report)


def _unverified_reference_render_blocker_warning(report: dict[str, Any]) -> dict[str, Any] | None:
    warnings = report.get("warnings", []) if isinstance(report, dict) else []
    for warning in warnings if isinstance(warnings, list) else []:
        if not isinstance(warning, dict):
            continue
        if warning.get("kind") != "reference_asr_unverified":
            continue
        return {
            "severity": "error",
            "kind": "render_blocked_unverified_reference_text",
            "reference_path": warning.get("reference_path", ""),
            "backend": warning.get("backend", ""),
            "message": (
                "Rendered real-eval requires lyrics or another verified reference transcript. "
                "ASR-only reference text can preserve timing while still selecting the wrong "
                "pronunciation content, so this case is blocked from producing misleading review audio."
            ),
        }
    return None


def _render_timing_blocker_warning(report: dict[str, Any]) -> dict[str, Any] | None:
    warnings = report.get("warnings", []) if isinstance(report, dict) else []
    for warning in warnings if isinstance(warnings, list) else []:
        if isinstance(warning, dict) and warning.get("kind") == "lyric_timing_conflict":
            return {
                "severity": "error",
                "kind": "render_blocked_lyric_timing_conflict",
                "message": (
                    "Rendered real-eval requires reference timing that agrees with timestamped lyrics. "
                    "The current lyrics and ASR/acoustic timing disagree, so this case is blocked instead "
                    "of producing a timeline-misleading review file."
                ),
            }
        if isinstance(warning, dict) and warning.get("kind") == "resampled_aligned_unit_timing":
            resampled_count = _positive_int(warning.get("resampled_timing_lattice_count"))
            exact_timed_count = _positive_int(warning.get("exact_timed_target_duration_count"))
            timed_count = _positive_int(warning.get("timed_target_duration_count"))
            return {
                "severity": "error",
                "kind": "render_blocked_resampled_aligned_unit_timing",
                "resampled_timing_lattice_count": resampled_count,
                "exact_timed_target_duration_count": exact_timed_count,
                "timed_target_duration_count": timed_count,
                "message": (
                    "Rendered real-eval requires exact reference unit start/end timing. "
                    "This case uses resampled or interpolated unit timing, which can shift syllables "
                    "even when total duration matches the original vocal."
                ),
            }
        if isinstance(warning, dict) and warning.get("kind") == "missing_aligned_unit_timing":
            positioned_count = _positive_int(warning.get("positioned_decision_count"))
            timed_count = _positive_int(warning.get("timed_target_duration_count"))
            return {
                "severity": "error",
                "kind": "render_blocked_missing_aligned_unit_timing",
                "positioned_decision_count": positioned_count,
                "timed_target_duration_count": timed_count,
                "message": (
                    "Rendered real-eval requires aligned reference unit timing. "
                    "This case would otherwise fall back to proportional segment timing and produce "
                    "misleading review audio."
                ),
            }

    ordering = report.get("ordering", {}) if isinstance(report, dict) else {}
    timeline_alignment = ordering.get("timeline_alignment", {}) if isinstance(ordering, dict) else {}
    if not isinstance(timeline_alignment, dict):
        return None
    positioned_count = _positive_int(timeline_alignment.get("positioned_decision_count"))
    timed_count = _positive_int(timeline_alignment.get("timed_target_duration_count"))
    resampled_count = _positive_int(timeline_alignment.get("resampled_timing_lattice_count"))
    if resampled_count > 0:
        exact_timed_count = _positive_int(timeline_alignment.get("exact_timed_target_duration_count"))
        return {
            "severity": "error",
            "kind": "render_blocked_resampled_aligned_unit_timing",
            "resampled_timing_lattice_count": resampled_count,
            "exact_timed_target_duration_count": exact_timed_count,
            "timed_target_duration_count": timed_count,
            "message": (
                "Rendered real-eval requires exact reference unit start/end timing. "
                "This case uses resampled or interpolated unit timing, which can shift syllables "
                "even when total duration matches the original vocal."
            ),
        }
    if positioned_count <= 0 or timed_count >= positioned_count:
        return None
    return {
        "severity": "error",
        "kind": "render_blocked_missing_aligned_unit_timing",
        "positioned_decision_count": positioned_count,
        "timed_target_duration_count": timed_count,
        "message": (
            "Rendered real-eval requires aligned reference unit timing. "
            "This case would otherwise fall back to proportional segment timing and produce "
            "misleading review audio."
        ),
    }


def _apply_rendered_eval_asr_backend(
    render: bool,
    requested_backend: str | None,
) -> tuple[bool, str | None] | None:
    backend = str(requested_backend or "").strip()
    if not backend and render:
        current = os.environ.get("VOCAL_PROCESS_ASR_BACKEND", "").strip()
        if not current or current.lower() == "auto":
            backend = RENDERED_EVAL_DEFAULT_ASR_BACKEND
    if not backend:
        return None
    existed = "VOCAL_PROCESS_ASR_BACKEND" in os.environ
    previous = os.environ.get("VOCAL_PROCESS_ASR_BACKEND")
    os.environ["VOCAL_PROCESS_ASR_BACKEND"] = backend
    return existed, previous


def _restore_asr_backend(state: tuple[bool, str | None] | None) -> None:
    if state is None:
        return
    existed, previous = state
    if existed:
        os.environ["VOCAL_PROCESS_ASR_BACKEND"] = str(previous or "")
    else:
        os.environ.pop("VOCAL_PROCESS_ASR_BACKEND", None)


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
            "exact_timed_target_duration_ratio": scorecard["exact_timed_target_duration_ratio"],
            "resampled_timing_lattice_ratio": scorecard["resampled_timing_lattice_ratio"],
            "aligned_timing_score": scorecard["aligned_timing_score"],
            "target_duration_alignment_score": scorecard["target_duration_alignment_score"],
            "stretch_warning_score": scorecard["stretch_warning_score"],
            "stretch_naturalness_score": scorecard["stretch_naturalness_score"],
            "continuity_warning_count": scorecard["continuity_warning_count"],
            "continuity_warning_ratio": scorecard["continuity_warning_ratio"],
            "continuity_score": scorecard["continuity_score"],
            "fade_applied_clip_count": scorecard["fade_applied_clip_count"],
            "stretch_quality_score": scorecard["stretch_quality_score"],
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
    stretch_plan = report.get("stretch_plan", [])

    review_ratio = _ratio(review_required_count, material_count)
    match_ordering_score = _clamp01((match_score_mean or 0.0) * (1.0 - review_ratio))

    positioned_decision_count = _positive_int(timeline_alignment.get("positioned_decision_count"))
    resolved_target_count = _positive_int(timeline_alignment.get("resolved_target_duration_count"))
    timed_target_count = _positive_int(timeline_alignment.get("timed_target_duration_count"))
    resampled_timing_lattice_count = _positive_int(timeline_alignment.get("resampled_timing_lattice_count"))
    exact_timed_target_count = _positive_int(
        timeline_alignment.get("exact_timed_target_duration_count"),
        fallback=max(timed_target_count - resampled_timing_lattice_count, 0),
    )
    positioned_decision_ratio = _ratio(positioned_decision_count, decision_count)
    resolved_target_duration_ratio = _ratio(resolved_target_count, decision_count)
    timed_target_duration_ratio = _ratio(timed_target_count, decision_count)
    exact_timed_target_duration_ratio = _ratio(exact_timed_target_count, decision_count)
    resampled_timing_lattice_ratio = _ratio(resampled_timing_lattice_count, decision_count)
    timeline_position_score = _clamp01(0.7 * positioned_decision_ratio + 0.3 * resolved_target_duration_ratio)
    aligned_timing_score = exact_timed_target_duration_ratio

    stretch_penalty = _ratio(extreme_stretch_count + 0.5 * moderate_stretch_count, material_count)
    stretch_warning_score = _clamp01(1.0 - stretch_penalty)
    stretch_naturalness_values = _stretch_plan_float_values(stretch_plan, "stretch_naturalness_score")
    stretch_naturalness_score = _mean(stretch_naturalness_values)
    if stretch_naturalness_score is None:
        stretch_naturalness_score = stretch_warning_score
    continuity_warning_count = _stretch_plan_non_empty_count(stretch_plan, "continuity_warning")
    continuity_warning_ratio = _ratio(continuity_warning_count, material_count)
    continuity_score = _clamp01(1.0 - continuity_warning_ratio)
    fade_applied_clip_count = _stretch_plan_positive_float_count(stretch_plan, "fade_seconds")
    stretch_quality_score = _weighted_score(
        [
            (stretch_warning_score, 0.45),
            (stretch_naturalness_score, 0.40),
            (continuity_score, 0.15),
        ]
    )

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
        and exact_timed_target_duration_ratio >= 0.95
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
        "exact_timed_target_duration_count": exact_timed_target_count,
        "exact_timed_target_duration_ratio": _round_float(exact_timed_target_duration_ratio),
        "resampled_timing_lattice_count": resampled_timing_lattice_count,
        "resampled_timing_lattice_ratio": _round_float(resampled_timing_lattice_ratio),
        "aligned_timing_score": _round_float(aligned_timing_score),
        "timeline_position_score": _round_float(timeline_position_score),
        "reference_duration_seconds": _round_float(reference_duration_seconds),
        "target_duration_total_seconds": _round_float(target_duration_total_seconds),
        "target_duration_delta_seconds": _round_float(target_duration_delta_seconds),
        "target_duration_delta_ratio": _round_float(target_duration_delta_ratio),
        "target_duration_alignment_score": _round_float(target_duration_alignment_score),
        "extreme_stretch_count": extreme_stretch_count,
        "moderate_stretch_count": moderate_stretch_count,
        "stretch_warning_score": _round_float(stretch_warning_score),
        "stretch_naturalness_score": _round_float(stretch_naturalness_score),
        "continuity_warning_count": continuity_warning_count,
        "continuity_warning_ratio": _round_float(continuity_warning_ratio),
        "continuity_score": _round_float(continuity_score),
        "fade_applied_clip_count": fade_applied_clip_count,
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
    render_failed = _render_summary_failed(render_summary)
    render_blocked = bool(render_summary.get("blocked")) if isinstance(render_summary, dict) else False
    channel_output_paths = _render_summary_channel_output_paths(render_summary)
    boundary_measurements = _render_summary_boundary_measurements(render_summary)
    acoustic_drift_measurements = _render_summary_acoustic_drift_measurements(render_summary)
    validation_paths = channel_output_paths or ([output_path] if output_path else [])
    channel_outputs = [
        _channel_output_validation(path, reference_duration_seconds, render_failed)
        for path in validation_paths
    ]
    output_exists = bool(channel_outputs) and all(entry["exists"] for entry in channel_outputs)
    output_duration_seconds = (
        _safe_float(channel_outputs[0].get("duration_seconds"))
        if channel_outputs
        else None
    )
    delta_seconds_values = [
        _safe_float(entry.get("duration_delta_seconds"))
        for entry in channel_outputs
        if _safe_float(entry.get("duration_delta_seconds")) is not None
    ]
    delta_ratio_values = [
        _safe_float(entry.get("duration_delta_ratio"))
        for entry in channel_outputs
        if _safe_float(entry.get("duration_delta_ratio")) is not None
    ]
    score_values = [
        _safe_float(entry.get("duration_alignment_score"))
        for entry in channel_outputs
        if _safe_float(entry.get("duration_alignment_score")) is not None
    ]
    delta_seconds = max(delta_seconds_values) if delta_seconds_values else None
    delta_ratio = max(delta_ratio_values) if delta_ratio_values else None
    duration_alignment_score = min(score_values) if score_values else None
    if not render_requested:
        status = "not_requested"
    elif render_blocked:
        status = "render_blocked"
    elif render_failed:
        status = "render_failed"
    elif not output_exists:
        status = "missing_output"
    elif not channel_outputs or any(entry.get("duration_seconds") is None for entry in channel_outputs) or reference_duration_seconds is None:
        status = "duration_unavailable"
    elif delta_ratio is not None and delta_ratio <= STRICT_DURATION_TOLERANCE_RATIO:
        status = "ok"
    else:
        status = "duration_mismatch"
    return {
        "format": "vocal_process_render_validation_v1",
        "status": status,
        "render_requested": render_requested,
        "render_blocked": render_blocked,
        "blocker_kind": str(render_summary.get("blocker_kind") or "") if isinstance(render_summary, dict) else "",
        "blocker_message": str(render_summary.get("message") or "") if isinstance(render_summary, dict) else "",
        "render_failed": render_failed,
        "output_exists": output_exists,
        "output_path": str(output_path) if output_path else "",
        "channel_outputs": channel_outputs,
        "output_duration_seconds": _round_float(output_duration_seconds),
        "reference_duration_seconds": _round_float(reference_duration_seconds),
        "duration_delta_seconds": _round_float(delta_seconds),
        "duration_delta_ratio": _round_float(delta_ratio),
        "duration_alignment_score": _round_float(duration_alignment_score),
        "render_boundary_measurements": boundary_measurements,
        "render_boundary_measured_count": _render_boundary_measured_count(boundary_measurements),
        "render_boundary_max_sample_jump": _round_float(_render_boundary_max_sample_jump(boundary_measurements)),
        "render_boundary_worst_join": _render_boundary_worst_join(boundary_measurements),
        "render_acoustic_drift_measurements": acoustic_drift_measurements,
        "render_acoustic_drift_measured_count": _render_acoustic_drift_measured_count(acoustic_drift_measurements),
        "render_acoustic_drift_reliable_f0_count": _render_acoustic_drift_reliable_f0_count(
            acoustic_drift_measurements
        ),
        "render_acoustic_drift_unreliable_f0_count": _render_acoustic_drift_unreliable_f0_count(
            acoustic_drift_measurements
        ),
        "render_acoustic_drift_max_abs_f0_cents": _round_float(
            _render_acoustic_drift_max(acoustic_drift_measurements, "max_abs_f0_cents_drift")
        ),
        "render_acoustic_drift_max_abs_reliable_f0_cents": _round_float(
            _render_acoustic_drift_max(acoustic_drift_measurements, "max_abs_reliable_f0_cents_drift")
        ),
        "render_acoustic_drift_max_abs_spectral_centroid_ratio_delta": _round_float(
            _render_acoustic_drift_max(acoustic_drift_measurements, "max_abs_spectral_centroid_ratio_delta")
        ),
        "render_acoustic_drift_worst_clip": _render_acoustic_drift_worst_clip(acoustic_drift_measurements),
    }


def _render_summary_channel_output_paths(render_summary: dict[str, Any]) -> list[Path]:
    if not isinstance(render_summary, dict):
        return []
    raw_paths = render_summary.get("channel_output_paths")
    if not isinstance(raw_paths, list):
        return []
    return [Path(str(path)) for path in raw_paths if str(path or "").strip()]


def _read_render_boundary_measurements(
    diagnostics_directory: Path,
    output_paths: Sequence[Path],
) -> list[dict[str, Any]]:
    return _read_render_diagnostic_measurements(
        diagnostics_directory,
        output_paths,
        stage="render.boundaries.measured",
    )


def _read_render_acoustic_drift_measurements(
    diagnostics_directory: Path,
    output_paths: Sequence[Path],
) -> list[dict[str, Any]]:
    return _read_render_diagnostic_measurements(
        diagnostics_directory,
        output_paths,
        stage="render.acoustic_drift.measured",
    )


def _read_render_diagnostic_measurements(
    diagnostics_directory: Path,
    output_paths: Sequence[Path],
    *,
    stage: str,
) -> list[dict[str, Any]]:
    measurements: list[dict[str, Any]] = []
    for output_path in output_paths:
        log_path = diagnostic_log_path(output_path, diagnostics_directory)
        if not log_path.exists():
            continue
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("stage") != stage:
                continue
            fields = record.get("fields")
            measurement = fields.get("measurement") if isinstance(fields, dict) else None
            if isinstance(measurement, dict):
                measurements.append(measurement)
    return measurements


def _render_summary_boundary_measurements(render_summary: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(render_summary, dict):
        return []
    raw_measurements = render_summary.get("render_boundary_measurements")
    if not isinstance(raw_measurements, list):
        return []
    return [measurement for measurement in raw_measurements if isinstance(measurement, dict)]


def _render_summary_acoustic_drift_measurements(render_summary: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(render_summary, dict):
        return []
    raw_measurements = render_summary.get("render_acoustic_drift_measurements")
    if not isinstance(raw_measurements, list):
        return []
    return [measurement for measurement in raw_measurements if isinstance(measurement, dict)]


def _render_boundary_measured_count(measurements: Sequence[dict[str, Any]]) -> int:
    total = 0
    for measurement in measurements:
        count = _positive_int(measurement.get("measured_boundary_count"))
        if count:
            total += count
            continue
        if measurement.get("status") == "ok":
            total += _positive_int(measurement.get("boundary_count"))
    return total


def _render_boundary_max_sample_jump(measurements: Sequence[dict[str, Any]]) -> float | None:
    values = [
        _safe_float(measurement.get("max_sample_jump"))
        for measurement in measurements
        if isinstance(measurement, dict)
    ]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _render_boundary_worst_join(measurements: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    worst_measurement: dict[str, Any] | None = None
    worst_sample_jump: float | None = None
    for measurement in measurements:
        if not isinstance(measurement, dict):
            continue
        sample_jump = _safe_float(measurement.get("max_sample_jump"))
        if sample_jump is None:
            continue
        current_best = worst_sample_jump if worst_sample_jump is not None else float("-inf")
        if worst_measurement is None or sample_jump > current_best:
            worst_measurement = measurement
            worst_sample_jump = sample_jump
            continue
        if sample_jump == worst_sample_jump:
            current_boundary_count = _positive_int(measurement.get("boundary_count"))
            worst_boundary_count = _positive_int(worst_measurement.get("boundary_count"))
            if current_boundary_count > worst_boundary_count:
                worst_measurement = measurement
                worst_sample_jump = sample_jump
    if worst_measurement is None:
        return None
    top_boundaries = worst_measurement.get("top_boundaries")
    if not isinstance(top_boundaries, list):
        return None
    worst_boundary = next((boundary for boundary in top_boundaries if isinstance(boundary, dict)), None)
    if worst_boundary is None:
        return None
    result = dict(worst_boundary)
    result["output_path"] = str(worst_measurement.get("output_path") or "")
    result["output_sample_rate"] = _positive_int(worst_measurement.get("output_sample_rate"))
    result["output_channels"] = _positive_int(worst_measurement.get("output_channels"))
    result["output_frame_count"] = _positive_int(worst_measurement.get("output_frame_count"))
    result["boundary_count"] = _positive_int(worst_measurement.get("boundary_count"))
    result["measured_boundary_count"] = _positive_int(worst_measurement.get("measured_boundary_count"))
    result["max_sample_jump"] = _round_float(_safe_float(worst_measurement.get("max_sample_jump")))
    return result


def _render_acoustic_drift_measured_count(measurements: Sequence[dict[str, Any]]) -> int:
    total = 0
    for measurement in measurements:
        total += _positive_int(measurement.get("measured_clip_count"))
    return total


def _render_acoustic_drift_reliable_f0_count(measurements: Sequence[dict[str, Any]]) -> int:
    total = 0
    for measurement in measurements:
        total += _positive_int(measurement.get("f0_reliable_clip_count"))
    return total


def _render_acoustic_drift_unreliable_f0_count(measurements: Sequence[dict[str, Any]]) -> int:
    total = 0
    for measurement in measurements:
        explicit = measurement.get("f0_unreliable_clip_count")
        if explicit is not None:
            total += _positive_int(explicit)
            continue
        total += max(
            _positive_int(measurement.get("f0_measured_clip_count"))
            - _positive_int(measurement.get("f0_reliable_clip_count")),
            0,
        )
    return total


def _render_acoustic_drift_max(measurements: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [
        _safe_float(measurement.get(key))
        for measurement in measurements
        if isinstance(measurement, dict)
    ]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _render_acoustic_drift_worst_clip(measurements: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    worst_measurement: dict[str, Any] | None = None
    worst_clip: dict[str, Any] | None = None
    worst_score = -1.0
    for measurement in measurements:
        if not isinstance(measurement, dict):
            continue
        top_clips = measurement.get("top_clips")
        if not isinstance(top_clips, list):
            continue
        for clip in top_clips:
            if not isinstance(clip, dict):
                continue
            score = _safe_float(clip.get("acoustic_drift_score"))
            if score is None:
                score = max(
                    (
                        abs(_safe_float(clip.get("f0_cents_drift")) or 0.0) / 1200.0
                        if clip.get("f0_drift_reliable", True)
                        else 0.0
                    ),
                    abs(_safe_float(clip.get("spectral_centroid_ratio_delta")) or 0.0),
                    abs(_safe_float(clip.get("spectral_bandwidth_ratio_delta")) or 0.0) * 0.5,
                )
            if score > worst_score:
                worst_score = score
                worst_clip = clip
                worst_measurement = measurement
    if worst_clip is None:
        return None
    result = dict(worst_clip)
    if worst_measurement is not None:
        result["clip_count"] = _positive_int(worst_measurement.get("clip_count"))
        result["measured_clip_count"] = _positive_int(worst_measurement.get("measured_clip_count"))
    return result


def _channel_output_validation(
    path: Path,
    reference_duration_seconds: float | None,
    render_failed: bool,
) -> dict[str, Any]:
    exists = path.exists()
    duration_seconds = _probe_duration(path) if exists and not render_failed else None
    delta_seconds = _duration_delta(duration_seconds, reference_duration_seconds)
    delta_ratio = _duration_delta_ratio(duration_seconds, reference_duration_seconds)
    return {
        "path": str(path),
        "exists": exists,
        "duration_seconds": _round_float(duration_seconds),
        "duration_delta_seconds": _round_float(delta_seconds),
        "duration_delta_ratio": _round_float(delta_ratio),
        "duration_alignment_score": _round_float(_duration_alignment_score(delta_ratio)),
    }


def _render_summary_failed(render_summary: dict[str, Any]) -> bool:
    if not render_summary:
        return False
    if render_summary.get("error"):
        return True
    batch_summary = render_summary.get("batch_summary")
    if not isinstance(batch_summary, dict):
        return False
    return _positive_int(batch_summary.get("failed")) > 0


def _suite_score_summary(results: Sequence[RealCaseResult]) -> dict[str, Any]:
    planning_scores = _summary_values(results, "planning_alignment_score")
    render_scores = _summary_values(results, "rendered_audio_alignment_score")
    match_scores = _summary_values(results, "match_ordering_score")
    exact_timing_scores = _summary_values(results, "exact_timed_target_duration_ratio")
    resampled_timing_ratios = _summary_values(results, "resampled_timing_lattice_ratio")
    stretch_quality_scores = _summary_values(results, "stretch_quality_score")
    stretch_naturalness_scores = _summary_values(results, "stretch_naturalness_score")
    continuity_warning_ratios = _summary_values(results, "continuity_warning_ratio")
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
        "exact_timed_target_duration_ratio": _stats(exact_timing_scores),
        "resampled_timing_lattice_ratio": _stats(resampled_timing_ratios),
        "stretch_quality_score": _stats(stretch_quality_scores),
        "stretch_naturalness_score": _stats(stretch_naturalness_scores),
        "continuity_warning_ratio": _stats(continuity_warning_ratios),
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


def _stretch_plan_float_values(stretch_plan: Any, key: str) -> list[float]:
    if not isinstance(stretch_plan, list):
        return []
    values = [_safe_float(entry.get(key)) for entry in stretch_plan if isinstance(entry, dict)]
    return [value for value in values if value is not None]


def _stretch_plan_non_empty_count(stretch_plan: Any, key: str) -> int:
    if not isinstance(stretch_plan, list):
        return 0
    return sum(1 for entry in stretch_plan if isinstance(entry, dict) and bool(entry.get(key)))


def _stretch_plan_positive_float_count(stretch_plan: Any, key: str) -> int:
    if not isinstance(stretch_plan, list):
        return 0
    return sum(
        1
        for entry in stretch_plan
        if isinstance(entry, dict) and (_safe_float(entry.get(key)) or 0.0) > 0.0
    )


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
    runtime_preflight = summary.get("runtime_preflight", {})
    preferred_backend = (
        runtime_preflight.get("preferred_backend", "")
        if isinstance(runtime_preflight, dict)
        else ""
    )
    lines = [
        "# 真实音频验收报告 / Real Audio Evaluation Report",
        "",
        f"- 根目录 Root: `{summary['root']}`",
        f"- 是否渲染 Render: `{summary['render']}`",
        f"- ASR 后端 ASR backend: `{preferred_backend}`",
        f"- 完成用例 Completed cases: `{summary.get('completed_case_count', summary['case_count'])}` / "
        f"`{summary.get('planned_case_count', summary['case_count'])}`",
        f"- 跳过用例 Skipped cases: `{summary.get('skipped_case_count', 0)}` / "
        f"`{summary.get('discovered_case_count', summary.get('planned_case_count', summary['case_count']))}`",
        "",
        "## 统计 Counts",
        "",
        f"- 数据拆分统计 Split counts: `{json.dumps(summary['split_counts'], ensure_ascii=False)}`",
        f"- 语言统计 Language counts: `{json.dumps(summary['language_counts'], ensure_ascii=False)}`",
        f"- 状态统计 Status counts: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- 告警统计 Warning counts: `{json.dumps(summary['warning_counts'], ensure_ascii=False)}`",
        f"- 跳过统计 Skipped counts: `{json.dumps(summary.get('skipped_case_counts', {}), ensure_ascii=False)}`",
        f"- 多维度跑分汇总 Score summary: `{json.dumps(summary.get('score_summary', {}), ensure_ascii=False)}`",
        f"- 基础设施阻塞 Infrastructure blocker: `{json.dumps(summary.get('infrastructure_blocker', {}), ensure_ascii=False)}`",
        "",
        *_render_group_score_markdown(summary),
        "",
        *_render_skipped_cases_markdown(summary),
        "",
        "## 用例明细 Cases",
        "",
        (
            "| 用例 Case | Split | Language | Status | 规划分 Plan Score | 渲染分 Render Score | "
            "拉伸质量 Stretch Quality | 拉伸自然度 Stretch Naturalness | 边界风险 Boundary Risks | "
            "声学漂移 Acoustic Drift | "
            "最低匹配 Min Match | 平均匹配 Mean Match | 定位率 Positioned | 计时率 Timed | "
            "目标时长差 Target Delta | 渲染时长差 Render Delta | 严格通过 Strict Pass | "
            "需复查 Review Needed | 输出音频 Output Audio |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        case_summary = result.summary
        scorecard = case_summary.get("scorecard", {})
        render_validation = case_summary.get("render_validation", {})
        min_score = case_summary.get("minimum_match_score", "")
        mean_score = case_summary.get("match_score_mean", "")
        plan_score = case_summary.get("planning_alignment_score", "")
        render_score = case_summary.get("rendered_audio_alignment_score", "")
        stretch_quality = case_summary.get("stretch_quality_score", "")
        stretch_naturalness = case_summary.get("stretch_naturalness_score", "")
        continuity_warnings = case_summary.get("continuity_warning_count", "")
        worst_join = render_validation.get("render_boundary_worst_join", {}) if isinstance(render_validation, dict) else {}
        worst_acoustic_drift = (
            render_validation.get("render_acoustic_drift_worst_clip", {})
            if isinstance(render_validation, dict)
            else {}
        )
        positioned_ratio = case_summary.get("positioned_decision_ratio", "")
        timed_ratio = case_summary.get("timed_target_duration_ratio", "")
        target_delta = scorecard.get("target_duration_delta_seconds", "") if isinstance(scorecard, dict) else ""
        render_delta = (
            render_validation.get("duration_delta_seconds", "") if isinstance(render_validation, dict) else ""
        )
        review_count = case_summary.get("review_required_match_count", "")
        boundary_risks_value = _render_boundary_risk_markdown_value(continuity_warnings, worst_join)
        acoustic_drift_value = _render_acoustic_drift_markdown_value(worst_acoustic_drift)
        lines.append(
            f"| {result.case.name} | {result.case.split} | {result.case.language or 'unknown'} | "
            f"{result.status} | {_markdown_value(plan_score)} | {_markdown_value(render_score)} | "
            f"{_markdown_value(stretch_quality)} | {_markdown_value(stretch_naturalness)} | "
            f"{_markdown_value(boundary_risks_value)} | {_markdown_value(acoustic_drift_value)} | "
            f"{_markdown_value(min_score)} | {_markdown_value(mean_score)} | {_markdown_value(positioned_ratio)} | "
            f"{_markdown_value(timed_ratio)} | {_markdown_value(target_delta)} | {_markdown_value(render_delta)} | "
            f"{case_summary.get('strict_render_pass', False)} | {review_count} | "
            f"{_markdown_value(result.output_path or '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_skipped_cases_markdown(summary: dict[str, Any]) -> list[str]:
    skipped_cases = summary.get("skipped_cases", [])
    if not isinstance(skipped_cases, list) or not skipped_cases:
        return []
    lines = [
        "## 跳过用例 Skipped Cases",
        "",
        "| 用例 Case | Split | 原人声语言 Reference Lang | 素材集语言 Material Lang | 原因 Reason | 说明 Message |",
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
        "## 分组跑分 Group Scores",
        "",
        (
            "| 分组 Group | 名称 Name | 用例数 Cases | 规划均分 Plan Mean | 渲染均分 Render Mean | "
            "匹配均分 Match Mean | 拉伸均分 Stretch Mean | 边界风险均值 Boundary Risk Mean | "
            "严格通过/失败 Strict Pass/Fail | 状态统计 Status Counts |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
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
                f"{_markdown_value(_score_stat_mean(score_summary, 'stretch_quality_score'))} | "
                f"{_markdown_value(_score_stat_mean(score_summary, 'continuity_warning_ratio'))} | "
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


def _render_boundary_risk_markdown_value(continuity_warnings: Any, worst_join: Any) -> str:
    worst_join_value = _render_boundary_join_markdown_value(worst_join)
    warnings_value = _markdown_value(continuity_warnings)
    warnings_count = _safe_float(continuity_warnings)
    if worst_join_value:
        if warnings_count is not None and warnings_count > 0:
            return " / ".join([warnings_value, worst_join_value])
        return worst_join_value
    return warnings_value


def _render_boundary_join_markdown_value(worst_join: Any) -> str:
    if not isinstance(worst_join, dict):
        return ""
    sample_jump = _markdown_value(worst_join.get("sample_jump"))
    output_frame_index = _markdown_value(worst_join.get("output_frame_index"))
    output_time_seconds = _markdown_value(worst_join.get("output_time_seconds"))
    if not sample_jump and not output_frame_index:
        return ""
    value = f"{sample_jump} @ {output_frame_index}".strip()
    if output_time_seconds:
        value += f" ({output_time_seconds}s)"
    return value


def _render_acoustic_drift_markdown_value(worst_clip: Any) -> str:
    if not isinstance(worst_clip, dict):
        return ""
    parts: list[str] = []
    f0_cents = _safe_float(worst_clip.get("f0_cents_drift"))
    if f0_cents is not None and worst_clip.get("f0_drift_reliable", True):
        parts.append(f"F0 {f0_cents:+.0f}c")
    elif f0_cents is not None:
        parts.append("F0 untrusted")
    centroid_delta = _safe_float(worst_clip.get("spectral_centroid_ratio_delta"))
    if centroid_delta is not None:
        parts.append(f"centroid {centroid_delta:+.3g}")
    clip_index = worst_clip.get("index")
    text_hint = str(worst_clip.get("text_hint") or "")
    if clip_index is not None:
        label = f"clip {clip_index}"
        if text_hint:
            label += f" {text_hint}"
        parts.append(label)
    return " / ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())

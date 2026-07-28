from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .batch import QueueItem, run_batch_queue
from .engine import list_audio_files
from .diagnostics import diagnostic_log_path
from .preflight import build_preflight_report
from .settings import ProcessingSettings


REAL_CASE_FORMAT = "vocal_process_real_cases_v1"
REAL_REPORT_FORMAT = "vocal_process_real_suite_report_v1"


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
class RealSuiteResult:
    root: Path
    report_directory: Path
    manifest_path: Path | None
    cases: tuple[RealCaseResult, ...]
    summary_path: Path
    markdown_path: Path


def discover_real_cases(root: Path, *, manifest_path: Path | None = None) -> list[RealCase]:
    root = root.expanduser()
    if manifest_path is not None and manifest_path.exists():
        return _load_cases_from_manifest(root, manifest_path)

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
    return cases


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
    cases = discover_real_cases(root, manifest_path=manifest_path)
    if case_filter:
        lowered = case_filter.lower()
        cases = [case for case in cases if lowered in case.name.lower()]
    if split_filter:
        lowered_split = split_filter.lower()
        cases = [case for case in cases if case.split.lower() == lowered_split]
    if max_cases is not None:
        cases = cases[: max(0, max_cases)]

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    report_directory = (output_root or (root / "output")).expanduser() / f"real-eval-{timestamp}"
    report_directory.mkdir(parents=True, exist_ok=True)
    summary_path = report_directory / "summary.json"
    markdown_path = report_directory / "summary.md"

    results: list[RealCaseResult] = []
    for case in cases:
        case.output_directory.mkdir(parents=True, exist_ok=True)
        analysis_path = report_directory / case.name / "analysis.json"
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        report = build_preflight_report(
            case.reference_path,
            case.material_directory,
            lyrics_file=case.lyrics_file,
            work_dir=case.output_directory / "_work",
            material_cache_dir=case.output_directory / "_analysis_cache",
            compute_device=compute_device,
            source_separation=source_separation,
        )
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
            summary = run_batch_queue([QueueItem(case.reference_path, render_output_path)], settings)
            status = "rendered" if summary.failed == 0 and summary.cancelled == 0 else "render_failed"
            if not render_output_path.exists():
                status = "render_failed"
            if render_report_path.exists():
                render_summary = {"batch_summary": summary.__dict__}
            else:
                render_summary = {"batch_summary": summary.__dict__}
        else:
            render_summary = {}

        results.append(
            RealCaseResult(
                case=case,
                status=status,
                analysis_report_path=analysis_path,
                output_path=render_output_path,
                render_report_path=render_report_path,
                summary=_case_summary(report, render_summary),
                warnings=warnings,
            )
        )

    suite_summary = {
        "format": REAL_REPORT_FORMAT,
        "root": str(root),
        "manifest_path": str(manifest_path) if manifest_path else "",
        "render": render,
        "case_count": len(results),
        "split_counts": _count_by((result.case.split for result in results)),
        "language_counts": _count_by((result.case.language or "unknown" for result in results)),
        "status_counts": _count_by((result.status for result in results)),
        "warning_counts": _count_by(
            warning.get("kind", "unknown") for result in results for warning in result.warnings
        ),
    }
    summary_path.write_text(
        json.dumps(
            {
                "format": REAL_REPORT_FORMAT,
                "suite": suite_summary,
                "cases": [
                    {
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
                    for result in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown_report(suite_summary, results), encoding="utf-8")
    return RealSuiteResult(
        root=root,
        report_directory=report_directory,
        manifest_path=manifest_path,
        cases=tuple(results),
        summary_path=summary_path,
        markdown_path=markdown_path,
    )


def write_manifest_template(root: Path, destination: Path) -> Path:
    root = root.expanduser()
    destination = destination.expanduser()
    cases = discover_real_cases(root)
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
    return 0


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
    match = re.search(r"_(CN|JP)$", stem, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _case_summary(report: dict[str, Any], render_summary: dict[str, Any]) -> dict[str, Any]:
    summary = dict(report.get("summary", {}))
    ordering = report.get("ordering", {})
    summary.update(
        {
            "status": report.get("status", "unknown"),
            "warning_count": len(report.get("warnings", [])),
            "ordered_material_count": len(ordering.get("ordering", [])) if isinstance(ordering, dict) else 0,
            "render_summary": render_summary,
            "timeline_alignment": ordering.get("timeline_alignment", {}) if isinstance(ordering, dict) else {},
        }
    )
    return summary


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
        f"- Cases: `{summary['case_count']}`",
        "",
        "## Counts",
        "",
        f"- Split counts: `{json.dumps(summary['split_counts'], ensure_ascii=False)}`",
        f"- Language counts: `{json.dumps(summary['language_counts'], ensure_ascii=False)}`",
        f"- Status counts: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- Warning counts: `{json.dumps(summary['warning_counts'], ensure_ascii=False)}`",
        "",
        "## Cases",
        "",
        "| Case | Split | Language | Status | Min Score | Review Needed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        case_summary = result.summary
        min_score = case_summary.get("minimum_match_score", "")
        review_count = case_summary.get("review_required_match_count", "")
        lines.append(
            f"| {result.case.name} | {result.case.split} | {result.case.language or 'unknown'} | "
            f"{result.status} | {min_score} | {review_count} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

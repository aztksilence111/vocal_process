from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .daw import export_daw_timeline_with_progress
from .engine import (
    AudioProcessorError,
    ProcessOptions,
    get_environment_report,
    probe_audio,
    process_audio,
    summarize_probe,
)
from .model_assist import backend_availability, build_model_assisted_pipeline_plan, list_model_candidates
from .model_runtime import get_model_runtime_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio-processor",
        description="Process audio files with Python and the system FFmpeg tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="verify Python, FFmpeg, and FFprobe")
    subparsers.add_parser("gui", help="open the desktop batch processor")

    models_parser = subparsers.add_parser(
        "models",
        help="show open-source model candidates for vocal analysis and ordering",
    )
    models_parser.add_argument(
        "--json",
        action="store_true",
        help="print the model-assisted pipeline plan as JSON",
    )

    probe_parser = subparsers.add_parser("probe", help="show audio metadata")
    probe_parser.add_argument("input", type=Path, help="audio file to inspect")
    probe_parser.add_argument(
        "--json",
        action="store_true",
        help="print raw ffprobe JSON",
    )

    process_parser = subparsers.add_parser("process", help="process or convert audio")
    process_parser.add_argument("input", type=Path, help="source audio file")
    process_parser.add_argument("output", type=Path, help="target audio file")
    process_parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="overwrite the output file if it already exists",
    )
    process_parser.add_argument("--trim-start", help="start timestamp, for example 00:00:10")
    process_parser.add_argument("--duration", help="duration in seconds or timestamp format")
    process_parser.add_argument("--gain-db", type=float, help="gain adjustment in dB")
    process_parser.add_argument(
        "--normalize",
        action="store_true",
        help="apply EBU R128 loudness normalization",
    )
    process_parser.add_argument("--highpass-hz", type=float, help="high-pass cutoff")
    process_parser.add_argument("--lowpass-hz", type=float, help="low-pass cutoff")
    process_parser.add_argument("--sample-rate", type=int, help="output sample rate")
    process_parser.add_argument("--channels", type=int, help="output channel count")
    process_parser.add_argument("--codec", help="FFmpeg audio codec name")

    daw_parser = subparsers.add_parser(
        "export-daw",
        help="export stretched material clips and a DAW timeline project",
    )
    daw_parser.add_argument("reference", type=Path, help="reference/original audio file")
    daw_parser.add_argument("material_directory", type=Path, help="folder containing material audio")
    daw_parser.add_argument("project", type=Path, help="target REAPER .rpp project path")
    daw_parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="overwrite generated clip and project files if they already exist",
    )
    daw_parser.add_argument("--gain-db", type=float, help="gain adjustment in dB")
    daw_parser.add_argument(
        "--normalize",
        action="store_true",
        help="apply EBU R128 loudness normalization to each clip",
    )
    daw_parser.add_argument("--highpass-hz", type=float, help="high-pass cutoff")
    daw_parser.add_argument("--lowpass-hz", type=float, help="low-pass cutoff")
    daw_parser.add_argument("--sample-rate", type=int, help="output sample rate")
    daw_parser.add_argument("--channels", type=int, help="output channel count")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            for line in get_environment_report():
                print(line)
            for line in get_model_runtime_report():
                print(line)
            return 0

        if args.command == "gui":
            from .gui import main as gui_main

            return gui_main()

        if args.command == "models":
            if args.json:
                print(json.dumps(build_model_assisted_pipeline_plan(), ensure_ascii=False, indent=2))
            else:
                availability = backend_availability()
                for candidate in list_model_candidates():
                    status = "available" if availability[candidate["name"]] else "not installed"
                    print(f"{candidate['name']} [{candidate['pipeline_stage']}]: {status}")
                    print(f"  {candidate['role']}")
                    print(f"  {candidate['repository_url']}")
            return 0

        if args.command == "probe":
            data = probe_audio(args.input)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                for key, value in summarize_probe(data):
                    print(f"{key}: {value}")
            return 0

        if args.command == "process":
            process_audio(
                ProcessOptions(
                    input_path=args.input,
                    output_path=args.output,
                    overwrite=args.overwrite,
                    trim_start=args.trim_start,
                    duration=args.duration,
                    gain_db=args.gain_db,
                    normalize=args.normalize,
                    highpass_hz=args.highpass_hz,
                    lowpass_hz=args.lowpass_hz,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                    codec=args.codec,
                )
            )
            print(f"Wrote {args.output}")
            return 0

        if args.command == "export-daw":
            result = export_daw_timeline_with_progress(
                args.reference,
                args.material_directory,
                args.project,
                ProcessOptions(
                    input_path=args.reference,
                    output_path=args.project,
                    overwrite=args.overwrite,
                    gain_db=args.gain_db,
                    normalize=args.normalize,
                    highpass_hz=args.highpass_hz,
                    lowpass_hz=args.lowpass_hz,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                    codec=None,
                ),
            )
            print(f"Wrote {result.project_path}")
            print(f"Wrote {result.manifest_path}")
            print(f"Wrote {result.csv_path}")
            return 0

        parser.error("unknown command")
        return 2
    except AudioProcessorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

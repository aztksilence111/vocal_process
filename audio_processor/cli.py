from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .batch import QueueItem, run_batch_queue
from .daw import export_daw_timeline_with_progress
from .engine import (
    AudioProcessorError,
    ProcessOptions,
    get_environment_report,
    probe_audio,
    process_audio,
    summarize_probe,
)
from .handoff import (
    export_melodyne_handoff_with_progress,
    export_vegas_handoff_with_progress,
    open_melodyne_handoff,
)
from .model_assist import build_model_assisted_pipeline_plan, list_model_candidates
from .preflight import build_preflight_report
from .model_runtime import backend_availability, get_model_runtime_report
from .settings import COMPUTE_DEVICE_OPTIONS, SOURCE_SEPARATION_OPTIONS, ProcessingSettings
from .vst3_bridge import bridge_request_template, bridge_watch_contract, run_bridge_request_file, run_bridge_watch


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

    batch_parser = subparsers.add_parser(
        "batch",
        help="run the GUI batch workflow headlessly for portable smoke tests",
    )
    batch_parser.add_argument("reference", type=Path, help="reference/original audio file")
    batch_parser.add_argument("output", type=Path, help="target WAV or REAPER .rpp path")
    batch_parser.add_argument(
        "--material-directory",
        type=Path,
        required=True,
        help="folder containing material audio",
    )
    batch_parser.add_argument("--lyrics-file", type=Path, help="optional lyrics text, LRC, SRT, or DOCX file")
    batch_parser.add_argument(
        "--daw-timeline-export",
        action="store_true",
        help="write a DAW timeline project instead of a flat WAV",
    )
    batch_parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="overwrite generated output files",
    )
    batch_parser.add_argument("--gain-db", type=float, help="gain adjustment in dB")
    batch_parser.add_argument(
        "--normalize",
        action="store_true",
        help="apply EBU R128 loudness normalization",
    )
    batch_parser.add_argument("--highpass-hz", type=float, help="high-pass cutoff")
    batch_parser.add_argument("--lowpass-hz", type=float, help="low-pass cutoff")
    batch_parser.add_argument("--sample-rate", type=int, help="output sample rate")
    batch_parser.add_argument("--channels", type=int, help="output channel count")
    batch_parser.add_argument("--codec", help="FFmpeg audio codec name")
    batch_parser.add_argument(
        "--compute-device",
        choices=COMPUTE_DEVICE_OPTIONS,
        default="auto",
        help="model runtime device: auto, cpu, or cuda",
    )
    batch_parser.add_argument(
        "--source-separation",
        choices=SOURCE_SEPARATION_OPTIONS,
        default="auto",
        help="reference vocal separation strategy: auto, always, or never",
    )

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

    melodyne_parser = subparsers.add_parser(
        "export-melodyne",
        help="export full-timeline WAV handoff files for Melodyne",
    )
    melodyne_parser.add_argument("reference", type=Path, help="reference/original audio file")
    melodyne_parser.add_argument("material_directory", type=Path, help="folder containing material audio")
    melodyne_parser.add_argument("output_directory", type=Path, help="target handoff directory")
    melodyne_parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="overwrite generated handoff files if they already exist",
    )
    melodyne_parser.add_argument("--gain-db", type=float, help="gain adjustment in dB")
    melodyne_parser.add_argument(
        "--normalize",
        action="store_true",
        help="apply EBU R128 loudness normalization to each clip",
    )
    melodyne_parser.add_argument("--highpass-hz", type=float, help="high-pass cutoff")
    melodyne_parser.add_argument("--lowpass-hz", type=float, help="low-pass cutoff")
    melodyne_parser.add_argument("--sample-rate", type=int, help="output sample rate")
    melodyne_parser.add_argument("--channels", type=int, help="output channel count")
    melodyne_parser.add_argument("--melodyne-exe", type=Path, help="optional Melodyne executable path")
    melodyne_parser.add_argument(
        "--open-melodyne",
        action="store_true",
        help="open the generated full-timeline WAV in Melodyne after export",
    )

    vegas_parser = subparsers.add_parser(
        "export-vegas",
        help="export VEGAS handoff files with Broadcast Wave timestamps",
    )
    vegas_parser.add_argument("reference", type=Path, help="reference/original audio file")
    vegas_parser.add_argument("material_directory", type=Path, help="folder containing material audio")
    vegas_parser.add_argument("output_directory", type=Path, help="target handoff directory")
    vegas_parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="overwrite generated handoff files if they already exist",
    )
    vegas_parser.add_argument("--gain-db", type=float, help="gain adjustment in dB")
    vegas_parser.add_argument(
        "--normalize",
        action="store_true",
        help="apply EBU R128 loudness normalization to each clip",
    )
    vegas_parser.add_argument("--highpass-hz", type=float, help="high-pass cutoff")
    vegas_parser.add_argument("--lowpass-hz", type=float, help="low-pass cutoff")
    vegas_parser.add_argument("--sample-rate", type=int, help="output sample rate")
    vegas_parser.add_argument("--channels", type=int, help="output channel count")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="preflight model ordering and stretch planning without rendering audio",
    )
    analyze_parser.add_argument("reference", type=Path, help="reference/original audio file")
    analyze_parser.add_argument("material_directory", type=Path, help="folder containing material audio")
    analyze_parser.add_argument("--lyrics-file", type=Path, help="optional lyrics text, LRC, SRT, or DOCX file")
    analyze_parser.add_argument("--output", type=Path, help="optional JSON report path")
    analyze_parser.add_argument(
        "--compute-device",
        choices=COMPUTE_DEVICE_OPTIONS,
        default="auto",
        help="model runtime device: auto, cpu, or cuda",
    )
    analyze_parser.add_argument(
        "--source-separation",
        choices=SOURCE_SEPARATION_OPTIONS,
        default="auto",
        help="reference vocal separation strategy: auto, always, or never",
    )

    bridge_parser = subparsers.add_parser(
        "vst3-bridge",
        help="run a JSON bridge request for future VST3/native host integration",
    )
    bridge_parser.add_argument("request", type=Path, nargs="?", help="bridge request JSON file")
    bridge_parser.add_argument("--response", type=Path, help="response JSON output file")
    bridge_parser.add_argument("--watch", type=Path, help="watch a request directory for *.request.json files")
    bridge_parser.add_argument("--responses", type=Path, help="response directory for watch mode")
    bridge_parser.add_argument(
        "--poll-ms",
        type=int,
        default=500,
        help="watch polling interval in milliseconds",
    )
    bridge_parser.add_argument(
        "--once",
        action="store_true",
        help="process available bridge requests once and exit",
    )
    bridge_parser.add_argument(
        "--contract",
        action="store_true",
        help="print the bridge file contract instead of handling a request",
    )
    bridge_parser.add_argument(
        "--template",
        action="store_true",
        help="print a bridge request template instead of running a request",
    )

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

        if args.command == "batch":
            settings = ProcessingSettings(
                material_directory=str(args.material_directory),
                lyrics_file=str(args.lyrics_file or ""),
                daw_timeline_export=args.daw_timeline_export,
                compute_device=args.compute_device,
                source_separation=args.source_separation,
                output_directory=str(args.output.parent),
                output_extension=args.output.suffix,
                overwrite=args.overwrite,
                gain_db=args.gain_db,
                normalize=args.normalize,
                highpass_hz=args.highpass_hz,
                lowpass_hz=args.lowpass_hz,
                sample_rate=args.sample_rate,
                channels=args.channels,
                codec=args.codec,
            )
            item = QueueItem(input_path=args.reference, output_path=args.output)

            def on_queue_progress(progress: float, message: str) -> None:
                print(f"{progress:.3f} {message}")

            summary = run_batch_queue([item], settings, on_queue_progress=on_queue_progress)
            if summary.failed or summary.cancelled:
                print(f"error: {item.message}", file=sys.stderr)
                return 1
            print(f"Wrote {item.output_path}")
            print(item.message)
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

        if args.command == "export-melodyne":
            result = export_melodyne_handoff_with_progress(
                args.reference,
                args.material_directory,
                args.output_directory,
                ProcessOptions(
                    input_path=args.reference,
                    output_path=args.output_directory / "melodyne_full.wav",
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
            print(f"Wrote {result.full_mix_path}")
            print(f"Wrote {result.manifest_path}")
            print(f"Wrote {result.csv_path}")
            print(f"Wrote {result.lanes_directory}")
            if args.open_melodyne:
                process = open_melodyne_handoff(result.full_mix_path, args.melodyne_exe)
                print(f"Started Melodyne: {process.pid}")
            return 0

        if args.command == "export-vegas":
            result = export_vegas_handoff_with_progress(
                args.reference,
                args.material_directory,
                args.output_directory,
                ProcessOptions(
                    input_path=args.reference,
                    output_path=args.output_directory / "vegas_full.wav",
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
            print(f"Wrote {result.full_mix_path}")
            print(f"Wrote {result.manifest_path}")
            print(f"Wrote {result.csv_path}")
            print(f"Wrote {result.lanes_directory}")
            if result.bwf_directory:
                print(f"Wrote {result.bwf_directory}")
            return 0

        if args.command == "analyze":
            report = build_preflight_report(
                args.reference,
                args.material_directory,
                lyrics_file=args.lyrics_file,
                compute_device=args.compute_device,
                source_separation=args.source_separation,
            )
            payload = json.dumps(report, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload, encoding="utf-8")
                print(f"Wrote {args.output}")
            else:
                print(payload)
            return 0

        if args.command == "vst3-bridge":
            if args.contract:
                print(json.dumps(bridge_watch_contract(), ensure_ascii=False, indent=2))
                return 0
            if args.template:
                print(json.dumps(bridge_request_template(), ensure_ascii=False, indent=2))
                return 0
            if args.watch is not None:
                processed = run_bridge_watch(
                    args.watch,
                    response_directory=args.responses,
                    poll_interval_seconds=max(args.poll_ms, 50) / 1000.0,
                    once=args.once,
                )
                print(json.dumps({"processed": processed}, ensure_ascii=False))
                return 0
            if args.request is None:
                parser.error("vst3-bridge requires a request file unless --template, --contract, or --watch is used")
                return 2
            response = run_bridge_request_file(args.request, args.response)
            if not response.get("ok"):
                print(json.dumps(response, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0

        parser.error("unknown command")
        return 2
    except AudioProcessorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import math
import os
import sys
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from audio_processor import model_assist, model_runtime, preflight
from audio_processor.phoneme import VowelConsonantProfile
from audio_processor import maintenance
from audio_processor import real_eval
from audio_processor import tls
from audio_processor.batch import (
    BatchSummary,
    QueueItem,
    _run_split_reference_channel_item,
    _should_split_reference_channels,
    create_queue,
    run_batch_queue,
)
from audio_processor.cli import build_parser
from audio_processor.cli import main as cli_main
from audio_processor.diagnostics import DiagnosticLogger, diagnostic_log_path
from audio_processor.daw import (
    DawExportResult,
    DawTimelineClip,
    DawTimelinePlan,
    export_daw_timeline_with_progress,
    plan_daw_timeline,
    render_reaper_project,
)
from audio_processor.handoff import (
    export_melodyne_handoff_with_progress,
    export_vegas_handoff_with_progress,
)
from audio_processor.engine import (
    AudioProcessorError,
    ProcessOptions,
    _build_duration_correction_args,
    _build_material_filter_graph,
    _build_rendered_clip_concat_args,
    _ensure_audio_duration,
    _write_rendered_clip_concat_list,
    assemble_material_to_reference_with_progress,
    build_material_assembly_args,
    build_material_clip_args,
    build_process_args,
    ensure_runtime_tool_paths,
    get_audio_duration_seconds,
    list_audio_files,
    plan_material_stretch_clips,
    process_material_clip_with_progress,
    probe_audio,
    render_material_stretch_plan,
    resolve_tool,
    summarize_probe,
    _run_progress_process,
    _build_vowel_core_filter_graph,
    _should_retry_rubberband_short_region_with_direct_trim,
    _vowel_core_stretch_regions,
)
from audio_processor.gui import LYRICS_EXTENSIONS
from audio_processor.i18n import TRANSLATIONS, normalize_language, translate, translate_status
from audio_processor.model_assist import (
    MaterialAnalysis,
    MaterialOrderDecision,
    VoiceSegment,
    VoiceUnitTiming,
    build_model_assisted_pipeline_plan,
    list_model_candidates,
    order_materials_for_reference,
    phonetic_similarity,
    plan_material_ordering,
    text_similarity,
)
from audio_processor.settings import ProcessingSettings, load_settings, save_settings
from audio_processor.vst3_bridge import (
    BRIDGE_REQUEST_FORMAT,
    BRIDGE_RESPONSE_FORMAT,
    bridge_request_template,
    bridge_watch_contract,
    run_bridge_request,
    run_bridge_request_file,
    run_bridge_watch,
)


def _write_test_wave(
    path: Path,
    *,
    duration_seconds: float = 1.0,
    sample_rate: int = 8000,
    channels: int = 1,
) -> None:
    n_channels = channels
    sample_width = 2
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(n_channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n_channels * n_frames)


def _write_tone_wave(path: Path, *, duration_seconds: float = 1.0, sample_rate: int = 8000) -> None:
    n_frames = int(duration_seconds * sample_rate)
    frames = bytearray()
    for index in range(n_frames):
        value = int(12000 * math.sin(2.0 * math.pi * 220.0 * (index / sample_rate)))
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def _write_duplicate_stereo_tone_wave(
    path: Path,
    *,
    duration_seconds: float = 1.0,
    sample_rate: int = 8000,
) -> None:
    n_frames = int(duration_seconds * sample_rate)
    frames = bytearray()
    for index in range(n_frames):
        value = int(12000 * math.sin(2.0 * math.pi * 220.0 * (index / sample_rate)))
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def _write_independent_stereo_wave(
    path: Path,
    *,
    duration_seconds: float = 1.0,
    sample_rate: int = 8000,
) -> None:
    n_frames = int(duration_seconds * sample_rate)
    frames = bytearray()
    midpoint = n_frames // 2
    for index in range(n_frames):
        left_gain = 1.0 if index < midpoint else 0.15
        right_gain = 0.15 if index < midpoint else 1.0
        left = int(12000 * left_gain * math.sin(2.0 * math.pi * 220.0 * (index / sample_rate)))
        right = int(12000 * right_gain * math.sin(2.0 * math.pi * 330.0 * (index / sample_rate)))
        frames.extend(left.to_bytes(2, byteorder="little", signed=True))
        frames.extend(right.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def _write_windowed_tone_wave(
    path: Path,
    *,
    leading_silence_seconds: float,
    tone_seconds: float,
    trailing_silence_seconds: float,
    sample_rate: int = 8000,
) -> None:
    total_frames = int(
        (leading_silence_seconds + tone_seconds + trailing_silence_seconds) * sample_rate
    )
    tone_start = int(leading_silence_seconds * sample_rate)
    tone_end = tone_start + int(tone_seconds * sample_rate)
    frames = bytearray()
    for index in range(total_frames):
        value = (
            int(12000 * math.sin(2.0 * math.pi * 220.0 * (index / sample_rate)))
            if tone_start <= index < tone_end
            else 0
        )
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


class ProcessCommandTests(unittest.TestCase):
    def test_builds_expected_ffmpeg_command(self) -> None:
        options = ProcessOptions(
            input_path=Path("input.wav"),
            output_path=Path("output.mp3"),
            overwrite=True,
            trim_start="00:00:10",
            duration="30",
            gain_db=-3,
            normalize=True,
            highpass_hz=80,
            lowpass_hz=12000,
            sample_rate=44100,
            channels=2,
            codec="libmp3lame",
        )

        self.assertEqual(
            build_process_args(options),
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                "00:00:10",
                "-i",
                "input.wav",
                "-t",
                "30",
                "-af",
                "highpass=f=80,lowpass=f=12000,volume=-3dB,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                "output.mp3",
            ],
        )

    def test_rejects_invalid_numeric_options(self) -> None:
        options = ProcessOptions(
            input_path=Path("input.wav"),
            output_path=Path("output.wav"),
            highpass_hz=0,
        )

        with self.assertRaises(AudioProcessorError):
            build_process_args(options)

    def test_progress_command_uses_ffmpeg_progress_output(self) -> None:
        options = ProcessOptions(
            input_path=Path("input.wav"),
            output_path=Path("output.wav"),
            overwrite=True,
        )

        args = build_process_args(options, progress=True)

        self.assertIn("-progress", args)
        self.assertIn("pipe:1", args)
        self.assertLess(args.index("-progress"), args.index("-i"))

    def test_wav_output_defaults_to_daw_friendly_pcm(self) -> None:
        options = ProcessOptions(
            input_path=Path("input.mp3"),
            output_path=Path("output.wav"),
            overwrite=True,
        )

        args = build_process_args(options)

        self.assertEqual(args[args.index("-codec:a") + 1], "pcm_s24le")

    def test_progress_process_cancels_while_stdout_is_idle(self) -> None:
        cancel_checks = {"count": 0}

        def should_cancel() -> bool:
            cancel_checks["count"] += 1
            return cancel_checks["count"] >= 2

        started_at = time.monotonic()
        with self.assertRaisesRegex(AudioProcessorError, "Processing cancelled"):
            _run_progress_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                duration_seconds=30.0,
                should_cancel=should_cancel,
            )

        self.assertLess(time.monotonic() - started_at, 5.0)


class MaterialAssemblyTests(unittest.TestCase):
    def test_lists_supported_material_audio_files_by_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "B.WAV").write_bytes(b"")
            (root / "a.mp3").write_bytes(b"")
            (root / "notes.txt").write_text("ignored", encoding="utf-8")
            (root / "nested").mkdir()

            files = list_audio_files(root)

        self.assertEqual([path.name for path in files], ["a.mp3", "B.WAV"])

    def test_material_filter_stretches_without_loop_or_hard_cut(self) -> None:
        graph = _build_material_filter_graph(
            2,
            1.25,
            ProcessOptions(input_path=Path("reference.wav"), output_path=Path("output.wav")),
            target_duration=3.5,
        )

        self.assertIn("concat=n=2:v=0:a=1", graph)
        self.assertIn("aformat=channel_layouts=mono", graph)
        self.assertIn("rubberband=tempo=1.25000000:pitch=1:formant=preserved", graph)
        self.assertIn("apad=whole_dur=3.500000", graph)
        self.assertIn("atrim=duration=3.500000", graph)
        self.assertIn("afade=t=in:st=0:d=0.010000", graph)
        self.assertIn("afade=t=out:st=3.490000:d=0.010000", graph)
        self.assertNotIn("stream_loop", graph)

    def test_single_material_file_uses_same_stretch_chain(self) -> None:
        graph = _build_material_filter_graph(
            1,
            0.5,
            ProcessOptions(input_path=Path("reference.wav"), output_path=Path("output.wav")),
        )

        self.assertNotIn("concat=", graph)
        self.assertTrue(graph.startswith("[0:a]aformat=channel_layouts=mono,rubberband=tempo=0.50000000"))

    def test_material_clip_command_stretches_without_loop_or_hard_cut(self) -> None:
        args = build_material_clip_args(
            Path("clip.wav"),
            Path("clip_stretched.wav"),
            1.5,
            ProcessOptions(input_path=Path("clip.wav"), output_path=Path("clip_stretched.wav")),
            target_duration=2.0,
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertTrue(filters.startswith("aformat=channel_layouts=mono,rubberband="))
        self.assertIn("rubberband=tempo=1.50000000:pitch=1:formant=preserved", filters)
        self.assertIn("channels=together", filters)
        self.assertIn("apad=whole_dur=2.000000", filters)
        self.assertIn("atrim=duration=2.000000", filters)
        self.assertIn("afade=t=in:st=0:d=0.010000", filters)
        self.assertIn("afade=t=out:st=1.990000:d=0.010000", filters)
        self.assertNotIn("stream_loop", args)

    def test_material_clip_command_stretches_vowel_core_for_short_text_expansion(self) -> None:
        args = build_material_clip_args(
            Path("clip.wav"),
            Path("clip_loop.wav"),
            0.35,
            ProcessOptions(input_path=Path("clip.wav"), output_path=Path("clip_loop.wav")),
            target_duration=2.0,
            text_hint="shi",
            source_duration=0.5,
            progress=True,
        )

        filters = args[args.index("-filter_complex") + 1]
        self.assertIn("[0:a]aformat=channel_layouts=mono[vc0mono]", filters)
        self.assertIn("asplit=2", filters)
        self.assertIn("atrim=start=0.000000:end=0.140000", filters)
        self.assertIn("atrim=start=0.140000:end=0.500000", filters)
        self.assertIn("rubberband=tempo=", filters)
        self.assertNotIn("aloop=loop=-1:size=2147483647:start=0", filters)
        self.assertIn("apad=whole_dur=2.000000", filters)
        self.assertIn("atrim=duration=2.000000", filters)
        self.assertIn("afade=t=in:st=0:d=0.010000", filters)
        self.assertIn("afade=t=out:st=0.990000:d=0.010000", filters)
        self.assertIn("-map", args)
        self.assertIn("[outa]", args)

    def test_tiny_target_material_clip_skips_rubberband_failure_path(self) -> None:
        args = build_material_clip_args(
            Path("clip.wav"),
            Path("clip_tiny.wav"),
            8.88310734,
            ProcessOptions(input_path=Path("clip.wav"), output_path=Path("clip_tiny.wav")),
            target_duration=0.014074,
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertTrue(filters.startswith("aformat=channel_layouts=mono,"))
        self.assertNotIn("rubberband=", filters)
        self.assertIn("apad=whole_dur=0.014074", filters)
        self.assertIn("atrim=duration=0.014074", filters)
        self.assertIn("afade=t=in:st=0:d=0.002533", filters)
        self.assertIn("afade=t=out:st=0.011541:d=0.002533", filters)

    def test_tiny_audible_material_clip_skips_rubberband_failure_path(self) -> None:
        args = build_material_clip_args(
            Path("a1.wav"),
            Path("clip_tiny_audible.wav"),
            1.2,
            ProcessOptions(input_path=Path("a1.wav"), output_path=Path("clip_tiny_audible.wav")),
            target_duration=13.351413,
            audible_target_duration=0.011413,
            pre_silence_seconds=13.34,
            source_window_duration_seconds=0.013695,
            source_duration=0.013695,
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertIn("aformat=channel_layouts=mono", filters)
        self.assertNotIn("rubberband=", filters)
        self.assertIn("apad=whole_dur=0.011413", filters)
        self.assertIn("atrim=duration=0.011413", filters)
        self.assertIn("afade=t=in:st=0:d=0.002054", filters)
        self.assertIn("afade=t=out:st=0.009359:d=0.002054", filters)
        self.assertIn("adelay=delays=13340:all=1", filters)
        self.assertIn("apad=whole_dur=13.351413", filters)

    def test_short_source_window_material_clip_uses_atempo_to_avoid_rubberband_clicks(self) -> None:
        args = build_material_clip_args(
            Path("ha.wav"),
            Path("clip_short_window.wav"),
            1.2,
            ProcessOptions(input_path=Path("ha.wav"), output_path=Path("clip_short_window.wav")),
            target_duration=0.043636,
            audible_target_duration=0.043636,
            source_window_duration_seconds=0.052364,
            source_duration=0.052364,
            text_hint="ha",
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertIn("-t", args)
        self.assertIn("aformat=channel_layouts=mono", filters)
        self.assertIn("atempo=1.20001833", filters)
        self.assertNotIn("rubberband=", filters)
        self.assertIn("apad=whole_dur=0.043636", filters)
        self.assertIn("atrim=duration=0.043636", filters)
        self.assertIn("afade=t=in:st=0:d=0.004000", filters)
        self.assertIn("afade=t=out:st=0.039636:d=0.004000", filters)

    def test_sub_100ms_short_source_window_material_clip_uses_atempo_to_avoid_rubberband_clicks(self) -> None:
        args = build_material_clip_args(
            Path("de.wav"),
            Path("clip_short_window.wav"),
            1.2,
            ProcessOptions(input_path=Path("de.wav"), output_path=Path("clip_short_window.wav")),
            target_duration=0.077110,
            audible_target_duration=0.077110,
            source_window_duration_seconds=0.092532,
            source_duration=0.092532,
            text_hint="de",
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertIn("-t", args)
        self.assertIn("aformat=channel_layouts=mono", filters)
        self.assertIn("atempo=1.20000000", filters)
        self.assertNotIn("rubberband=", filters)
        self.assertIn("apad=whole_dur=0.077110", filters)
        self.assertIn("atrim=duration=0.077110", filters)
        self.assertIn("afade=t=in:st=0:d=0.006169", filters)
        self.assertIn("afade=t=out:st=0.070941:d=0.006169", filters)

    def test_short_region_rubberband_failure_retries_direct_trim(self) -> None:
        retry = _should_retry_rubberband_short_region_with_direct_trim(
            AudioProcessorError("rubberband filter failed: Operation not permitted"),
            ["ffmpeg", "-af", "aformat=channel_layouts=mono,rubberband=tempo=1.2"],
            source_duration=0.130,
            target_duration=0.400,
            text_hint="de",
        )

        self.assertTrue(retry)

    def test_tiny_audible_material_plan_reports_direct_trim_strategy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "a1.wav"
            _write_test_wave(reference, duration_seconds=13.351413)
            _write_tone_wave(material, duration_seconds=0.285938)

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[13.351413],
                audible_target_durations=[0.011413],
                pre_silence_seconds=[13.34],
                material_text_hints=["a1"],
            )

        self.assertEqual(clips[0].stretch_strategy, "tiny_target_direct_trim")
        self.assertAlmostEqual(clips[0].audible_target_duration_seconds or 0.0, 0.011413)
        self.assertIsNone(clips[0].source_window_duration_seconds)
        self.assertEqual(clips[0].quality_warning, "")
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["formant_preservation"], "direct_trim_no_pitch_shift")

    def test_short_region_material_plan_reports_atempo_backend(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "ha.wav"
            _write_test_wave(reference, duration_seconds=0.043636)
            _write_test_wave(material, duration_seconds=0.052364)

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.043636],
                material_text_hints=["ha"],
            )

        rendered = render_material_stretch_plan(clips)
        self.assertEqual(clips[0].stretch_strategy, "rubberband_full_clip")
        self.assertEqual(clips[0].stretch_backend, "atempo")
        self.assertEqual(rendered[0]["formant_preservation"], "atempo_pitch_preserved_short_cv")

    def test_short_material_high_compression_uses_chained_atempo(self) -> None:
        args = build_material_clip_args(
            Path("ha.wav"),
            Path("clip_short_window.wav"),
            5.0,
            ProcessOptions(input_path=Path("ha.wav"), output_path=Path("clip_short_window.wav")),
            target_duration=0.05,
            audible_target_duration=0.05,
            source_window_duration_seconds=0.25,
            source_duration=0.25,
            text_hint="ha",
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertIn("atempo=2.00000000,atempo=2.00000000,atempo=1.25000000", filters)
        self.assertNotIn("rubberband=", filters)

    def test_material_assembly_uses_per_clip_stretch_plan(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "4.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "3.0"}, "streams": []},
            ],
        ):
            args = build_material_assembly_args(
                Path("reference.wav"),
                [Path("a.wav"), Path("b.wav")],
                Path("out.wav"),
                ProcessOptions(input_path=Path("reference.wav"), output_path=Path("out.wav"), overwrite=True),
                material_target_durations=[2.0, 2.0],
            )

        filters = args[args.index("-filter_complex") + 1]
        self.assertIn("[0:a]aformat=channel_layouts=mono,rubberband=tempo=0.50000000:pitch=1:formant=preserved", filters)
        self.assertIn("[1:a]aformat=channel_layouts=mono,rubberband=tempo=1.50000000:pitch=1:formant=preserved", filters)
        self.assertIn("[clip0][clip1]concat=n=2:v=0:a=1[outa]", filters)
        self.assertIn("atrim=duration=2.000000", filters)
        self.assertIn("afade=t=in:st=0:d=0.010000", filters)
        self.assertNotIn("stream_loop", filters)

    def test_tiny_target_stretch_plan_uses_direct_trim_strategy(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "0.125"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("ka1.wav")],
                target_durations=[0.014074],
                material_text_hints=["ka1"],
            )

        self.assertEqual(clips[0].stretch_strategy, "tiny_target_direct_trim")
        self.assertAlmostEqual(clips[0].target_duration_seconds, 0.014074)
        self.assertEqual(clips[0].quality_warning, "")
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["formant_preservation"], "direct_trim_no_pitch_shift")
        self.assertEqual(rendered[0]["channel_coherence"], "material_mono_fold_then_mono_output")

    def test_tone_number_pinyin_material_counts_as_short_text_for_tiny_direct_trim(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "0.14"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("liang1.wav")],
                target_durations=[0.02],
                material_text_hints=["liang1"],
            )

        self.assertEqual(clips[0].stretch_strategy, "tiny_target_direct_trim")
        self.assertEqual(clips[0].quality_warning, "")
        self.assertEqual(clips[0].continuity_warning, "single_syllable_boundary_risk")

    def test_material_stretch_plan_flags_extreme_ratios(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "10.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("short.wav")],
            )

        self.assertEqual(clips[0].target_duration_seconds, 10.0)
        self.assertEqual(clips[0].quality_warning, "extreme_stretch_ratio")
        self.assertEqual(clips[0].continuity_warning, "extreme_boundary_risk")
        self.assertLess(clips[0].stretch_naturalness_score, 0.1)
        self.assertEqual(clips[0].fade_seconds, 0.01)

    def test_material_stretch_plan_preserves_partial_model_target_durations(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "10.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("wo.wav"), Path("unknown.wav"), Path("ni.wav")],
                target_durations=[2.0, None, 3.0],
            )

        self.assertEqual([clip.target_duration_seconds for clip in clips], [2.0, 5.0, 3.0])
        self.assertEqual(
            [round(clip.requested_tempo or 0.0, 6) for clip in clips],
            [0.5, 0.2, 0.333333],
        )

    def test_material_stretch_plan_preserves_complete_model_target_durations_without_total_scaling(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "10.0"}, "streams": []},
                {"format": {"duration": "0.4"}, "streams": []},
                {"format": {"duration": "0.5"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("ni.wav"), Path("ai.wav")],
                target_durations=[0.3, 0.6],
            )

        self.assertEqual([round(clip.target_duration_seconds, 6) for clip in clips], [0.3, 0.6])
        self.assertEqual([round(clip.requested_tempo or 0.0, 6) for clip in clips], [1.333333, 0.833333])

    def test_material_stretch_plan_keeps_model_targets_inside_rubberband_bounds(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("too_short.wav"), Path("remaining.wav")],
                target_durations=[0.001, None],
            )

        self.assertEqual(round(sum(clip.target_duration_seconds for clip in clips), 6), 1.0)
        self.assertAlmostEqual(clips[0].target_duration_seconds, 0.01)
        self.assertAlmostEqual(clips[0].tempo, 100.0)
        self.assertEqual(clips[0].quality_warning, "extreme_stretch_ratio")

    def test_short_material_compression_uses_source_window_trim(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "ko.wav"
            _write_test_wave(reference, duration_seconds=0.2)
            _write_tone_wave(material, duration_seconds=1.0)

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.2],
                material_text_hints=["ko"],
            )

            self.assertAlmostEqual(clips[0].source_window_start_seconds, 0.0)
            self.assertAlmostEqual(clips[0].source_window_duration_seconds or 0.0, 0.24, places=2)
            self.assertAlmostEqual(clips[0].requested_tempo or 0.0, 1.2, places=2)
            self.assertEqual(clips[0].quality_warning, "")
            rendered = render_material_stretch_plan(clips)
            self.assertAlmostEqual(rendered[0]["source_window_duration_seconds"] or 0.0, 0.24, places=2)

            args = build_material_clip_args(
                material,
                root / "out.wav",
                clips[0].tempo,
                ProcessOptions(input_path=material, output_path=root / "out.wav"),
                target_duration=clips[0].target_duration_seconds,
                audible_target_duration=clips[0].audible_target_duration_seconds,
                source_window_start_seconds=clips[0].source_window_start_seconds,
                source_window_duration_seconds=clips[0].source_window_duration_seconds,
                text_hint="ko",
                source_duration=clips[0].source_window_duration_seconds,
                progress=True,
            )
            self.assertIn("-t", args)
            self.assertIn("0.240000", args)

    def test_utau_oto_ini_guides_short_material_source_window(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "ka.wav"
            _write_test_wave(reference, duration_seconds=0.2)
            _write_test_wave(material, duration_seconds=0.3)
            (root / "oto.ini").write_text(
                "\u304b.wav=,10,120,80,40,5\n",
                encoding="utf-8",
            )

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.2],
                material_text_hints=["ka"],
            )

        self.assertEqual(clips[0].source_window_source, "utau_oto_ini")
        self.assertAlmostEqual(clips[0].source_window_start_seconds, 0.001, places=3)
        self.assertAlmostEqual(clips[0].source_window_duration_seconds or 0.0, 0.231, places=3)
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["source_window_source"], "utau_oto_ini")

    def test_utau_oto_short_target_window_avoids_extreme_compression_warning(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "shi.wav"
            _write_test_wave(reference, duration_seconds=0.045)
            _write_test_wave(material, duration_seconds=0.35)
            (root / "oto.ini").write_text(
                "shi.wav=,0,220,0,132,46\n",
                encoding="utf-8",
            )

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.045],
                material_text_hints=["shi"],
            )

        self.assertEqual(clips[0].source_window_source, "utau_oto_ini")
        self.assertAlmostEqual(clips[0].source_window_duration_seconds or 0.0, 0.08775, places=5)
        self.assertLess(clips[0].requested_tempo or 0.0, 2.0)
        self.assertEqual(clips[0].quality_warning, "moderate_stretch_ratio")
        self.assertEqual(clips[0].continuity_warning, "moderate_boundary_risk")
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["quality_warning"], "moderate_stretch_ratio")

    def test_utau_oto_tiny_target_keeps_faded_direct_trim_without_boundary_warning(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "shi.wav"
            _write_test_wave(reference, duration_seconds=0.02)
            _write_test_wave(material, duration_seconds=0.35)
            (root / "oto.ini").write_text(
                "shi.wav=,0,220,0,132,46\n",
                encoding="utf-8",
            )

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.02],
                material_text_hints=["shi"],
            )

        self.assertEqual(clips[0].source_window_source, "utau_oto_ini")
        self.assertAlmostEqual(clips[0].source_window_duration_seconds or 0.0, 0.039, places=5)
        self.assertAlmostEqual(clips[0].requested_tempo or 0.0, 1.95, places=5)
        self.assertEqual(clips[0].stretch_strategy, "tiny_target_direct_trim")
        self.assertEqual(clips[0].stretch_backend, "rubberband")
        self.assertEqual(clips[0].quality_warning, "")
        self.assertEqual(clips[0].continuity_warning, "")

    def test_utau_oto_mid_short_target_window_avoids_extreme_compression_warning(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "zi1.wav"
            _write_test_wave(reference, duration_seconds=0.2)
            _write_test_wave(material, duration_seconds=0.5)
            (root / "oto.ini").write_text(
                "zi1.wav=,0,420,0,120,0\n",
                encoding="utf-8",
            )

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.2],
                material_text_hints=["zi1"],
            )

        self.assertEqual(clips[0].source_window_source, "utau_oto_ini")
        self.assertAlmostEqual(clips[0].source_window_duration_seconds or 0.0, 0.39, places=5)
        self.assertLess(clips[0].requested_tempo or 0.0, 2.0)
        self.assertEqual(clips[0].quality_warning, "moderate_stretch_ratio")

    def test_source_window_skips_vowel_core_graph_for_trimmed_input(self) -> None:
        args = build_material_clip_args(
            Path("shi.wav"),
            Path("out.wav"),
            0.5,
            ProcessOptions(input_path=Path("shi.wav"), output_path=Path("out.wav")),
            target_duration=1.0,
            audible_target_duration=1.0,
            source_window_start_seconds=0.12,
            source_window_duration_seconds=0.5,
            text_hint="shi",
            source_duration=0.5,
        )

        self.assertIn("-ss", args)
        self.assertIn("-t", args)
        self.assertIn("-af", args)
        self.assertNotIn("-filter_complex", args)

    def test_source_window_plan_reports_atempo_not_signalsmith_for_short_cv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "shi.wav"
            _write_test_wave(reference, duration_seconds=0.4)
            _write_windowed_tone_wave(
                material,
                leading_silence_seconds=0.12,
                tone_seconds=0.20,
                trailing_silence_seconds=0.68,
            )

            clips = plan_material_stretch_clips(
                reference,
                [material],
                target_durations=[0.4],
                material_text_hints=["shi"],
            )

        self.assertGreater(clips[0].source_window_start_seconds, 0.0)
        self.assertIsNotNone(clips[0].source_window_duration_seconds)
        self.assertEqual(clips[0].stretch_strategy, "rubberband_full_clip")
        self.assertEqual(clips[0].stretch_backend, "atempo")
        self.assertEqual(clips[0].phoneme_regions, ())

    def test_short_material_expansion_uses_vowel_core_stretch(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "2.0"}, "streams": []},
                {"format": {"duration": "0.5"}, "streams": []},
            ],
        ), patch("audio_processor.engine.signalsmith_stretch_available", return_value=True):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("shi.wav")],
                material_text_hints=["shi"],
            )

        self.assertAlmostEqual(clips[0].timeline_requested_tempo or 0.0, 0.25)
        self.assertAlmostEqual(clips[0].requested_tempo or 0.0, 0.5)
        self.assertAlmostEqual(clips[0].tempo, 0.5)
        self.assertEqual(clips[0].stretch_strategy, "syllable_vowel_core_stretch")
        self.assertEqual(clips[0].quality_warning, "moderate_stretch_ratio")
        self.assertEqual(clips[0].continuity_warning, "single_syllable_boundary_risk")
        self.assertAlmostEqual(clips[0].stretch_naturalness_score, 0.48)
        self.assertAlmostEqual(clips[0].audible_target_duration_seconds or 0.0, 1.0)
        self.assertAlmostEqual(clips[0].post_silence_seconds, 1.0)
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(clips[0].stretch_backend, "rubberband")
        self.assertEqual(rendered[0]["stretch_backend"], "rubberband")
        self.assertEqual(rendered[0]["rubberband_profile"], "vocal_smooth")
        self.assertEqual(
            rendered[0]["boundary_conditioning"],
            "vowel_core_stretch+fade_in_out+tempo_safe_silence_pad",
        )
        self.assertAlmostEqual(rendered[0]["audible_target_duration_seconds"], 1.0)
        self.assertAlmostEqual(rendered[0]["post_silence_seconds"], 1.0)
        self.assertEqual(rendered[0]["formant_preservation"], "vowel_core_rubberband_formant_preserved")
        self.assertEqual(
            [region["kind"] for region in rendered[0]["phoneme_regions"]],
            ["consonant_attack", "vowel_core"],
        )

    def test_vowel_core_regions_prioritize_acoustic_voiced_span(self) -> None:
        acoustic_profile = VowelConsonantProfile(
            vowel_start_seconds=0.136,
            vowel_end_seconds=0.928,
            detection_source="librosa_voiced_f0",
            confidence=0.95,
            voiced_ratio=0.70,
        )
        with patch(
            "audio_processor.engine.analyze_vowel_consonant_profile",
            return_value=acoustic_profile,
        ):
            regions = _vowel_core_stretch_regions(
                1.037,
                4.148,
                "chi",
                source_path=Path("chi.wav"),
            )

        self.assertEqual(
            [region.kind for region in regions],
            ["consonant_attack", "vowel_core", "consonant_coda"],
        )
        self.assertAlmostEqual(regions[0].source_start_seconds, 0.0)
        self.assertAlmostEqual(regions[0].source_end_seconds, 0.136)
        self.assertAlmostEqual(regions[1].source_start_seconds, 0.136)
        self.assertAlmostEqual(regions[1].source_end_seconds, 0.928)
        self.assertAlmostEqual(regions[2].source_start_seconds, 0.928)
        self.assertAlmostEqual(regions[2].source_end_seconds, 1.037)
        self.assertLess(regions[0].target_duration_seconds / regions[0].source_duration_seconds, 1.21)
        self.assertLess(regions[2].target_duration_seconds / regions[2].source_duration_seconds, 1.13)

    def test_short_vowel_core_expansion_uses_atempo_chain(self) -> None:
        acoustic_profile = VowelConsonantProfile(
            vowel_start_seconds=0.046717,
            vowel_end_seconds=0.136,
            detection_source="librosa_voiced_f0",
            confidence=0.95,
            voiced_ratio=0.70,
        )
        with patch(
            "audio_processor.engine.analyze_vowel_consonant_profile",
            return_value=acoustic_profile,
        ):
            graph = _build_vowel_core_filter_graph(
                "[0:a]",
                "outa",
                source_duration=0.166848,
                target_duration=0.333696,
                text_hint="ju",
                options=ProcessOptions(input_path=Path("ju.wav"), output_path=Path("out.wav")),
                label_prefix="vc0",
                source_path=Path("ju.wav"),
                output_duration=0.4,
            )

        self.assertIn("atempo=0.50000000,atempo=0.70000000", graph)
        self.assertNotIn("rubberband=", graph)

    def test_duration_correction_command_preserves_exact_duration_without_extra_fades(self) -> None:
        args = _build_duration_correction_args(
            Path("clip.wav"),
            Path("clip.fixed.wav"),
            2.0,
            ProcessOptions(input_path=Path("clip.wav"), output_path=Path("clip.fixed.wav"), overwrite=True),
            progress=True,
        )

        filters = args[args.index("-af") + 1]
        self.assertIn("apad=whole_dur=2.000000", filters)
        self.assertIn("atrim=duration=2.000000", filters)
        self.assertIn("asetpts=N/SR/TB", filters)
        self.assertNotIn("afade=", filters)

    def test_duration_correction_replaces_mismatched_audio(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clip = root / "clip.wav"
            _write_test_wave(clip, duration_seconds=0.5)

            def fake_correction(args: list[str], **_: object) -> None:
                _write_test_wave(Path(args[-1]), duration_seconds=2.0)

            with patch("audio_processor.engine._run_progress_process", side_effect=fake_correction) as run_mock:
                corrected = _ensure_audio_duration(
                    clip,
                    2.0,
                    ProcessOptions(input_path=clip, output_path=clip, overwrite=True),
                )
                final_duration = get_audio_duration_seconds(probe_audio(clip))

        self.assertAlmostEqual(corrected, 2.0)
        self.assertEqual(run_mock.call_count, 1)
        self.assertAlmostEqual(final_duration, 2.0)

    def test_flat_wav_assembly_reuses_duplicate_render_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "material.wav"
            output = root / "output.wav"
            _write_test_wave(reference, duration_seconds=2.0)
            _write_test_wave(material, duration_seconds=1.0)

            def fake_render(
                input_path: Path,
                output_path: Path,
                tempo: float,
                options: ProcessOptions,
                *,
                target_duration: float | None = None,
                audible_target_duration: float | None = None,
                pre_silence_seconds: float = 0.0,
                source_window_start_seconds: float = 0.0,
                source_window_duration_seconds: float | None = None,
                text_hint: str = "",
                on_progress=None,
                should_cancel=None,
            ) -> None:
                _write_test_wave(output_path, duration_seconds=target_duration or 1.0)

            def fake_concat(args: list[str], **_: object) -> None:
                _write_test_wave(Path(args[-1]), duration_seconds=2.0)

            with patch("audio_processor.engine.process_material_clip_with_progress", side_effect=fake_render) as render_mock:
                with patch("audio_processor.engine._run_progress_process", side_effect=fake_concat) as concat_mock:
                    assemble_material_to_reference_with_progress(
                        reference,
                        root,
                        output,
                        ProcessOptions(input_path=reference, output_path=output, overwrite=True),
                        material_paths=[material, material],
                        material_target_durations=[1.0, 1.0],
                    )

        self.assertEqual(render_mock.call_count, 1)
        concat_mock.assert_called_once()

    def test_flat_wav_assembly_regenerates_wrong_duration_render_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material = root / "material.wav"
            output = root / "output.wav"
            cache_root = root / ".vocalprocess_render_cache"
            cache_root.mkdir()
            stale_cache = cache_root / "fixed-cache-key.wav"
            _write_test_wave(reference, duration_seconds=2.0)
            _write_test_wave(material, duration_seconds=1.0)
            _write_test_wave(stale_cache, duration_seconds=0.25)

            def fake_render(
                input_path: Path,
                output_path: Path,
                tempo: float,
                options: ProcessOptions,
                *,
                target_duration: float | None = None,
                audible_target_duration: float | None = None,
                pre_silence_seconds: float = 0.0,
                source_window_start_seconds: float = 0.0,
                source_window_duration_seconds: float | None = None,
                text_hint: str = "",
                on_progress=None,
                should_cancel=None,
            ) -> None:
                _write_test_wave(output_path, duration_seconds=target_duration or 1.0)

            def fake_concat(args: list[str], **_: object) -> None:
                _write_test_wave(Path(args[-1]), duration_seconds=2.0)

            with patch("audio_processor.engine._material_render_cache_key", return_value="fixed-cache-key"):
                with patch("audio_processor.engine.process_material_clip_with_progress", side_effect=fake_render) as render_mock:
                    with patch("audio_processor.engine._run_progress_process", side_effect=fake_concat):
                        assemble_material_to_reference_with_progress(
                            reference,
                            root,
                            output,
                            ProcessOptions(input_path=reference, output_path=output, overwrite=True),
                            material_paths=[material, material],
                            material_target_durations=[1.0, 1.0],
                        )
                        final_cache_duration = get_audio_duration_seconds(probe_audio(stale_cache))

        self.assertEqual(render_mock.call_count, 1)
        self.assertAlmostEqual(final_cache_duration, 1.0)

    def test_render_cache_clips_inherit_reference_rate_but_force_mono_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material_a = root / "material_a.wav"
            material_b = root / "material_b.wav"
            output = root / "output.wav"
            _write_test_wave(reference, duration_seconds=2.0, sample_rate=44100, channels=2)
            _write_test_wave(material_a, duration_seconds=1.0, sample_rate=8000, channels=1)
            _write_test_wave(material_b, duration_seconds=1.0, sample_rate=48000, channels=1)
            render_shapes: list[tuple[int | None, int | None]] = []
            concat_args: list[str] = []

            def fake_render(
                input_path: Path,
                output_path: Path,
                tempo: float,
                options: ProcessOptions,
                *,
                target_duration: float | None = None,
                audible_target_duration: float | None = None,
                pre_silence_seconds: float = 0.0,
                source_window_start_seconds: float = 0.0,
                source_window_duration_seconds: float | None = None,
                text_hint: str = "",
                on_progress=None,
                should_cancel=None,
            ) -> None:
                del input_path, tempo, audible_target_duration, pre_silence_seconds
                del source_window_start_seconds, source_window_duration_seconds, text_hint
                del on_progress, should_cancel
                render_shapes.append((options.sample_rate, options.channels))
                _write_test_wave(
                    output_path,
                    duration_seconds=target_duration or 1.0,
                    sample_rate=options.sample_rate or 8000,
                    channels=options.channels or 1,
                )

            def fake_concat(args: list[str], **_: object) -> None:
                concat_args[:] = [str(part) for part in args]
                _write_test_wave(output, duration_seconds=2.0, sample_rate=44100, channels=2)

            with patch("audio_processor.engine.process_material_clip_with_progress", side_effect=fake_render):
                with patch("audio_processor.engine._run_progress_process", side_effect=fake_concat):
                    assemble_material_to_reference_with_progress(
                        reference,
                        root,
                        output,
                        ProcessOptions(input_path=reference, output_path=output, overwrite=True),
                        material_paths=[material_a, material_b],
                        material_target_durations=[1.0, 1.0],
                    )

        self.assertEqual(render_shapes, [(44100, 1), (44100, 1)])
        self.assertIn("-ar", concat_args)
        self.assertEqual(concat_args[concat_args.index("-ar") + 1], "44100")
        self.assertIn("-ac", concat_args)
        self.assertEqual(concat_args[concat_args.index("-ac") + 1], "1")
        concat_command = " ".join(concat_args)
        self.assertIn("lowpass=f=12000", concat_command)
        self.assertIn("adeclick=", concat_command)
        self.assertIn("alimiter=limit=0.900000", concat_command)

    def test_large_rendered_clip_concat_uses_concat_list_to_avoid_windows_command_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clip_paths = []
            for index in range(70):
                path = root / f"clip-{index}.wav"
                _write_test_wave(path, duration_seconds=0.01)
                clip_paths.append(path)

            list_path = _write_rendered_clip_concat_list(clip_paths, root, root / "out.wav")
            args = _build_rendered_clip_concat_args(
                clip_paths,
                root / "out.wav",
                ProcessOptions(input_path=root / "ref.wav", output_path=root / "out.wav", overwrite=True),
                concat_list_path=list_path,
                progress=True,
            )

        self.assertIn("-f", args)
        self.assertIn("concat", args)
        self.assertEqual(args.count("-i"), 1)
        self.assertTrue(list_path.name.endswith(".concat.txt"))


class DawTimelineTests(unittest.TestCase):
    def test_plans_separate_daw_clips_on_reference_timeline(self) -> None:
        reference = Path("reference.wav")
        materials = [Path("001.wav"), Path("002.wav")]

        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "4.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "3.0"}, "streams": []},
            ],
        ):
            plan = plan_daw_timeline(reference, materials, Path("project") / "audio")

        self.assertEqual(plan.reference_duration_seconds, 4.0)
        self.assertEqual(plan.material_duration_seconds, 4.0)
        self.assertEqual(plan.tempo, 1.0)
        self.assertEqual([clip.start_seconds for clip in plan.clips], [0.0, 1.0])
        self.assertEqual([clip.target_duration_seconds for clip in plan.clips], [1.0, 3.0])
        self.assertEqual([clip.tempo for clip in plan.clips], [1.0, 1.0])
        self.assertEqual([clip.rendered_path.name for clip in plan.clips], ["0001_001.wav", "0002_002.wav"])

    def test_plans_daw_clips_from_model_target_durations(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "4.0"}, "streams": []},
                {"format": {"duration": "1.0"}, "streams": []},
                {"format": {"duration": "3.0"}, "streams": []},
            ],
        ):
            plan = plan_daw_timeline(
                Path("reference.wav"),
                [Path("001.wav"), Path("002.wav")],
                Path("project") / "audio",
                target_durations=[2.0, 2.0],
            )

        self.assertEqual([clip.target_duration_seconds for clip in plan.clips], [2.0, 2.0])
        self.assertEqual([clip.tempo for clip in plan.clips], [0.5, 1.5])

    def test_reaper_project_references_separate_rendered_clips(self) -> None:
        result = DawExportResult(
            project_path=Path("song_daw") / "song.rpp",
            manifest_path=Path("song_daw") / "timeline.json",
            csv_path=Path("song_daw") / "timeline.csv",
            audio_directory=Path("song_daw") / "audio",
            clips=[
                DawTimelineClip(
                    index=1,
                    source_path=Path("materials") / "a.wav",
                    rendered_path=Path("song_daw") / "audio" / "0001_a.wav",
                    start_seconds=0.0,
                    source_duration_seconds=1.0,
                    target_duration_seconds=1.5,
                    actual_duration_seconds=1.5,
                ),
                DawTimelineClip(
                    index=2,
                    source_path=Path("materials") / "b.wav",
                    rendered_path=Path("song_daw") / "audio" / "0002_b.wav",
                    start_seconds=1.5,
                    source_duration_seconds=1.0,
                    target_duration_seconds=2.5,
                    actual_duration_seconds=2.5,
                ),
            ],
        )
        plan = DawTimelinePlan(
            reference_path=Path("reference.wav"),
            reference_duration_seconds=4.0,
            material_duration_seconds=2.0,
            tempo=0.5,
            clips=result.clips,
        )

        project = render_reaper_project(result, plan)

        self.assertIn('NAME "VocalProcess Stretched Clips"', project)
        self.assertIn("POSITION 0.000000", project)
        self.assertIn("POSITION 1.500000", project)
        self.assertIn("LENGTH 2.500000", project)
        self.assertIn('FILE "audio/0001_a.wav"', project)
        self.assertIn('FILE "audio/0002_b.wav"', project)


class TimelineHandoffTests(unittest.TestCase):
    def _fake_daw_result(self, output_directory: Path) -> DawExportResult:
        audio_directory = output_directory / "audio"
        return DawExportResult(
            project_path=output_directory / "timeline.rpp",
            manifest_path=output_directory / "timeline.json",
            csv_path=output_directory / "timeline.csv",
            audio_directory=audio_directory,
            clips=[
                DawTimelineClip(
                    index=1,
                    source_path=Path("materials") / "first.wav",
                    rendered_path=audio_directory / "0001_first.wav",
                    start_seconds=0.0,
                    source_duration_seconds=1.0,
                    target_duration_seconds=1.5,
                    actual_duration_seconds=1.5,
                    text_hint="first",
                ),
                DawTimelineClip(
                    index=2,
                    source_path=Path("materials") / "second.wav",
                    rendered_path=audio_directory / "0002_second.wav",
                    start_seconds=1.5,
                    source_duration_seconds=1.0,
                    target_duration_seconds=2.5,
                    actual_duration_seconds=2.5,
                    text_hint="second",
                ),
            ],
        )

    def test_melodyne_handoff_renders_full_timeline_lanes(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        output_directory = root / "melodyne"
        fake_result = self._fake_daw_result(output_directory)

        with patch(
            "audio_processor.handoff.probe_audio",
            return_value={
                "format": {"duration": "4.0"},
                "streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 2}],
            },
        ), patch(
            "audio_processor.handoff.export_daw_timeline_with_progress",
            return_value=fake_result,
        ), patch("audio_processor.handoff.run_command") as run_mock:
            result = export_melodyne_handoff_with_progress(
                Path("reference.wav"),
                Path("materials"),
                output_directory,
                ProcessOptions(
                    input_path=Path("reference.wav"),
                    output_path=output_directory / "melodyne_full.wav",
                    overwrite=True,
                ),
            )

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(result.target, "melodyne")
        self.assertEqual(result.full_mix_path.name, "melodyne_full.wav")
        self.assertEqual(len(result.lanes), 2)
        self.assertIsNone(result.bwf_directory)
        self.assertEqual(manifest["format"], "vocal_process_timeline_handoff_v1")
        self.assertEqual(manifest["target"], "melodyne")
        self.assertEqual(run_mock.call_count, 3)
        second_lane_command = " ".join(str(part) for part in run_mock.call_args_list[1].args[0])
        self.assertIn("adelay=delays=1500:all=1", second_lane_command)
        self.assertIn("atrim=duration=4.000000", second_lane_command)

    def test_vegas_handoff_writes_bwf_timestamp_metadata(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        output_directory = root / "vegas"
        fake_result = self._fake_daw_result(output_directory)

        with patch(
            "audio_processor.handoff.probe_audio",
            return_value={
                "format": {"duration": "4.0"},
                "streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 2}],
            },
        ), patch(
            "audio_processor.handoff.export_daw_timeline_with_progress",
            return_value=fake_result,
        ), patch("audio_processor.handoff.run_command") as run_mock:
            result = export_vegas_handoff_with_progress(
                Path("reference.wav"),
                Path("materials"),
                output_directory,
                ProcessOptions(
                    input_path=Path("reference.wav"),
                    output_path=output_directory / "vegas_full.wav",
                    overwrite=True,
                ),
            )

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(result.target, "vegas")
        self.assertEqual(len(result.bwf_clips), 2)
        self.assertEqual(result.bwf_clips[1].time_reference_samples, 66150)
        self.assertEqual(manifest["bwf_clips"][1]["time_reference_samples"], 66150)
        bwf_commands = [call.args[0] for call in run_mock.call_args_list if "-write_bext" in call.args[0]]
        self.assertEqual(len(bwf_commands), 2)
        self.assertIn("time_reference=66150", bwf_commands[1])


class ToolResolutionTests(unittest.TestCase):
    def test_resolve_tool_prefers_portable_bin(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portable_tool = root / "bin" / "ffmpeg.exe"
            portable_tool.parent.mkdir()
            portable_tool.write_bytes(b"")

            with patch("audio_processor.engine._runtime_tool_roots", return_value=[root]):
                with patch("shutil.which", return_value=None):
                    self.assertEqual(resolve_tool("ffmpeg"), str(portable_tool))

    def test_runtime_tool_paths_expose_portable_bin_to_child_libraries(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portable_bin = root / "bin"
            portable_bin.mkdir()
            (portable_bin / "ffmpeg.exe").write_bytes(b"")

            with patch("audio_processor.engine._runtime_tool_roots", return_value=[root]):
                with patch.dict(os.environ, {"PATH": r"C:\Windows"}, clear=False):
                    tool_dirs = ensure_runtime_tool_paths()
                    path_parts = os.environ["PATH"].split(os.pathsep)

        self.assertEqual(tool_dirs, [portable_bin.resolve()])
        self.assertEqual(path_parts[0], str(portable_bin.resolve()))

    def test_runtime_tool_paths_expose_existing_path_tool_dir_to_child_libraries(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tool_dir = Path(temp_dir) / "ffmpeg-bin"
            tool_dir.mkdir()
            executable = tool_dir / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")
            executable.write_bytes(b"")

            def fake_which(name: str) -> str | None:
                if name == "ffmpeg":
                    return str(executable)
                return None

            with patch("audio_processor.engine._runtime_tool_roots", return_value=[]):
                with patch("audio_processor.engine._windows_common_runtime_tool_directories", return_value=[]):
                    with patch("shutil.which", side_effect=fake_which):
                        with patch.dict(os.environ, {"PATH": r"C:\Windows"}, clear=False):
                            tool_dirs = ensure_runtime_tool_paths()
                            path_parts = os.environ["PATH"].split(os.pathsep)

        self.assertEqual(tool_dirs, [tool_dir.resolve()])
        self.assertEqual(path_parts[0], str(tool_dir.resolve()))

    def test_runtime_tool_paths_expose_configured_tool_dir_when_path_is_stripped(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tool_dir = Path(temp_dir) / "configured-ffmpeg"
            tool_dir.mkdir()
            executable = tool_dir / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")
            executable.write_bytes(b"")

            env = {"PATH": r"C:\Windows", "VOCAL_PROCESS_FFMPEG_DIR": str(tool_dir)}
            with patch("audio_processor.engine._runtime_tool_roots", return_value=[]):
                with patch("audio_processor.engine._windows_common_runtime_tool_directories", return_value=[]):
                    with patch("shutil.which", return_value=None):
                        with patch.dict(os.environ, env, clear=True):
                            tool_dirs = ensure_runtime_tool_paths()
                            path_parts = os.environ["PATH"].split(os.pathsep)

        self.assertEqual(tool_dirs, [tool_dir.resolve()])
        self.assertEqual(path_parts[0], str(tool_dir.resolve()))


class ProbeSummaryTests(unittest.TestCase):
    def test_summarizes_probe_data(self) -> None:
        data = {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "duration": "1.250000",
                    "sample_rate": "44100",
                    "channels": 2,
                    "bit_rate": "1411200",
                }
            ],
            "format": {"format_name": "wav", "bit_rate": "1411200"},
        }

        self.assertEqual(
            summarize_probe(data),
            [
                ("format", "wav"),
                ("duration", "00:00:01.250"),
                ("codec", "pcm_s16le"),
                ("sample_rate", "44100"),
                ("channels", "2"),
                ("bit_rate", "1411200"),
            ],
        )

    def test_get_audio_duration_seconds_uses_format_fallback(self) -> None:
        self.assertEqual(
            get_audio_duration_seconds({"streams": [], "format": {"duration": "2.5"}}),
            2.5,
        )

    def test_probe_audio_rejects_missing_json_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.wav"
            path.write_bytes(b"placeholder")

            with patch(
                "audio_processor.engine.run_command",
                return_value=SimpleNamespace(stdout=None, stderr=""),
            ):
                with self.assertRaisesRegex(AudioProcessorError, "no JSON metadata"):
                    probe_audio(path)

    def test_probe_audio_falls_back_to_python_wave_when_ffprobe_json_is_empty(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.wav"
            _write_test_wave(path)

            with patch(
                "audio_processor.engine.run_command",
                return_value=SimpleNamespace(stdout="", stderr=""),
            ):
                data = probe_audio(path)

        self.assertEqual(data["probe_fallback"], "python_wave")
        self.assertGreater(get_audio_duration_seconds(data), 0)

    def test_probe_audio_wraps_invalid_json_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.wav"
            path.write_bytes(b"placeholder")

            with patch(
                "audio_processor.engine.run_command",
                return_value=SimpleNamespace(stdout="not json", stderr=""),
            ):
                with self.assertRaisesRegex(AudioProcessorError, "invalid JSON metadata"):
                    probe_audio(path)


class SourceSeparationTests(unittest.TestCase):
    def test_uvr_vocal_output_finder_ignores_instrumental_stems(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "song_(Instrumental).wav").write_bytes(b"")
            vocal = root / "song_(Vocals).wav"
            vocal.write_bytes(b"")

            self.assertEqual(model_runtime.find_uvr_vocal_output(root), vocal)

    def test_maybe_separate_vocals_uses_uvr_before_demucs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            vocal = root / "uvr-vocals.wav"
            reference.write_bytes(b"placeholder")
            vocal.write_bytes(b"placeholder")
            notes: list[str] = []

            with patch.dict(os.environ, {"VOCAL_PROCESS_SOURCE_SEPARATOR": "uvr-only"}):
                with patch("audio_processor.model_runtime.find_uvr_vocal_output", return_value=None):
                    with patch("audio_processor.model_runtime.separate_vocals_with_uvr", return_value=vocal):
                        result = model_runtime._maybe_separate_vocals(
                            reference,
                            work_dir=root / "work",
                            compute_device="cpu",
                            source_separation="auto",
                            notes=notes,
                        )

        self.assertEqual(result, vocal)
        self.assertFalse(any("demucs" in note.lower() for note in notes))

    def test_reference_cache_key_includes_uvr_model(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            reference.write_bytes(b"placeholder")

            with patch.dict(os.environ, {"VOCAL_PROCESS_UVR_MODEL": "htdemucs"}):
                first = model_runtime._reference_cache_path(root, reference, None, "cpu", "auto")
            with patch.dict(os.environ, {"VOCAL_PROCESS_UVR_MODEL": "custom-mdx.onnx"}):
                second = model_runtime._reference_cache_path(root, reference, None, "cpu", "auto")

        self.assertNotEqual(first.name, second.name)

    def test_reference_channel_lanes_extract_stereo_input_as_mono_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            _write_independent_stereo_wave(reference, duration_seconds=1.0, sample_rate=8000)

            lanes = model_runtime.prepare_reference_channel_lanes(
                reference,
                work_dir=root / "work",
                source_separation="never",
            )
            lane_metadata = [probe_audio(lane.reference_path) for lane in lanes]

        self.assertEqual([lane.label for lane in lanes], ["left", "right"])
        self.assertTrue(all(lane.split_from_stereo for lane in lanes))
        self.assertEqual(
            [stream["channels"] for data in lane_metadata for stream in data["streams"] if stream.get("codec_type") == "audio"],
            [1, 1],
        )

    def test_reference_channel_lanes_do_not_split_duplicate_stereo_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            _write_duplicate_stereo_tone_wave(reference, duration_seconds=1.0, sample_rate=8000)

            topology = model_runtime.analyze_reference_channel_topology(reference)
            lanes = model_runtime.prepare_reference_channel_lanes(
                reference,
                work_dir=root / "work",
                source_separation="never",
            )

        self.assertFalse(topology.split_recommended)
        self.assertIn(topology.reason, {"same_content_stereo_or_harmony", "near_duplicate_stereo_channels"})
        self.assertEqual([lane.label for lane in lanes], ["mono"])
        self.assertFalse(lanes[0].split_from_stereo)

    def test_batch_split_reference_channels_requires_independent_channel_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_directory = root / "materials"
            material_directory.mkdir()
            settings = ProcessingSettings(
                material_directory=str(material_directory),
                split_reference_channels=True,
            )
            duplicate = root / "duplicate.wav"
            independent = root / "independent.wav"
            _write_duplicate_stereo_tone_wave(duplicate, duration_seconds=1.0, sample_rate=8000)
            _write_independent_stereo_wave(independent, duration_seconds=1.0, sample_rate=8000)

            duplicate_result = _should_split_reference_channels(settings, duplicate)
            independent_result = _should_split_reference_channels(settings, independent)

        self.assertFalse(duplicate_result[0])
        self.assertEqual(duplicate_result[1], 2)
        self.assertIsNotNone(duplicate_result[2])
        self.assertTrue(independent_result[0])
        self.assertEqual(independent_result[1], 2)
        self.assertEqual(independent_result[2].reason, "independent_channel_content")

    def test_split_reference_channel_item_forces_mono_lane_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            output = root / "out" / "render.wav"
            material_directory = root / "materials"
            material_directory.mkdir()
            diagnostics = DiagnosticLogger(root / "diagnostics.jsonl")
            settings = ProcessingSettings(
                material_directory=str(material_directory),
                split_reference_channels=True,
                channels=2,
                source_separation="auto",
                output_directory=str(output.parent),
            )
            item = QueueItem(reference, output)
            lanes = (
                model_runtime.ReferenceChannelLane(
                    index=0,
                    label="left",
                    reference_path=root / "left.wav",
                    source_path=reference,
                    split_from_stereo=True,
                ),
                model_runtime.ReferenceChannelLane(
                    index=1,
                    label="right",
                    reference_path=root / "right.wav",
                    source_path=reference,
                    split_from_stereo=True,
                ),
            )
            captured: list[tuple[Path, Path, ProcessingSettings]] = []

            def fake_run_batch(
                lane_items: list[object],
                lane_settings: ProcessingSettings,
                **kwargs: object,
            ) -> BatchSummary:
                del kwargs
                lane_item = lane_items[0]
                captured.append((lane_item.input_path, lane_item.output_path, lane_settings))
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            with patch("audio_processor.batch.prepare_reference_channel_lanes", return_value=lanes):
                with patch("audio_processor.batch.run_batch_queue", side_effect=fake_run_batch):
                    outputs = _run_split_reference_channel_item(
                        item,
                        settings,
                        diagnostics,
                        lambda _progress, _message: None,
                        None,
                    )

        self.assertEqual(outputs, [root / "out" / "render_left.wav", root / "out" / "render_right.wav"])
        self.assertEqual([entry[0] for entry in captured], [root / "left.wav", root / "right.wav"])
        self.assertEqual([entry[1] for entry in captured], outputs)
        self.assertTrue(all(entry[2].channels == 1 for entry in captured))
        self.assertTrue(all(entry[2].source_separation == "never" for entry in captured))
        self.assertTrue(all(not entry[2].split_reference_channels for entry in captured))

    def test_batch_split_reference_channels_skips_mono_reference_before_lane_preparation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material_dir = root / "materials"
            material_dir.mkdir()
            _write_test_wave(reference, channels=1)
            _write_test_wave(material_dir / "wo.wav")
            settings = ProcessingSettings(
                material_directory=str(material_dir),
                split_reference_channels=True,
                output_directory=str(root / "out"),
                overwrite=True,
            )
            item = create_queue([reference], settings)[0]
            captured: dict[str, object] = {}

            def fake_ordering(reference_path: Path, *args: object, **kwargs: object) -> object:
                del args, kwargs
                captured["reference_path"] = reference_path
                raise AudioProcessorError("stop after mono split decision")

            with patch("audio_processor.batch.prepare_reference_channel_lanes") as lane_mock:
                with patch("audio_processor.batch.speech_runtime_preflight_report", return_value={}):
                    with patch("audio_processor.batch.build_model_ordering", side_effect=fake_ordering):
                        summary = run_batch_queue([item], settings)

            records = [
                json.loads(line)
                for line in diagnostic_log_path(item.output_path).read_text(encoding="utf-8").splitlines()
            ]
            split_skipped = next(
                record for record in records if record["stage"] == "reference.channels.split_skipped"
            )

        lane_mock.assert_not_called()
        self.assertEqual(summary.failed, 1)
        self.assertEqual(captured["reference_path"], reference)
        self.assertEqual(split_skipped["fields"]["detected_channels"], 1)
        self.assertEqual(split_skipped["fields"]["reason"], "mono_reference")


class SettingsTests(unittest.TestCase):
    def test_default_output_extension_is_wav(self) -> None:
        self.assertEqual(ProcessingSettings().output_extension, ".wav")

    def test_settings_round_trip(self) -> None:
        settings = ProcessingSettings(
            language="en",
            material_directory="materials",
            manual_lyrics_enabled=True,
            lyrics_file="lyrics.txt",
            split_reference_channels=True,
            daw_timeline_export=True,
            source_separation="never",
            output_directory="out",
            output_extension="wav",
            overwrite=False,
            gain_db=-2.5,
            normalize=True,
            sample_rate=48000,
            channels=1,
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_settings(settings, path)
            loaded = load_settings(path)

        self.assertEqual(loaded.output_extension, ".wav")
        self.assertEqual(loaded.language, "en")
        self.assertEqual(loaded.material_directory, "materials")
        self.assertTrue(loaded.manual_lyrics_enabled)
        self.assertEqual(loaded.lyrics_file, "lyrics.txt")
        self.assertEqual(loaded.effective_lyrics_file(), "lyrics.txt")
        self.assertTrue(loaded.split_reference_channels)
        self.assertTrue(loaded.daw_timeline_export)
        self.assertEqual(loaded.source_separation, "never")
        self.assertEqual(loaded.output_directory, "out")
        self.assertFalse(loaded.overwrite)
        self.assertEqual(loaded.gain_db, -2.5)
        self.assertTrue(loaded.normalize)
        self.assertEqual(loaded.sample_rate, 48000)
        self.assertEqual(loaded.channels, 1)

    def test_legacy_settings_enable_manual_lyrics_when_path_exists(self) -> None:
        loaded = ProcessingSettings.from_dict({"lyrics_file": "lyrics.txt"})

        self.assertTrue(loaded.manual_lyrics_enabled)
        self.assertEqual(loaded.effective_lyrics_file(), "lyrics.txt")

    def test_disabled_manual_lyrics_keeps_path_but_ignores_it(self) -> None:
        settings = ProcessingSettings(
            manual_lyrics_enabled=False,
            lyrics_file="lyrics.txt",
        )

        self.assertEqual(settings.lyrics_file, "lyrics.txt")
        self.assertEqual(settings.effective_lyrics_file(), "")

    def test_create_queue_applies_output_settings(self) -> None:
        settings = ProcessingSettings(output_directory="out", output_extension="mp3")
        queue = create_queue([Path("song.wav")], settings)

        self.assertEqual(queue[0].input_path, Path("song.wav"))
        self.assertEqual(queue[0].output_path, Path("out") / "song.mp3")

    def test_output_path_avoids_overwriting_same_extension_source(self) -> None:
        settings = ProcessingSettings(output_extension=".mp3")

        self.assertEqual(settings.output_path_for(Path("song.mp3")), Path("song_processed.mp3"))

    def test_daw_timeline_output_path_is_project_file(self) -> None:
        settings = ProcessingSettings(
            material_directory="materials",
            daw_timeline_export=True,
            output_directory="out",
        )

        self.assertEqual(settings.output_path_for(Path("song.wav")), Path("out") / "song_daw" / "song.rpp")


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostic_logger_writes_jsonl_events(self) -> None:
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "run.diagnostics.jsonl"
            diagnostics = DiagnosticLogger(log_path)

            diagnostics.event("test.stage", "Collected metadata", input_path=Path("song.wav"))

            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["stage"], "test.stage")
        self.assertEqual(records[0]["fields"]["input_path"], "song.wav")

    def test_batch_failure_records_diagnostics_log(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "reference.wav"
            input_path.write_bytes(b"placeholder")
            item = create_queue([input_path], ProcessingSettings())[0]

            probe_data = {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                "format": {"duration": "1.0", "format_name": "wav"},
            }
            with patch("audio_processor.batch.probe_audio", return_value=probe_data):
                with patch(
                    "audio_processor.batch.process_audio_with_progress",
                    side_effect=AudioProcessorError("render failed"),
                ):
                    summary = run_batch_queue([item], ProcessingSettings())

            log_path = diagnostic_log_path(item.output_path)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(summary.failed, 1)
        self.assertEqual(item.status, "Failed")
        self.assertIn("Diagnostics:", item.message)
        self.assertIn("batch.item.started", [record["stage"] for record in records])
        self.assertIn("inputs.reference", [record["stage"] for record in records])
        self.assertIn("batch.item.failed", [record["stage"] for record in records])
        failure = next(record for record in records if record["stage"] == "batch.item.failed")
        self.assertIn("elapsed_seconds", failure["fields"])

    def test_batch_overwrite_removes_stale_output_before_rendering(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "reference.wav"
            input_path.write_bytes(b"placeholder")
            settings = ProcessingSettings(output_directory=str(root / "out"), overwrite=True)
            item = create_queue([input_path], settings)[0]
            item.output_path.parent.mkdir(parents=True)
            _write_test_wave(item.output_path, duration_seconds=0.25)

            probe_data = {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                "format": {"duration": "1.0", "format_name": "wav"},
            }
            with patch("audio_processor.batch.probe_audio", return_value=probe_data):
                with patch(
                    "audio_processor.batch.process_audio_with_progress",
                    side_effect=AudioProcessorError("render failed"),
                ):
                    summary = run_batch_queue([item], settings)

            log_path = diagnostic_log_path(item.output_path)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            output_exists = item.output_path.exists()

        self.assertEqual(summary.failed, 1)
        self.assertFalse(output_exists)
        self.assertIn("outputs.stale_removed", [record["stage"] for record in records])

    def test_input_diagnostics_do_not_abort_when_material_probe_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "reference.wav"
            material_dir = root / "materials"
            material_dir.mkdir()
            material_path = material_dir / "bad.wav"
            input_path.write_bytes(b"placeholder")
            material_path.write_bytes(b"placeholder")
            settings = ProcessingSettings(material_directory=str(material_dir), overwrite=True)
            item = create_queue([input_path], settings)[0]

            probe_data = {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                "format": {"duration": "1.0", "format_name": "wav"},
            }

            def fake_probe(path: Path) -> dict[str, object]:
                if Path(path) == material_path:
                    raise AudioProcessorError("metadata failed")
                return probe_data

            with patch("audio_processor.batch.probe_audio", side_effect=fake_probe):
                with patch(
                    "audio_processor.batch.build_model_ordering",
                    side_effect=AudioProcessorError("model failed"),
                ):
                    summary = run_batch_queue([item], settings)

            log_path = diagnostic_log_path(item.output_path)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(summary.failed, 1)
        materials_record = next(record for record in records if record["stage"] == "inputs.materials")
        self.assertEqual(materials_record["fields"]["metadata_failure_count"], 1)
        self.assertIn("batch.item.failed", [record["stage"] for record in records])

    def test_batch_ignores_stored_lyrics_file_when_manual_lyrics_disabled(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "reference.wav"
            material_dir = root / "materials"
            lyrics_file = root / "lyrics.txt"
            material_dir.mkdir()
            input_path.write_bytes(b"placeholder")
            _write_test_wave(material_dir / "wo.wav")
            lyrics_file.write_text("\u6211", encoding="utf-8")
            settings = ProcessingSettings(
                material_directory=str(material_dir),
                manual_lyrics_enabled=False,
                lyrics_file=str(lyrics_file),
                overwrite=True,
            )
            item = create_queue([input_path], settings)[0]
            captured: dict[str, object] = {}

            probe_data = {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                "format": {"duration": "1.0", "format_name": "wav"},
            }

            def fake_ordering(*args: object, **kwargs: object) -> object:
                del args
                captured["lyrics_file"] = kwargs.get("lyrics_file")
                raise AudioProcessorError("stop after capture")

            with patch("audio_processor.batch.probe_audio", return_value=probe_data):
                with patch("audio_processor.batch.build_model_ordering", side_effect=fake_ordering):
                    summary = run_batch_queue([item], settings)

        self.assertEqual(summary.failed, 1)
        self.assertIsNone(captured["lyrics_file"])

    def test_batch_passes_lyrics_file_when_manual_lyrics_enabled(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "reference.wav"
            material_dir = root / "materials"
            lyrics_file = root / "lyrics.txt"
            material_dir.mkdir()
            input_path.write_bytes(b"placeholder")
            _write_test_wave(material_dir / "wo.wav")
            lyrics_file.write_text("\u6211", encoding="utf-8")
            settings = ProcessingSettings(
                material_directory=str(material_dir),
                manual_lyrics_enabled=True,
                lyrics_file=str(lyrics_file),
                overwrite=True,
            )
            item = create_queue([input_path], settings)[0]
            captured: dict[str, object] = {}

            probe_data = {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                "format": {"duration": "1.0", "format_name": "wav"},
            }

            def fake_ordering(*args: object, **kwargs: object) -> object:
                del args
                captured["lyrics_file"] = kwargs.get("lyrics_file")
                raise AudioProcessorError("stop after capture")

            with patch("audio_processor.batch.probe_audio", return_value=probe_data):
                with patch("audio_processor.batch.build_model_ordering", side_effect=fake_ordering):
                    summary = run_batch_queue([item], settings)

        self.assertEqual(summary.failed, 1)
        self.assertEqual(captured["lyrics_file"], lyrics_file)


class RealEvalTests(unittest.TestCase):
    def test_discovers_language_compatible_real_cases_without_tracking_audio_assets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set"
            lyrics = root / "lyrics"
            origin.mkdir()
            lyrics.mkdir()
            (material_root / "vmzCN").mkdir(parents=True)
            (material_root / "vmzJP").mkdir(parents=True)
            _write_test_wave(origin / "song_CN.wav")
            _write_test_wave(material_root / "vmzCN" / "wo.wav")
            _write_test_wave(material_root / "vmzJP" / "a.wav")
            (lyrics / "song_CN.lrc").write_text("[00:00.00]\u6211\n", encoding="utf-8")

            cases, skipped_cases = real_eval.discover_real_cases_with_skips(root)
            manifest_path = real_eval.write_manifest_template(root, root / "cases.generated.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual([case.name for case in cases], ["song_CN__vmzCN"])
        self.assertEqual([case.name for case in skipped_cases], ["song_CN__vmzJP"])
        self.assertEqual(skipped_cases[0].reason, "language_mismatch")
        self.assertEqual(skipped_cases[0].language_compatibility["reference_language"], "CN")
        self.assertEqual(skipped_cases[0].language_compatibility["material_set_language"], "JP")
        self.assertTrue(all(case.language == "CN" for case in cases))
        self.assertEqual(len(manifest["cases"]), 1)
        self.assertEqual(len(manifest["skipped_cases"]), 1)
        self.assertEqual({entry["language"] for entry in manifest["cases"]}, {"CN"})

    def test_real_eval_reports_skipped_language_mismatches_without_running_preflight(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report") as preflight_mock:
                    result = real_eval.run_real_suite(root, render=False, output_root=root / "reports")

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))
            markdown = result.markdown_path.read_text(encoding="utf-8")

        preflight_mock.assert_not_called()
        self.assertEqual(result.cases, ())
        self.assertEqual(len(result.skipped_cases), 1)
        self.assertEqual(summary_payload["suite"]["planned_case_count"], 0)
        self.assertEqual(summary_payload["suite"]["discovered_case_count"], 1)
        self.assertEqual(summary_payload["suite"]["skipped_case_counts"], {"language_mismatch": 1})
        self.assertEqual(summary_payload["skipped_cases"][0]["language_compatibility"]["reference_language"], "JP")
        self.assertEqual(summary_payload["skipped_cases"][0]["language_compatibility"]["material_set_language"], "CN")
        self.assertIn("Skipped Cases", markdown)
        self.assertIn("language_mismatch", markdown)

    def test_rendered_real_eval_scores_output_duration_and_matching(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            reference_path = origin / "song_CN.wav"
            _write_test_wave(reference_path, duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)
            _write_test_wave(material_root / "ai.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "ok",
                "summary": {
                    "material_count": 2,
                    "warning_count": 0,
                    "error_warning_count": 0,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.82,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [],
                "ordering": {
                    "ordering": [
                        {
                            "rank": 1,
                            "score": 0.9,
                            "confidence_label": "strong",
                            "target_duration_seconds": 1.5,
                        },
                        {
                            "rank": 2,
                            "score": 0.82,
                            "confidence_label": "strong",
                            "target_duration_seconds": 2.5,
                        },
                    ],
                    "timeline_alignment": {
                        "decision_count": 2,
                        "positioned_decision_count": 2,
                        "resolved_target_duration_count": 2,
                        "timed_target_duration_count": 2,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 1.5,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.96,
                        "continuity_warning": "",
                        "fade_seconds": 0.01,
                    },
                    {
                        "target_duration_seconds": 2.5,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.94,
                        "continuity_warning": "",
                        "fade_seconds": 0.01,
                    },
                ],
            }

            def fake_run_batch_queue(
                items: list[QueueItem],
                settings: ProcessingSettings,
                **kwargs: object,
            ) -> BatchSummary:
                self.assertIn("should_cancel", kwargs)
                self.assertEqual(len(items), 1)
                _write_test_wave(items[0].output_path, duration_seconds=4.02)
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            def fake_probe(path: Path) -> dict[str, object]:
                path = Path(path)
                duration = 4.02 if path.parent.name == "vmzCN" else 4.0
                return {"streams": [{"codec_type": "audio", "duration": str(duration)}], "format": {}}

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue", side_effect=fake_run_batch_queue):
                    with patch("audio_processor.real_eval.probe_audio", side_effect=fake_probe):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            allow_unverified_reference_render=True,
                            source_separation="never",
                            output_root=root / "reports",
                        )

            case_summary = result.cases[0].summary
            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))
            markdown = result.markdown_path.read_text(encoding="utf-8")

        self.assertEqual(result.cases[0].status, "rendered")
        self.assertEqual(case_summary["render_validation"]["status"], "ok")
        self.assertTrue(case_summary["strict_render_pass"])
        self.assertAlmostEqual(case_summary["match_score_mean"], 0.86)
        self.assertGreater(case_summary["planning_alignment_score"], 0.9)
        self.assertGreater(case_summary["rendered_audio_alignment_score"], 0.9)
        self.assertAlmostEqual(case_summary["stretch_naturalness_score"], 0.95)
        self.assertEqual(case_summary["continuity_warning_count"], 0)
        self.assertIn("stretch_quality_score", summary_payload["suite"]["score_summary"])
        self.assertIn("stretch_naturalness_score", summary_payload["suite"]["score_summary"])
        self.assertIn("continuity_warning_ratio", summary_payload["suite"]["score_summary"])
        self.assertIn("score_summary", summary_payload["suite"])
        self.assertIn("group_score_summary", summary_payload["suite"])
        self.assertIn("song_CN", summary_payload["suite"]["group_score_summary"]["by_reference"])
        self.assertIn("vmzCN", summary_payload["suite"]["group_score_summary"]["by_material_set"])
        self.assertIn("Group Scores", markdown)
        self.assertIn("Render Score", markdown)
        self.assertIn("Stretch Quality", markdown)
        self.assertIn("Boundary Risks", markdown)
        self.assertIn("Timed", markdown)
        self.assertIn("Strict Pass", markdown)

    def test_rendered_real_eval_splits_stereo_reference_and_enables_real_lyrics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            lyrics_root = root / "lyrics"
            origin.mkdir()
            material_root.mkdir(parents=True)
            lyrics_root.mkdir()
            reference_path = origin / "song_CN.wav"
            _write_independent_stereo_wave(reference_path, duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)
            lyrics_path = lyrics_root / "song_CN.txt"
            lyrics_path.write_text("\u6211\n", encoding="utf-8")

            preflight_report = {
                "status": "ok",
                "summary": {
                    "material_count": 1,
                    "warning_count": 0,
                    "error_warning_count": 0,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 1,
                        "exact_timed_target_duration_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }
            captured: dict[str, object] = {}

            def fake_run_batch_queue(
                items: list[QueueItem],
                settings: ProcessingSettings,
                **kwargs: object,
            ) -> BatchSummary:
                del kwargs
                captured["settings"] = settings
                base = items[0].output_path
                _write_test_wave(base.with_name(f"{base.stem}_left{base.suffix}"), duration_seconds=4.0)
                _write_test_wave(base.with_name(f"{base.stem}_right{base.suffix}"), duration_seconds=4.0)
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            def fake_probe(path: Path) -> dict[str, object]:
                path = Path(path)
                return {
                    "streams": [
                        {
                            "codec_type": "audio",
                            "duration": "4.0",
                            "channels": 2 if path == reference_path else 1,
                        }
                    ],
                    "format": {},
                }

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue", side_effect=fake_run_batch_queue):
                    with patch("audio_processor.real_eval.probe_audio", side_effect=fake_probe):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            allow_unverified_reference_render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

        settings = captured["settings"]
        self.assertEqual(result.cases[0].status, "rendered")
        self.assertTrue(settings.manual_lyrics_enabled)
        self.assertTrue(settings.split_reference_channels)
        self.assertEqual(
            result.cases[0].summary["render_validation"]["status"],
            "ok",
        )
        self.assertEqual(
            len(result.cases[0].summary["render_validation"]["channel_outputs"]),
            2,
        )

    def test_real_eval_output_root_separates_audio_reports_and_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            reference_path = origin / "song_CN.wav"
            _write_test_wave(reference_path, duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "ok",
                "summary": {
                    "material_count": 1,
                    "warning_count": 0,
                    "error_warning_count": 0,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }
            captured = {}

            def fake_run_batch_queue(
                items: list[QueueItem],
                settings: ProcessingSettings,
                **kwargs: object,
            ) -> BatchSummary:
                del kwargs
                captured["settings"] = settings
                captured["output_path"] = items[0].output_path
                _write_test_wave(items[0].output_path, duration_seconds=4.0)
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue", side_effect=fake_run_batch_queue):
                    with patch(
                        "audio_processor.real_eval.probe_audio",
                        return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                    ):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            allow_unverified_reference_render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

        settings = captured["settings"]
        output_path = captured["output_path"]
        self.assertEqual(result.report_directory.parent, root / "output" / "reports")
        self.assertEqual(output_path, root / "output" / "audio" / "song_CN" / "vmzCN" / "song_CN.wav")
        self.assertEqual(
            Path(settings.render_cache_directory),
            root / "output" / "cache" / "song_CN" / "vmzCN",
        )
        self.assertEqual(
            Path(settings.diagnostics_directory),
            result.report_directory / "cases" / "song_CN__vmzCN" / "render",
        )
        self.assertEqual(
            result.cases[0].analysis_report_path,
            result.report_directory / "cases" / "song_CN__vmzCN" / "analysis.json",
        )
        self.assertEqual(
            result.cases[0].render_report_path,
            result.report_directory / "cases" / "song_CN__vmzCN" / "render" / "song_CN.diagnostics.jsonl",
        )

    def test_rendered_real_eval_blocks_output_without_aligned_unit_timing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "ha.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "review_required",
                "summary": {
                    "material_count": 1,
                    "warning_count": 1,
                    "error_warning_count": 1,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [
                    {
                        "severity": "error",
                        "kind": "missing_aligned_unit_timing",
                        "positioned_decision_count": 1,
                        "timed_target_duration_count": 0,
                    }
                ],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 0,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue") as run_batch_mock:
                    with patch(
                        "audio_processor.real_eval.probe_audio",
                        return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                    ):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            allow_unverified_reference_render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        run_batch_mock.assert_not_called()
        self.assertEqual(result.cases[0].status, "render_blocked")
        self.assertIsNone(result.cases[0].output_path)
        self.assertEqual(result.cases[0].summary["render_validation"]["status"], "render_blocked")
        self.assertTrue(
            any(warning["kind"] == "render_blocked_missing_aligned_unit_timing" for warning in result.cases[0].warnings)
        )
        self.assertEqual(summary_payload["suite"]["status_counts"], {"render_blocked": 1})
        self.assertEqual(summary_payload["suite"]["recommended_exit_code"], 1)

    def test_rendered_real_eval_blocks_resampled_unit_timing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            lyrics_root = root / "lyrics"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            lyrics_root.mkdir(parents=True)
            _write_test_wave(origin / "song_CN.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)
            (lyrics_root / "song_CN.txt").write_text("\u6211", encoding="utf-8")

            preflight_report = {
                "status": "review_required",
                "summary": {
                    "material_count": 1,
                    "warning_count": 1,
                    "error_warning_count": 1,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [
                    {
                        "severity": "error",
                        "kind": "resampled_aligned_unit_timing",
                        "resampled_timing_lattice_count": 1,
                        "exact_timed_target_duration_count": 0,
                        "timed_target_duration_count": 1,
                    }
                ],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 1,
                        "exact_timed_target_duration_count": 0,
                        "resampled_timing_lattice_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue") as run_batch_mock:
                    with patch(
                        "audio_processor.real_eval.probe_audio",
                        return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                    ):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        run_batch_mock.assert_not_called()
        self.assertEqual(result.cases[0].status, "render_blocked")
        self.assertIsNone(result.cases[0].output_path)
        self.assertTrue(
            any(
                warning["kind"] == "render_blocked_resampled_aligned_unit_timing"
                for warning in result.cases[0].warnings
            )
        )
        self.assertEqual(result.cases[0].summary["aligned_timing_score"], 0.0)
        self.assertEqual(result.cases[0].summary["render_validation"]["blocker_kind"], "render_blocked_resampled_aligned_unit_timing")
        self.assertEqual(summary_payload["suite"]["status_counts"], {"render_blocked": 1})

    def test_rendered_real_eval_blocks_unverified_reference_asr_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "ha.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "review_required",
                "summary": {
                    "material_count": 1,
                    "warning_count": 1,
                    "error_warning_count": 1,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [
                    {
                        "severity": "error",
                        "kind": "reference_asr_unverified",
                        "reference_path": str(origin / "song_JP.wav"),
                        "backend": "whisperx",
                    }
                ],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue") as run_batch_mock:
                    with patch(
                        "audio_processor.real_eval.probe_audio",
                        return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                    ):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        run_batch_mock.assert_not_called()
        self.assertEqual(result.cases[0].status, "render_blocked")
        self.assertIsNone(result.cases[0].output_path)
        self.assertTrue(
            any(warning["kind"] == "render_blocked_unverified_reference_text" for warning in result.cases[0].warnings)
        )
        self.assertEqual(result.cases[0].summary["render_validation"]["blocker_kind"], "render_blocked_unverified_reference_text")
        self.assertEqual(summary_payload["suite"]["status_counts"], {"render_blocked": 1})

    def test_rendered_real_eval_allows_unverified_reference_asr_when_explicit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "ha.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "review_required",
                "summary": {
                    "material_count": 1,
                    "warning_count": 1,
                    "error_warning_count": 1,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [{"severity": "error", "kind": "reference_asr_unverified"}],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "timed_target_duration_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [
                    {
                        "target_duration_seconds": 4.0,
                        "quality_warning": "",
                        "stretch_naturalness_score": 0.95,
                        "continuity_warning": "",
                    }
                ],
            }

            def fake_run_batch_queue(
                items: list[QueueItem],
                settings: ProcessingSettings,
                **kwargs: object,
            ) -> BatchSummary:
                del settings, kwargs
                _write_test_wave(items[0].output_path, duration_seconds=4.0)
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            with patch("audio_processor.real_eval.build_preflight_report", return_value=preflight_report):
                with patch("audio_processor.real_eval.run_batch_queue", side_effect=fake_run_batch_queue) as run_batch_mock:
                    with patch(
                        "audio_processor.real_eval.probe_audio",
                        return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                    ):
                        result = real_eval.run_real_suite(
                            root,
                            render=True,
                            allow_unverified_reference_render=True,
                            source_separation="never",
                            output_root=root / "output",
                        )

        run_batch_mock.assert_called_once()
        self.assertEqual(result.cases[0].status, "rendered")
        self.assertIsNotNone(result.cases[0].output_path)

    def test_real_eval_render_defaults_auto_asr_backend_to_whisperx(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            state = real_eval._apply_rendered_eval_asr_backend(render=True, requested_backend=None)
            try:
                self.assertEqual(os.environ["VOCAL_PROCESS_ASR_BACKEND"], "whisperx")
            finally:
                real_eval._restore_asr_backend(state)

            self.assertNotIn("VOCAL_PROCESS_ASR_BACKEND", os.environ)

    def test_real_eval_render_keeps_explicit_asr_backend(self) -> None:
        with patch.dict(os.environ, {"VOCAL_PROCESS_ASR_BACKEND": "funasr"}, clear=True):
            state = real_eval._apply_rendered_eval_asr_backend(render=True, requested_backend=None)
            try:
                self.assertIsNone(state)
                self.assertEqual(os.environ["VOCAL_PROCESS_ASR_BACKEND"], "funasr")
            finally:
                real_eval._restore_asr_backend(state)

    def test_real_eval_render_failed_validation_ignores_stale_output_duration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.wav"
            _write_test_wave(output, duration_seconds=1.0)

            validation = real_eval._render_validation(
                {"batch_summary": {"total": 1, "completed": 0, "failed": 1, "cancelled": 0}},
                4.0,
                output,
            )

        self.assertEqual(validation["status"], "render_failed")
        self.assertTrue(validation["output_exists"])
        self.assertTrue(validation["render_failed"])
        self.assertIsNone(validation["output_duration_seconds"])
        self.assertIsNone(validation["duration_delta_ratio"])

    def test_real_eval_flushes_partial_summary_after_case_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "a_CN.wav", duration_seconds=4.0)
            _write_test_wave(origin / "b_CN.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)

            preflight_report = {
                "status": "ok",
                "summary": {
                    "material_count": 1,
                    "warning_count": 0,
                    "error_warning_count": 0,
                    "review_required_match_count": 0,
                    "minimum_match_score": 0.9,
                    "extreme_stretch_count": 0,
                    "moderate_stretch_count": 0,
                },
                "warnings": [],
                "ordering": {
                    "ordering": [{"rank": 1, "score": 0.9, "confidence_label": "strong"}],
                    "timeline_alignment": {
                        "decision_count": 1,
                        "positioned_decision_count": 1,
                        "resolved_target_duration_count": 1,
                        "target_duration_total_seconds": 4.0,
                    },
                },
                "stretch_plan": [{"target_duration_seconds": 4.0, "quality_warning": ""}],
            }
            calls = {"count": 0}

            def fake_preflight(reference_path: Path, material_directory: Path, **kwargs: object) -> dict[str, object]:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RuntimeError("analysis boom")
                summaries = list((root / "reports").glob("real-eval-*/summary.json"))
                self.assertEqual(len(summaries), 1)
                partial_payload = json.loads(summaries[0].read_text(encoding="utf-8"))
                self.assertEqual([case["status"] for case in partial_payload["cases"]], ["analysis_failed"])
                return preflight_report

            with patch("audio_processor.real_eval.build_preflight_report", side_effect=fake_preflight):
                with patch(
                    "audio_processor.real_eval.probe_audio",
                    return_value={"streams": [{"codec_type": "audio", "duration": "4.0"}], "format": {}},
                ):
                    result = real_eval.run_real_suite(root, render=False, output_root=root / "reports")

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        self.assertEqual([case.status for case in result.cases], ["analysis_failed", "ok"])
        self.assertEqual(summary_payload["suite"]["status_counts"], {"analysis_failed": 1, "ok": 1})
        self.assertEqual(summary_payload["suite"]["completed_case_count"], 2)
        self.assertEqual(summary_payload["suite"]["planned_case_count"], 2)

    def test_real_eval_classifies_model_download_failure_as_infrastructure_blocker(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "a.wav", duration_seconds=1.0)
            error_message = (
                "WhisperX transcription failed for song_JP.wav: "
                "MaxRetryError(\"HTTPSConnectionPool(host='huggingface.co', port=443): "
                "SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED\")"
            )
            runtime_preflight = {
                "preferred_backend": "whisperx",
                "allow_model_download": True,
                "available": True,
                "issue": "",
            }

            with patch("audio_processor.real_eval.speech_runtime_preflight_report", return_value=runtime_preflight):
                with patch("audio_processor.real_eval.build_preflight_report", side_effect=RuntimeError(error_message)):
                    result = real_eval.run_real_suite(
                        root,
                        render=True,
                        allow_unverified_reference_render=True,
                        source_separation="never",
                        output_root=root / "reports",
                    )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))
            markdown = result.markdown_path.read_text(encoding="utf-8")

        warning = summary_payload["cases"][0]["warnings"][0]
        blocker = summary_payload["suite"]["infrastructure_blocker"]
        self.assertEqual(warning["kind"], "asr_model_download_failed")
        self.assertTrue(warning["infrastructure_blocker"])
        self.assertTrue(blocker["blocked"])
        self.assertEqual(blocker["case_failure_kinds"], {"asr_model_download_failed": 1})
        self.assertEqual(summary_payload["suite"]["recommended_exit_code"], 2)
        self.assertIn("Infrastructure blocker", markdown)

    def test_real_eval_classifies_hub_local_entry_missing_as_download_blocker(self) -> None:
        message = (
            "WhisperX transcription failed for song_JP.wav: LocalEntryNotFoundError: "
            "An error happened while trying to locate the file on the Hub and we cannot find "
            "the requested files in the local cache."
        )

        self.assertEqual(real_eval._analysis_failure_kind(message), "asr_model_download_failed")

    def test_real_eval_stops_after_shared_infrastructure_blocker(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "first_JP.wav", duration_seconds=4.0)
            _write_test_wave(origin / "second_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "a.wav", duration_seconds=1.0)
            error_message = (
                "WhisperX transcription failed for first_JP.wav: "
                "HTTPSConnectionPool(host='huggingface.co'): CERTIFICATE_VERIFY_FAILED"
            )

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report", side_effect=RuntimeError(error_message)) as preflight_mock:
                    result = real_eval.run_real_suite(
                        root,
                        render=True,
                        allow_unverified_reference_render=True,
                        source_separation="never",
                        output_root=root / "reports",
                    )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        self.assertEqual(preflight_mock.call_count, 1)
        self.assertEqual([case.status for case in result.cases], ["analysis_failed", "analysis_blocked"])
        self.assertEqual(
            summary_payload["suite"]["status_counts"],
            {"analysis_failed": 1, "analysis_blocked": 1},
        )
        self.assertTrue(summary_payload["suite"]["infrastructure_blocker"]["blocked"])
        self.assertEqual(summary_payload["suite"]["infrastructure_blocker"]["analysis_blocked_case_count"], 1)
        self.assertEqual(len(summary_payload["suite"]["infrastructure_blocker"]["case_failure_messages"]), 1)

    def test_real_eval_main_returns_nonzero_for_infrastructure_blocker(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "a.wav", duration_seconds=1.0)
            error_message = (
                "WhisperX transcription failed for song_JP.wav: "
                "HTTPSConnectionPool(host='huggingface.co'): CERTIFICATE_VERIFY_FAILED"
            )

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report", side_effect=RuntimeError(error_message)):
                    with patch("builtins.print"):
                        exit_code = real_eval.main(
                            [
                                "--root",
                                str(root),
                                "--render",
                                "--allow-unverified-reference-render",
                                "--source-separation",
                                "never",
                                "--output-root",
                                str(root / "reports"),
                            ]
                        )

        self.assertEqual(exit_code, 2)

    def test_real_eval_main_returns_nonzero_for_analysis_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzJP"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "song_JP.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "a.wav", duration_seconds=1.0)

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report", side_effect=RuntimeError("analysis boom")):
                    with patch("builtins.print"):
                        exit_code = real_eval.main(
                            [
                                "--root",
                                str(root),
                                "--source-separation",
                                "never",
                                "--output-root",
                                str(root / "reports"),
                            ]
                        )

            summary_path = sorted((root / "reports").glob("real-eval-*/summary.json"))[-1]
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary_payload["suite"]["recommended_exit_code"], 1)

    def test_real_eval_stop_file_cancels_before_starting_preflight(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "first_CN.wav", duration_seconds=4.0)
            _write_test_wave(origin / "second_CN.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)
            stop_file = root / "stop"
            stop_file.write_text("stop", encoding="utf-8")

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report") as preflight_mock:
                    result = real_eval.run_real_suite(
                        root,
                        render=True,
                        source_separation="never",
                        output_root=root / "reports",
                        stop_file=stop_file,
                    )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        preflight_mock.assert_not_called()
        self.assertEqual([case.status for case in result.cases], ["cancelled", "cancelled"])
        self.assertEqual(summary_payload["suite"]["status_counts"], {"cancelled": 2})
        self.assertEqual(summary_payload["suite"]["recommended_exit_code"], real_eval.CANCELLED_EXIT_CODE)
        self.assertEqual(summary_payload["cases"][0]["warnings"][0]["kind"], "cancelled")

    def test_real_eval_cancellation_during_preflight_marks_remaining_cases_cancelled(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            origin = root / "origin_vocal"
            material_root = root / "material_set" / "vmzCN"
            origin.mkdir(parents=True)
            material_root.mkdir(parents=True)
            _write_test_wave(origin / "first_CN.wav", duration_seconds=4.0)
            _write_test_wave(origin / "second_CN.wav", duration_seconds=4.0)
            _write_test_wave(material_root / "wo.wav", duration_seconds=1.0)
            cancel_state = {"cancelled": False}

            def fake_preflight(*args: object, **kwargs: object) -> dict[str, object]:
                self.assertTrue(callable(kwargs.get("should_cancel")))
                cancel_state["cancelled"] = True
                raise AudioProcessorError("Processing cancelled")

            with patch(
                "audio_processor.real_eval.speech_runtime_preflight_report",
                return_value={"preferred_backend": "whisperx", "available": True, "issue": ""},
            ):
                with patch("audio_processor.real_eval.build_preflight_report", side_effect=fake_preflight) as preflight_mock:
                    result = real_eval.run_real_suite(
                        root,
                        render=False,
                        source_separation="never",
                        output_root=root / "reports",
                        should_cancel=lambda: cancel_state["cancelled"],
                    )

            summary_payload = json.loads(result.summary_path.read_text(encoding="utf-8"))

        self.assertEqual(preflight_mock.call_count, 1)
        self.assertEqual([case.status for case in result.cases], ["cancelled", "cancelled"])
        self.assertEqual(summary_payload["suite"]["recommended_exit_code"], real_eval.CANCELLED_EXIT_CODE)

    def test_real_eval_infrastructure_signature_ignores_request_ids_and_reference_paths(self) -> None:
        first = (
            "WhisperX transcription failed for tests_real\\origin_vocal\\first_CN.wav: "
            "HTTPSConnectionPool(host='huggingface.co'): CERTIFICATE_VERIFY_FAILED "
            "(Request ID: 11111111-1111-1111-1111-111111111111)"
        )
        second = (
            "WhisperX transcription failed for tests_real\\origin_vocal\\second_JP.wav: "
            "HTTPSConnectionPool(host='huggingface.co'): CERTIFICATE_VERIFY_FAILED "
            "(Request ID: 22222222-2222-2222-2222-222222222222)"
        )

        self.assertEqual(real_eval._failure_message_signature(first), real_eval._failure_message_signature(second))


class MaintenanceRunnerTests(unittest.TestCase):
    def test_loads_maintenance_plan_template(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_path = root / "maintenance.plan.json"
            plan_path.write_text(
                json.dumps(maintenance.maintenance_plan_template(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            plan = maintenance.load_maintenance_plan(plan_path)

        self.assertEqual(plan.name, "development-maintenance")
        self.assertTrue(plan.repeat)
        self.assertGreaterEqual(len(plan.tasks), 3)
        self.assertEqual(plan.tasks[0].name, "compileall")

    def test_runs_one_maintenance_cycle_and_records_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_path = root / "maintenance.plan.json"
            session_dir = root / "session"
            plan_path.write_text(
                json.dumps(
                    {
                        "format": maintenance.MAINTENANCE_PLAN_FORMAT,
                        "name": "smoke",
                        "repeat": True,
                        "cycle_pause_seconds": 0,
                        "tasks": [
                            {
                                "name": "echo",
                                "command": sys.executable,
                                "args": ["-c", "print('hello from maintenance')"],
                                "cwd": ".",
                                "timeout_seconds": 10,
                                "continue_on_failure": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = maintenance.run_maintenance_session(
                plan_path,
                workspace_root=root,
                session_dir=session_dir,
                duration_hours=0.001,
                poll_interval_seconds=0.01,
                once=True,
            )
            state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
            heartbeat = json.loads((session_dir / "heartbeat.json").read_text(encoding="utf-8"))
            events = (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            stdout_texts = [
                path.read_text(encoding="utf-8")
                for path in (session_dir / "task-logs").glob("*.stdout.txt")
            ]

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.cycles_completed, 1)
            self.assertEqual(result.task_runs, 1)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(heartbeat["status"], "completed")
            self.assertTrue(any("\"task.completed\"" in line for line in events))
            self.assertTrue(any("hello from maintenance" in text for text in stdout_texts))

    def test_maintenance_stop_file_stops_running_child_process(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_path = root / "maintenance.plan.json"
            session_dir = root / "session"
            stop_file = session_dir / "stop"
            plan_path.write_text(
                json.dumps(
                    {
                        "format": maintenance.MAINTENANCE_PLAN_FORMAT,
                        "name": "stop-smoke",
                        "repeat": True,
                        "cycle_pause_seconds": 0,
                        "tasks": [
                            {
                                "name": "self-stop",
                                "command": sys.executable,
                                "args": [
                                    "-c",
                                    (
                                        "import os, time; "
                                        "from pathlib import Path; "
                                        "Path(os.environ['VOCAL_PROCESS_STOP_FILE']).write_text('stop', encoding='utf-8'); "
                                        "time.sleep(30)"
                                    ),
                                ],
                                "cwd": ".",
                                "timeout_seconds": 30,
                                "continue_on_failure": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            started_at = time.monotonic()
            result = maintenance.run_maintenance_session(
                plan_path,
                workspace_root=root,
                session_dir=session_dir,
                duration_hours=0.01,
                poll_interval_seconds=0.01,
                once=True,
                stop_file=stop_file,
            )
            elapsed = time.monotonic() - started_at
            events = [
                json.loads(line)
                for line in (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            task_completed = next(event for event in events if event["kind"] == "task.completed")

        self.assertLess(elapsed, 10)
        self.assertEqual(result.status, "stopped")
        self.assertEqual(task_completed["result"]["status"], "stopped")
        self.assertEqual(task_completed["result"]["termination"], "stop_file")


class CliTests(unittest.TestCase):
    def test_gui_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["gui"])

        self.assertEqual(args.command, "gui")

    def test_maintenance_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["maintenance", "--template"])

        self.assertEqual(args.command, "maintenance")
        self.assertTrue(args.template)

    def test_export_daw_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["export-daw", "reference.wav", "materials", "song.rpp"])

        self.assertEqual(args.command, "export-daw")
        self.assertEqual(args.reference, Path("reference.wav"))

    def test_export_melodyne_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["export-melodyne", "reference.wav", "materials", "handoff"])

        self.assertEqual(args.command, "export-melodyne")
        self.assertEqual(args.output_directory, Path("handoff"))

    def test_export_vegas_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["export-vegas", "reference.wav", "materials", "handoff"])

        self.assertEqual(args.command, "export-vegas")
        self.assertEqual(args.output_directory, Path("handoff"))

    def test_models_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["models", "--json"])

        self.assertEqual(args.command, "models")
        self.assertTrue(args.json)

    def test_batch_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(
            [
                "batch",
                "reference.wav",
                "out.wav",
                "--material-directory",
                "materials",
                "--lyrics-file",
                "lyrics.txt",
                "--split-reference-channels",
                "--overwrite",
            ]
        )

        self.assertEqual(args.command, "batch")
        self.assertEqual(args.reference, Path("reference.wav"))
        self.assertEqual(args.output, Path("out.wav"))
        self.assertEqual(args.material_directory, Path("materials"))
        self.assertEqual(args.lyrics_file, Path("lyrics.txt"))
        self.assertTrue(args.split_reference_channels)
        self.assertTrue(args.overwrite)

    def test_batch_subcommand_accepts_compute_device(self) -> None:
        args = build_parser().parse_args(
            [
                "batch",
                "reference.wav",
                "out.wav",
                "--material-directory",
                "materials",
                "--compute-device",
                "cpu",
            ]
        )

        self.assertEqual(args.compute_device, "cpu")

    def test_batch_subcommand_accepts_source_separation(self) -> None:
        args = build_parser().parse_args(
            [
                "batch",
                "reference.wav",
                "out.wav",
                "--material-directory",
                "materials",
                "--source-separation",
                "never",
            ]
        )

        self.assertEqual(args.source_separation, "never")

    def test_batch_subcommand_enables_manual_lyrics_when_file_is_passed(self) -> None:
        captured: dict[str, ProcessingSettings] = {}

        def fake_run_batch(items: list[object], settings: ProcessingSettings, **kwargs: object) -> BatchSummary:
            del items, kwargs
            captured["settings"] = settings
            return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

        with patch("audio_processor.cli.run_batch_queue", side_effect=fake_run_batch):
            exit_code = cli_main(
                [
                    "batch",
                    "reference.wav",
                    "out.wav",
                    "--material-directory",
                    "materials",
                    "--lyrics-file",
                    "lyrics.txt",
                    "--overwrite",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(captured["settings"].manual_lyrics_enabled)
        self.assertEqual(captured["settings"].effective_lyrics_file(), "lyrics.txt")

    def test_vst3_bridge_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["vst3-bridge", "request.json", "--response", "response.json"])

        self.assertEqual(args.command, "vst3-bridge")
        self.assertEqual(args.request, Path("request.json"))
        self.assertEqual(args.response, Path("response.json"))

    def test_analyze_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["analyze", "reference.wav", "materials", "--output", "analysis.json"])

        self.assertEqual(args.command, "analyze")
        self.assertEqual(args.reference, Path("reference.wav"))
        self.assertEqual(args.material_directory, Path("materials"))
        self.assertEqual(args.output, Path("analysis.json"))

    def test_vst3_bridge_watch_arguments_are_registered(self) -> None:
        args = build_parser().parse_args(["vst3-bridge", "--watch", "requests", "--responses", "responses", "--once"])

        self.assertEqual(args.command, "vst3-bridge")
        self.assertEqual(args.watch, Path("requests"))
        self.assertEqual(args.responses, Path("responses"))
        self.assertTrue(args.once)


class I18nTests(unittest.TestCase):
    def test_translation_key_sets_match(self) -> None:
        self.assertEqual(set(TRANSLATIONS["zh"]), set(TRANSLATIONS["en"]))

    def test_normalize_language_defaults_to_chinese(self) -> None:
        self.assertEqual(normalize_language("en"), "en")
        self.assertEqual(normalize_language("zh"), "zh")
        self.assertEqual(normalize_language("missing"), "zh")
        self.assertEqual(normalize_language(None), "zh")

    def test_translates_gui_text_and_status(self) -> None:
        self.assertEqual(translate("zh", "add_files"), "添加文件")
        self.assertEqual(translate("en", "add_files"), "Add Files")
        self.assertEqual(translate_status("zh", "Queued"), "排队中")

    def test_supported_lyrics_extensions_are_documented(self) -> None:
        self.assertEqual(LYRICS_EXTENSIONS, {".txt", ".doc", ".docx", ".lrc", ".srt"})


class ModelAssistTests(unittest.TestCase):
    @staticmethod
    def _fake_janome_tokenizer() -> object:
        class FakeTokenizer:
            readings = {
                "愛して": "アイシテ",
                "愛してる": "アイシテル",
                "中": "チュウ",
            }

            def tokenize(self, text: str) -> list[object]:
                tokens: list[object] = []
                index = 0
                keys = sorted(self.readings, key=len, reverse=True)
                while index < len(text):
                    matched_key = next((key for key in keys if text.startswith(key, index)), "")
                    if matched_key:
                        tokens.append(SimpleNamespace(surface=matched_key, phonetic=self.readings[matched_key]))
                        index += len(matched_key)
                        continue
                    character = text[index]
                    if character.strip():
                        tokens.append(SimpleNamespace(surface=character, phonetic=character))
                    index += 1
                return tokens

        return FakeTokenizer()

    def test_model_candidates_cover_required_pipeline_stages(self) -> None:
        stages = {candidate["pipeline_stage"] for candidate in list_model_candidates()}

        self.assertIn("source_separation", stages)
        self.assertIn("voice_activity_detection", stages)
        self.assertIn("asr_alignment", stages)
        self.assertIn("speaker_similarity", stages)
        self.assertIn("pronunciation_normalization", stages)

    def test_pipeline_plan_is_serializable(self) -> None:
        plan = build_model_assisted_pipeline_plan()

        self.assertEqual(plan["format"], "vocal_process_model_pipeline_plan_v1")
        json.dumps(plan, ensure_ascii=False)

    def test_text_similarity_supports_chinese_without_spaces(self) -> None:
        self.assertGreater(text_similarity("我爱你", "我爱你"), text_similarity("我爱你", "天气很好"))

    def test_orders_materials_by_transcript_similarity(self) -> None:
        decisions = order_materials_for_reference(
            [
                VoiceSegment(0.0, 1.0, "hello world"),
                VoiceSegment(1.0, 2.0, "good night"),
            ],
            [
                MaterialAnalysis(Path("002.wav"), transcript="good night"),
                MaterialAnalysis(Path("001.wav"), transcript="hello world"),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["001.wav", "002.wav"])
        self.assertTrue(all(decision.reason == "transcript_similarity" for decision in decisions))

    def test_orders_materials_by_reference_text_position(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 4.0, "hello world this is a test for vocal process")],
            [
                MaterialAnalysis(Path("003.wav"), transcript="for vocal process"),
                MaterialAnalysis(Path("001.wav"), transcript="hello world"),
                MaterialAnalysis(Path("002.wav"), transcript="this is a test"),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["001.wav", "002.wav", "003.wav"])
        self.assertEqual([decision.reason for decision in decisions], ["reference_text_position"] * 3)

    def test_aggregate_reference_positions_are_localized_to_real_segments(self) -> None:
        reference_segments = [
            VoiceSegment(0.0, 1.0, "alpha"),
            VoiceSegment(1.0, 3.0, "beta gamma"),
        ]
        decisions = order_materials_for_reference(
            reference_segments,
            [
                MaterialAnalysis(Path("gamma.wav"), transcript="", filename_text="gamma", duration_seconds=1.0),
                MaterialAnalysis(Path("alpha.wav"), transcript="", filename_text="alpha", duration_seconds=1.0),
                MaterialAnalysis(Path("beta.wav"), transcript="", filename_text="beta", duration_seconds=1.0),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["alpha.wav", "beta.wav", "gamma.wav"])
        self.assertEqual([decision.reference_segment_index for decision in decisions], [0, 1, 1])
        self.assertEqual([decision.reference_text for decision in decisions], ["alpha", "beta gamma", "beta gamma"])
        self.assertEqual([decision.text_position for decision in decisions], [0, 0, 1])

    def test_orders_materials_by_filename_when_transcript_is_missing(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 4.0, "alpha beta gamma")],
            [
                MaterialAnalysis(Path("002_alpha.wav"), transcript="", filename_text="alpha"),
                MaterialAnalysis(Path("001_beta.wav"), transcript="", filename_text="beta"),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["002_alpha.wav", "001_beta.wav"])
        self.assertEqual([decision.material_text for decision in decisions], ["alpha", "beta"])

    def test_filename_label_authority_suppresses_short_material_asr_hallucination(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "newOTTO_CN"
            material_dir.mkdir()
            material_path = material_dir / "ha.wav"
            _write_test_wave(material_path, duration_seconds=0.5)

            with patch(
                "audio_processor.model_runtime._transcribe_audio",
                return_value={"backend": "whisperx", "text": "\u4ed6", "segments": [], "notes": []},
            ) as transcribe_mock:
                with patch("audio_processor.model_runtime._detect_vad_segments", return_value=()):
                    with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                        library = model_runtime.analyze_material_library(material_dir)

        material = library.materials[0]
        transcribe_mock.assert_not_called()
        self.assertEqual(material.transcript, "ha")
        self.assertEqual(material.segments[0].text, "ha")
        self.assertEqual(material.analysis_source, "filename_label_authority")
        self.assertEqual(material.material_text_source, "filename_label_authority")
        self.assertTrue(material.asr_skipped_for_filename_label)
        self.assertEqual(material.parsed_filename_units, ("ha",))
        self.assertEqual(material.parsed_filename_phonetic_units, ("ha",))
        self.assertTrue(any("material_filename_label_authority" in note for note in material.notes))

        rendered = model_runtime.render_material_analysis(material)
        self.assertEqual(rendered["material_text_source"], "filename_label_authority")
        self.assertTrue(rendered["asr_skipped_for_filename_label"])
        self.assertEqual(rendered["parsed_filename_phonetic_units"], ["ha"])

    def test_material_display_text_collapses_duplicate_filename_authority(self) -> None:
        material = MaterialAnalysis(
            Path("ha.wav"),
            transcript="ha",
            filename_text="ha",
            duration_seconds=0.4,
        )

        self.assertEqual(model_assist._material_display_text(material), "ha")

    def test_short_reference_scores_filename_and_duration_separately(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 0.5, "ha")],
            [
                MaterialAnalysis(Path("wrong_long.wav"), transcript="zz", filename_text="zz", duration_seconds=3.0),
                MaterialAnalysis(Path("right_short.wav"), transcript="", filename_text="ha", duration_seconds=0.45),
            ],
        )

        self.assertEqual(decisions[0].material_path.name, "right_short.wav")
        self.assertGreater(decisions[0].filename_score, 0)
        self.assertGreater(decisions[0].duration_score, 0.8)

    def test_global_assignment_avoids_greedy_first_match_trap(self) -> None:
        plan = plan_material_ordering(
            [
                VoiceSegment(0.0, 1.0, "a", speaker_id="s1"),
                VoiceSegment(1.0, 2.0, "b", speaker_id="s2"),
            ],
            [
                MaterialAnalysis(
                    Path("speaker_b.wav"),
                    transcript="a",
                    duration_seconds=1.0,
                    speaker_label="s2",
                    speaker_embedding=(1.0,),
                ),
                MaterialAnalysis(
                    Path("plain_a.wav"),
                    transcript="a",
                    duration_seconds=1.0,
                    speaker_embedding=(1.0,),
                ),
            ],
        )

        self.assertEqual(plan.strategy, "global_assignment")
        self.assertEqual([decision.material_path.name for decision in plan.decisions], ["plain_a.wav", "speaker_b.wav"])
        self.assertEqual([decision.reference_segment_index for decision in plan.decisions], [0, 1])
        self.assertEqual(len(plan.score_matrix), 2)
        self.assertEqual(len(plan.score_matrix[0]), 2)

    def test_phonetic_similarity_supports_chinese_short_materials(self) -> None:
        self.assertGreater(phonetic_similarity("是", "shi"), 0.8)

        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 0.5, "是")],
            [
                MaterialAnalysis(Path("wrong.wav"), transcript="", filename_text="ma", duration_seconds=2.0),
                MaterialAnalysis(Path("right.wav"), transcript="", filename_text="shi", duration_seconds=2.0),
            ],
        )

        self.assertEqual(decisions[0].material_path.name, "right.wav")
        self.assertGreater(decisions[0].phonetic_score, 0.8)
        self.assertEqual(decisions[0].reason, "reference_text_position")

    def test_orders_pinyin_filenames_by_chinese_reference_phonetic_position(self) -> None:
        reference_segments = [VoiceSegment(0.0, 3.0, "\u6211\u662f\u4f60")]
        materials = [
            MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=1.0),
            MaterialAnalysis(Path("wo.wav"), transcript="", filename_text="wo", duration_seconds=1.0),
            MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=1.0),
        ]
        decisions = order_materials_for_reference(reference_segments, materials)
        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual([decision.material_path.name for decision in decisions], ["wo.wav", "shi.wav", "ni.wav"])
        self.assertEqual([decision.text_position for decision in decisions], [None, None, None])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2])
        self.assertTrue(all(decision.reason == "reference_text_position" for decision in decisions))
        self.assertTrue(all(decision.phonetic_score > 0.8 for decision in decisions))
        self.assertEqual([score.phonetic_position for score in plan.score_matrix[0]], [2, 0, 1])
        self.assertEqual(plan.score_matrix[0][1].reference_phonetic_units, ("wo", "shi", "ni"))
        self.assertEqual(plan.score_matrix[0][1].material_phonetic_units, ("wo",))

    def test_reference_phonetic_unit_sequence_skips_unrelated_noise_and_reuses_duplicate_syllables(self) -> None:
        reference_segments = [VoiceSegment(0.0, 3.0, "\u4f60\u7231\u4f60")]
        materials = [
            MaterialAnalysis(Path("noise.wav"), transcript="thank you", filename_text="noise", duration_seconds=0.8),
            MaterialAnalysis(Path("ni1.wav"), transcript="", filename_text="ni", duration_seconds=1.0),
            MaterialAnalysis(Path("ai.wav"), transcript="", filename_text="ai", duration_seconds=1.0),
            MaterialAnalysis(Path("ni2.wav"), transcript="", filename_text="ni", duration_seconds=1.0),
        ]

        decisions = order_materials_for_reference(reference_segments, materials)
        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual([decision.material_path.name for decision in decisions], ["ni1.wav", "ai.wav", "ni2.wav"])
        self.assertEqual(len(decisions), 3)
        self.assertNotIn("noise.wav", [decision.material_path.name for decision in decisions])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2])
        self.assertEqual([decision.reference_segment_index for decision in decisions], [0, 0, 0])
        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertIsNone(plan.score_matrix[0][0].phonetic_position)
        self.assertEqual([score.phonetic_position for score in plan.score_matrix[0][1:]], [0, 1, 0])

    def test_reference_phonetic_unit_sequence_reuses_material_when_reference_has_more_units(self) -> None:
        reference_segments = [VoiceSegment(0.0, 4.0, "\u4f60\u7231\u4f60\u7231")]
        materials = [
            MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=0.5),
            MaterialAnalysis(Path("ai.wav"), transcript="", filename_text="ai", duration_seconds=0.5),
        ]

        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertEqual([decision.material_path.name for decision in plan.decisions], ["ni.wav", "ai.wav", "ni.wav", "ai.wav"])
        self.assertEqual([decision.phonetic_position for decision in plan.decisions], [0, 1, 2, 3])
        self.assertEqual(len(plan.decisions), 4)

    def test_reference_sequence_prefers_natural_stretch_for_same_pronunciation(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                0.5,
                "\u4f60",
                unit_timings=(VoiceUnitTiming(0, "ni", 0.0, 0.5, timing_source="aligned"),),
                language_hint="CN",
            )
        ]
        materials = [
            MaterialAnalysis(Path("ni_too_long.wav"), transcript="", filename_text="ni", duration_seconds=2.0, language_hint="CN"),
            MaterialAnalysis(Path("ni_close.wav"), transcript="", filename_text="ni", duration_seconds=0.5, language_hint="CN"),
        ]

        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertEqual(plan.decisions[0].material_path.name, "ni_close.wav")
        self.assertGreater(plan.decisions[0].duration_score, 0.9)
        self.assertLess(model_assist._stretch_naturalness_score_for_target(0.5, 2.0, "ni"), 0.2)

    def test_reference_sequence_scores_exact_unit_match_independent_of_tiny_target_duration(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                0.03,
                "shi",
                language_hint="JP",
                unit_timings=(VoiceUnitTiming(0, "shi", 0.0, 0.03, timing_source="aligned"),),
            )
        ]
        materials = [
            MaterialAnalysis(
                Path("shi.wav"),
                transcript="shi",
                filename_text="shi",
                duration_seconds=0.3,
                language_hint="JP",
            )
        ]

        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertEqual(plan.decisions[0].material_path.name, "shi.wav")
        self.assertEqual(plan.decisions[0].phonetic_score, 1.0)
        self.assertGreaterEqual(plan.decisions[0].score, 0.8)

    def test_orders_tone_number_pinyin_filenames_by_phonetic_position(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 3.0, "\u6211\u662f\u4f60")],
            [
                MaterialAnalysis(Path("ni3.wav"), transcript="", filename_text="ni3", duration_seconds=1.0),
                MaterialAnalysis(Path("shi4.wav"), transcript="", filename_text="shi4", duration_seconds=1.0),
                MaterialAnalysis(Path("wo3.wav"), transcript="", filename_text="wo3", duration_seconds=1.0),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["wo3.wav", "shi4.wav", "ni3.wav"])
        self.assertEqual([decision.text_position for decision in decisions], [None, None, None])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2])
        self.assertTrue(all(decision.phonetic_score > 0.8 for decision in decisions))

    def test_orders_number_prefixed_pinyin_filenames_by_phonetic_position(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 3.0, "\u6211\u662f\u4f60")],
            [
                MaterialAnalysis(Path("003_ni3.wav"), transcript="", filename_text="003 ni3", duration_seconds=1.0),
                MaterialAnalysis(Path("001_wo3.wav"), transcript="", filename_text="001 wo3", duration_seconds=1.0),
                MaterialAnalysis(Path("002_shi4.wav"), transcript="", filename_text="002 shi4", duration_seconds=1.0),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["001_wo3.wav", "002_shi4.wav", "003_ni3.wav"])
        self.assertEqual([decision.text_position for decision in decisions], [None, None, None])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2])
        self.assertTrue(all(decision.phonetic_score > 0.8 for decision in decisions))

    def test_tone_marks_disambiguate_homophone_positions(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 2.0, "\u8bd7\u662f")],
            [MaterialAnalysis(Path("shi4.wav"), transcript="", filename_text="shi4", duration_seconds=1.0)],
        )
        score = plan_material_ordering(
            [VoiceSegment(0.0, 2.0, "\u8bd7\u662f")],
            [MaterialAnalysis(Path("shi4.wav"), transcript="", filename_text="shi4", duration_seconds=1.0)],
        ).score_matrix[0][0]

        self.assertIsNone(decisions[0].text_position)
        self.assertEqual(decisions[0].phonetic_position, 1)
        self.assertEqual(decisions[0].phonetic_tone_position_count, 1)
        self.assertGreater(decisions[0].phonetic_tone_score, 0.9)
        self.assertEqual(score.phonetic_tone_position, 1)

    def test_filename_label_variant_suffix_does_not_tone_lock_chinese_material(self) -> None:
        reference_segments = [VoiceSegment(0.0, 4.0, "\u540c\u5fd7\u540c\u5fd7", language_hint="CN")]
        materials = [
            MaterialAnalysis(
                Path("zhi1.wav"),
                transcript="zhi1",
                filename_text="zhi1",
                duration_seconds=0.3,
                language_hint="CN",
                material_text_source="filename_label_authority",
                asr_skipped_for_filename_label=True,
                parsed_filename_units=("zhi1",),
                parsed_filename_phonetic_units=("zhi",),
            ),
            MaterialAnalysis(
                Path("tong.wav"),
                transcript="tong",
                filename_text="tong",
                duration_seconds=0.3,
                language_hint="CN",
                material_text_source="filename_label_authority",
                asr_skipped_for_filename_label=True,
                parsed_filename_units=("tong",),
                parsed_filename_phonetic_units=("tong",),
            ),
        ]

        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertEqual(
            [decision.material_path.name for decision in plan.decisions],
            ["tong.wav", "zhi1.wav", "tong.wav", "zhi1.wav"],
        )
        self.assertEqual([decision.phonetic_position for decision in plan.decisions], [0, 1, 2, 3])
        self.assertTrue(
            all(decision.phonetic_tone_score == 0 for decision in plan.decisions if decision.material_path.name == "zhi1.wav")
        )

    def test_orders_japanese_kana_and_romaji_filenames_by_phonetic_position(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 4.0, "\u3042\u3044\u3057\u3066")],
            [
                MaterialAnalysis(Path("te.wav"), transcript="", filename_text="te", duration_seconds=1.0),
                MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=1.0),
                MaterialAnalysis(Path("a.wav"), transcript="", filename_text="a", duration_seconds=1.0),
                MaterialAnalysis(Path("i.wav"), transcript="", filename_text="i", duration_seconds=1.0),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["a.wav", "i.wav", "shi.wav", "te.wav"])
        self.assertEqual([decision.text_position for decision in decisions], [None, None, None, None])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2, 3])
        self.assertTrue(all(decision.phonetic_score > 0.8 for decision in decisions))

    def test_japanese_long_vowels_and_gemination_collapse_to_mora_units(self) -> None:
        self.assertEqual(
            model_assist._phonetic_units("\u30b9\u30fc\u30d1\u30fc", language_hint="JP"),
            ["su", "pa"],
        )
        self.assertEqual(
            model_assist._phonetic_units("\u304c\u3063\u3053\u3046", language_hint="JP"),
            ["ga", "ko"],
        )
        self.assertEqual(model_assist._phonetic_units("\u304d\u3087\u3046", language_hint="JP"), ["kyo"])
        self.assertEqual(model_assist._phonetic_units("\u3044\u3063\u3057\u3087\u3046", language_hint="JP"), ["i", "sho"])
        self.assertEqual(
            model_assist._phonetic_units(
                "\u3044\u3063\u3057\u3087\u3046\u3053\u306e\u307e\u307e"
                "\u3057\u3063\u307d\u306e\u304b\u308f\u3044\u3061\u307e\u3044\u3067"
                "\u3064\u306a\u304c\u308c\u305f\u3069\u308c\u3044\u304b\uff1f",
                language_hint="JP",
            )[:2],
            ["i", "sho"],
        )
        self.assertEqual(model_assist._phonetic_units("suupaa", language_hint="JP"), ["su", "pa"])
        self.assertEqual(model_assist._phonetic_units("gakkou", language_hint="JP"), ["ga", "ko"])
        self.assertEqual(model_assist._phonetic_units("aishite", language_hint="JP"), ["a", "i", "shi", "te"])
        self.assertEqual(model_assist._phonetic_units("PlasticLove", language_hint="JP"), ["plasticlove"])
        self.assertEqual(
            model_assist._phonetic_units("1000\u30bb\u30f3\u30cd\u30f3", language_hint="JP"),
            ["se", "n", "ne", "n"],
        )
        self.assertEqual(
            model_assist._phonetic_units("\u304d\u3087\u3046\u3075\u3063\u3066 \u304b\u3093\u3058\u3087\u3046\u306e", language_hint="JP"),
            ["kyo", "fu", "te", "ka", "n", "jo", "no"],
        )
        self.assertEqual(
            model_assist._phonetic_units("\u3042\u308b\u304b\u3044\uff1f", language_hint="JP"),
            ["a", "ru", "ka", "i"],
        )

    def test_reference_alignment_text_drives_japanese_phonetic_ordering_units(self) -> None:
        decisions = order_materials_for_reference(
            [
                VoiceSegment(
                    0.0,
                    1.0,
                    "1000\u5e74",
                    language_hint="JP",
                    alignment_text="\u305b\u3093\u306d\u3093",
                )
            ],
            [
                MaterialAnalysis(Path("ne.wav"), transcript="", filename_text="ne", duration_seconds=1.0, language_hint="JP"),
                MaterialAnalysis(Path("se.wav"), transcript="", filename_text="se", duration_seconds=1.0, language_hint="JP"),
                MaterialAnalysis(Path("n.wav"), transcript="", filename_text="n", duration_seconds=1.0, language_hint="JP"),
            ],
        )

        self.assertEqual(
            [decision.material_path.name for decision in decisions],
            ["se.wav", "n.wav", "ne.wav", "n.wav"],
        )
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2, 3])

    def test_orders_japanese_long_vowel_romaji_without_extra_clip_slots(self) -> None:
        decisions = order_materials_for_reference(
            [VoiceSegment(0.0, 2.0, "suupaa", language_hint="JP")],
            [
                MaterialAnalysis(Path("pa.wav"), transcript="", filename_text="pa", duration_seconds=1.0, language_hint="JP"),
                MaterialAnalysis(Path("su.wav"), transcript="", filename_text="su", duration_seconds=1.0, language_hint="JP"),
                MaterialAnalysis(Path("u.wav"), transcript="", filename_text="u", duration_seconds=1.0, language_hint="JP"),
                MaterialAnalysis(Path("a.wav"), transcript="", filename_text="a", duration_seconds=1.0, language_hint="JP"),
            ],
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["su.wav", "pa.wav"])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1])

    def test_reference_sequence_uses_segment_lattice_instead_of_aggregate_romaji_expansion(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                1.0,
                "I know",
                unit_timings=(
                    VoiceUnitTiming(0, "i", 0.0, 0.4, timing_source="aligned"),
                    VoiceUnitTiming(1, "know", 0.4, 1.0, timing_source="aligned"),
                ),
                language_hint="JP",
            ),
            VoiceSegment(
                1.0,
                2.0,
                "there love",
                unit_timings=(
                    VoiceUnitTiming(0, "there", 1.0, 1.6, timing_source="aligned"),
                    VoiceUnitTiming(1, "love", 1.6, 2.0, timing_source="aligned"),
                ),
                language_hint="JP",
            ),
        ]
        materials = [
            MaterialAnalysis(Path("i.wav"), transcript="", filename_text="i", duration_seconds=0.2, language_hint="JP"),
            MaterialAnalysis(Path("know.wav"), transcript="", filename_text="know", duration_seconds=0.2, language_hint="JP"),
            MaterialAnalysis(Path("there.wav"), transcript="", filename_text="there", duration_seconds=0.2, language_hint="JP"),
            MaterialAnalysis(Path("love.wav"), transcript="", filename_text="love", duration_seconds=0.2, language_hint="JP"),
            MaterialAnalysis(Path("extra.wav"), transcript="", filename_text="extra", duration_seconds=0.2, language_hint="JP"),
        ]

        plan = plan_material_ordering(reference_segments, materials)

        self.assertEqual(plan.strategy, "reference_phonetic_unit_sequence")
        self.assertEqual([decision.material_path.name for decision in plan.decisions], ["i.wav", "know.wav", "there.wav", "love.wav"])
        self.assertEqual([decision.reference_segment_index for decision in plan.decisions], [0, 0, 1, 1])
        self.assertEqual([decision.phonetic_position for decision in plan.decisions], [0, 1, 0, 1])
        self.assertNotIn("extra.wav", [decision.material_path.name for decision in plan.decisions])

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            plan.decisions,
            reference_duration=2.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, plan.decisions, target_durations)
        self.assertEqual(summary["decision_count"], 4)
        self.assertEqual(summary["positioned_decision_count"], 4)
        self.assertEqual(summary["timed_target_duration_count"], 4)
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (0.4, 0.6, 0.6, 0.4))

    def test_orders_japanese_kanji_lyrics_by_janome_pronunciation(self) -> None:
        with patch("audio_processor.model_assist._janome_tokenizer", return_value=self._fake_janome_tokenizer()):
            decisions = order_materials_for_reference(
                [VoiceSegment(0.0, 4.0, "\u611b\u3057\u3066", language_hint="JP")],
                [
                    MaterialAnalysis(Path("te.wav"), transcript="", filename_text="te", duration_seconds=1.0, language_hint="JP"),
                    MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=1.0, language_hint="JP"),
                    MaterialAnalysis(Path("a.wav"), transcript="", filename_text="a", duration_seconds=1.0, language_hint="JP"),
                    MaterialAnalysis(Path("i.wav"), transcript="", filename_text="i", duration_seconds=1.0, language_hint="JP"),
                ],
            )

        self.assertEqual([decision.material_path.name for decision in decisions], ["a.wav", "i.wav", "shi.wav", "te.wav"])
        self.assertEqual([decision.phonetic_position for decision in decisions], [0, 1, 2, 3])
        self.assertTrue(all(decision.language_hint == "JP" for decision in decisions))

    def test_chinese_language_hint_keeps_cjk_on_pinyin_path(self) -> None:
        with patch("audio_processor.model_assist._janome_tokenizer", return_value=self._fake_janome_tokenizer()):
            decisions = order_materials_for_reference(
                [VoiceSegment(0.0, 1.0, "\u4e2d", language_hint="CN")],
                [
                    MaterialAnalysis(Path("chu.wav"), transcript="", filename_text="chu", duration_seconds=1.0, language_hint="CN"),
                    MaterialAnalysis(Path("zhong.wav"), transcript="", filename_text="zhong", duration_seconds=1.0, language_hint="CN"),
                ],
            )

        self.assertEqual(decisions[0].material_path.name, "zhong.wav")
        self.assertEqual(decisions[0].language_hint, "CN")

    def test_repeated_phonetic_positions_are_diagnosed_and_downweighted(self) -> None:
        plan = plan_material_ordering(
            [VoiceSegment(0.0, 2.0, "\u662f\u4e8b")],
            [MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=1.0)],
        )
        score = plan.score_matrix[0][0]

        self.assertEqual(score.phonetic_position, 0)
        self.assertEqual(score.phonetic_position_count, 2)
        self.assertGreater(score.phonetic_score, 0.6)
        self.assertLess(score.phonetic_score, 0.92)

    def test_phonetic_span_units_follow_multi_syllable_filename_phrases(self) -> None:
        plan = plan_material_ordering(
            [VoiceSegment(0.0, 3.0, "\u6211\u662f\u8c01")],
            [
                MaterialAnalysis(Path("wo-shi.wav"), transcript="", filename_text="wo shi", duration_seconds=2.0),
                MaterialAnalysis(Path("shui.wav"), transcript="", filename_text="shui", duration_seconds=1.0),
            ],
        )

        first = plan.score_matrix[0][0]
        second = plan.score_matrix[0][1]
        self.assertEqual([decision.material_path.name for decision in plan_material_ordering(
            [VoiceSegment(0.0, 3.0, "\u6211\u662f\u8c01")],
            [
                MaterialAnalysis(Path("wo-shi.wav"), transcript="", filename_text="wo shi", duration_seconds=2.0),
                MaterialAnalysis(Path("shui.wav"), transcript="", filename_text="shui", duration_seconds=1.0),
            ],
        ).decisions], ["wo-shi.wav", "shui.wav"])
        self.assertEqual(first.phonetic_span_units, 2)
        self.assertEqual(second.phonetic_span_units, 1)

    def test_compact_phonetic_match_keeps_per_position_span_and_rejects_cross_unit_single_syllable(self) -> None:
        self.assertEqual(
            model_assist._phonetic_position_spans_for_units(
                ["ni", "yin", "shi"],
                ["in"],
                allow_compact_match=True,
            ),
            ((1, 1),),
        )
        self.assertEqual(
            model_assist._phonetic_position_spans_for_units(
                ["zhen", "ai"],
                ["na"],
                allow_compact_match=True,
            ),
            (),
        )

    def test_cn_single_phonetic_material_does_not_expand_aligned_timing_lattice(self) -> None:
        text = "\u662f\u4e2a\u672a\u77e5\u529b\u91cf\u7684\u7275\u5f15"
        reference = VoiceSegment(
            0.0,
            9.0,
            text,
            unit_timings=tuple(
                VoiceUnitTiming(index, unit, float(index), float(index + 1), timing_source="test_alignment")
                for index, unit in enumerate(text)
            ),
            language_hint="CN",
        )
        plan = plan_material_ordering(
            [reference],
            [
                MaterialAnalysis(
                    Path("in1.wav"),
                    filename_text="in1",
                    duration_seconds=0.2,
                    language_hint="CN",
                )
            ],
        )

        self.assertEqual(plan.decisions[0].phonetic_span_units, 1)
        target_durations = model_runtime._target_durations_for_decisions(
            [reference],
            plan.decisions,
            reference_duration=9.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(
            [reference],
            plan.decisions,
            target_durations,
        )
        self.assertEqual(summary["resampled_timing_lattice_count"], 0)
        self.assertEqual(summary["decision_details"][0]["position_unit_span"], 1)


class ModelRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        model_runtime._module_status.cache_clear()
        model_runtime._prepare_native_dependency_paths.cache_clear()
        model_runtime._prepare_speechbrain_lazy_import_compat.cache_clear()
        super().tearDown()

    def test_model_cache_root_falls_back_when_candidate_is_not_creatable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blocked = root / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            fallback = root / "fallback-cache"

            with patch(
                "audio_processor.model_runtime._model_cache_candidates",
                return_value=[blocked / "models", fallback],
            ):
                cache_root = model_runtime._model_cache_root()

        self.assertEqual(cache_root, fallback)

    def test_python_https_trust_respects_existing_ca_bundle(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = root / "existing.pem"
            existing.write_text("existing", encoding="utf-8")

            with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": str(existing)}, clear=True):
                with patch("audio_processor.tls._windows_certificate_entries", side_effect=AssertionError):
                    result = tls.ensure_python_https_trust(root / "tls")

        self.assertEqual(result, existing)

    def test_python_https_trust_exports_windows_ca_bundle(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                with patch("audio_processor.tls._certifi_bundle_bytes", return_value=b"BASE\n"):
                    with patch("audio_processor.tls._windows_certificate_entries", return_value=(b"one", b"one", b"two")):
                        result = tls.ensure_python_https_trust(root / "tls")
                env_snapshot = {
                    "REQUESTS_CA_BUNDLE": os.environ.get("REQUESTS_CA_BUNDLE"),
                    "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE"),
                    "CURL_CA_BUNDLE": os.environ.get("CURL_CA_BUNDLE"),
                }

            self.assertIsNotNone(result)
            assert result is not None
            bundle_text = result.read_text(encoding="ascii")

        self.assertIn("BASE", bundle_text)
        self.assertEqual(bundle_text.count("BEGIN CERTIFICATE"), 2)
        self.assertEqual(env_snapshot["REQUESTS_CA_BUNDLE"], str(result))
        self.assertEqual(env_snapshot["SSL_CERT_FILE"], str(result))
        self.assertEqual(env_snapshot["CURL_CA_BUNDLE"], str(result))

    def test_faster_whisper_cache_requires_nonempty_required_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = root / "models--Systran--faster-whisper-base" / "snapshots" / "rev"
            snapshot.mkdir(parents=True)
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (snapshot / filename).write_text("x", encoding="utf-8")

            self.assertEqual(model_runtime._find_complete_faster_whisper_snapshot(root), snapshot)
            (snapshot / "model.bin").write_bytes(b"")

            self.assertIsNone(model_runtime._find_complete_faster_whisper_snapshot(root))

    def test_faster_whisper_model_source_uses_complete_local_snapshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = root / "models--Systran--faster-whisper-base" / "snapshots" / "rev"
            snapshot.mkdir(parents=True)
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (snapshot / filename).write_text("x", encoding="utf-8")

            with patch.dict(os.environ, {"VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD": "1"}, clear=False):
                with patch("audio_processor.model_runtime._prepare_faster_whisper_snapshot") as prepare_mock:
                    source = model_runtime._faster_whisper_model_source("base", root)

        self.assertEqual(source, snapshot)
        prepare_mock.assert_not_called()

    def test_prepare_faster_whisper_snapshot_cleans_zero_byte_cache_and_prefetches_serially(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken = root / "models--Systran--faster-whisper-base" / "snapshots" / "old"
            broken.mkdir(parents=True)
            (broken / "config.json").write_bytes(b"")
            downloaded: list[str] = []

            def fake_download(repo_id: str, filename: str, *, cache_dir: str, local_files_only: bool) -> str:
                self.assertEqual(repo_id, "Systran/faster-whisper-base")
                self.assertFalse(local_files_only)
                downloaded.append(filename)
                snapshot = Path(cache_dir) / "models--Systran--faster-whisper-base" / "snapshots" / "rev"
                snapshot.mkdir(parents=True, exist_ok=True)
                target = snapshot / filename
                target.write_text("x", encoding="utf-8")
                return str(target)

            with patch.dict(os.environ, {}, clear=True):
                with patch("audio_processor.model_runtime._ensure_model_download_tls", return_value=None):
                    with patch("audio_processor.model_runtime._faster_whisper_repo_id", return_value="Systran/faster-whisper-base"):
                        with patch("huggingface_hub.list_repo_files", return_value=[
                            "README.md",
                            "config.json",
                            "model.bin",
                            "tokenizer.json",
                            "vocabulary.txt",
                        ]):
                            with patch("huggingface_hub.hf_hub_download", side_effect=fake_download):
                                result = model_runtime._prepare_faster_whisper_snapshot("base", root)

        self.assertIsNotNone(result)
        self.assertFalse((broken / "config.json").exists())
        self.assertEqual(downloaded, ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])

    def test_material_set_language_heuristic_detects_chinese_pinyin_assets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            material_dir = Path(temp_dir) / "materials"
            material_dir.mkdir()
            for filename in ("wo3.wav", "zhong1.wav", "guo2.wav", "xiang.wav", "bang.wav"):
                _write_test_wave(material_dir / filename)

            language = model_runtime.infer_material_set_language(material_dir)

        self.assertEqual(language, "CN")

    def test_build_model_ordering_rejects_explicit_reference_material_language_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "song_JP.wav"
            material_dir = root / "vmzCN"
            material_dir.mkdir()
            _write_test_wave(reference)
            _write_test_wave(material_dir / "wo.wav")

            with patch("audio_processor.model_runtime.analyze_reference") as analyze_reference_mock:
                with self.assertRaisesRegex(AudioProcessorError, "Language mismatch.*JP.*CN"):
                    model_runtime.build_model_ordering(
                        reference,
                        material_dir,
                        compute_device="cpu",
                        source_separation="never",
                    )

        analyze_reference_mock.assert_not_called()

    def test_build_model_ordering_rejects_lyrics_detected_reference_material_language_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            lyrics = root / "reference.lrc"
            material_dir = root / "vmzCN"
            material_dir.mkdir()
            _write_test_wave(reference)
            _write_test_wave(material_dir / "wo.wav")
            lyrics.write_text("[00:00.00]\u3053\u3093\u306b\u3061\u306f\n", encoding="utf-8")

            with patch("audio_processor.model_runtime.analyze_reference") as analyze_reference_mock:
                with self.assertRaisesRegex(AudioProcessorError, "Language mismatch.*JP.*CN"):
                    model_runtime.build_model_ordering(
                        reference,
                        material_dir,
                        lyrics_file=lyrics,
                        compute_device="cpu",
                        source_separation="never",
                    )

        analyze_reference_mock.assert_not_called()

    def test_build_model_ordering_rejects_asr_detected_reference_material_language_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material_dir = root / "vmzCN"
            material_dir.mkdir()
            _write_test_wave(reference)
            _write_test_wave(material_dir / "wo.wav")
            reference_analysis = model_runtime.ReferenceAnalysis(
                source_path=reference,
                vocal_path=reference,
                transcript="\u3053\u3093\u306b\u3061\u306f",
                segments=(VoiceSegment(0.0, 1.0, "\u3053\u3093\u306b\u3061\u306f"),),
                speaker_embedding=None,
                backend="fake",
                notes=("language=ja",),
            )

            with patch("audio_processor.model_runtime.analyze_reference", return_value=reference_analysis):
                with patch("audio_processor.model_runtime.analyze_material_library") as material_library_mock:
                    with self.assertRaisesRegex(AudioProcessorError, "Language mismatch.*JP.*CN"):
                        model_runtime.build_model_ordering(
                            reference,
                            material_dir,
                            compute_device="cpu",
                            source_separation="never",
                        )

        material_library_mock.assert_not_called()

    def test_default_material_cache_dir_uses_work_root_not_source_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_root = root / "work"
            material_dir = root / "materials"
            other_dir = root / "other-materials"
            material_dir.mkdir()
            other_dir.mkdir()

            cache_dir = model_runtime._default_material_cache_dir(work_root, material_dir, "cpu")
            other_cache_dir = model_runtime._default_material_cache_dir(work_root, other_dir, "cpu")
            cache_path = model_runtime._material_cache_path(material_dir, cache_dir)

        self.assertEqual(cache_dir.parent.parent, work_root)
        self.assertEqual(cache_path.parent, cache_dir)
        self.assertEqual(cache_path.name, model_runtime.MATERIAL_CACHE_FILE)
        self.assertNotIn(str(material_dir), str(cache_dir))
        self.assertNotEqual(cache_dir, other_cache_dir)

    def test_model_cache_snapshot_reuses_matching_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "materials"
            material_dir.mkdir()
            material_path = material_dir / "001.wav"
            _write_test_wave(material_path)
            cache_path = material_dir / ".vocalprocess_material_cache.json"
            snapshot = model_runtime._material_snapshot([material_path])
            filename_label_policy = model_runtime._material_filename_label_policy(material_dir, [material_path])
            cache_path.write_text(
                json.dumps(
                    {
                        "format": model_runtime.MATERIAL_CACHE_FORMAT,
                        "asr_model": model_runtime.DEFAULT_ASR_MODEL,
                        "asr_backend": model_runtime._asr_backend_cache_key(),
                        "material_filename_label_policy": filename_label_policy.cache_key,
                        "material_label_analysis_strategy": model_runtime.MATERIAL_LABEL_ANALYSIS_STRATEGY,
                        "snapshot": snapshot,
                        "materials": [
                            {
                                "path": str(material_path),
                                "duration_seconds": 1.0,
                                "transcript": "alpha",
                                "filename_text": "001",
                                "segments": [],
                                "vad_segments": [],
                                "speaker_embedding": None,
                                "backend": "cache",
                                "notes": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch("audio_processor.model_runtime._transcribe_audio") as transcribe_mock:
                with patch("audio_processor.model_runtime._detect_vad_segments") as vad_mock:
                    with patch("audio_processor.model_runtime._speaker_embedding") as speaker_mock:
                        library = model_runtime.analyze_material_library(material_dir)

        self.assertEqual(library.materials[0].transcript, "alpha")
        transcribe_mock.assert_not_called()
        vad_mock.assert_not_called()
        speaker_mock.assert_not_called()

    def test_model_cache_snapshot_rejects_different_asr_backend(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "materials"
            material_dir.mkdir()
            material_path = material_dir / "001.wav"
            _write_test_wave(material_path)
            cache_path = material_dir / ".vocalprocess_material_cache.json"
            snapshot = model_runtime._material_snapshot([material_path])
            filename_label_policy = model_runtime._material_filename_label_policy(material_dir, [material_path])
            cache_path.write_text(
                json.dumps(
                    {
                        "format": model_runtime.MATERIAL_CACHE_FORMAT,
                        "asr_model": model_runtime.DEFAULT_ASR_MODEL,
                        "asr_backend": "auto",
                        "material_filename_label_policy": filename_label_policy.cache_key,
                        "material_label_analysis_strategy": model_runtime.MATERIAL_LABEL_ANALYSIS_STRATEGY,
                        "snapshot": snapshot,
                        "materials": [
                            {
                                "path": str(material_path),
                                "duration_seconds": 1.0,
                                "transcript": "stale",
                                "filename_text": "001",
                                "segments": [],
                                "vad_segments": [],
                                "speaker_embedding": None,
                                "backend": "cache",
                                "notes": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"VOCAL_PROCESS_ASR_BACKEND": "whisperx"}, clear=False):
                with patch(
                    "audio_processor.model_runtime._transcribe_audio",
                    return_value={"backend": "whisperx", "text": "fresh", "segments": [], "notes": []},
                ) as transcribe_mock:
                    with patch("audio_processor.model_runtime._detect_vad_segments", return_value=()):
                        with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                            library = model_runtime.analyze_material_library(material_dir)

        self.assertEqual(library.materials[0].transcript, "fresh")
        transcribe_mock.assert_called_once()

    def test_material_analysis_can_write_cache_outside_source_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "materials"
            cache_root = root / "analysis-cache"
            material_dir.mkdir()
            material_path = material_dir / "001.wav"
            _write_test_wave(material_path)

            with patch(
                "audio_processor.model_runtime._transcribe_audio",
                return_value={"backend": "fake", "text": "alpha", "segments": [], "notes": []},
            ):
                with patch("audio_processor.model_runtime._detect_vad_segments", return_value=()):
                    with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                        library = model_runtime.analyze_material_library(
                            material_dir,
                            material_cache_dir=cache_root,
                        )

            self.assertEqual(library.materials[0].transcript, "alpha")
            self.assertFalse((material_dir / ".vocalprocess_material_cache.json").exists())
            self.assertTrue((cache_root / ".vocalprocess_material_cache.json").exists())

    def test_material_analysis_cancellation_stops_before_vad_and_speaker_work(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "materials"
            material_dir.mkdir()
            first = material_dir / "001.wav"
            second = material_dir / "002.wav"
            _write_test_wave(first)
            _write_test_wave(second)
            cancel_state = {"cancelled": False}

            def cancel_after_transcribe(*args: object, **kwargs: object) -> dict[str, object]:
                cancel_state["cancelled"] = True
                return {"backend": "fake", "text": "alpha", "segments": [], "notes": []}

            with patch("audio_processor.model_runtime._transcribe_audio", side_effect=cancel_after_transcribe):
                with patch("audio_processor.model_runtime._detect_vad_segments") as vad_mock:
                    with patch("audio_processor.model_runtime._speaker_embedding") as speaker_mock:
                        with self.assertRaisesRegex(AudioProcessorError, "Processing cancelled"):
                            model_runtime.analyze_material_library(
                                material_dir,
                                should_cancel=lambda: cancel_state["cancelled"],
                            )

        vad_mock.assert_not_called()
        speaker_mock.assert_not_called()

    def test_pinyin_positioned_decisions_split_single_segment_target_duration(self) -> None:
        reference_segments = [VoiceSegment(0.0, 3.0, "\u6211\u662f\u4f60")]
        decisions = order_materials_for_reference(
            reference_segments,
            [
                MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=0.7),
                MaterialAnalysis(Path("wo.wav"), transcript="", filename_text="wo", duration_seconds=0.8),
                MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=0.9),
            ],
        )

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=3.0,
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["wo.wav", "shi.wav", "ni.wav"])
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (1.0, 1.0, 1.0))
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)
        self.assertEqual(summary["mode"], "phonetic_or_text_position_split")
        self.assertEqual(summary["split_reference_segment_indices"], [0])
        self.assertEqual(len(summary["decision_details"]), 3)
        self.assertEqual(summary["decision_details"][0]["source_filename_phonetic_units"], ["wo"])
        self.assertEqual(summary["decision_details"][0]["reference_phonetic_units"], ["wo", "shi", "ni"])
        self.assertEqual(summary["decision_details"][0]["position_mode"], "phonetic_position")
        self.assertEqual(summary["decision_details"][0]["position_unit_span"], 1)
        self.assertEqual(summary["decision_details"][0]["target_duration_seconds"], 1.0)
        self.assertEqual(summary["decision_details"][0]["target_duration_source"], "proportional_segment_split")

    def test_positioned_decisions_use_aligned_unit_timing_for_target_duration(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                3.0,
                "\u6211\u662f\u4f60",
                unit_timings=(
                    VoiceUnitTiming(0, "\u6211", 0.0, 0.4, timing_source="test_alignment"),
                    VoiceUnitTiming(1, "\u662f", 0.4, 1.6, timing_source="test_alignment"),
                    VoiceUnitTiming(2, "\u4f60", 1.6, 3.0, timing_source="test_alignment"),
                ),
            )
        ]
        decisions = order_materials_for_reference(
            reference_segments,
            [
                MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=0.7),
                MaterialAnalysis(Path("wo.wav"), transcript="", filename_text="wo", duration_seconds=0.8),
                MaterialAnalysis(Path("shi.wav"), transcript="", filename_text="shi", duration_seconds=0.9),
            ],
        )

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=3.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)

        self.assertEqual([decision.material_path.name for decision in decisions], ["wo.wav", "shi.wav", "ni.wav"])
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (0.4, 1.2, 1.4))
        self.assertEqual(summary["mode"], "aligned_unit_timing")
        self.assertEqual(summary["reference_unit_timing_count"], 3)
        self.assertEqual(summary["timed_target_duration_count"], 3)
        self.assertEqual(summary["decision_details"][0]["target_duration_source"], "aligned_unit_timing")
        self.assertEqual(summary["decision_details"][0]["target_start_seconds"], 0.0)
        self.assertEqual(summary["decision_details"][0]["target_end_seconds"], 0.4)

    def test_aligned_unit_targets_are_not_scaled_to_fill_uncovered_reference_tail(self) -> None:
        reference_segments = [
            VoiceSegment(
                10.0,
                20.0,
                "\u4f60\u7231",
                unit_timings=(
                    VoiceUnitTiming(0, "\u4f60", 10.0, 10.3, timing_source="test_alignment"),
                    VoiceUnitTiming(1, "\u7231", 10.3, 10.9, timing_source="test_alignment"),
                ),
            )
        ]
        decisions = order_materials_for_reference(
            reference_segments,
            [
                MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=0.4),
                MaterialAnalysis(Path("ai.wav"), transcript="", filename_text="ai", duration_seconds=0.5),
            ],
        )

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=10.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (0.3, 0.6))
        self.assertAlmostEqual(summary["target_duration_total_seconds"], 0.9)
        self.assertEqual(summary["timed_target_duration_count"], 2)

    def test_positioned_decisions_resample_unit_timing_lattice_when_target_units_expand(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                4.0,
                "a b c d",
                unit_timings=(
                    VoiceUnitTiming(0, "a", 0.0, 1.0, timing_source="test_alignment"),
                    VoiceUnitTiming(1, "b", 1.0, 4.0, timing_source="test_alignment"),
                ),
            )
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("a.wav"),
                score=0.9,
                reference_text="a b c d",
                material_text="a",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_position_count=1,
                phonetic_span_units=1,
                confidence_label="strong",
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("b.wav"),
                score=0.9,
                reference_text="a b c d",
                material_text="b",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=1,
                phonetic_position=1,
                phonetic_position_count=1,
                phonetic_span_units=1,
                confidence_label="strong",
            ),
            MaterialOrderDecision(
                rank=3,
                material_path=Path("c.wav"),
                score=0.9,
                reference_text="a b c d",
                material_text="c",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=2,
                phonetic_position=2,
                phonetic_position_count=1,
                phonetic_span_units=1,
                confidence_label="strong",
            ),
            MaterialOrderDecision(
                rank=4,
                material_path=Path("d.wav"),
                score=0.9,
                reference_text="a b c d",
                material_text="d",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=3,
                phonetic_position=3,
                phonetic_position_count=1,
                phonetic_span_units=1,
                confidence_label="strong",
            ),
        ]

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=4.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (0.5, 0.5, 1.5, 1.5))
        self.assertEqual(summary["timed_target_duration_count"], 4)
        self.assertEqual(summary["exact_timed_target_duration_count"], 0)
        self.assertEqual(summary["resampled_timing_lattice_count"], 4)
        self.assertEqual(summary["mode"], "aligned_unit_timing")
        self.assertTrue(all(detail["target_duration_source"] == "aligned_unit_timing" for detail in summary["decision_details"]))
        self.assertTrue(all(detail["timing_lattice_resampled"] for detail in summary["decision_details"]))

    def test_funasr_unit_slots_are_scaled_to_reference_duration_when_gaps_are_sparse(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                3.0,
                "\u6211\u7231\u4f60",
                unit_timings=(
                    VoiceUnitTiming(0, "\u6211", 0.5, 0.6, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(1, "\u7231", 1.2, 1.3, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(2, "\u4f60", 1.8, 2.0, timing_source="funasr_timestamp"),
                ),
            )
        ]
        decisions = order_materials_for_reference(
            reference_segments,
            [
                MaterialAnalysis(Path("ni.wav"), transcript="", filename_text="ni", duration_seconds=0.4),
                MaterialAnalysis(Path("wo.wav"), transcript="", filename_text="wo", duration_seconds=0.4),
                MaterialAnalysis(Path("ai.wav"), transcript="", filename_text="ai", duration_seconds=0.4),
            ],
        )

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=3.0,
        )

        self.assertEqual([decision.material_path.name for decision in decisions], ["wo.wav", "ai.wav", "ni.wav"])
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (1.4, 1.2, 0.4))
        self.assertAlmostEqual(sum(float(duration or 0.0) for duration in target_durations), 3.0)

    def test_partial_positioned_decisions_fill_remaining_target_duration(self) -> None:
        reference_segments = [
            VoiceSegment(0.0, 2.0, "\u6211\u662f"),
            VoiceSegment(2.0, 10.0, "tail"),
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("wo.wav"),
                score=0.8,
                reference_text="\u6211\u662f",
                material_text="wo",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_span_units=1,
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("shi.wav"),
                score=0.8,
                reference_text="\u6211\u662f",
                material_text="shi",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=1,
                phonetic_position=1,
                phonetic_span_units=1,
            ),
            MaterialOrderDecision(
                rank=3,
                material_path=Path("unknown.wav"),
                score=0.0,
                reference_text="",
                material_text="unknown",
                reason="unmatched_filename_fallback",
            ),
        ]

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=10.0,
        )

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (1.0, 1.0, 8.0))
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)
        self.assertEqual(summary["resolved_target_duration_count"], 3)
        self.assertAlmostEqual(summary["target_duration_total_seconds"], 10.0)

    def test_absolute_segment_timeline_keeps_inter_segment_silence_out_of_audible_targets(self) -> None:
        reference_segments = [
            VoiceSegment(10.0, 20.0, "\u6211\u662f", timing_source="asr_segment"),
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("wo.wav"),
                score=0.8,
                reference_text="\u6211\u662f",
                material_text="wo",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_span_units=1,
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("shi.wav"),
                score=0.8,
                reference_text="\u6211\u662f",
                material_text="shi",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=1,
                phonetic_position=1,
                phonetic_span_units=1,
            ),
        ]

        active_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=30.0,
            preserve_positioned_active_total=True,
        )
        slots, audible, pre_silences, post_silences = model_runtime._target_timeline_slots_for_decisions(
            reference_segments,
            decisions,
            active_durations,
            reference_duration=30.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(
            reference_segments,
            decisions,
            slots,
            target_audible_durations=audible,
            target_pre_silences=pre_silences,
            target_post_silences=post_silences,
        )

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in audible), (5.0, 5.0))
        self.assertEqual(tuple(round(value, 6) for value in pre_silences), (10.0, 0.0))
        self.assertEqual(tuple(round(value, 6) for value in post_silences), (0.0, 10.0))
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in slots), (15.0, 15.0))
        self.assertAlmostEqual(summary["target_duration_total_seconds"], 30.0)
        self.assertAlmostEqual(summary["target_audible_duration_total_seconds"], 10.0)
        self.assertAlmostEqual(summary["target_pre_silence_total_seconds"], 10.0)
        self.assertAlmostEqual(summary["target_post_silence_total_seconds"], 10.0)

    def test_absolute_segment_timeline_handles_unpositioned_fallback_decision(self) -> None:
        reference_segments = [
            VoiceSegment(5.0, 7.0, "\u6211", timing_source="asr_segment"),
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("wo.wav"),
                score=0.8,
                reference_text="\u6211",
                material_text="wo",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_span_units=1,
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("fallback.wav"),
                score=0.1,
                reference_text="",
                material_text="fallback",
                reason="unmatched_filename_fallback",
            ),
        ]

        slots, audible, pre_silences, post_silences = model_runtime._target_timeline_slots_for_decisions(
            reference_segments,
            decisions,
            (2.0, 1.0),
            reference_duration=10.0,
        )

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in audible), (2.0, 1.0))
        self.assertEqual(tuple(round(value, 6) for value in pre_silences), (5.0, 0.0))
        self.assertEqual(tuple(round(value, 6) for value in post_silences), (2.0, 0.0))
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in slots), (9.0, 1.0))

    def test_duplicate_positioned_decisions_do_not_expand_segment_unit_count(self) -> None:
        reference_segments = [VoiceSegment(0.0, 2.0, "you")]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("you_a.wav"),
                score=0.2,
                reference_text="you",
                material_text="you",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("you_b.wav"),
                score=0.2,
                reference_text="you",
                material_text="you",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
            ),
            MaterialOrderDecision(
                rank=3,
                material_path=Path("you_c.wav"),
                score=0.2,
                reference_text="you",
                material_text="you",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
            ),
        ]

        target_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=2.0,
        )
        summary = model_runtime._render_timeline_alignment_summary(reference_segments, decisions, target_durations)

        self.assertEqual(summary["decision_details"][0]["reference_segment_unit_count"], 1)
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in target_durations), (0.666667, 0.666667, 0.666667))

    def test_absolute_timeline_duplicate_positions_preserve_segment_active_budget(self) -> None:
        reference_segments = [
            VoiceSegment(10.0, 10.5, "ha", timing_source="asr_segment", language_hint="JP"),
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("ha_a.wav"),
                score=0.8,
                reference_text="ha",
                material_text="ha",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_span_units=1,
                language_hint="JP",
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("ha_b.wav"),
                score=0.8,
                reference_text="ha",
                material_text="ha",
                reason="reference_text_position",
                reference_segment_index=0,
                text_position=0,
                phonetic_position=0,
                phonetic_span_units=1,
                language_hint="JP",
            ),
        ]

        active_durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=40.0,
            preserve_positioned_active_total=True,
        )
        slots, audible, pre_silences, post_silences = model_runtime._target_timeline_slots_for_decisions(
            reference_segments,
            decisions,
            active_durations,
            reference_duration=40.0,
        )

        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in active_durations), (0.5, 0.5))
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in audible), (0.25, 0.25))
        self.assertEqual(tuple(round(value, 6) for value in pre_silences), (10.0, 0.0))
        self.assertEqual(tuple(round(value, 6) for value in post_silences), (0.0, 29.5))
        self.assertEqual(tuple(round(duration or 0.0, 6) for duration in slots), (10.25, 29.75))

    def test_jp_aligned_unit_timing_smooths_sub_consonant_slots(self) -> None:
        reference_segments = [
            VoiceSegment(
                0.0,
                1.0,
                "kakiku",
                timing_source="asr_segment",
                language_hint="JP",
                unit_timings=(
                    VoiceUnitTiming(0, "ka", 0.0, 0.010, timing_source="whisperx"),
                    VoiceUnitTiming(1, "ki", 0.010, 0.500, timing_source="whisperx"),
                    VoiceUnitTiming(2, "ku", 0.500, 1.000, timing_source="whisperx"),
                ),
            ),
        ]
        decisions = [
            MaterialOrderDecision(
                rank=1,
                material_path=Path("ka.wav"),
                score=0.8,
                reference_text="ka",
                material_text="ka",
                reason="reference_text_position",
                reference_segment_index=0,
                phonetic_position=0,
                phonetic_span_units=1,
                language_hint="JP",
            ),
            MaterialOrderDecision(
                rank=2,
                material_path=Path("ki.wav"),
                score=0.8,
                reference_text="ki",
                material_text="ki",
                reason="reference_text_position",
                reference_segment_index=0,
                phonetic_position=1,
                phonetic_span_units=1,
                language_hint="JP",
            ),
            MaterialOrderDecision(
                rank=3,
                material_path=Path("ku.wav"),
                score=0.8,
                reference_text="ku",
                material_text="ku",
                reason="reference_text_position",
                reference_segment_index=0,
                phonetic_position=2,
                phonetic_span_units=1,
                language_hint="JP",
            ),
        ]

        durations = model_runtime._target_durations_for_decisions(
            reference_segments,
            decisions,
            reference_duration=1.0,
            preserve_positioned_active_total=True,
        )

        self.assertGreaterEqual(durations[0] or 0.0, 0.045)
        self.assertAlmostEqual(sum(duration or 0.0 for duration in durations), 1.0)
        self.assertLess(durations[1] or 0.0, 0.490)

    def test_preflight_warns_when_filename_pronunciation_matches_multiple_positions(self) -> None:
        decision = model_runtime.OrderingDecision(
            rank=1,
            source_path=Path("shi.wav"),
            score=0.42,
            transcript_score=0.0,
            filename_score=0.0,
            duration_score=0.8,
            speaker_score=0.0,
            vad_score=1.0,
            phonetic_score=0.74,
            evidence_count=3,
            confidence_label="medium",
            reference_text="\u662f\u4e8b",
            material_text="shi",
            reason="reference_text_position",
            reference_segment_index=0,
            text_position=0,
            phonetic_position_count=2,
            target_duration_seconds=1.0,
        )
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u662f\u4e8b",
                segments=(VoiceSegment(0.0, 2.0, "\u662f\u4e8b"),),
                speaker_embedding=None,
                backend="test",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(Path("shi.wav"),),
            target_durations=(1.0,),
            decisions=(decision,),
            analysis_report={},
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertTrue(any(warning["kind"] == "ambiguous_phonetic_position" for warning in warnings))

    def test_preflight_passes_material_text_to_stretch_continuity_diagnostics(self) -> None:
        decision = model_runtime.OrderingDecision(
            rank=1,
            source_path=Path("wo.wav"),
            score=0.9,
            transcript_score=0.0,
            filename_score=0.9,
            duration_score=0.9,
            speaker_score=0.0,
            vad_score=1.0,
            phonetic_score=0.9,
            evidence_count=3,
            confidence_label="strong",
            reference_text="\u6211",
            material_text="wo",
            reason="reference_text_position",
            reference_segment_index=0,
            phonetic_position=0,
            target_duration_seconds=2.0,
        )
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 2.0, "\u6211", timing_source="lyric_text"),),
                speaker_embedding=None,
                backend="test",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(Path("wo.wav"),),
            target_durations=(2.0,),
            decisions=(decision,),
            analysis_report={"backend_summary": {}},
        )

        with patch("audio_processor.preflight.build_model_ordering", return_value=ordering):
            with patch(
                "audio_processor.engine.probe_audio",
                side_effect=[
                    {"format": {"duration": "2.0"}, "streams": []},
                    {"format": {"duration": "0.5"}, "streams": []},
                ],
            ), patch("audio_processor.engine.signalsmith_stretch_available", return_value=True):
                report = preflight.build_preflight_report(Path("reference.wav"), Path("materials"))

        self.assertEqual(report["summary"]["continuity_warning_count"], 1)
        self.assertEqual(report["summary"]["fade_applied_clip_count"], 1)
        self.assertAlmostEqual(report["summary"]["stretch_naturalness_score_mean"] or 0.0, 0.48)
        self.assertAlmostEqual(report["stretch_plan"][0]["timeline_requested_tempo"], 0.25)
        self.assertAlmostEqual(report["stretch_plan"][0]["requested_rubberband_tempo"], 0.5)
        self.assertEqual(report["stretch_plan"][0]["continuity_warning"], "single_syllable_boundary_risk")
        self.assertEqual(
            report["stretch_plan"][0]["boundary_conditioning"],
            "vowel_core_stretch+fade_in_out+tempo_safe_silence_pad",
        )
        self.assertEqual(
            report["optimization"]["render_continuity"]["boundary_conditioning"],
            "vowel_core_stretch+per_clip_fade_in_out+tempo_safe_silence_pad",
        )
        self.assertTrue(any(warning["kind"] == "single_syllable_boundary_risk" for warning in report["warnings"]))

    def test_preflight_requires_aligned_unit_timing_for_positioned_decisions(self) -> None:
        decision = model_runtime.OrderingDecision(
            rank=1,
            source_path=Path("wo.wav"),
            score=0.8,
            transcript_score=0.0,
            filename_score=0.9,
            duration_score=0.8,
            speaker_score=0.0,
            vad_score=1.0,
            phonetic_score=0.9,
            evidence_count=3,
            confidence_label="strong",
            reference_text="\u6211",
            material_text="wo",
            reason="reference_text_position",
            reference_segment_index=0,
            phonetic_position=0,
            target_duration_seconds=1.0,
        )
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 1.0, "\u6211"),),
                speaker_embedding=None,
                backend="test",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(Path("wo.wav"),),
            target_durations=(1.0,),
            decisions=(decision,),
            analysis_report={
                "timeline_alignment": {
                    "positioned_decision_count": 1,
                    "timed_target_duration_count": 0,
                }
            },
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertTrue(any(warning["kind"] == "missing_aligned_unit_timing" for warning in warnings))

    def test_preflight_rejects_resampled_unit_timing_for_strict_timeline(self) -> None:
        decision = model_runtime.OrderingDecision(
            rank=1,
            source_path=Path("wo.wav"),
            score=0.8,
            transcript_score=0.0,
            filename_score=0.9,
            duration_score=0.8,
            speaker_score=0.0,
            vad_score=1.0,
            phonetic_score=0.9,
            evidence_count=3,
            confidence_label="strong",
            reference_text="\u6211",
            material_text="wo",
            reason="reference_text_position",
            reference_segment_index=0,
            phonetic_position=0,
            target_duration_seconds=1.0,
        )
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 1.0, "\u6211", timing_source="asr_segment_with_lyric_text"),),
                speaker_embedding=None,
                backend="test",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(Path("wo.wav"),),
            target_durations=(1.0,),
            decisions=(decision,),
            analysis_report={
                "timeline_alignment": {
                    "positioned_decision_count": 1,
                    "timed_target_duration_count": 1,
                    "exact_timed_target_duration_count": 0,
                    "resampled_timing_lattice_count": 1,
                }
            },
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertTrue(
            any(
                warning["kind"] == "resampled_aligned_unit_timing" and warning["severity"] == "error"
                for warning in warnings
            )
        )

    def test_preflight_marks_lyric_timing_conflict_as_error(self) -> None:
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 1.0, "\u6211", timing_source="asr_segment_with_lyric_text"),),
                speaker_embedding=None,
                backend="whisperx",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(),
            target_durations=(),
            decisions=(),
            analysis_report={
                "notes": [
                    "lyric_timing_conflict: timestamped lyrics disagree with ASR/acoustic timing; conflicts=1/1"
                ]
            },
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertTrue(
            any(warning["kind"] == "lyric_timing_conflict" and warning["severity"] == "error" for warning in warnings)
        )

    def test_preflight_marks_unverified_reference_asr_for_strict_acceptance(self) -> None:
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 1.0, "\u6211", timing_source="asr_segment"),),
                speaker_embedding=None,
                backend="whisperx",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(),
            target_durations=(),
            decisions=(),
            analysis_report={},
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertTrue(any(warning["kind"] == "reference_asr_unverified" for warning in warnings))

    def test_preflight_accepts_reference_text_retargeted_to_lyrics(self) -> None:
        ordering = model_runtime.ModelOrderingResult(
            reference=model_runtime.ReferenceAnalysis(
                source_path=Path("reference.wav"),
                vocal_path=Path("reference.wav"),
                transcript="\u6211",
                segments=(VoiceSegment(0.0, 1.0, "\u6211", timing_source="asr_segment_with_lyric_text"),),
                speaker_embedding=None,
                backend="whisperx",
            ),
            library=model_runtime.MaterialLibraryAnalysis(
                material_directory=Path("materials"),
                materials=(),
                backend_summary={},
            ),
            ordered_paths=(),
            target_durations=(),
            decisions=(),
            analysis_report={},
        )

        warnings = preflight._preflight_warnings(ordering, [])

        self.assertFalse(any(warning["kind"] == "reference_asr_unverified" for warning in warnings))

    def test_reference_analysis_cache_reuses_matching_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            _write_test_wave(reference)
            transcript = {
                "backend": "fake",
                "text": "alpha",
                "segments": [model_runtime.TranscriptSegment(0.0, 1.0, "alpha")],
                "notes": [],
            }

            with patch("audio_processor.model_runtime._maybe_separate_vocals", return_value=reference):
                with patch("audio_processor.model_runtime._transcribe_audio", return_value=transcript) as transcribe_mock:
                    with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                        first = model_runtime.analyze_reference(
                            reference,
                            work_dir=root / "work",
                            source_separation="never",
                        )
                        second = model_runtime.analyze_reference(
                            reference,
                            work_dir=root / "work",
                            source_separation="never",
                        )

        self.assertEqual(first.transcript, "alpha")
        self.assertEqual(second.transcript, "alpha")
        self.assertEqual(transcribe_mock.call_count, 1)

    def test_aligned_chars_are_cached_as_reference_unit_timings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            _write_test_wave(reference)
            transcript = {
                "backend": "fake",
                "text": "\u6211\u662f",
                "segments": [
                    model_runtime.TranscriptSegment(
                        0.0,
                        1.0,
                        "\u6211\u662f",
                        timing_source="whisperx_char_alignment",
                        unit_timings=(
                            VoiceUnitTiming(0, "\u6211", 0.0, 0.35, timing_source="whisperx_char_alignment"),
                            VoiceUnitTiming(1, "\u662f", 0.35, 1.0, timing_source="whisperx_char_alignment"),
                        ),
                    )
                ],
                "notes": [],
            }

            with patch("audio_processor.model_runtime._maybe_separate_vocals", return_value=reference):
                with patch("audio_processor.model_runtime._transcribe_audio", return_value=transcript):
                    with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                        first = model_runtime.analyze_reference(
                            reference,
                            work_dir=root / "work",
                            source_separation="never",
                        )
                        second = model_runtime.analyze_reference(
                            reference,
                            work_dir=root / "work",
                            source_separation="never",
                        )

        self.assertEqual([timing.unit for timing in first.segments[0].unit_timings], ["\u6211", "\u662f"])
        self.assertEqual(second.segments[0].unit_timings[1].start_seconds, 0.35)
        self.assertEqual(second.segments[0].timing_source, "asr_segment_with_unit_timing")

    def test_whisperx_char_entries_become_timeline_unit_timings(self) -> None:
        segment = {
            "text": "\u6211\u662f you",
            "chars": [
                {"char": "\u6211", "start": 0.0, "end": 0.3, "score": 0.9},
                {"char": "\u662f", "start": 0.3, "end": 0.8, "score": 0.8},
                {"char": " ", "start": 0.8, "end": 0.82, "score": 0.1},
                {"char": "y", "start": 0.82, "end": 0.9, "score": 0.7},
                {"char": "o", "start": 0.9, "end": 1.0, "score": 0.7},
                {"char": "u", "start": 1.0, "end": 1.1, "score": 0.7},
            ],
        }

        timings = model_runtime._unit_timings_from_aligned_chars(segment)

        self.assertEqual([timing.unit for timing in timings], ["\u6211", "\u662f", "you"])
        self.assertEqual([timing.position for timing in timings], [0, 1, 2])
        self.assertEqual(timings[2].start_seconds, 0.82)
        self.assertEqual(timings[2].end_seconds, 1.1)

    def test_japanese_whisperx_char_entries_merge_long_vowels_into_mora_timings(self) -> None:
        segment = {
            "text": "\u30b9\u30fc\u30d1\u30fc",
            "chars": [
                {"char": "\u30b9", "start": 0.0, "end": 0.18, "score": 0.9},
                {"char": "\u30fc", "start": 0.18, "end": 0.55, "score": 0.8},
                {"char": "\u30d1", "start": 0.55, "end": 0.74, "score": 0.9},
                {"char": "\u30fc", "start": 0.74, "end": 1.2, "score": 0.8},
            ],
        }

        timings = model_runtime._unit_timings_from_aligned_chars(segment, language_hint="JP")

        self.assertEqual([timing.unit for timing in timings], ["su", "pa"])
        self.assertEqual([timing.position for timing in timings], [0, 1])
        self.assertEqual(
            [(round(timing.start_seconds, 6), round(timing.end_seconds, 6)) for timing in timings],
            [(0.0, 0.55), (0.55, 1.2)],
        )

    def test_japanese_romaji_timeline_units_collapse_long_vowels_and_gemination(self) -> None:
        self.assertEqual(model_runtime._timeline_units("suupaa", language_hint="JP"), ["su", "pa"])
        self.assertEqual(model_runtime._timeline_units("gakkou", language_hint="JP"), ["ga", "ko"])
        self.assertEqual(model_runtime._timeline_units("PlasticLove", language_hint="JP"), ["plasticlove"])

    def test_japanese_lyric_long_vowel_retarget_keeps_original_span_duration(self) -> None:
        timings = model_runtime._retarget_unit_timings_to_text(
            (
                VoiceUnitTiming(0, "x0", 0.0, 0.2, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(1, "x1", 0.2, 0.55, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(2, "x2", 0.55, 0.75, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(3, "x3", 0.75, 1.2, timing_source="whisperx_char_alignment"),
            ),
            "\u30b9\u30fc\u30d1\u30fc",
            language_hint="JP",
        )

        self.assertEqual([timing.unit for timing in timings], ["su", "pa"])
        self.assertEqual(
            [(round(timing.start_seconds, 6), round(timing.end_seconds, 6)) for timing in timings],
            [(0.0, 0.55), (0.55, 1.2)],
        )

    def test_funasr_timestamps_become_timeline_unit_timings(self) -> None:
        timings = model_runtime._unit_timings_from_funasr_timestamps(
            "\u6211\u7231 you",
            ((0.1, 0.3), (0.3, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)),
        )

        self.assertEqual([timing.unit for timing in timings], ["\u6211", "\u7231", "you"])
        self.assertEqual([timing.position for timing in timings], [0, 1, 2])
        self.assertEqual(timings[0].timing_source, "funasr_timestamp")
        self.assertEqual(timings[2].start_seconds, 0.6)
        self.assertEqual(timings[2].end_seconds, 1.0)

    def test_funasr_timestamp_mismatch_resamples_to_reference_units(self) -> None:
        timings = model_runtime._unit_timings_from_funasr_timestamps(
            "a b c d",
            ((0.0, 1.0), (1.0, 4.0)),
        )

        self.assertEqual([timing.unit for timing in timings], ["a", "b", "c", "d"])
        self.assertEqual([timing.position for timing in timings], [0, 1, 2, 3])
        self.assertTrue(all(timing.timing_source == "funasr_timestamp_resampled" for timing in timings))
        self.assertEqual(
            [(round(timing.start_seconds, 6), round(timing.end_seconds, 6)) for timing in timings],
            [(0.0, 0.5), (0.5, 1.0), (1.0, 2.5), (2.5, 4.0)],
        )

    def test_transcribe_audio_uses_funasr_when_requested(self) -> None:
        class FakeFunasrModel:
            def generate(self, **kwargs: object) -> list[dict[str, object]]:
                if "input" not in kwargs:
                    raise AssertionError("missing input")
                return [
                    {
                        "text": "\u4f60\u597d",
                        "timestamp": [[100, 300], [300, 650]],
                    }
                ]

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "input.wav"
            _write_test_wave(audio)

            with patch.dict(
                os.environ,
                {"VOCAL_PROCESS_ASR_BACKEND": "funasr", "VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD": "1"},
                clear=False,
            ):
                with patch("audio_processor.model_runtime.ensure_runtime_tool_paths", return_value=[]):
                    with patch("audio_processor.model_runtime._ensure_model_download_tls", return_value=None):
                        with patch("audio_processor.model_runtime._module_available", return_value=True):
                            with patch("audio_processor.model_runtime._load_funasr_model", return_value=FakeFunasrModel()):
                                result = model_runtime._transcribe_audio(audio, compute_device="cpu")

        self.assertEqual(result["backend"], "funasr")
        self.assertEqual(result["text"], "\u4f60\u597d")
        self.assertEqual(result["segments"][0].timing_source, "funasr_timestamp")
        self.assertEqual([timing.unit for timing in result["segments"][0].unit_timings], ["\u4f60", "\u597d"])

    def test_transcribe_audio_skips_funasr_for_japanese_language_hint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "input.wav"
            _write_test_wave(audio)

            with patch.dict(
                os.environ,
                {"VOCAL_PROCESS_ASR_BACKEND": "funasr", "VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD": "1"},
                clear=False,
            ):
                with patch("audio_processor.model_runtime.ensure_runtime_tool_paths", return_value=[]):
                    with patch("audio_processor.model_runtime._ensure_model_download_tls", return_value=None):
                        with patch("audio_processor.model_runtime._speech_runtime_issue", return_value=""):
                            with patch(
                                "audio_processor.model_runtime._module_available",
                                side_effect=lambda name: name in {"funasr", "whisper"},
                            ):
                                with patch("audio_processor.model_runtime._transcribe_with_funasr") as funasr_mock:
                                    with patch(
                                        "audio_processor.model_runtime._transcribe_with_whisper",
                                        return_value={
                                            "backend": "whisper",
                                            "text": "\u3042\u3044",
                                            "segments": [],
                                            "notes": ["fallback"],
                                        },
                                    ) as whisper_mock:
                                        result = model_runtime._transcribe_audio(
                                            audio,
                                            compute_device="cpu",
                                            language_hint="JP",
                                        )

        self.assertEqual(result["backend"], "whisper")
        funasr_mock.assert_not_called()
        whisper_mock.assert_called_once()
        self.assertIn("asr_backend_language_guard", whisper_mock.call_args.kwargs["fallback_note"])
        self.assertEqual(whisper_mock.call_args.kwargs["language_hint"], "JP")

    def test_lrc_timestamps_are_parsed_but_conflicts_are_not_trusted_silently(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "line.lrc"
            lyrics.write_text("\ufeff[00:10.00]alpha\n[00:12.00]beta\n", encoding="utf-8")

            lyric_segments = model_runtime.parse_lyrics_file(lyrics)
            notes = model_runtime._lyric_timing_notes(
                lyrics,
                [
                    model_runtime.TranscriptSegment(0.0, 1.0, "alpha", timing_source="whisperx_alignment"),
                    model_runtime.TranscriptSegment(1.0, 2.0, "beta", timing_source="whisperx_alignment"),
                ],
            )

        self.assertEqual(lyric_segments[0].start_seconds, 10.0)
        self.assertEqual(lyric_segments[0].text, "alpha")
        self.assertEqual(lyric_segments[0].timing_source, "lrc_timestamp")
        self.assertTrue(any(note.startswith("lyric_timing_conflict:") for note in notes))

    def test_japanese_lyrics_collapse_inline_and_adjacent_pronunciation_annotations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text(
                "\u611b\u3057\u3066\uff08\u3042\u3044\u3057\u3066\uff09 / aishite\n"
                "\u3042\u3044\u3057\u3066\n"
                "aishite\n"
                "\u611b\u3057\u3066\n"
                "\u611b\u3057\u3066\n",
                encoding="utf-8",
            )

            with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
                lyric_segments = model_runtime.parse_lyrics_file(lyrics)

        self.assertEqual([segment.text for segment in lyric_segments], ["\u611b\u3057\u3066", "\u611b\u3057\u3066", "\u611b\u3057\u3066"])
        self.assertEqual([segment.language_hint for segment in lyric_segments], ["JP", "JP", "JP"])
        self.assertEqual([segment.alignment_text for segment in lyric_segments], ["\u3042\u3044\u3057\u3066", "", ""])

    def test_japanese_lyrics_collapse_punctuated_romaji_annotation_line(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text(
                "\u306e\u3069\u3082\u3068\u306b\u304b\u307f\u3064\u304f"
                "\u304d\u3070\u306f\u307e\u3060\u3042\u308b\u304b\u3044\uff1f\n"
                "o do mo to ni ka mi tsu ku ki ba wa ma da a ru ka'i?\n",
                encoding="utf-8",
            )

            with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
                lyric_segments = model_runtime.parse_lyrics_file(lyrics)

        self.assertEqual(
            [segment.text for segment in lyric_segments],
            ["\u306e\u3069\u3082\u3068\u306b\u304b\u307f\u3064\u304f\u304d\u3070\u306f\u307e\u3060\u3042\u308b\u304b\u3044\uff1f"],
        )
        self.assertEqual(
            [segment.alignment_text for segment in lyric_segments],
            ["o do mo to ni ka mi tsu ku ki ba wa ma da a ru ka'i?"],
        )

    def test_japanese_lyrics_alignment_text_preserves_multi_kanji_inline_readings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text(
                "\u6c7a\uff08\u304d\uff09\u307e\u3063\u305f\u8a00\u8449\uff08\u3053\u3068\u3070\uff09"
                "\u5782\uff08\u305f\uff09\u308c\u3066\u307e\u305f\u30d2\u30e5\u30fc\u30de\u30f3\n",
                encoding="utf-8",
            )

            with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
                lyric_segments = model_runtime.parse_lyrics_file(lyrics)

        self.assertEqual(
            [segment.text for segment in lyric_segments],
            ["\u6c7a\u307e\u3063\u305f\u8a00\u8449\u5782\u308c\u3066\u307e\u305f\u30d2\u30e5\u30fc\u30de\u30f3"],
        )
        self.assertEqual(
            [segment.alignment_text for segment in lyric_segments],
            ["\u304d\u307e\u3063\u305f\u3053\u3068\u3070\u305f\u308c\u3066\u307e\u305f\u30d2\u30e5\u30fc\u30de\u30f3"],
        )

    def test_force_align_lyrics_uses_alignment_text_and_retargets_original_units(self) -> None:
        class FakeWhisperX:
            def __init__(self) -> None:
                self.target_segments: list[dict[str, object]] = []

            def align(self, segments, *args, **kwargs):
                self.target_segments = segments
                return {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 2.0,
                            "text": segments[0]["text"],
                            "chars": [
                                {"char": "\u3042", "start": 0.0, "end": 0.2, "score": 0.9},
                                {"char": "\u3044", "start": 0.2, "end": 0.7, "score": 0.8},
                                {"char": "\u3057", "start": 0.7, "end": 1.4, "score": 0.7},
                                {"char": "\u3066", "start": 1.4, "end": 2.0, "score": 0.9},
                            ],
                        }
                    ]
                }

        fake_whisperx = FakeWhisperX()

        with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
            segments = model_runtime._force_align_lyrics_with_whisperx(
                [
                    VoiceSegment(
                        0.0,
                        1.0,
                        "\u611b\u3057\u3066",
                        language_hint="JP",
                        alignment_text="\u3042\u3044\u3057\u3066",
                    )
                ],
                [VoiceSegment(0.0, 2.0, "ignored", language_hint="JP")],
                audio=object(),
                align_model=object(),
                metadata={},
                device="cpu",
                whisperx=fake_whisperx,
                language_hint="JP",
            )

        self.assertEqual(fake_whisperx.target_segments[0]["text"], "\u3042\u3044\u3057\u3066")
        self.assertEqual(segments[0].text, "\u611b\u3057\u3066")
        self.assertEqual(segments[0].alignment_text, "\u3042\u3044\u3057\u3066")
        self.assertEqual(segments[0].timing_source, "whisperx_lyrics_char_alignment")
        self.assertEqual([timing.unit for timing in segments[0].unit_timings], ["a", "i", "shi", "te"])
        self.assertEqual([round(timing.duration_seconds, 6) for timing in segments[0].unit_timings], [0.2, 0.5, 0.7, 0.6])

    def test_force_align_lyrics_prefers_kana_text_over_romaji_annotation(self) -> None:
        class FakeWhisperX:
            def __init__(self) -> None:
                self.target_segments: list[dict[str, object]] = []

            def align(self, segments, *args, **kwargs):
                self.target_segments = segments
                return {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.2,
                            "text": segments[0]["text"],
                            "chars": [
                                {"char": "\u3042", "start": 0.0, "end": 0.2, "score": 0.9},
                                {"char": "\u3063", "start": 0.2, "end": 0.25, "score": 0.8},
                                {"char": "\u306f", "start": 0.25, "end": 0.7, "score": 0.8},
                                {"char": "\u3063", "start": 0.7, "end": 0.75, "score": 0.8},
                                {"char": "\u306f", "start": 0.75, "end": 1.2, "score": 0.8},
                            ],
                        }
                    ]
                }

        fake_whisperx = FakeWhisperX()
        segments = model_runtime._force_align_lyrics_with_whisperx(
            [VoiceSegment(0.0, 1.0, "\u3042\u3063\u306f\u3063\u306f", language_hint="JP", alignment_text="a hha hha")],
            [VoiceSegment(0.0, 1.2, "ignored", language_hint="JP")],
            audio=object(),
            align_model=object(),
            metadata={},
            device="cpu",
            whisperx=fake_whisperx,
            language_hint="JP",
        )

        self.assertEqual(fake_whisperx.target_segments[0]["text"], "\u3042\u3063\u306f\u3063\u306f")
        self.assertEqual(segments[0].text, "\u3042\u3063\u306f\u3063\u306f")
        self.assertEqual([timing.unit for timing in segments[0].unit_timings], ["a", "ha", "ha"])
        self.assertEqual(segments[0].timing_source, "whisperx_lyrics_char_alignment")

    def test_force_align_lyrics_reuses_coarse_exact_timings_when_chars_are_missing(self) -> None:
        class FakeWhisperX:
            def align(self, segments, *args, **kwargs):
                return {
                    "segments": [
                        {
                            "start": 9.0,
                            "end": 9.02,
                            "text": segments[0]["text"],
                            "chars": [],
                        }
                    ]
                }

        segments = model_runtime._force_align_lyrics_with_whisperx(
            [VoiceSegment(0.0, 1.0, "\u7231\u4eba\u540c\u5fd7", language_hint="CN")],
            [
                VoiceSegment(
                    10.0,
                    11.0,
                    "\u7231\u4eba\u540c\u5fd7",
                    language_hint="CN",
                    unit_timings=(
                        VoiceUnitTiming(0, "x0", 10.0, 10.2, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(1, "x1", 10.2, 10.5, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(2, "x2", 10.5, 10.8, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(3, "x3", 10.8, 11.0, timing_source="whisperx_char_alignment"),
                    ),
                )
            ],
            audio=object(),
            align_model=object(),
            metadata={},
            device="cpu",
            whisperx=FakeWhisperX(),
            language_hint="CN",
        )

        self.assertEqual(segments[0].timing_source, "whisperx_lyrics_coarse_unit_alignment")
        self.assertEqual([timing.unit for timing in segments[0].unit_timings], ["\u7231", "\u4eba", "\u540c", "\u5fd7"])
        self.assertEqual([round(timing.duration_seconds, 6) for timing in segments[0].unit_timings], [0.2, 0.3, 0.3, 0.2])
        self.assertAlmostEqual(segments[0].start_seconds, 10.0)
        self.assertAlmostEqual(segments[0].end_seconds, 11.0)

    def test_funasr_repair_fills_missing_cn_lyric_after_previous_line(self) -> None:
        lyrics_segments = (
            VoiceSegment(
                213.0,
                215.2,
                "\u54e6\u8ba9\u6211\u76f8\u4fe1\u4f60\u7684\u5fe0\u8d1e",
                language_hint="CN",
                timing_source="whisperx_lyrics_char_alignment",
                unit_timings=(
                    VoiceUnitTiming(0, "\u54e6", 213.0, 213.2, timing_source="whisperx_lyrics_char_alignment"),
                    VoiceUnitTiming(1, "\u8d1e", 214.92, 215.12, timing_source="whisperx_lyrics_char_alignment"),
                ),
            ),
            VoiceSegment(
                237.371,
                237.392,
                "\u7231\u4eba\u540c\u5fd7",
                language_hint="CN",
                timing_source="whisperx_lyrics_alignment",
            ),
        )
        funasr_segments = (
            model_runtime.TranscriptSegment(
                40.0,
                238.0,
                "\u7231\u4eba\u540c\u5fd7\u7231\u4eba\u540c\u5fd7\u55ef",
                timing_source="funasr_timestamp",
                unit_timings=(
                    VoiceUnitTiming(0, "\u7231", 40.0, 40.2, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(1, "\u4eba", 40.2, 40.4, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(2, "\u540c", 40.4, 40.6, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(3, "\u5fd7", 40.6, 40.8, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(4, "\u7231", 215.55, 215.75, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(5, "\u4eba", 215.75, 215.91, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(6, "\u540c", 215.91, 216.11, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(7, "\u5fd7", 216.11, 216.825, timing_source="funasr_timestamp"),
                    VoiceUnitTiming(8, "\u55ef", 236.74, 237.01, timing_source="funasr_timestamp"),
                ),
            ),
        )

        repaired = model_runtime._repair_missing_lyric_unit_timings_with_funasr(
            lyrics_segments,
            funasr_segments,
            language_hint="CN",
        )

        self.assertEqual(repaired[1].timing_source, "funasr_lyrics_unit_alignment")
        self.assertEqual([timing.unit for timing in repaired[1].unit_timings], ["\u7231", "\u4eba", "\u540c", "\u5fd7"])
        self.assertEqual({timing.timing_source for timing in repaired[1].unit_timings}, {"funasr_lyrics_unit_alignment"})
        self.assertAlmostEqual(repaired[1].start_seconds, 215.55)
        self.assertAlmostEqual(repaired[1].end_seconds, 216.825)

    def test_timeline_alignment_uses_reference_alignment_text_units_without_resampling(self) -> None:
        reference = VoiceSegment(
            0.0,
            1.0,
            "1000\u5e74",
            language_hint="JP",
            alignment_text="\u305b\u3093\u306d\u3093",
            unit_timings=(
                VoiceUnitTiming(0, "se", 0.0, 0.2, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(1, "n", 0.2, 0.4, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(2, "ne", 0.4, 0.7, timing_source="whisperx_char_alignment"),
                VoiceUnitTiming(3, "n", 0.7, 1.0, timing_source="whisperx_char_alignment"),
            ),
        )
        decisions = [
            MaterialOrderDecision(
                rank=index + 1,
                material_path=Path(f"{unit}.wav"),
                score=1.0,
                reference_text=reference.text,
                material_text=unit,
                reason="test",
                reference_segment_index=0,
                phonetic_position=index,
                phonetic_position_count=1,
                phonetic_span_units=1,
                language_hint="JP",
            )
            for index, unit in enumerate(("se", "n", "ne", "n"))
        ]

        targets = model_runtime._positioned_target_durations([reference], decisions)
        summary = model_runtime._render_timeline_alignment_summary([reference], decisions, targets)

        self.assertEqual(summary["timed_target_duration_count"], 4)
        self.assertEqual(summary["exact_timed_target_duration_count"], 4)
        self.assertEqual(summary["resampled_timing_lattice_count"], 0)
        self.assertEqual(summary["decision_details"][0]["reference_segment_text"], "1000\u5e74")
        self.assertEqual(summary["decision_details"][0]["reference_text_units"], ["se", "n", "ne", "n"])

    def test_timeline_alignment_clips_overlong_material_span_to_exact_reference_units(self) -> None:
        reference = VoiceSegment(
            0.0,
            1.0,
            "\u3055",
            language_hint="JP",
            unit_timings=(VoiceUnitTiming(0, "sa", 0.0, 1.0, timing_source="whisperx_char_alignment"),),
        )
        decision = MaterialOrderDecision(
            rank=1,
            material_path=Path("sai.wav"),
            score=1.0,
            reference_text=reference.text,
            material_text="sai",
            reason="test",
            reference_segment_index=0,
            phonetic_position=0,
            phonetic_position_count=1,
            phonetic_span_units=2,
            language_hint="JP",
        )

        targets = model_runtime._positioned_target_durations([reference], [decision])
        summary = model_runtime._render_timeline_alignment_summary([reference], [decision], targets)

        self.assertEqual(summary["timed_target_duration_count"], 1)
        self.assertEqual(summary["exact_timed_target_duration_count"], 1)
        self.assertEqual(summary["resampled_timing_lattice_count"], 0)
        self.assertEqual(summary["decision_details"][0]["position_unit_end"], 1)

    def test_japanese_lyric_text_retargets_original_aligned_unit_durations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text("\u611b\u3057\u3066\n\u3042\u3044\u3057\u3066\n", encoding="utf-8")
            transcript_segments = [
                model_runtime.TranscriptSegment(
                    0.0,
                    2.0,
                    "\u611b\u3057\u3066",
                    timing_source="whisperx_char_alignment",
                    unit_timings=(
                        VoiceUnitTiming(0, "x0", 0.0, 0.2, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(1, "x1", 0.2, 0.7, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(2, "x2", 0.7, 1.4, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(3, "x3", 1.4, 2.0, timing_source="whisperx_char_alignment"),
                    ),
                )
            ]

            with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
                segments = model_runtime._segments_from_transcript(transcript_segments, lyrics_file=lyrics)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "\u611b\u3057\u3066")
        self.assertEqual(segments[0].timing_source, "asr_segment_with_lyric_text")
        self.assertEqual([timing.unit for timing in segments[0].unit_timings], ["a", "i", "shi", "te"])
        self.assertEqual([round(timing.duration_seconds, 6) for timing in segments[0].unit_timings], [0.2, 0.5, 0.7, 0.6])

    def test_japanese_lyrics_annotations_keep_full_timing_coverage_when_units_expand(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text("\u611b\u3057\u3066\uff08\u3042\u3044\u3057\u3066\uff09 / aishite\n", encoding="utf-8")
            transcript_segments = [
                model_runtime.TranscriptSegment(
                    0.0,
                    1.0,
                    "\u611b\u3057\u3066",
                    timing_source="whisperx_char_alignment",
                    unit_timings=(
                        VoiceUnitTiming(0, "x0", 0.0, 0.3, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(1, "x1", 0.3, 1.0, timing_source="whisperx_char_alignment"),
                    ),
                )
            ]

            with patch("audio_processor.model_assist._janome_tokenizer", return_value=ModelAssistTests._fake_janome_tokenizer()):
                segments = model_runtime._segments_from_transcript(transcript_segments, lyrics_file=lyrics)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "\u611b\u3057\u3066")
        self.assertEqual([timing.unit for timing in segments[0].unit_timings], ["a", "i", "shi", "te"])
        self.assertAlmostEqual(segments[0].unit_timings[0].start_seconds, 0.0)
        self.assertAlmostEqual(segments[0].unit_timings[-1].end_seconds, 1.0)
        self.assertAlmostEqual(sum(timing.duration_seconds for timing in segments[0].unit_timings), 1.0)

    def test_lyrics_alignment_failure_does_not_fall_back_to_asr_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text("\u611b\u3057\u3066\n", encoding="utf-8")
            transcript_segments = [
                model_runtime.TranscriptSegment(
                    0.0,
                    1.0,
                    "ignored",
                    timing_source="whisperx_char_alignment",
                    unit_timings=(
                        VoiceUnitTiming(0, "x0", 0.0, 0.5, timing_source="whisperx_char_alignment"),
                        VoiceUnitTiming(1, "x1", 0.5, 1.0, timing_source="whisperx_char_alignment"),
                    ),
                )
            ]

            segments, notes = model_runtime._segments_from_transcript_with_notes(
                transcript_segments,
                lyrics_file=lyrics,
                language_hint="JP",
            )

        self.assertEqual(segments, ())
        self.assertTrue(any("refusing sequential or ASR-text fallback" in note for note in notes))

    def test_lyrics_alignment_uses_original_acoustic_timing_and_skips_residual_segments(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_CN.txt"
            lyrics.write_text("\u6211\n\u662f\n", encoding="utf-8")
            transcript_segments = [
                model_runtime.TranscriptSegment(0.0, 1.0, "\u6211", timing_source="whisperx_alignment"),
                model_runtime.TranscriptSegment(
                    1.0,
                    2.0,
                    "electric guitar solo",
                    timing_source="whisperx_alignment",
                ),
                model_runtime.TranscriptSegment(2.0, 3.0, "\u662f", timing_source="whisperx_alignment"),
            ]

            segments, notes = model_runtime._segments_from_transcript_with_notes(
                transcript_segments,
                lyrics_file=lyrics,
                language_hint="CN",
            )

        self.assertEqual([segment.text for segment in segments], ["\u6211", "\u662f"])
        self.assertEqual(
            [(segment.start_seconds, segment.end_seconds) for segment in segments],
            [(0.0, 1.0), (2.0, 3.0)],
        )
        self.assertTrue(any("skipped_acoustic_segments=1/3" in note for note in notes))

    def test_lyrics_alignment_allows_long_acoustic_segment_to_cover_many_lyric_lines(self) -> None:
        lyrics = [
            VoiceSegment(0.0, 1.0, "\u6bcf\u4e00\u6b21\u95ed\u4e0a\u4e86\u773c\u5c31\u60f3\u5230\u4e86\u4f60", language_hint="CN"),
            VoiceSegment(1.0, 2.0, "\u4f60\u50cf\u4e00\u53e5\u7f8e\u4e3d\u7684\u53e3\u53f7\u6325\u4e0d\u53bb", language_hint="CN"),
            VoiceSegment(2.0, 3.0, "\u5728\u8fd9\u6279\u5224\u6597\u4e89\u7684\u4e16\u754c\u91cc", language_hint="CN"),
            VoiceSegment(3.0, 4.0, "\u6bcf\u4e2a\u4eba\u90fd\u8981\u5b66\u4e60\u4fdd\u62a4\u81ea\u5df1", language_hint="CN"),
            VoiceSegment(4.0, 5.0, "\u8ba9\u6211\u76f8\u4fe1\u4f60\u7684\u5fe0\u8d1e", language_hint="CN"),
            VoiceSegment(5.0, 6.0, "\u7231\u4eba\u540c\u5fd7", language_hint="CN"),
            VoiceSegment(6.0, 7.0, "\u54e6\u8ba9\u6211\u76f8\u4fe1\u4f60\u7684\u5fe0\u8d1e", language_hint="CN"),
            VoiceSegment(7.0, 8.0, "\u7231\u4eba\u540c\u5fd7", language_hint="CN"),
        ]
        transcript_segments = [
            model_runtime.TranscriptSegment(
                10.0,
                34.0,
                (
                    "\u6bcf\u4e00\u6b21\u6bd4\u8f83\u4eae\u773c\u5c31\u60f3\u5230\u4e86\u4f60"
                    "\u4f60\u60f3\u4e00\u53e5\u7f8e\u4e3d\u7684\u53e3\u53f7"
                    "\u5728\u8fd9\u6279\u5224\u6597\u4e89\u7684\u4e16\u754c\u91cc"
                    "\u6bcf\u4e2a\u4eba\u90fd\u8981\u5b66\u4e60\u4fdd\u62a4\u81ea\u5df1"
                    "\u8ba9\u6211\u76f8\u4fe1\u4f60\u7684\u91cd\u75c7\u7231\u4eba\u4ece\u4e4b"
                ),
                timing_source="whisperx_char_alignment",
            ),
            model_runtime.TranscriptSegment(34.0, 38.0, "\u54e6\u54e6\u54e6", timing_source="whisperx_char_alignment"),
            model_runtime.TranscriptSegment(38.0, 38.2, "\u554a", timing_source="whisperx_char_alignment"),
        ]

        alignment = model_runtime._align_lyrics_to_transcript_segments(
            lyrics,
            transcript_segments,
            language_hint="CN",
        )

        self.assertEqual(alignment.unmatched_lyric_count, 0)
        self.assertEqual([segment.text for segment in alignment.segments], [segment.text for segment in lyrics])
        self.assertEqual(alignment.segments[0].start_seconds, 10.0)
        self.assertEqual(alignment.segments[5].end_seconds, 34.0)
        self.assertEqual(alignment.segments[-1].timing_source, "lyrics_coarse_low_confidence_window")

    def test_lyrics_alignment_spreads_low_confidence_windows_over_remaining_acoustic_segments(self) -> None:
        lyrics = [
            VoiceSegment(0.0, 1.0, f"line {index}", language_hint="JP")
            for index in range(5)
        ]
        transcript_segments = [
            model_runtime.TranscriptSegment(10.0, 20.0, "unrelated audio block"),
            model_runtime.TranscriptSegment(30.0, 40.0, "another unrelated block"),
        ]

        alignment = model_runtime._align_lyrics_to_transcript_segments(
            lyrics,
            transcript_segments,
            language_hint="JP",
        )

        self.assertEqual(alignment.unmatched_lyric_count, 0)
        self.assertEqual(alignment.skipped_acoustic_count, 0)
        self.assertEqual([segment.text for segment in alignment.segments], [segment.text for segment in lyrics])
        self.assertTrue(all(segment.timing_source == "lyrics_coarse_low_confidence_window" for segment in alignment.segments))
        self.assertTrue(all(not segment.unit_timings for segment in alignment.segments))
        self.assertLess(alignment.segments[0].start_seconds, alignment.segments[2].start_seconds)
        self.assertEqual(alignment.segments[-1].end_seconds, 40.0)

    def test_auto_asr_skips_uncached_accelerated_backends(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "input.wav"
            _write_test_wave(audio)

            with patch.dict(
                os.environ,
                {"VOCAL_PROCESS_ASR_BACKEND": "auto", "VOCAL_PROCESS_ENABLE_FUNASR_AUTO": ""},
                clear=False,
            ):
                with patch("audio_processor.model_runtime._module_available", return_value=True):
                    with patch("audio_processor.model_runtime._faster_whisper_model_cached", return_value=False):
                        with patch("audio_processor.model_runtime._whisperx_model_cached", return_value=False):
                            with patch("audio_processor.model_runtime._transcribe_with_faster_whisper") as faster_mock:
                                with patch("audio_processor.model_runtime._load_whisperx_model") as whisperx_mock:
                                    with patch(
                                        "audio_processor.model_runtime._transcribe_with_whisper",
                                        return_value={
                                            "backend": "whisper",
                                            "text": "alpha",
                                            "segments": [],
                                            "notes": ["fallback"],
                                        },
                                    ) as whisper_mock:
                                        result = model_runtime._transcribe_audio(audio, compute_device="cpu")

        self.assertEqual(result["backend"], "whisper")
        faster_mock.assert_not_called()
        whisperx_mock.assert_not_called()
        whisper_mock.assert_called_once()

    def test_torch_availability_requires_native_extension(self) -> None:
        model_runtime._module_status.cache_clear()

        def fake_import(module_name: str) -> object:
            if module_name == "torch":
                return object()
            if module_name == "torch._C":
                raise ModuleNotFoundError("No module named 'torch._C'")
            return object()

        with patch("audio_processor.model_runtime._prepare_native_dependency_paths"):
            with patch("audio_processor.model_runtime.importlib.import_module", side_effect=fake_import):
                available = model_runtime._module_available("torch")
                reason = model_runtime._module_unavailable_reason("torch")

        self.assertFalse(available)
        self.assertIn("torch._C", reason)
        self.assertIn("full portable package", reason)

    def test_speech_runtime_issue_reports_incomplete_torch(self) -> None:
        with patch("audio_processor.model_runtime._module_available", return_value=False):
            with patch(
                "audio_processor.model_runtime._module_unavailable_reason",
                return_value="ModuleNotFoundError: No module named 'torch._C'",
            ):
                issue = model_runtime._speech_runtime_issue("auto", allow_model_download=False)

        self.assertIn("Speech recognition runtime is unavailable", issue)
        self.assertIn("torch._C", issue)
        self.assertIn("full portable package", issue)

    def test_torch_safe_globals_allow_omegaconf_checkpoint_containers(self) -> None:
        class Container:
            pass

        class ContainerMetadata:
            pass

        class Metadata:
            pass

        class Node:
            pass

        class DictConfig:
            pass

        class ListConfig:
            pass

        class AnyNode:
            pass

        class BooleanNode:
            pass

        class FloatNode:
            pass

        class IntegerNode:
            pass

        class StringNode:
            pass

        class TorchVersion:
            pass

        class Introspection:
            pass

        class Output:
            pass

        class Problem:
            pass

        class Resolution:
            pass

        class Specifications:
            pass

        class Segment:
            pass

        class SlidingWindow:
            pass

        added: list[object] = []
        modules = {
            "torch.serialization": SimpleNamespace(add_safe_globals=lambda values: added.extend(values)),
            "torch.torch_version": SimpleNamespace(TorchVersion=TorchVersion),
            "omegaconf.base": SimpleNamespace(
                Container=Container,
                ContainerMetadata=ContainerMetadata,
                Metadata=Metadata,
                Node=Node,
            ),
            "omegaconf.dictconfig": SimpleNamespace(DictConfig=DictConfig),
            "omegaconf.listconfig": SimpleNamespace(ListConfig=ListConfig),
            "omegaconf.nodes": SimpleNamespace(
                AnyNode=AnyNode,
                BooleanNode=BooleanNode,
                FloatNode=FloatNode,
                IntegerNode=IntegerNode,
                StringNode=StringNode,
            ),
            "pyannote.audio.core.model": SimpleNamespace(Introspection=Introspection, Output=Output),
            "pyannote.audio.core.task": SimpleNamespace(
                Problem=Problem,
                Resolution=Resolution,
                Specifications=Specifications,
            ),
            "pyannote.core.segment": SimpleNamespace(Segment=Segment, SlidingWindow=SlidingWindow),
        }

        with patch("audio_processor.model_runtime.importlib.import_module", side_effect=modules.__getitem__):
            model_runtime._prepare_torch_weights_safe_globals()

        self.assertIn(model_runtime.Any, added)
        self.assertIn(list, added)
        self.assertIn(dict, added)
        self.assertIn(model_runtime.defaultdict, added)
        self.assertIn(model_runtime.Counter, added)
        self.assertIn(int, added)
        self.assertIn(str, added)
        self.assertIn(TorchVersion, added)
        self.assertIn(ListConfig, added)
        self.assertIn(DictConfig, added)
        self.assertIn(AnyNode, added)
        self.assertIn(Introspection, added)
        self.assertIn(Specifications, added)
        self.assertIn(SlidingWindow, added)

    def test_speechbrain_lazy_modules_do_not_load_optional_integrations_for_file_attr(self) -> None:
        class FakeLazyModule:
            lazy_module = None

            def ensure_module(self, stacklevel: int) -> object:
                raise ImportError("optional integration should not load")

            def __getattr__(self, attr: str) -> object:
                return self.ensure_module(1)

        module = SimpleNamespace(LazyModule=FakeLazyModule)
        model_runtime._prepare_speechbrain_lazy_import_compat.cache_clear()
        with patch("audio_processor.model_runtime.importlib.import_module", return_value=module):
            model_runtime._prepare_speechbrain_lazy_import_compat()

        lazy = FakeLazyModule()
        self.assertFalse(hasattr(lazy, "__file__"))
        with self.assertRaisesRegex(ImportError, "optional integration"):
            getattr(lazy, "k2")

    def test_ffmpeg_start_failure_message_points_to_portable_bin(self) -> None:
        with patch(
            "audio_processor.model_runtime.ensure_runtime_tool_paths",
            return_value=[Path("VocalProcess") / "bin"],
        ):
            message = model_runtime._ffmpeg_start_failure_message("Whisper", Path("song.wav"))

        self.assertIn("FFmpeg executable could not be started", message)
        self.assertIn("bin\\ffmpeg.exe", message)
        self.assertIn("VocalProcess", message)

    def test_prepare_text_output_encoding_sets_utf8_streams(self) -> None:
        class FakeStream:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def reconfigure(self, **kwargs: object) -> None:
                self.calls.append(kwargs)

        stdout = FakeStream()
        stderr = FakeStream()

        with patch.object(sys, "stdout", stdout):
            with patch.object(sys, "stderr", stderr):
                with patch.dict(os.environ, {}, clear=True):
                    model_runtime._prepare_text_output_encoding()
                    self.assertEqual(os.environ["PYTHONIOENCODING"], "utf-8")
                    self.assertEqual(os.environ["PYTHONUTF8"], "1")

        self.assertEqual(stdout.calls, [{"encoding": "utf-8", "errors": "replace"}])
        self.assertEqual(stderr.calls, [{"encoding": "utf-8", "errors": "replace"}])

    def test_whisperx_model_file_not_found_is_not_reported_as_ffmpeg_missing(self) -> None:
        class HubFileNotFoundError(FileNotFoundError):
            pass

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "input.wav"
            _write_test_wave(audio)
            hub_error = HubFileNotFoundError(
                "An error happened while trying to locate the file on the Hub and we cannot find "
                "the requested files in the local cache."
            )

            with patch.dict(
                os.environ,
                {"VOCAL_PROCESS_ASR_BACKEND": "whisperx", "VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD": "1"},
                clear=False,
            ):
                with patch.dict(sys.modules, {"whisperx": SimpleNamespace()}, clear=False):
                    with patch("audio_processor.model_runtime.ensure_runtime_tool_paths", return_value=[]):
                        with patch("audio_processor.model_runtime._ensure_model_download_tls", return_value=None):
                            with patch("audio_processor.model_runtime._speech_runtime_issue", return_value=""):
                                with patch("audio_processor.model_runtime._module_available", return_value=True):
                                    with patch("audio_processor.model_runtime._prepare_torchaudio_legacy_api"):
                                        with patch(
                                            "audio_processor.model_runtime._load_whisperx_model",
                                            side_effect=hub_error,
                                        ):
                                            with self.assertRaises(AudioProcessorError) as raised:
                                                model_runtime._transcribe_audio(audio, compute_device="cpu")

        message = str(raised.exception)
        self.assertIn("WhisperX transcription failed", message)
        self.assertIn("HubFileNotFoundError", message)
        self.assertIn("requested files in the local cache", message)
        self.assertNotIn("FFmpeg executable could not be started", message)


class DawRenderReuseTests(unittest.TestCase):
    def test_daw_export_reuses_duplicate_stretched_clip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.wav"
            material_dir = root / "materials"
            material_dir.mkdir()
            material = material_dir / "same.wav"
            project = root / "out" / "song.rpp"
            _write_test_wave(reference, duration_seconds=2.0)
            _write_test_wave(material, duration_seconds=1.0)

            def fake_render(
                input_path: Path,
                output_path: Path,
                tempo: float,
                options: ProcessOptions,
                *,
                target_duration: float | None = None,
                audible_target_duration: float | None = None,
                pre_silence_seconds: float = 0.0,
                source_window_start_seconds: float = 0.0,
                source_window_duration_seconds: float | None = None,
                text_hint: str = "",
                on_progress: object | None = None,
                should_cancel: object | None = None,
            ) -> None:
                _write_test_wave(output_path, duration_seconds=target_duration or 1.0)

            with patch("audio_processor.daw.process_material_clip_with_progress", side_effect=fake_render) as render_mock:
                result = export_daw_timeline_with_progress(
                    reference,
                    material_dir,
                    project,
                    ProcessOptions(input_path=reference, output_path=project, overwrite=True),
                    material_paths=[material, material],
                    target_durations=[1.0, 1.0],
                )

                self.assertEqual(render_mock.call_count, 1)
                self.assertEqual(len(result.clips), 2)
                self.assertTrue(result.clips[0].rendered_path.exists())
                self.assertTrue(result.clips[1].rendered_path.exists())


class Vst3BridgeTests(unittest.TestCase):
    def test_bridge_template_is_serializable(self) -> None:
        template = bridge_request_template()

        self.assertEqual(template["format"], BRIDGE_REQUEST_FORMAT)
        json.dumps(template, ensure_ascii=False)

    def test_bridge_request_returns_batch_response(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            progress_path = root / "song.progress.json"
            request = {
                "format": BRIDGE_REQUEST_FORMAT,
                "command": "render_timeline",
                "reference_path": "reference.wav",
                "material_directory": "materials",
                "output_path": str(root / "song.rpp"),
                "progress_path": str(progress_path),
                "compute_device": "cpu",
                "overwrite": True,
            }

            def fake_run_batch(items: list[object], settings: ProcessingSettings, **kwargs: object) -> BatchSummary:
                item = items[0]
                on_queue_progress = kwargs.get("on_queue_progress")
                if callable(on_queue_progress):
                    on_queue_progress(0.5, "half done")
                item.status = "Done"
                item.message = "Complete"
                item.progress = 1.0
                self.assertTrue(settings.daw_timeline_export)
                self.assertEqual(settings.compute_device, "cpu")
                return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

            with patch("audio_processor.vst3_bridge.run_batch_queue", side_effect=fake_run_batch):
                response = run_bridge_request(request)

            progress = json.loads(progress_path.read_text(encoding="utf-8"))

        self.assertEqual(response["format"], BRIDGE_RESPONSE_FORMAT)
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "Done")
        self.assertEqual(progress["format"], "vocal_process_vst3_bridge_progress_v1")
        self.assertTrue(progress["done"])

    def test_bridge_request_enables_manual_lyrics_when_file_is_present(self) -> None:
        request = {
            "format": BRIDGE_REQUEST_FORMAT,
            "command": "render",
            "reference_path": "reference.wav",
            "material_directory": "materials",
            "output_path": "out/song.wav",
            "lyrics_file": "lyrics.txt",
            "overwrite": True,
        }
        captured: dict[str, ProcessingSettings] = {}

        def fake_run_batch(items: list[object], settings: ProcessingSettings, **kwargs: object) -> BatchSummary:
            del items, kwargs
            captured["settings"] = settings
            return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

        with patch("audio_processor.vst3_bridge.run_batch_queue", side_effect=fake_run_batch):
            response = run_bridge_request(request)

        self.assertTrue(response["ok"])
        self.assertTrue(captured["settings"].manual_lyrics_enabled)
        self.assertEqual(captured["settings"].effective_lyrics_file(), "lyrics.txt")

    def test_bridge_request_file_writes_response_next_to_request(self) -> None:
        request = {
            "format": BRIDGE_REQUEST_FORMAT,
            "request_id": "bridge-test-002",
            "command": "render_timeline",
            "reference_path": "reference.wav",
            "material_directory": "materials",
            "output_path": "out/song.rpp",
            "compute_device": "cpu",
            "overwrite": True,
        }

        def fake_run_batch(items: list[object], settings: ProcessingSettings, **kwargs: object) -> BatchSummary:
            item = items[0]
            item.status = "Done"
            item.message = "Complete"
            return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_path = root / "bridge-test-002.request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")

            with patch("audio_processor.vst3_bridge.run_batch_queue", side_effect=fake_run_batch):
                response = run_bridge_request_file(request_path)

            response_path = root / "bridge-test-002.response.json"
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            self.assertTrue(response_path.exists())

        self.assertEqual(payload["request_id"], "bridge-test-002")
        self.assertEqual(response["request_id"], "bridge-test-002")

    def test_bridge_watch_processes_request_files(self) -> None:
        request = {
            "format": BRIDGE_REQUEST_FORMAT,
            "request_id": "bridge-test-001",
            "command": "render_timeline",
            "reference_path": "reference.wav",
            "material_directory": "materials",
            "output_path": "out/song.rpp",
            "compute_device": "cpu",
            "overwrite": True,
        }

        def fake_run_batch(items: list[object], settings: ProcessingSettings, **kwargs: object) -> BatchSummary:
            item = items[0]
            item.status = "Done"
            item.message = "Complete"
            self.assertTrue(settings.daw_timeline_export)
            self.assertEqual(settings.compute_device, "cpu")
            return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_dir = root / "requests"
            response_dir = root / "responses"
            request_dir.mkdir()
            response_dir.mkdir()
            request_path = request_dir / "bridge-test-001.request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")

            with patch("audio_processor.vst3_bridge.run_batch_queue", side_effect=fake_run_batch):
                processed = run_bridge_watch(request_dir, response_directory=response_dir, once=True)

            response_path = response_dir / "bridge-test-001.response.json"
            done_path = request_dir / "bridge-test-001.done.json"
            heartbeat_path = response_dir / "bridge.heartbeat.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))

            self.assertTrue(done_path.exists())
            self.assertTrue(heartbeat_path.exists())

        self.assertEqual(processed, 1)
        self.assertTrue(response["ok"])
        self.assertEqual(response["request_id"], "bridge-test-001")

    def test_bridge_watch_contract_is_serializable(self) -> None:
        contract = bridge_watch_contract()

        self.assertEqual(contract["format"], "vocal_process_vst3_bridge_contract_v1")
        json.dumps(contract, ensure_ascii=False)

    def test_analyze_cli_writes_json_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "analysis.json"

            with patch(
                "audio_processor.cli.build_preflight_report",
                return_value={
                    "format": "vocal_process_preflight_analysis_v1",
                    "status": "ok",
                },
            ):
                code = cli_main([
                    "analyze",
                    "reference.wav",
                    "materials",
                    "--output",
                    str(output),
                ])

            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["format"], "vocal_process_preflight_analysis_v1")


if __name__ == "__main__":
    unittest.main()

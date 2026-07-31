from __future__ import annotations

import json
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
from audio_processor import maintenance
from audio_processor import real_eval
from audio_processor import tls
from audio_processor.batch import BatchSummary, create_queue, run_batch_queue
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
    probe_audio,
    render_material_stretch_plan,
    resolve_tool,
    summarize_probe,
    _run_progress_process,
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


def _write_test_wave(path: Path, *, duration_seconds: float = 1.0, sample_rate: int = 8000) -> None:
    n_channels = 1
    sample_width = 2
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(n_channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n_frames)


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
        self.assertTrue(graph.startswith("[0:a]rubberband=tempo=0.50000000"))

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
        self.assertIn("rubberband=tempo=1.50000000:pitch=1:formant=preserved", filters)
        self.assertIn("apad=whole_dur=2.000000", filters)
        self.assertIn("atrim=duration=2.000000", filters)
        self.assertIn("afade=t=in:st=0:d=0.010000", filters)
        self.assertIn("afade=t=out:st=1.990000:d=0.010000", filters)
        self.assertNotIn("stream_loop", args)

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
        self.assertNotIn("rubberband=", filters)
        self.assertIn("apad=whole_dur=0.014074", filters)
        self.assertIn("atrim=duration=0.014074", filters)
        self.assertNotIn("afade=", filters)

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
        self.assertIn("[0:a]rubberband=tempo=0.50000000:pitch=1:formant=preserved", filters)
        self.assertIn("[1:a]rubberband=tempo=1.50000000:pitch=1:formant=preserved", filters)
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
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["formant_preservation"], "direct_trim_no_pitch_shift")

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

    def test_short_material_expansion_uses_syllable_safe_tail_padding(self) -> None:
        with patch(
            "audio_processor.engine.probe_audio",
            side_effect=[
                {"format": {"duration": "2.0"}, "streams": []},
                {"format": {"duration": "0.5"}, "streams": []},
            ],
        ):
            clips = plan_material_stretch_clips(
                Path("reference.wav"),
                [Path("shi.wav")],
                material_text_hints=["是"],
            )

        self.assertAlmostEqual(clips[0].requested_tempo or 0.0, 0.25)
        self.assertAlmostEqual(clips[0].tempo, 0.35)
        self.assertEqual(clips[0].stretch_strategy, "syllable_formant_expand_with_tail_fill")
        self.assertEqual(clips[0].quality_warning, "extreme_stretch_ratio")
        self.assertEqual(clips[0].continuity_warning, "single_syllable_boundary_risk")
        self.assertLess(clips[0].stretch_naturalness_score, 0.2)
        rendered = render_material_stretch_plan(clips)
        self.assertEqual(rendered[0]["boundary_conditioning"], "fade_in_out")
        self.assertEqual(rendered[0]["formant_preservation"], "rubberband_formant_preserved")

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


class SettingsTests(unittest.TestCase):
    def test_default_output_extension_is_wav(self) -> None:
        self.assertEqual(ProcessingSettings().output_extension, ".wav")

    def test_settings_round_trip(self) -> None:
        settings = ProcessingSettings(
            language="en",
            material_directory="materials",
            lyrics_file="lyrics.txt",
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
        self.assertEqual(loaded.lyrics_file, "lyrics.txt")
        self.assertTrue(loaded.daw_timeline_export)
        self.assertEqual(loaded.source_separation, "never")
        self.assertEqual(loaded.output_directory, "out")
        self.assertFalse(loaded.overwrite)
        self.assertEqual(loaded.gain_db, -2.5)
        self.assertTrue(loaded.normalize)
        self.assertEqual(loaded.sample_rate, 48000)
        self.assertEqual(loaded.channels, 1)

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
                "--overwrite",
            ]
        )

        self.assertEqual(args.command, "batch")
        self.assertEqual(args.reference, Path("reference.wav"))
        self.assertEqual(args.output, Path("out.wav"))
        self.assertEqual(args.material_directory, Path("materials"))
        self.assertEqual(args.lyrics_file, Path("lyrics.txt"))
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
            ):
                with patch("audio_processor.model_runtime._detect_vad_segments", return_value=()):
                    with patch("audio_processor.model_runtime._speaker_embedding", return_value=None):
                        library = model_runtime.analyze_material_library(material_dir)

        self.assertEqual(library.materials[0].transcript, "ha")
        self.assertEqual(library.materials[0].segments[0].text, "ha")
        self.assertTrue(any("material_filename_label_authority" in note for note in library.materials[0].notes))

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
        self.assertEqual(model_assist._phonetic_units("suupaa", language_hint="JP"), ["su", "pa"])
        self.assertEqual(model_assist._phonetic_units("gakkou", language_hint="JP"), ["ga", "ko"])
        self.assertEqual(model_assist._phonetic_units("aishite", language_hint="JP"), ["a", "i", "shi", "te"])
        self.assertEqual(model_assist._phonetic_units("PlasticLove", language_hint="JP"), ["plasticlove"])

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
            ):
                report = preflight.build_preflight_report(Path("reference.wav"), Path("materials"))

        self.assertEqual(report["summary"]["continuity_warning_count"], 1)
        self.assertEqual(report["summary"]["fade_applied_clip_count"], 1)
        self.assertLess(report["summary"]["stretch_naturalness_score_mean"] or 1.0, 0.2)
        self.assertEqual(report["stretch_plan"][0]["continuity_warning"], "single_syllable_boundary_risk")
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

    def test_japanese_lyric_text_retargets_original_aligned_unit_durations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lyrics = root / "song_JP.txt"
            lyrics.write_text("\u611b\u3057\u3066\n\u3042\u3044\u3057\u3066\n", encoding="utf-8")
            transcript_segments = [
                model_runtime.TranscriptSegment(
                    0.0,
                    2.0,
                    "ignored",
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
                    "ignored",
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

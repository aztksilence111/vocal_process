from __future__ import annotations

import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from audio_processor import model_runtime
from audio_processor.batch import BatchSummary, create_queue, run_batch_queue
from audio_processor.cli import build_parser
from audio_processor.diagnostics import DiagnosticLogger, diagnostic_log_path
from audio_processor.daw import (
    DawExportResult,
    DawTimelineClip,
    DawTimelinePlan,
    plan_daw_timeline,
    render_reaper_project,
)
from audio_processor.engine import (
    AudioProcessorError,
    ProcessOptions,
    _build_material_filter_graph,
    build_material_assembly_args,
    build_material_clip_args,
    build_process_args,
    get_audio_duration_seconds,
    list_audio_files,
    plan_material_stretch_clips,
    probe_audio,
    resolve_tool,
    summarize_probe,
)
from audio_processor.gui import LYRICS_EXTENSIONS
from audio_processor.i18n import TRANSLATIONS, normalize_language, translate, translate_status
from audio_processor.model_assist import (
    MaterialAnalysis,
    VoiceSegment,
    build_model_assisted_pipeline_plan,
    list_model_candidates,
    order_materials_for_reference,
    text_similarity,
)
from audio_processor.settings import ProcessingSettings, load_settings, save_settings
from audio_processor.vst3_bridge import (
    BRIDGE_REQUEST_FORMAT,
    BRIDGE_RESPONSE_FORMAT,
    bridge_request_template,
    run_bridge_request,
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
        self.assertNotIn("stream_loop", graph)
        self.assertNotIn("atrim", graph)

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
        self.assertNotIn("atrim", filters)
        self.assertNotIn("stream_loop", args)

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
        self.assertNotIn("atrim", filters)
        self.assertNotIn("stream_loop", filters)

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


class SettingsTests(unittest.TestCase):
    def test_default_output_extension_is_wav(self) -> None:
        self.assertEqual(ProcessingSettings().output_extension, ".wav")

    def test_settings_round_trip(self) -> None:
        settings = ProcessingSettings(
            language="en",
            material_directory="materials",
            lyrics_file="lyrics.txt",
            daw_timeline_export=True,
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


class CliTests(unittest.TestCase):
    def test_gui_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["gui"])

        self.assertEqual(args.command, "gui")

    def test_export_daw_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["export-daw", "reference.wav", "materials", "song.rpp"])

        self.assertEqual(args.command, "export-daw")
        self.assertEqual(args.reference, Path("reference.wav"))

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

    def test_vst3_bridge_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["vst3-bridge", "request.json", "--response", "response.json"])

        self.assertEqual(args.command, "vst3-bridge")
        self.assertEqual(args.request, Path("request.json"))
        self.assertEqual(args.response, Path("response.json"))


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
    def test_model_candidates_cover_required_pipeline_stages(self) -> None:
        stages = {candidate["pipeline_stage"] for candidate in list_model_candidates()}

        self.assertIn("source_separation", stages)
        self.assertIn("voice_activity_detection", stages)
        self.assertIn("asr_alignment", stages)
        self.assertIn("speaker_similarity", stages)

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


class ModelRuntimeTests(unittest.TestCase):
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

    def test_model_cache_snapshot_reuses_matching_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_dir = root / "materials"
            material_dir.mkdir()
            material_path = material_dir / "001.wav"
            _write_test_wave(material_path)
            cache_path = material_dir / ".vocalprocess_material_cache.json"
            snapshot = model_runtime._material_snapshot([material_path])
            cache_path.write_text(
                json.dumps(
                    {
                        "format": "vocal_process_material_cache_v1",
                        "asr_model": model_runtime.DEFAULT_ASR_MODEL,
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


class Vst3BridgeTests(unittest.TestCase):
    def test_bridge_template_is_serializable(self) -> None:
        template = bridge_request_template()

        self.assertEqual(template["format"], BRIDGE_REQUEST_FORMAT)
        json.dumps(template, ensure_ascii=False)

    def test_bridge_request_returns_batch_response(self) -> None:
        request = {
            "format": BRIDGE_REQUEST_FORMAT,
            "command": "render_timeline",
            "reference_path": "reference.wav",
            "material_directory": "materials",
            "output_path": "out/song.rpp",
            "compute_device": "cpu",
            "overwrite": True,
        }

        def fake_run_batch(items: list[object], settings: ProcessingSettings) -> BatchSummary:
            item = items[0]
            item.status = "Done"
            item.message = "Complete"
            self.assertTrue(settings.daw_timeline_export)
            self.assertEqual(settings.compute_device, "cpu")
            return BatchSummary(total=1, completed=1, failed=0, cancelled=0)

        with patch("audio_processor.vst3_bridge.run_batch_queue", side_effect=fake_run_batch):
            response = run_bridge_request(request)

        self.assertEqual(response["format"], BRIDGE_RESPONSE_FORMAT)
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "Done")


if __name__ == "__main__":
    unittest.main()

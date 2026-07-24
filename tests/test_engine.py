from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from audio_processor.batch import create_queue
from audio_processor.cli import build_parser
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
    build_material_clip_args,
    build_process_args,
    get_audio_duration_seconds,
    list_audio_files,
    probe_audio,
    resolve_tool,
    summarize_probe,
)
from audio_processor.gui import LYRICS_EXTENSIONS
from audio_processor.i18n import TRANSLATIONS, normalize_language, translate, translate_status
from audio_processor.settings import ProcessingSettings, load_settings, save_settings


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


class DawTimelineTests(unittest.TestCase):
    def test_plans_separate_daw_clips_on_reference_timeline(self) -> None:
        reference = Path("reference.wav")
        materials = [Path("001.wav"), Path("002.wav")]

        with patch(
            "audio_processor.daw.probe_audio",
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
        self.assertEqual([clip.rendered_path.name for clip in plan.clips], ["0001_001.wav", "0002_002.wav"])

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


class CliTests(unittest.TestCase):
    def test_gui_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["gui"])

        self.assertEqual(args.command, "gui")

    def test_export_daw_subcommand_is_registered(self) -> None:
        args = build_parser().parse_args(["export-daw", "reference.wav", "materials", "song.rpp"])

        self.assertEqual(args.command, "export-daw")
        self.assertEqual(args.reference, Path("reference.wav"))


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


if __name__ == "__main__":
    unittest.main()

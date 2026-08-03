from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np

from audio_processor.signalsmith_stretch import (
    render_signalsmith_regions,
    signalsmith_stretch_available,
    stretch_channel_first_to_frames,
)


class SignalsmithStretchTests(unittest.TestCase):
    @unittest.skipUnless(signalsmith_stretch_available(), "python-stretch is unavailable")
    def test_channel_first_stretch_returns_exact_requested_frames(self) -> None:
        sample_rate = 16_000
        source_frames = sample_rate // 2
        time_axis = np.arange(source_frames, dtype=np.float32) / sample_rate
        source = (0.2 * np.sin(2 * np.pi * 220 * time_axis))[None, :]

        rendered = stretch_channel_first_to_frames(
            source,
            sample_rate=sample_rate,
            target_frames=sample_rate * 2,
        )

        self.assertEqual(rendered.shape, (1, sample_rate * 2))
        self.assertGreater(float(np.sqrt(np.mean(rendered**2))), 0.02)

    @unittest.skipUnless(signalsmith_stretch_available(), "python-stretch is unavailable")
    def test_region_renderer_preserves_requested_duration(self) -> None:
        import soundfile as sf

        sample_rate = 16_000
        source_frames = sample_rate // 2
        time_axis = np.arange(source_frames, dtype=np.float32) / sample_rate
        source = (0.2 * np.sin(2 * np.pi * 220 * time_axis)).astype(np.float32)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.wav"
            output_path = root / "rendered.wav"
            sf.write(source_path, source, sample_rate, subtype="PCM_16")
            region = SimpleNamespace(
                kind="vowel_core",
                source_start_seconds=0.0,
                source_end_seconds=0.5,
                target_duration_seconds=2.0,
            )

            render_signalsmith_regions(
                source_path,
                output_path,
                [region],
                target_duration_seconds=2.0,
            )

            rendered, rendered_rate = sf.read(output_path, dtype="float32")

        self.assertEqual(rendered_rate, sample_rate)
        self.assertEqual(len(rendered), sample_rate * 2)
        self.assertGreater(float(np.sqrt(np.mean(rendered**2))), 0.02)

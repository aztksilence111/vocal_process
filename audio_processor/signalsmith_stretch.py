from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, Sequence


class SignalsmithStretchError(RuntimeError):
    """Raised when the optional Signalsmith PCM stretch backend cannot render."""


class StretchRegion(Protocol):
    kind: str
    source_start_seconds: float
    source_end_seconds: float
    target_duration_seconds: float


CancelCallback = Callable[[], bool]


def signalsmith_stretch_available() -> bool:
    try:
        _load_runtime()
    except SignalsmithStretchError:
        return False
    return True


def render_signalsmith_regions(
    input_path: Path,
    output_path: Path,
    regions: Sequence[StretchRegion],
    *,
    target_duration_seconds: float,
    should_cancel: CancelCallback | None = None,
) -> int:
    """Render source regions to exact target length using Signalsmith Stretch."""
    if target_duration_seconds <= 0:
        raise SignalsmithStretchError("Target duration must be greater than 0")
    if not regions:
        raise SignalsmithStretchError("Signalsmith stretch requires at least one region")

    np, soundfile, stretch_type = _load_runtime()
    try:
        source, sample_rate = soundfile.read(
            str(input_path),
            dtype="float32",
            always_2d=True,
        )
    except Exception as exc:
        raise SignalsmithStretchError(f"Could not decode audio for Signalsmith stretch: {exc}") from exc

    if source.size == 0 or sample_rate <= 0:
        raise SignalsmithStretchError("Decoded audio is empty")

    channels = int(source.shape[1])
    source_frames = int(source.shape[0])
    source_channels = np.ascontiguousarray(source.T, dtype=np.float32)
    rendered_parts = []
    for region in regions:
        _raise_if_cancelled(should_cancel)
        start_frame = _seconds_to_frame(region.source_start_seconds, sample_rate, source_frames)
        end_frame = _seconds_to_frame(region.source_end_seconds, sample_rate, source_frames)
        end_frame = max(end_frame, min(start_frame + 1, source_frames))
        target_frames = max(1, int(round(region.target_duration_seconds * sample_rate)))
        segment = source_channels[:, start_frame:end_frame]
        if region.kind.startswith("hard_silence"):
            rendered = np.zeros((channels, target_frames), dtype=np.float32)
        else:
            rendered = _stretch_segment(
                segment,
                target_frames,
                sample_rate,
                stretch_type,
                np,
            )
        rendered_parts.append(_fit_exact_frames(rendered, target_frames, np))

    target_frames = max(1, int(round(target_duration_seconds * sample_rate)))
    rendered_audio = np.concatenate(rendered_parts, axis=1)
    rendered_audio = _fit_exact_frames(rendered_audio, target_frames, np)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        soundfile.write(
            str(output_path),
            np.ascontiguousarray(rendered_audio.T, dtype=np.float32),
            sample_rate,
            subtype="PCM_24",
        )
    except Exception as exc:
        raise SignalsmithStretchError(f"Could not write Signalsmith output: {exc}") from exc
    return int(sample_rate)


def stretch_channel_first_to_frames(
    source,
    *,
    sample_rate: int,
    target_frames: int,
):
    """Stretch a channel-first float32 NumPy buffer to a precise frame count."""
    np, _, stretch_type = _load_runtime()
    channels = int(source.shape[0]) if getattr(source, "ndim", 0) == 2 else 0
    if channels <= 0:
        raise SignalsmithStretchError("Signalsmith input must be a non-empty channel-first matrix")
    source_frames = int(source.shape[1])
    if source_frames <= 0 or target_frames <= 0:
        raise SignalsmithStretchError("Signalsmith input and output frame counts must be greater than 0")
    return _stretch_segment(
        np.ascontiguousarray(source, dtype=np.float32),
        target_frames,
        sample_rate,
        stretch_type,
        np,
    )


def _load_runtime():
    try:
        import numpy as np  # type: ignore
        import soundfile  # type: ignore
        from python_stretch import Signalsmith  # type: ignore
    except Exception as exc:
        raise SignalsmithStretchError(
            "Signalsmith Stretch runtime is unavailable; install python-stretch to enable it"
        ) from exc
    return np, soundfile, Signalsmith.Stretch


def _stretch_segment(source, target_frames: int, sample_rate: int, stretch_type, np):
    source_frames = int(source.shape[1])
    if source_frames == target_frames:
        return source.copy()
    if source_frames < 2:
        return _fit_exact_frames(source, target_frames, np)

    # The binding uses input_frames / output_frames: values below one lengthen audio.
    time_factor = source_frames / float(target_frames)
    try:
        stretch = stretch_type(0)
        stretch.preset(int(source.shape[0]), max(int(sample_rate), 1))
        stretch.timeFactor = time_factor
        rendered = stretch.process(source)
    except Exception as exc:
        raise SignalsmithStretchError(f"Signalsmith stretch processing failed: {exc}") from exc
    return _fit_exact_frames(np.asarray(rendered, dtype=np.float32), target_frames, np)


def _fit_exact_frames(source, target_frames: int, np):
    current_frames = int(source.shape[1])
    if current_frames == target_frames:
        return source
    if current_frames > target_frames:
        return source[:, :target_frames]
    padding = np.zeros((int(source.shape[0]), target_frames - current_frames), dtype=np.float32)
    return np.concatenate((source, padding), axis=1)


def _seconds_to_frame(seconds: float, sample_rate: int, total_frames: int) -> int:
    return min(max(int(round(max(seconds, 0.0) * sample_rate)), 0), total_frames)


def _raise_if_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise SignalsmithStretchError("Processing cancelled")

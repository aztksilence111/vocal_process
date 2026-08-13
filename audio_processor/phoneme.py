from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class VowelConsonantProfile:
    vowel_start_seconds: float
    vowel_end_seconds: float
    detection_source: str
    confidence: float
    voiced_ratio: float


def analyze_vowel_consonant_profile(
    path: Path,
    *,
    duration_seconds: float,
    text_hint: str = "",
) -> VowelConsonantProfile:
    expanded = path.expanduser()
    try:
        stat = expanded.stat()
    except OSError:
        return _text_fallback_profile(duration_seconds, text_hint)
    return _analyze_cached(
        str(expanded.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
        max(float(duration_seconds), 0.0),
        str(text_hint or ""),
    )


@lru_cache(maxsize=512)
def _analyze_cached(
    path: str,
    size: int,
    mtime_ns: int,
    duration_seconds: float,
    text_hint: str,
) -> VowelConsonantProfile:
    del size, mtime_ns
    fallback = _text_fallback_profile(duration_seconds, text_hint)
    if duration_seconds <= 0:
        return fallback

    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return fallback

    try:
        waveform, sample_rate = librosa.load(path, sr=16000, mono=True)
        if waveform.size < 256:
            return fallback
        hop_length = 128
        frame_length = 1024
        rms = librosa.feature.rms(
            y=waveform,
            frame_length=frame_length,
            hop_length=hop_length,
            center=True,
        )[0]
        f0, voiced_flag, voiced_probability = librosa.pyin(
            waveform,
            fmin=65.0,
            fmax=500.0,
            sr=sample_rate,
            frame_length=frame_length,
            hop_length=hop_length,
            fill_na=np.nan,
        )
        if f0 is None or len(f0) == 0:
            return fallback

        rms_threshold = max(
            float(np.max(rms)) * 0.08,
            float(np.percentile(rms, 35)) * 0.75,
            1e-5,
        )
        voiced = np.isfinite(f0) & (rms >= rms_threshold)
        if voiced_flag is not None:
            voiced &= voiced_flag
        if voiced_probability is not None:
            voiced &= voiced_probability >= 0.30

        run_start, run_end = _longest_true_run(voiced, max_gap_frames=2)
        run_length = max(run_end - run_start, 0)
        if run_length < max(3, int(len(voiced) * 0.08)):
            return fallback

        frame_times = librosa.frames_to_time(
            range(len(voiced)),
            sr=sample_rate,
            hop_length=hop_length,
        )
        half_frame = frame_length / sample_rate / 2.0
        acoustic_start = max(float(frame_times[run_start]) - half_frame, 0.0)
        acoustic_end = min(float(frame_times[min(run_end - 1, len(frame_times) - 1)]) + half_frame, duration_seconds)

        # Filename text identifies the syllable, but it does not provide a
        # reliable time alignment. Use the acoustic voiced span directly;
        # the text-derived span remains the fallback when F0 evidence fails.
        vowel_start = acoustic_start
        vowel_end = acoustic_end
        minimum_vowel = min(0.030, duration_seconds * 0.5)
        if vowel_end - vowel_start < minimum_vowel:
            return fallback

        voiced_ratio = run_length / max(len(voiced), 1)
        confidence = min(0.98, max(0.35, voiced_ratio + 0.25))
        return VowelConsonantProfile(
            vowel_start_seconds=vowel_start,
            vowel_end_seconds=vowel_end,
            detection_source="librosa_voiced_f0",
            confidence=confidence,
            voiced_ratio=voiced_ratio,
        )
    except Exception:
        return fallback


def _longest_true_run(values: object, *, max_gap_frames: int) -> tuple[int, int]:
    run_start = -1
    best_start = 0
    best_end = 0
    gap = 0
    for index, value in enumerate(values):  # type: ignore[union-attr]
        if bool(value):
            if run_start < 0:
                run_start = index
            gap = 0
            continue
        if run_start < 0:
            continue
        gap += 1
        if gap > max_gap_frames:
            end = index - gap + 1
            if end - run_start > best_end - best_start:
                best_start, best_end = run_start, end
            run_start = -1
            gap = 0
    if run_start >= 0:
        end = len(values) - gap  # type: ignore[arg-type]
        if end - run_start > best_end - best_start:
            best_start, best_end = run_start, end
    return best_start, best_end


def _text_fallback_profile(duration_seconds: float, text_hint: str) -> VowelConsonantProfile:
    token = _phonetic_label_token(text_hint)
    boundaries = _vowel_boundaries(token)
    if not token or boundaries is None or duration_seconds <= 0:
        return VowelConsonantProfile(
            vowel_start_seconds=0.0,
            vowel_end_seconds=max(duration_seconds, 0.0),
            detection_source="text_fallback_full",
            confidence=0.15,
            voiced_ratio=1.0,
        )

    vowel_start, vowel_end = boundaries
    token_length = max(len(token), 1)
    return VowelConsonantProfile(
        vowel_start_seconds=duration_seconds * vowel_start / token_length,
        vowel_end_seconds=duration_seconds * vowel_end / token_length,
        detection_source="text_fallback",
        confidence=0.25,
        voiced_ratio=0.0,
    )


def _phonetic_label_token(text: str) -> str:
    tokens = re.findall(r"[a-zA-Z\u00DC\u00fc]+", str(text or "").lower())
    return tokens[0] if tokens else ""


def _vowel_boundaries(token: str) -> tuple[int, int] | None:
    if not token:
        return None
    vowels = set("aeiou\u00fc")
    start = next((index for index, character in enumerate(token) if character in vowels), None)
    if start is None:
        return None
    end = start
    while end < len(token) and token[end] in vowels:
        end += 1
    return start, end

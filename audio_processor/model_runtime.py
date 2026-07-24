from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from xml.etree import ElementTree

from .engine import AudioProcessorError, get_audio_duration_seconds, list_audio_files, probe_audio
from .model_assist import MaterialAnalysis, VoiceSegment, list_model_candidates, order_materials_for_reference
from .settings import get_config_dir


ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    speaker_id: str | None = None


@dataclass(frozen=True)
class AudioAnalysis:
    path: Path
    duration_seconds: float
    transcript: str
    segments: tuple[TranscriptSegment, ...]
    vad_segments: tuple[tuple[float, float], ...]
    speaker_embedding: tuple[float, ...] | None
    analysis_source: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceAnalysis:
    source_path: Path
    vocal_path: Path
    transcript: str
    segments: tuple[VoiceSegment, ...]
    speaker_embedding: tuple[float, ...] | None
    backend: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialLibraryAnalysis:
    material_directory: Path
    materials: tuple[AudioAnalysis, ...]
    backend_summary: dict[str, bool]
    notes: tuple[str, ...] = ()

    @property
    def material_paths(self) -> list[Path]:
        return [analysis.path for analysis in self.materials]


@dataclass(frozen=True)
class OrderingDecision:
    rank: int
    source_path: Path
    score: float
    transcript_score: float
    speaker_score: float
    vad_score: float
    reference_text: str
    material_text: str
    reason: str


@dataclass(frozen=True)
class ModelOrderingResult:
    reference: ReferenceAnalysis
    library: MaterialLibraryAnalysis
    ordered_paths: tuple[Path, ...]
    decisions: tuple[OrderingDecision, ...]
    analysis_report: dict[str, Any]


DEFAULT_ASR_MODEL = os.environ.get("VOCAL_PROCESS_ASR_MODEL", "base")
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
PYANNOTE_DIA_MODEL = "pyannote/speaker-diarization-community-1"
SPEAKER_EMBEDDING_MODEL = "speechbrain/spkrec-ecapa-voxceleb"


def build_model_ordering(
    reference_path: Path,
    material_directory: Path,
    *,
    lyrics_file: Path | None = None,
    work_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> ModelOrderingResult:
    work_root = _prepare_work_root(work_dir)
    material_directory = material_directory.expanduser()
    material_paths = list_audio_files(material_directory)
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    notes: list[str] = []
    backend_summary = backend_availability()
    _notify_progress(on_progress, 0.02, "Preparing reference analysis")
    reference = analyze_reference(
        reference_path,
        lyrics_file=lyrics_file,
        work_dir=work_root,
        on_progress=on_progress,
        notes=notes,
    )

    _notify_progress(on_progress, 0.25, "Preparing material analysis")
    library = analyze_material_library(
        material_directory,
        work_dir=work_root,
        on_progress=on_progress,
        notes=notes,
    )

    reference_segments = reference.segments
    if not reference_segments and reference.transcript.strip():
        reference_segments = (
            VoiceSegment(
                start_seconds=0.0,
                end_seconds=max(reference_duration_for(reference.source_path), 0.0),
                text=reference.transcript,
            ),
        )

    material_analyses = [
        MaterialAnalysis(
            path=analysis.path,
            transcript=analysis.transcript,
            voice_segments=tuple(
                VoiceSegment(
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                    confidence=segment.confidence,
                    speaker_id=segment.speaker_id,
                )
                for segment in analysis.segments
            ),
            speaker_embedding=analysis.speaker_embedding,
            vad_coverage=_vad_coverage(analysis.vad_segments, analysis.duration_seconds),
            duration_seconds=analysis.duration_seconds,
            analysis_source=analysis.analysis_source,
        )
        for analysis in library.materials
    ]

    decisions = order_materials_for_reference(
        reference_segments,
        material_analyses,
        reference_embedding=reference.speaker_embedding,
    )
    ordered_paths = tuple(decision.material_path for decision in decisions)

    report = {
        "format": "vocal_process_model_ordering_v1",
        "reference": render_reference_analysis(reference),
        "materials": [render_material_analysis(analysis) for analysis in library.materials],
        "ordering": [
            {
                "rank": decision.rank,
                "material_path": str(decision.material_path),
                "score": decision.score,
                "transcript_score": decision.transcript_score,
                "speaker_score": decision.speaker_score,
                "vad_score": decision.vad_score,
                "reference_text": decision.reference_text,
                "material_text": decision.material_text,
                "reason": decision.reason,
            }
            for decision in decisions
        ],
        "backend_summary": backend_summary,
        "notes": notes,
    }
    _notify_progress(on_progress, 1.0, "Model-assisted ordering complete")
    return ModelOrderingResult(
        reference=reference,
        library=library,
        ordered_paths=ordered_paths,
        decisions=tuple(
            OrderingDecision(
                rank=decision.rank,
                source_path=decision.material_path,
                score=decision.score,
                transcript_score=decision.transcript_score,
                speaker_score=decision.speaker_score,
                vad_score=decision.vad_score,
                reference_text=decision.reference_text,
                material_text=decision.material_text,
                reason=decision.reason,
            )
            for decision in decisions
        ),
        analysis_report=report,
    )


def analyze_reference(
    reference_path: Path,
    *,
    lyrics_file: Path | None = None,
    work_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
    notes: list[str] | None = None,
) -> ReferenceAnalysis:
    notes = notes if notes is not None else []
    normalized_reference = reference_path.expanduser()
    if not normalized_reference.exists():
        raise AudioProcessorError(f"Reference audio does not exist: {normalized_reference}")

    vocal_path = _maybe_separate_vocals(normalized_reference, work_dir=work_dir, notes=notes)
    transcript_result = _transcribe_audio(vocal_path, work_dir=work_dir)
    reference_embedding = _speaker_embedding(vocal_path, work_dir=work_dir)
    segments = _segments_from_transcript(transcript_result["segments"], lyrics_file=lyrics_file)
    if not segments:
        segments = (
            VoiceSegment(
                start_seconds=0.0,
                end_seconds=max(get_audio_duration_seconds(probe_audio(vocal_path)), 0.0),
                text=transcript_result["text"],
                confidence=0.0,
            ),
        )

    _notify_progress(on_progress, 0.2, "Reference analysis complete")
    return ReferenceAnalysis(
        source_path=normalized_reference,
        vocal_path=vocal_path,
        transcript=transcript_result["text"],
        segments=segments,
        speaker_embedding=reference_embedding,
        backend=transcript_result["backend"],
        notes=tuple(notes),
    )


def analyze_material_library(
    material_directory: Path,
    *,
    work_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
    notes: list[str] | None = None,
) -> MaterialLibraryAnalysis:
    notes = notes if notes is not None else []
    material_paths = list_audio_files(material_directory)
    if not material_paths:
        raise AudioProcessorError("Material directory does not contain supported audio files")

    analyses: list[AudioAnalysis] = []
    total = len(material_paths)
    for index, path in enumerate(material_paths):
        progress = 0.25 + ((index + 1) / max(total, 1)) * 0.65
        _notify_progress(on_progress, progress, f"Analyzing material {index + 1}/{total}: {path.name}")
        transcript_result = _transcribe_audio(path, work_dir=work_dir)
        vad_segments = _detect_vad_segments(path)
        speaker_embedding = _speaker_embedding(path, work_dir=work_dir)
        duration = get_audio_duration_seconds(probe_audio(path))
        analyses.append(
            AudioAnalysis(
                path=path,
                duration_seconds=duration,
                transcript=transcript_result["text"],
                segments=tuple(
                    TranscriptSegment(
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        text=segment.text,
                        confidence=segment.confidence,
                        speaker_id=segment.speaker_id,
                    )
                    for segment in transcript_result["segments"]
                ),
                vad_segments=vad_segments,
                speaker_embedding=speaker_embedding,
                analysis_source=transcript_result["backend"],
                notes=tuple(transcript_result.get("notes", ())),
            )
        )

    return MaterialLibraryAnalysis(
        material_directory=material_directory.expanduser(),
        materials=tuple(analyses),
        backend_summary=backend_availability(),
        notes=tuple(notes),
    )


def render_reference_analysis(reference: ReferenceAnalysis) -> dict[str, Any]:
    return {
        "source_path": str(reference.source_path),
        "vocal_path": str(reference.vocal_path),
        "backend": reference.backend,
        "transcript": reference.transcript,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "confidence": segment.confidence,
                "speaker_id": segment.speaker_id,
            }
            for segment in reference.segments
        ],
        "speaker_embedding": list(reference.speaker_embedding) if reference.speaker_embedding else None,
        "notes": list(reference.notes),
    }


def render_material_analysis(analysis: AudioAnalysis) -> dict[str, Any]:
    return {
        "path": str(analysis.path),
        "duration_seconds": analysis.duration_seconds,
        "backend": analysis.analysis_source,
        "transcript": analysis.transcript,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "confidence": segment.confidence,
                "speaker_id": segment.speaker_id,
            }
            for segment in analysis.segments
        ],
        "vad_segments": [{"start": start, "end": end} for start, end in analysis.vad_segments],
        "speaker_embedding": list(analysis.speaker_embedding) if analysis.speaker_embedding else None,
        "notes": list(analysis.notes),
    }


def backend_availability() -> dict[str, bool]:
    return {
        candidate["name"]: _module_available(candidate["optional_dependency"])
        for candidate in list_model_candidates()
    }


def plan_reference_text_segments(
    reference_transcript: str,
    *,
    lyrics_file: Path | None = None,
) -> tuple[VoiceSegment, ...]:
    lyrics_segments = parse_lyrics_file(lyrics_file) if lyrics_file else []
    if lyrics_segments:
        return tuple(lyrics_segments)

    transcript = reference_transcript.strip()
    if not transcript:
        return ()

    sentences = [part.strip() for part in transcript.split("\n") if part.strip()]
    if not sentences:
        sentences = [transcript]

    duration = max(len(sentences), 1)
    segments: list[VoiceSegment] = []
    for index, sentence in enumerate(sentences):
        segments.append(
            VoiceSegment(
                start_seconds=float(index),
                end_seconds=float(index + 1),
                text=sentence,
                confidence=1.0,
            )
        )
    return tuple(segments)


def parse_lyrics_file(path: Path | None) -> list[VoiceSegment]:
    if path is None:
        return []

    lyrics_path = path.expanduser()
    if not lyrics_path.exists():
        raise AudioProcessorError(f"Lyrics file does not exist: {lyrics_path}")

    suffix = lyrics_path.suffix.lower()
    if suffix in {".txt", ".lrc", ".srt"}:
        text = lyrics_path.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".docx":
        text = _read_docx_text(lyrics_path)
    elif suffix == ".doc":
        text = _read_legacy_doc_text(lyrics_path)
    else:
        raise AudioProcessorError(f"Unsupported lyrics file format: {lyrics_path}")

    segments = _lyrics_text_to_segments(text, suffix=suffix)
    if not segments:
        raise AudioProcessorError(f"Lyrics file did not contain usable text: {lyrics_path}")
    return segments


def _lyrics_text_to_segments(text: str, *, suffix: str) -> list[VoiceSegment]:
    lines: list[str] = []
    if suffix == ".lrc":
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            cleaned = _strip_lrc_timestamp(stripped)
            if cleaned:
                lines.append(cleaned)
    elif suffix == ".srt":
        block: list[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                if block:
                    lines.extend(block[1:])
                    block = []
                continue
            block.append(stripped)
        if block:
            lines.extend(block[1:])
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]

    return [
        VoiceSegment(start_seconds=float(index), end_seconds=float(index + 1), text=line, confidence=1.0)
        for index, line in enumerate(lines)
        if line
    ]


def _strip_lrc_timestamp(line: str) -> str:
    while line.startswith("[") and "]" in line:
        line = line.split("]", 1)[1].strip()
    return line


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml_text = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise AudioProcessorError(f"Could not read DOCX lyrics file: {path}") from exc

    root = ElementTree.fromstring(xml_text)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
            if node.text
        ]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_legacy_doc_text(path: Path) -> str:
    word_text = _try_word_com_text(path)
    if word_text:
        return word_text

    antiword = shutil.which("antiword")
    if antiword:
        result = subprocess.run(
            [antiword, str(path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stdout.strip():
            return result.stdout

    raise AudioProcessorError(
        "DOC lyrics need either Microsoft Word, antiword, or a conversion to DOCX/TXT before analysis."
    )


def _try_word_com_text(path: Path) -> str:
    try:
        import win32com.client  # type: ignore
    except Exception:
        return ""

    try:
        word = win32com.client.Dispatch("Word.Application")
    except Exception:
        return ""

    document = None
    try:
        document = word.Documents.Open(str(path))
        text = document.Content.Text
        return text or ""
    except Exception:
        return ""
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:
            pass
        try:
            word.Quit()
        except Exception:
            pass


def _maybe_separate_vocals(path: Path, *, work_dir: Path | None = None, notes: list[str]) -> Path:
    if not _module_available("demucs"):
        notes.append("demucs unavailable; using original reference audio")
        return path

    work_root = _prepare_work_root(work_dir)
    separated_root = work_root / "demucs"
    separated_root.mkdir(parents=True, exist_ok=True)
    candidate = separated_root / "htdemucs" / path.stem / "vocals.wav"
    if candidate.exists():
        notes.append(f"reference vocals reused from demucs cache: {candidate}")
        return candidate

    try:
        import demucs.separate  # type: ignore
    except Exception as exc:
        notes.append(f"demucs import failed: {exc}")
        return path

    cmd = [
        "--two-stems",
        "vocals",
        "-n",
        "htdemucs",
        "--out",
        str(separated_root),
        str(path),
    ]
    try:
        demucs.separate.main(cmd)
    except SystemExit as exc:
        if getattr(exc, "code", 0) not in (0, None):
            notes.append(f"demucs exited with code {exc.code}")
            return path
    except Exception as exc:
        notes.append(f"demucs separation failed: {exc}")
        return path

    if candidate.exists():
        notes.append(f"reference vocals separated with demucs: {candidate}")
        return candidate

    for found in separated_root.rglob("vocals.wav"):
        notes.append(f"reference vocals separated with demucs: {found}")
        return found

    notes.append("demucs completed but no vocals stem was found")
    return path


def _transcribe_audio(path: Path, *, work_dir: Path | None = None) -> dict[str, Any]:
    work_root = _prepare_work_root(work_dir)
    if _module_available("whisperx"):
        try:
            import whisperx  # type: ignore

            model = whisperx.load_model(
                DEFAULT_ASR_MODEL,
                DEFAULT_DEVICE,
                compute_type=DEFAULT_COMPUTE_TYPE,
                download_root=str(_model_cache_root() / "whisperx"),
            )
            audio = whisperx.load_audio(str(path))
            result = model.transcribe(audio, batch_size=4)
            align_model, metadata = whisperx.load_align_model(
                language_code=result["language"], device=DEFAULT_DEVICE
            )
            aligned = whisperx.align(
                result["segments"],
                align_model,
                metadata,
                audio,
                DEFAULT_DEVICE,
                return_char_alignments=False,
            )
            segments = [
                TranscriptSegment(
                    start_seconds=float(segment.get("start", 0.0) or 0.0),
                    end_seconds=float(segment.get("end", 0.0) or 0.0),
                    text=str(segment.get("text", "")).strip(),
                    confidence=_segment_confidence(segment),
                    speaker_id=segment.get("speaker"),
                )
                for segment in aligned.get("segments", [])
                if str(segment.get("text", "")).strip()
            ]
            text = " ".join(segment.text for segment in segments).strip()
            return {"backend": "whisperx", "text": text, "segments": segments, "notes": []}
        except Exception as exc:
            # Fall through to Whisper if WhisperX is unavailable or fails on a specific file.
            return _transcribe_with_whisper(path, work_dir=work_root, fallback_note=f"whisperx failed: {exc}")

    return _transcribe_with_whisper(path, work_dir=work_root, fallback_note="whisperx unavailable")


def _transcribe_with_whisper(
    path: Path,
    *,
    work_dir: Path,
    fallback_note: str,
) -> dict[str, Any]:
    if not _module_available("whisper"):
        raise AudioProcessorError(
            "Model-assisted ordering requires a speech recognition backend, but none is installed. "
            "Install openai-whisper or whisperx before running material assembly."
        )

    try:
        import whisper  # type: ignore

        model = whisper.load_model(DEFAULT_ASR_MODEL, download_root=str(_model_cache_root() / "whisper"))
        result = model.transcribe(str(path), fp16=False, verbose=None)
        segments = [
            TranscriptSegment(
                start_seconds=float(segment.get("start", 0.0) or 0.0),
                end_seconds=float(segment.get("end", 0.0) or 0.0),
                text=str(segment.get("text", "")).strip(),
                confidence=None,
                speaker_id=None,
            )
            for segment in result.get("segments", [])
            if str(segment.get("text", "")).strip()
        ]
        text = " ".join(segment.text for segment in segments).strip()
        notes = [fallback_note] if fallback_note else []
        return {"backend": "whisper", "text": text, "segments": segments, "notes": notes}
    except Exception as exc:
        raise AudioProcessorError(f"Whisper transcription failed for {path}: {exc}") from exc


def _detect_vad_segments(path: Path) -> tuple[tuple[float, float], ...]:
    pyannote_segments = _detect_pyannote_segments(path)
    if pyannote_segments:
        return pyannote_segments

    if not _module_available("silero_vad"):
        torch_hub_segments = _detect_vad_segments_with_torch_hub(path)
        if torch_hub_segments:
            return torch_hub_segments
        return ((0.0, get_audio_duration_seconds(probe_audio(path))),)

    try:
        from silero_vad import get_speech_timestamps, load_silero_vad, read_audio  # type: ignore

        model = load_silero_vad()
        wav = read_audio(str(path))
        timestamps = get_speech_timestamps(wav, model, return_seconds=True)
        segments = []
        for entry in timestamps:
            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", 0.0) or 0.0)
            if end > start:
                segments.append((start, end))
        if segments:
            return tuple(segments)
    except Exception:
        pass

    return ((0.0, get_audio_duration_seconds(probe_audio(path))),)


def _detect_pyannote_segments(path: Path) -> tuple[tuple[float, float], ...]:
    token = os.environ.get("PYANNOTE_AUTH_TOKEN") or os.environ.get("HF_TOKEN")
    if not token or not _module_available("pyannote.audio"):
        return ()

    try:
        from pyannote.audio import Pipeline  # type: ignore

        pipeline = Pipeline.from_pretrained(PYANNOTE_DIA_MODEL, token=token)
        diarization = pipeline(str(path))
        segments: list[tuple[float, float]] = []
        for turn, _, _speaker in diarization.itertracks(yield_label=True):
            start = float(turn.start)
            end = float(turn.end)
            if end > start:
                segments.append((start, end))
        return tuple(segments)
    except Exception:
        return ()


def _detect_vad_segments_with_torch_hub(path: Path) -> tuple[tuple[float, float], ...]:
    if not _module_available("torch"):
        return ()

    try:
        import torch  # type: ignore

        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            source="github",
            trust_repo=True,
        )
        get_speech_timestamps, _, read_audio, _, _ = utils
        wav = read_audio(str(path))
        timestamps = get_speech_timestamps(wav, model, return_seconds=True)
        segments = []
        for entry in timestamps:
            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", 0.0) or 0.0)
            if end > start:
                segments.append((start, end))
        return tuple(segments)
    except Exception:
        return ()


def _speaker_embedding(path: Path, *, work_dir: Path | None = None) -> tuple[float, ...] | None:
    if not _module_available("speechbrain"):
        return None

    try:
        import torch  # type: ignore
        from speechbrain.dataio.dataio import read_audio  # type: ignore
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

        savedir = _model_cache_root() / "speechbrain" / "ecapa"
        if not _speechbrain_model_cached(savedir) and os.environ.get("VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD") != "1":
            return None

        classifier = EncoderClassifier.from_hparams(
            source=SPEAKER_EMBEDDING_MODEL,
            savedir=str(savedir),
        )
        waveform = read_audio(str(path))
        if waveform.dim() == 1:
            waveform = waveform.unsqueeze(0)
        with torch.no_grad():
            embedding = classifier.encode_batch(waveform)
        vector = embedding.squeeze().detach().cpu().flatten().tolist()
        return tuple(float(value) for value in vector)
    except Exception:
        return None


def _speechbrain_model_cached(savedir: Path) -> bool:
    return (savedir / "hyperparams.yaml").exists()


def _segments_from_transcript(
    segments: Sequence[TranscriptSegment],
    *,
    lyrics_file: Path | None = None,
) -> tuple[VoiceSegment, ...]:
    if lyrics_file is not None:
        lyric_segments = parse_lyrics_file(lyrics_file)
        if lyric_segments:
            if segments:
                max_count = min(len(lyric_segments), len(segments))
                paired: list[VoiceSegment] = []
                for index in range(max_count):
                    lyric = lyric_segments[index]
                    transcript_segment = segments[index]
                    paired.append(
                        VoiceSegment(
                            start_seconds=transcript_segment.start_seconds,
                            end_seconds=transcript_segment.end_seconds,
                            text=lyric.text,
                            confidence=transcript_segment.confidence,
                            speaker_id=transcript_segment.speaker_id,
                        )
                    )
                if len(lyric_segments) > max_count and segments:
                    last_end = paired[-1].end_seconds if paired else segments[-1].end_seconds
                    for index in range(max_count, len(lyric_segments)):
                        lyric = lyric_segments[index]
                        paired.append(
                            VoiceSegment(
                                start_seconds=last_end + float(index - max_count),
                                end_seconds=last_end + float(index - max_count + 1),
                                text=lyric.text,
                                confidence=1.0,
                            )
                        )
                return tuple(paired)
            return tuple(lyric_segments)

    return tuple(
        VoiceSegment(
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text,
            confidence=segment.confidence,
            speaker_id=segment.speaker_id,
        )
        for segment in segments
    )


def reference_duration_for(path: Path) -> float:
    return get_audio_duration_seconds(probe_audio(path))


def _prepare_work_root(work_dir: Path | None) -> Path:
    root = (work_dir or Path(tempfile.gettempdir()) / "vocal_process_model_cache").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _model_cache_root() -> Path:
    configured = os.environ.get("VOCAL_PROCESS_MODEL_CACHE")
    root = Path(configured).expanduser() if configured else get_config_dir() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _maybe_module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _module_available(module_name: str) -> bool:
    if module_name == "silero_vad":
        return _maybe_module_available(module_name)
    return _maybe_module_available(module_name)


def _vad_coverage(vad_segments: Sequence[tuple[float, float]], duration_seconds: float) -> float | None:
    if duration_seconds <= 0:
        return None

    covered = sum(max(end - start, 0.0) for start, end in vad_segments)
    return min(max(covered / duration_seconds, 0.0), 1.0)


def _segment_confidence(segment: dict[str, Any]) -> float | None:
    confidence = segment.get("confidence")
    try:
        return None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        return None


def _notify_progress(callback: ProgressCallback | None, progress: float, message: str) -> None:
    if callback is not None:
        callback(progress, message)

# Model Assisted Vocal Pipeline

## Problem Review

The previous material stretch assembly workflow was technically able to concatenate files and match the reference duration, but it did not understand vocals.

Main failure reasons:

1. The original audio was used only as a duration reference.
2. Material clips were ordered by filename, not by lyrics, phonemes, phrase timing, or voice similarity.
3. No stage separated vocals from a full song mix.
4. No voice activity detection identified where singing or speech actually starts and ends.
5. No ASR or alignment stage mapped reference words to material words.
6. No speaker or timbre similarity score checked whether a material clip matched the intended voice.

This explains the user test result where processing could finish without a useful output. A valid WAV or DAW project can still be musically meaningless if the ordering and alignment inputs are wrong.

## Diagnostic Logging

Every batch item now writes a JSONL diagnostics file:

1. Flat WAV output: `<output_stem>.diagnostics.jsonl`.
2. DAW project output: `<project_folder>\diagnostics.jsonl`.

The log records:

1. Run id and timestamp.
2. Processing mode.
3. Input path and output path.
4. Processing settings snapshot.
5. Reference audio metadata.
6. Material file list, durations, codec, sample rate, and channel count.
7. Lyrics file path if selected.
8. Completion, cancellation, expected processing errors, and unexpected exceptions.
9. Model ordering scores split into transcript, filename hint, duration, speaker, and VAD components.
10. Per-material stretch plans, including source duration, target duration, rubberband tempo, and moderate/extreme stretch warnings.

JSONL is used so a failed run can still leave useful partial evidence. Each line is independent JSON and can be inspected with a text editor or parsed by tools later.

## Open Source Model Candidates

The model layer is now part of the core material assembly flow. Some backends are still staged because they can require GPU, ONNX Runtime, model-account setup, or Hugging Face authorization.

| Stage | Candidate | GitHub | Purpose |
| --- | --- | --- | --- |
| Source separation | UVR Headless Runner | https://github.com/chyinan/uvr-headless-runner | Run UVR-style MDX, Demucs, or VR separation through an isolated Python 3.10 worker. |
| Source separation | Demucs | https://github.com/facebookresearch/demucs | Extract vocals from a mixed original song before alignment. |
| Voice activity detection | Silero VAD | https://github.com/snakers4/silero-vad | Detect vocal/speech regions efficiently. |
| Diarization and speaker features | pyannote.audio | https://github.com/pyannote/pyannote-audio | Segment speakers and extract speaker-related features. |
| ASR and word alignment | WhisperX | https://github.com/m-bain/whisperX | Transcribe and align words to timestamps. |
| Accelerated ASR | Faster Whisper | https://github.com/SYSTRAN/faster-whisper | Run Whisper-style ASR through CTranslate2 when installed. |
| ASR fallback | OpenAI Whisper | https://github.com/openai/whisper | General multilingual speech recognition. |
| Native ASR candidate | whisper.cpp | https://github.com/ggerganov/whisper.cpp | Candidate for smaller CPU-first native ASR builds. |
| Speaker similarity | SpeechBrain | https://github.com/speechbrain/speechbrain | Score voice/speaker similarity with pretrained speaker-recognition models. |
| Music structure | Librosa | https://github.com/librosa/librosa | Candidate for repeated-section/self-similarity analysis. |
| Music structure | MSAF | https://github.com/urinieto/msaf | Candidate for verse/chorus structural segmentation. |

## Proposed Processing Architecture

1. Prepare reference:
   - If the original is a mixed song, run source separation first.
   - Keep the separated vocal stem as the analysis target.

2. Detect vocal regions:
   - Run VAD or diarization on the reference vocal.
   - Run the same analysis on every material clip.

3. Transcribe and align:
   - Use WhisperX where word-level timestamps are needed.
   - Use Whisper as a fallback when rough transcript matching is enough.

4. Score material clips:
   - Compare reference segment text with material transcript text.
   - Compare filename pronunciation hints when ASR is weak, especially for one-character or one-syllable material clips.
   - Compare material duration against the target reference segment duration.
   - Compare speaker or voice embeddings where available.
   - Penalize clips with weak VAD confidence or extreme stretch ratio.

5. Build an editable timeline:
   - Produce a timeline manifest with source clip, reference segment, start time, target duration, transcript score, and speaker score.
   - Render each chosen material clip separately with Rubber Band stretch, then concatenate or place clips on a DAW timeline.
   - Export REAPER `.rpp`, `timeline.json`, `timeline.csv`, and WAV clips.

## Current Code Status

Implemented now:

1. Structured diagnostics logs in the batch layer.
2. `audio_processor.model_assist` data structures.
3. Open-source model candidate registry.
4. Optional backend availability checks.
5. Transcript-based material ordering helper.
6. Real local runtime integration for Demucs, OpenAI Whisper, Silero VAD through torch hub, and cache-gated SpeechBrain speaker embeddings.
7. GUI and CLI environment checks report the local model runtime and cache state.
8. Batch material assembly requires model-assisted ordering when a material folder is selected.
9. DAW timeline export receives the model-ordered material path list instead of raw filename order.
10. Flat WAV assembly now uses per-clip Rubber Band stretching before concatenation, so a single syllable is not forced through one whole-material stretch pass.
11. Stretch diagnostics record quality warnings for ratios that are likely to damage intelligibility.
12. A JSON-based VST3 bridge helper exists as `audio_processor.vst3_bridge` and `audio-processor vst3-bridge`.
13. `audio-processor analyze` writes a preflight report before rendering, including ordering scores, filename hints, target durations, stretch tempo, and review warnings.
14. Batch diagnostics now emits `model.ordering.review_required` when low match scores or risky stretch ratios should be reviewed before trusting the output.
15. A native JUCE VST3 bridge plug-in exists under `native\vst3_bridge` and calls the same helper process outside the audio callback.
16. `source_separation=never` can be selected in GUI/CLI/VST3 bridge requests when the original audio is already an isolated vocal stem.
17. Reference analysis is cached, and loaded ASR/VAD/speaker models are reused inside the process.
18. `faster-whisper`, WhisperX, and pyannote.audio are part of the full Python 3.11 model runtime; the code falls back automatically when a backend fails on a specific file.
19. Preflight reports include an optimization section for duplicate render reuse and repeated reference text hints.
20. DAW timeline export reuses exact duplicate stretched WAV renders when source file, target duration, tempo, and render options match.

CLI entry:

```powershell
python -m audio_processor models
python -m audio_processor models --json
```

Gated or future work:

1. pyannote.audio pretrained diarization still needs Hugging Face token authorization and model terms acceptance.
2. WhisperX word-level alignment may require first-run language alignment model downloads.
3. SpeechBrain first-run downloads require `VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD=1`; cached models are used when present.
4. Librosa/MSAF structure analysis is tracked for future section-level optimization. The current implementation only reports repeated text and exact duplicate render reuse because those paths are safe.
5. UVR MDX/VR models require a selected model path/name in the Python 3.10 worker.

## Runtime Optimization Plan

The current 28-second sample taking 7-10 minutes indicates model startup and source separation dominate runtime more than Rubber Band rendering. The implemented low-risk optimizations are:

1. Skip Demucs when the user marks the reference as already isolated vocals.
2. Reuse loaded Whisper/Faster Whisper/WhisperX/Silero/SpeechBrain models inside the same process.
3. Cache reference analysis across repeated runs.
4. Reuse exact duplicate DAW clip renders.
5. Keep material analysis cache keyed by file snapshot.

Further safe optimization candidates:

1. Add a current 64-bit GPU path and set compute device to `cuda` when available.
2. Use UVR headless runner before in-process Demucs when the Python 3.10 worker is configured; verify it with `scripts\check_uvr_worker.ps1` before long manual tests.
3. Add Librosa/MSAF section analysis to detect repeated verse/chorus structures, then reuse timing templates only after preflight confirms similar durations and no low-confidence text matches.
4. Avoid pitch tracking by default; pitch analysis should be optional because text/timing matching is cheaper and more directly relevant to word ordering.
5. For long songs, split reference analysis into cached sections so only changed sections are re-analyzed during iterative manual tests.

## Next Development Steps

1. Use manual test diagnostics and `analysis.json` reports to tune weak transcripts, filename hints, and extreme stretch ratios.
2. Improve ordering with WhisperX word-level alignment where alignment model caches are available.
3. Enable pyannote diarization paths after Hugging Face authorization is provided.
4. Add safe repeated-section timing reuse after enough manual diagnostics prove the heuristic.
5. Validate the native VST3 bridge in additional real DAW hosts and keep heavy processing in the helper process.

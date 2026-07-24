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

JSONL is used so a failed run can still leave useful partial evidence. Each line is independent JSON and can be inspected with a text editor or parsed by tools later.

## Open Source Model Candidates

The model layer should be optional and staged, because these dependencies can be large and may require GPU, PyTorch, ONNX Runtime, or model-account setup.

| Stage | Candidate | GitHub | Purpose |
| --- | --- | --- | --- |
| Source separation | Demucs | https://github.com/facebookresearch/demucs | Extract vocals from a mixed original song before alignment. |
| Voice activity detection | Silero VAD | https://github.com/snakers4/silero-vad | Detect vocal/speech regions efficiently. |
| Diarization and speaker features | pyannote.audio | https://github.com/pyannote/pyannote-audio | Segment speakers and extract speaker-related features. |
| ASR and word alignment | WhisperX | https://github.com/m-bain/whisperX | Transcribe and align words to timestamps. |
| ASR fallback | OpenAI Whisper | https://github.com/openai/whisper | General multilingual speech recognition. |
| Speaker similarity | SpeechBrain | https://github.com/speechbrain/speechbrain | Score voice/speaker similarity with pretrained speaker-recognition models. |

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
   - Compare speaker or voice embeddings where available.
   - Penalize clips with weak VAD confidence or extreme stretch ratio.

5. Build an editable timeline:
   - Produce a timeline manifest with source clip, reference segment, start time, target duration, transcript score, and speaker score.
   - Render each chosen material clip separately with Rubber Band stretch.
   - Export REAPER `.rpp`, `timeline.json`, `timeline.csv`, and WAV clips.

## Current Code Status

Implemented now:

1. Structured diagnostics logs in the batch layer.
2. `audio_processor.model_assist` data structures.
3. Open-source model candidate registry.
4. Optional backend availability checks.
5. Transcript-based material ordering helper.
6. CLI entry:

```powershell
python -m audio_processor models
python -m audio_processor models --json
```

Not implemented yet:

1. Installing or bundling the large model dependencies.
2. Running Demucs, WhisperX, pyannote, Silero VAD, or SpeechBrain inference.
3. GUI controls for selecting a model backend.
4. Model-generated DAW timeline decisions.

## Next Development Steps

1. Add an `analyze` command that writes `analysis.json` from optional installed model backends.
2. Add a GUI analysis step that can run before rendering.
3. Extend the DAW timeline planner to use `analysis.json` instead of filename order.
4. Keep the old filename order only as an explicit fallback mode.
5. Package model backends separately from the small portable GUI unless a target test machine and dependency budget are confirmed.

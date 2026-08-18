# Project Analysis

## Latest Update - 2026-08-15 Manual Lyrics Mode Boundary

The user clarified a product-level boundary: no-lyrics operation is not an experimental side path. It is one of the original core workflows. The system must be able to sort and align from the original/reference vocal when no lyrics file exists, while still reporting that this mode depends on ASR recognition quality.

Architecture decision:

1. Strict rendered real-eval and normal GUI batch are separate acceptance surfaces. The former may keep blockers that prevent misleading formal score artifacts; the latter must expose a clear user choice between manual lyrics and original-vocal ASR mode.
2. `manual_lyrics_enabled` now controls whether a stored `lyrics_file` participates in processing. This prevents stale paths from silently changing the run mode.
3. When manual lyrics is disabled, `ProcessingSettings.effective_lyrics_file()` returns an empty string and batch processing passes no lyrics file into `build_model_ordering()`. The model then follows the original no-lyrics path: reference vocal ASR/alignment defines target sequence and timing evidence.
4. When manual lyrics is enabled, the GUI validates that a supported lyrics/subtitle file exists before running. The text becomes the target-text authority, while reference vocal alignment remains timing evidence.
5. CLI batch and VST3 bridge enable manual lyrics automatically when a lyrics path is explicitly provided, preserving existing automation contracts.

Practical impact:

1. Users can save time by leaving manual lyrics disabled for rough or no-lyrics workflows.
2. Users can enable manual lyrics when they want to reduce ASR text errors, including Chinese ASR mistakes.
3. Reports still need to distinguish ASR-derived content and exact timing quality; this GUI change does not weaken the timeline trust diagnostics added in the previous pass.

Validation:

1. Full `.venv311\Scripts\python.exe -m unittest discover -s tests` passed with `198` tests.
2. Focused regression coverage checks settings compatibility, disabled manual lyrics ignoring stored paths, enabled manual lyrics passing the path, CLI batch auto-enable, and VST3 bridge auto-enable.
3. `compileall`, `.venv311\Scripts\python.exe -m audio_processor check`, and `git diff --check` passed.

## Latest Update - 2026-08-15 Strict Reference Timeline Gate

The latest user correction is important: content truth is not the only blocker. A rendered file can also have the wrong timeline even when the output duration matches the original vocal. Chinese cases have the same ASR-risk class as Japanese cases; language-specific confidence must not become a reason to trust ASR-only text or interpolated timing.

Architecture decision:

1. Formal rendered evaluation now has three distinct acceptance dimensions: reference text truth, exact unit timing truth, and final render duration. The old score path over-weighted final duration and `timed_target_duration_count`.
2. `timed_target_duration_count` only means a duration was assigned from something shaped like unit timing. It is insufficient when the timing lattice was resampled or interpolated.
3. `exact_timed_target_duration_count` is now the stricter signal for per-unit start/end acceptance. `resampled_timing_lattice_count` exposes how much of a case depends on interpolated timing.
4. `aligned_timing_score` now follows exact timing, not all timed-looking spans. This makes future score summaries harsher but closer to what manual listening is rejecting.
5. Timestamped lyric conflicts are timeline blockers. If lyrics and ASR/acoustic segment timing disagree, the system should stop and report the conflict rather than selecting one silently.

Implications:

1. A CN case without trusted lyrics/transcript remains ASR-only and should be blocked for formal render just like a JP case.
2. A lyrics-backed case can still be blocked if the ASR/forced-alignment timing must be resampled to fit the lyric units.
3. Future real-eval review should read `exact_timed_target_duration_ratio`, `resampled_timing_lattice_ratio`, and `render_validation.blocker_kind` before any listening pass.
4. The next valid audio generation pass should use verified text plus exact unit timing coverage. Otherwise manual listening will continue to mix DSP artifacts with wrong target placement.

Validation:

1. Full `.venv311\Scripts\python.exe -m unittest discover -s tests` passed with `192` tests.
2. `compileall`, `audio_processor check`, and `git diff --check` passed; diff check only reported LF/CRLF warnings.
3. Real strict gate rerun for `1000nenyikiteru_JP__MGRoid` stayed blocked at `tests_real\output\jp-reference-timing-trust-gate-20260815\reports\real-eval-20260815-221655\summary.json` and produced no audio files.

## Latest Update - 2026-08-15 Reference Vocal Truth Gate

The latest manual correction changes the failure class. The most damaging mismatch is not that material audio is unrelated to itself; it is that the rendered sequence can become unrelated to the original/reference vocal when the reference-side target text is ASR-only and unverified.

Current architecture decision:

1. The reference side now has a formal truth gate for rendered real-eval. Lyrics/subtitles or another verified transcript may define the target text; original-vocal alignment may define timing. If neither verified text nor trusted lyric input exists, ASR-only reference text is not allowed to drive formal review WAV generation.
2. `reference_asr_unverified` is therefore treated as a render blocker by default. The system can still run analysis and diagnostics, but it must not create audio that looks like acceptance output while matching a hallucinated target string.
3. `--allow-unverified-reference-render` is an explicit escape hatch for DSP experiments only. Results produced with it are not content-valid manual-listening evidence.
4. This separates two problems that were previously conflated: reference vocal truth quality and material stretch/render quality. Mono output, atempo, OTO windows, and de-clicking can reduce artifacts, but they cannot make a wrong reference transcript become the right song content.

MGRoid render policy refinement:

1. MGRoid-style UTAU `oto.ini` metadata is now used as source-window evidence for short labeled material. Offset, consonant, cutoff, preutterance, and overlap fields are more suitable than raw RMS alone for deciding how much of a recorded syllable should be preserved before stretching.
2. OTO lookup must stay label-exact enough to avoid false reuse. Longer romanized labels are not split into individual phonetic units for OTO lookup, because that can make `kan.wav` incorrectly inherit a `ka` entry.
3. Short-CV atempo remains the preferred low-artifact path for many compressed MGRoid clips, but high ratios are represented as a chain of legal FFmpeg `atempo` filters rather than a single unsupported ratio.

Validation:

1. `dev_preflight.ps1 -Workspace E:\Workplace\demo -FullGitProbe` passed.
2. Full `.venv311\Scripts\python.exe -m unittest discover -s tests` passed with `189` tests.
3. `compileall`, `.venv311\Scripts\python.exe -m audio_processor check`, and `git diff --check` passed; diff check only reported existing LF/CRLF warnings.
4. Strict gate evidence: `tests_real\output\jp-reference-trust-gate-20260815-rerun\reports\real-eval-20260815-201712\summary.json` reports `render_blocked_unverified_reference_text` and no final WAV files.
5. DSP-only atempo-chain smoke: `.tmp\mgroid-atempo-chain-smoke\ga_chain.wav`, mono `44100 Hz`, `0.099819 s`, `peak=0.528753`, `jump999=0.083761`, `jumpmax=0.086546`.

Next work:

1. Add or discover verified lyrics/subtitle truth for JP real cases before generating new formal listening WAVs.
2. Once verified reference text exists, rerun the JP suite under the mono+OTO+atempo-chain policy and evaluate both matching validity and residual click/formant artifacts.
3. Continue improving phonetic grouping and continuity only against content-valid targets, otherwise score deltas remain misleading.

## Latest Update - 2026-08-14 JP Vocal-Smooth Strategy and MGRoid Corpus

The latest manual review makes the current quality problem more concrete: Japanese output is not merely below strict score thresholds; it is audibly wrong. Stereo mismatch and formant drift remain perceptible after forcing Rubber Band `channels=together`, so the next strategy must reduce destructive processing before adding larger vocoder features.

Open-source review:

1. HiFiShifter is architecturally relevant because it treats audio clips as offline-rendered, cached timeline slices and exposes multiple rendering engines, including WORLD, PC-NSF-HiFiGAN, VocalShifter Library, SoundTouch, and Signalsmith. Its value for this project is the separation of clip slicing, analysis, renderer selection, cache identity, and final mixdown rather than direct code copying.
2. Rubber Band's API documentation distinguishes `OptionChannelsApart` and `OptionChannelsTogether`; the former prioritizes individual channel fidelity at the cost of synchronisation and can make stereo wider/phasy, while the latter targets center clarity and mono compatibility. This supports using stereo-coherent processing for vocal material.
3. Rubber Band's documentation says the R3/Finer engine is generally higher quality for vocals and soft onsets. FFmpeg's current `rubberband` filter does not expose an engine selector in this environment, but it does expose smoother transient/window/pitch-quality controls.
4. SoundTouch explicitly warns that processing stereo as two separate mono channels loses phase coherency, and describes its core time-stretch as WSOLA-like time-domain processing with tunable sequence/seek/overlap windows. This reinforces the channel-coherence requirement and gives a possible future backend experiment, not an immediate replacement.
5. Signalsmith Stretch documents best time-stretch results for modest changes around `0.75x..1.5x`, and states that its formant correction is not as sharp as monophonic algorithms such as PSOLA. The existing Python binding used here does not expose the formant-base control needed to tune Japanese female voice formants.
6. WORLD is the strongest open-source direction for measurable F0 and spectral-envelope/formant diagnostics because it estimates F0, aperiodicity, and spectral envelope. Migrating WORLD synthesis into this project would be a larger vocoder branch, not a small render-filter patch.

Current implementation decision:

1. Disable Signalsmith for large short-label vowel-core stretching. It remains available only when the requested ratio stays within the documented clean range.
2. Use a vocal-smooth Rubber Band profile for short/high-ratio vocal material: soft transient detection, long window, smoothing, high-consistency pitch processing, formant preservation, and channels together.
3. Treat consonant attack and coda as fixed anchors. Do not stretch them; allocate expansion to the voiced/vowel core. This directly targets repeated-consonant artifacts heard in long Japanese vowels.
4. Keep cache identity explicit with `material_render_filter_v13_jp_vocal_smooth_stereo` and per-clip `rubberband_profile` diagnostics.

MGRoid corpus:

1. `tests_real\material_set\MGRoid` is detected as `JP`.
2. It adds four compatible Japanese evaluation cases and skips CN references as language mismatches.
3. The set contains mixed mono/stereo and 44.1k/48k WAV files, plus some longer 3s vowel/syllable samples. This is useful for testing female Japanese voice quality, but it makes final-output-only review important; cache files are not suitable for listening review.
4. A focused JP+MGRoid rendered run is active under `tests_real\output\jp-mgroid-v13`. The acceptance question is manual listening first, then score deltas.

Residual risk:

1. Rubber Band formant preservation may still be insufficient for extreme single-syllable expansion. If MGRoid listening still shows child-like or low formants, the next branch should add WORLD/PyWorld-based F0 and spectral-envelope drift diagnostics before any larger vocoder migration.
2. Better material coverage can improve ordering but will not solve formant drift if the renderer still has to stretch a short clip too far. The next scoring report should separate material-choice wins from renderer-quality failures.

## Latest Update - 2026-08-14 Human Listening Quality Review

Manual listening changes the current diagnosis. The project is no longer blocked by gross render duration drift: the old-code full rendered suite completed `13/13` compatible cases with `render_duration_delta_ratio.mean=0.0`, and its rendered alignment mean reached `0.866752`. The output is now usable as timing evidence, but not yet as listening-quality evidence.

The strict failure remains real. `strict_render_pass_count=0/13`, `match_ordering_score.mean=0.568635`, `stretch_quality_score.mean=0.733137`, `stretch_naturalness_score.mean=0.720577`, and `continuity_warning_ratio.mean=0.309001` show that automated metrics still see material-selection and stretch-risk problems. The user's manual feedback adds failure modes the old metrics under-measure: stereo phase/channel incoherence, repeated consonant attacks during long vowel targets, and unstable formant/timbre preservation.

The durable product contract is now sharper:

1. Timing, ordering, and duration are allowed to change.
2. Pitch, formants, timbre, and speaker identity should not be intentionally changed.
3. Stereo material must either be processed coherently as one stereo signal or safely folded to a reviewed mono policy; independent channel stretching is not acceptable for vocal identity.
4. Consonant/attack/coda material should not be repeatedly stretched as if it were a vowel sustain.
5. Human review needs separated outputs, reports, diagnostics, and render caches so final audio can be audited without being mixed with scoring artifacts.

Immediate architectural adjustments:

1. Rubber Band filter construction now requests `channels=together`, which directly targets stereo incoherence from independent left/right processing.
2. The render cache format is bumped to `material_render_filter_v12_stereo_coherent_tiny_direct_trim` so older per-channel cache output is not reused.
3. Real-eval output roots are split into `audio`, `reports`, and `cache`, with per-case analysis/render diagnostics under the report case directory.
4. Batch settings now carry explicit render-cache and diagnostics directories so future suites can keep final review WAVs separate from intermediate scoring files.

Next DSP direction:

1. Add diagnostics before larger algorithm changes: stereo correlation/phase coherence, F0 median drift, spectral-envelope or MFCC/formant-envelope drift, and per-boundary transient repetition counters.
2. Treat consonants and attacks as anchors. Copy or minimally stretch them, and allocate long-duration expansion only to a verified voiced/vowel core.
3. Avoid independent region resynthesis that reintroduces the same attack more than once. A long vowel target should have one onset, one stable sustain region, and a controlled release.
4. Use backend policy by clip class: direct trim for tiny audible targets, stereo-coherent Rubber Band for ordinary whole clips, Signalsmith for exact-frame vowel-core stretching only when boundaries and channel handling are reliable.
5. Keep strict thresholds unchanged. The next acceptance target is not only higher score; it is lower audible formant drift, fewer repeated consonants, and cleaner manual review folders.

## Latest Update - 2026-08-13 Adaptive Acoustic Boundaries for Signalsmith Stretch

The next Signalsmith quality pass targeted a concrete boundary-allocation defect rather than changing strict acceptance thresholds. The previous implementation used the filename-derived first-vowel span as a hard intersection with the acoustic voiced/F0 span. This made long material clips such as `chi.wav` and `bo.wav` use a fixed short attack and a large core that could include unvoiced or consonant content, increasing the load on extreme core stretching.

The new boundary policy is:

1. If `librosa.pyin` produces a high-confidence voiced span, that acoustic span directly defines the vowel core.
2. Filename phonetic boundaries remain the fallback for clips without reliable acoustic evidence.
3. The attack region is limited by both `180ms` and `28%` of the source duration.
4. The coda region is limited by both `140ms` and `22%` of the source duration.
5. Signalsmith still receives exact per-region target frame counts, while Rubber Band remains the fallback backend.

This is a public-DSP improvement inspired by the HiFiShifter/Signalsmith architecture. It does not copy proprietary tuning behavior or claim to reproduce Vegas/Melodyne. The purpose is to keep consonant and tail material out of the high-expansion vowel core while preserving exact output timing.

Evidence from the local `vmzJP` material set:

1. `chi.wav` now uses an acoustic core of approximately `0.136s..0.928s`, with `0.136s` attack and `0.109s` coda.
2. `bo.wav` now uses an acoustic core of approximately `0.008s..0.860s`, with `0.008s` attack and `0.140s` coda.
3. `chi.wav`, `bo.wav`, and `shi.wav` rendered through the Signalsmith path to their requested durations with deltas of `0`, `0`, and approximately `-0.000001s`.

Verification passed with 167 unit tests, compileall, `audio_processor check`, and diff check. The next required measurement is a bounded Signalsmith/Rubber Band A/B comparison on the same real clips, followed by the focused PlasticLove/vmzJP rendered smoke. Full rendered real-eval should only be resumed after those measurements identify a general quality improvement.

## Latest Update - 2026-08-04 Main Promotion Completed

The Signalsmith branch promotion was completed without force-push. `origin/main` was an ancestor of the completed branch, so `main` was fast-forwarded to the Signalsmith work after backing up the old main state as `archive/main-before-signalsmith-vowel-core`.

## Latest Update - 2026-08-04 Branch Promotion Closeout Gate

The project rule for completed branches has been strengthened: uploaded feature branches are not the final delivery state. The closeout must back up the old `main`, promote the completed branch to `main`, push both refs, and report the final branch state; otherwise the task remains explicitly blocked. For the Signalsmith work, `origin/main` was an ancestor of the completed branch, so the promotion was completed as a fast-forward after backing up `main`.

## Latest Update - 2026-08-04 Remote Push Completed

The Signalsmith backend work has been uploaded to GitHub. Branch `codex/cancel-phonetic-accuracy` is synchronized with origin through `cf0930a`, which includes the implementation commit `02c4bf3`.

## Latest Update - 2026-08-03 Signalsmith Backend Integration

Closeout status: the Signalsmith implementation was committed locally as `02c4bf3`; the initial remote push failed because GitHub HTTPS failed twice, but the 2026-08-04 retry succeeded.

The stretch implementation now borrows the part of HiFiShifter that is relevant to this project: Signalsmith Stretch as a public, pitch-preserving PCM time-stretch backend with output length driven by the requested frame count. The complete HiFiShifter editor, pitch curves, formant controls, and processor chain remain out of scope.

The runtime boundary is now explicit. For short filename-labeled material with a detected or label-derived vowel core, `plan_material_stretch_clips()` reports `stretch_backend=signalsmith`. `process_material_clip_with_progress()` decodes the source through `soundfile`, sends consonant attack, vowel core, and coda regions to Signalsmith separately, concatenates their exact target frame counts, and then uses FFmpeg only for effects, fades, encoding, and final duration correction. If `python-stretch` is unavailable or the source cannot be decoded by `soundfile`, the prior Rubber Band filter graph is used and the failure is surfaced through progress diagnostics.

This addresses the specific limitation in the old path: the vowel core was capped at Rubber Band tempo `0.35` and then loop-filled for more extreme expansion. Signalsmith can request the actual target frame count, so the clip is no longer forced into that loop-fill branch when the native backend is available. This does not make arbitrary four-times expansion natural; quality warnings and strict thresholds remain unchanged, and Signalsmith itself still needs comparison against real vocal material.

Verification:

1. `python -m unittest discover`: 166 tests passed.
2. `python -m compileall -q audio_processor tests`, `python -m audio_processor check`, and `git diff --check` passed.
3. A direct Signalsmith sine test and an end-to-end FFmpeg material render both produced exactly 2.000000 seconds from a 0.5-second source.
4. The six-minute PlasticLove real-eval attempt produced no completed case and is recorded as incomplete, not as evidence of quality improvement.

The next measurement should be a bounded one-clip or small-subset A/B comparison between Signalsmith and Rubber Band, followed by a larger real-eval only after the new backend's continuity and naturalness metrics are observed. Do not remove fallback behavior or relax strict gates.

## Latest Closeout - 2026-08-03

The branch now has a stable separation of responsibilities: filename labels define trusted short-material pronunciation before ASR, while the reference side retains lyrics/original-vocal timing authority. Rendering preserves exact target duration and has a vowel-core-aware expansion path instead of treating every short clip as uniformly stretchable.

The remaining risk is not an unbounded stretch limit. Sparse or weakly matched material cannot be made natural merely by extending the vowel core. The next engineering work should use generic near-phonetic substitutes and adaptive attack/core/coda allocation, measured first against the focused PlasticLove vmzJP smoke and then against broader rendered evaluation. Do not relax strict gates, hard-code material names, or reverse engineer proprietary Vegas/Melodyne implementations.

## Latest Update - 2026-08-03 Vowel-Core Stretch Strategy

This round moved the stretch pipeline one level closer to the behavior the user asked for. Instead of treating a short clip as one block and stretching or looping the whole file, the renderer now attempts to identify a vowel core and split the clip into consonant attack, vowel core, and optional coda regions. The new `audio_processor.phoneme` module uses local `librosa.pyin` voiced/F0 analysis when available, but it always has a filename-label fallback so the pipeline does not depend on perfect acoustic segmentation.

The implementation is intentionally heuristic, not a proprietary clone. It is based on public audio-processing ideas: voiced-region detection, per-region stretch allocation, and final exact-duration correction. The voice-like region carries most of the extra duration, while consonant regions are only lightly expanded. When no reliable vowel core is available, the code falls back to the existing short-text loop-fill path.

The practical effect is visible in the render graph. Short material now uses `syllable_vowel_core_stretch`, which emits a split FFmpeg graph with `asplit`, per-region `atrim`, per-region Rubber Band, and final concat plus duration correction. Cache keys were bumped to `material_render_filter_v7_vowel_core_stretch`, and preflight now reports `vowel_core_stretch` boundary conditioning for this path.

Verification:

1. `compileall -q audio_processor tests` passed.
2. Targeted unit tests for vowel-core short-material stretching passed.
3. Full `unittest discover` passed with 164 tests.
4. `audio_processor check` passed.
5. Synthetic FFmpeg smoke on `shi.wav` from 0.5s to 2.0s passed.
6. Rendered real smoke at `.tmp\vowel-core-real-smoke\real-eval-20260803-224804\summary.md` completed with `status=rendered`, `render_duration_delta_ratio.mean=0.0`, `rendered_audio_alignment_score=0.782933`, and `stretch_quality_score=0.293067`.

Residual risk is still the same class of problem as before: the system can now preserve more natural attack/core structure, but it still cannot manufacture missing phonetic evidence. `weak_text_signal`, `single_syllable_extreme_stretch`, `continuity_warning_ratio`, and `single_syllable_boundary_risk` remain high for the PlasticLove vmzJP cohort, which means the next improvement should be better candidate selection and a more careful breakdown of very sparse syllables.

## Latest Update - 2026-08-03 Filename-Label-First Material Authority

This round implemented the next architecture target from the 2026-08-02 resume notes: material filename labels must become the primary text authority before material ASR runs. The previous "label authority" path was still ASR-first; it could overwrite hallucinated material transcripts after transcription, but it still paid the ASR cost and still allowed stale ASR-first cache payloads to appear authoritative unless cache policy happened to reject them.

The material side now has an explicit filename-label-first branch. `analyze_material_library()` probes duration, evaluates `_material_filename_label_policy()`, and for parseable short labels creates `AudioAnalysis` directly from `_filename_text_hint()`. These analyses have `analysis_source="filename_label_authority"`, `material_text_source="filename_label_authority"`, `asr_skipped_for_filename_label=true`, parsed filename text units, parsed filename phonetic units, and a full-duration transcript segment marked `timing_source="filename_label_authority"`. `_transcribe_audio()` is not called for these trusted label clips.

The cache and report contract was upgraded at the same time. Material cache format is now `vocal_process_material_cache_v4_filename_label_first`, cache payloads include `material_label_analysis_strategy="filename_label_first_v1"`, and `render_material_analysis()` serializes the new structured diagnostics. This rejects stale ASR-first material caches instead of letting old transcripts pollute matching reports or JSON diagnostics.

The target side intentionally stayed unchanged. Lyrics still define target units when present, and original-vocal ASR/alignment still supplies unit timing. Without lyrics, original-vocal ASR/aligned units continue to define the target lattice and timing. This keeps the current product model intact: cross-language or Chinese-voice material can serve Japanese targets when filename phonetic units match, and semantic phrase matching is not the primary path.

Verification:

1. `compileall -q audio_processor tests` passed.
2. Targeted filename/cache tests passed, including the regression that trusted `ha.wav` does not call `_transcribe_audio()`.
3. `ModelAssistTests` and `ModelRuntimeTests` passed with 76 tests.
4. Full `unittest discover` passed with 164 tests.
5. `audio_processor check` passed.
6. `git diff --check` passed with only CRLF conversion warnings.
7. Non-render `PlasticLove_JP__vmzJP` smoke at `.tmp\filename-label-first-smoke\real-eval-20260803-202928\summary.md` showed 104/104 vmzJP material files using `filename_label_authority`, 411 ordered materials, and 411/411 positioned and timed decisions.
8. Rendered `PlasticLove_JP__vmzJP` smoke at `.tmp\filename-label-first-render-smoke\real-eval-20260803-203308\summary.md` completed with `status=rendered`, `rendered_audio_alignment_score=0.782725`, `render_duration_delta_ratio.mean=0.0`, `match_ordering_score=0.37506`, `stretch_quality_score=0.290289`, and strict pass still false.

Residual risk is now more isolated. The PlasticLove material analysis no longer shows JP/vmzJP clips being treated as Chinese ASR text, but audio quality remains limited by sparse material coverage and stretch pressure: `weak_text_signal=171`, `low_match_score=4`, `single_syllable_extreme_stretch=277`, `single_syllable_boundary_risk=277`, and `continuity_warning_ratio=0.744526`. The next durable improvement should be generic near-phonetic substitute selection and stretch allocation for missing/sparse syllables, not song-specific exceptions, long phrase semantic matching, or relaxed strict thresholds.

## Latest Update - 2026-08-01 Segment-Lattice Ordering for Unit-Timing Coverage

The full rendered suite at `tests_real\output\real-eval-20260801-164819\summary.json` proved the render-duration path is now stable: every compatible case rendered and `render_validation.status` was `ok`, with output duration deltas effectively zero. The remaining urgent timeline defect was no longer final WAV length. It was target-unit coverage, especially `PlasticLove_JP__vmzJP`, where only about 30% of decisions had one-to-one reference positions and timed target durations.

The root cause was a mismatch between two reference-unit lattices. Ordering built a single aggregate reference string and computed JP phonetic units from that aggregate; timeline localization later mapped decisions back to per-segment ASR/aligned units. In long mixed JP/romaji/English ASR text, the aggregate parser expanded the text to 1349 pseudo units, while the segment-derived reference lattice had only 411 target units. After those 411 units, the system kept creating unpositioned material decisions and filled them with weighted durations. That preserved total output duration after render correction, but it destroyed the one-to-one target/reference unit contract.

`audio_processor.model_assist` now builds a segment-derived reference phonetic lattice for the `reference_phonetic_unit_sequence` strategy. Candidate lookup, material reuse, decision localization, and tone-aware matching all use that lattice, so the decision list is bounded by the actual target units. Extra material files are not appended after the reference target lattice is exhausted. This better matches both priority modes: without lyrics, the original-vocal ASR/aligned units define targets; with lyrics, lyric text should supply the target lattice before timings are retargeted from the original vocal.

Cached-data recomputation for `PlasticLove_JP__vmzJP` shows the architectural effect without a full model rerun: `decision_count=411`, `positioned_decision_count=411`, `timed_target_duration_count=411`, `bad_positioned=0`, and `target_duration_total_seconds=308.72381`. The remaining risk is material quality and stretch naturalness, not timeline coverage for this failure class. A non-rendered real-eval smoke timed out before case completion, but it wrote a partial recoverable summary at `.tmp\segment-lattice-smoke\real-eval-20260801-223958\summary.json`.

Verification passed with focused ordering/runtime tests, full `unittest discover` with 164 tests, `compileall`, `audio_processor check`, and `git diff --check` with only CRLF conversion warnings.

## Latest Update - 2026-08-01 Short-Text Loop Fill for Extreme Expansion

本轮把短字音长目标的渲染问题从“时长准确但后段静音”推进到“时长准确且有声音内容填充”。旧策略 `syllable_formant_expand_with_tail_fill` 在短文本素材扩展超过 Rubber Band 安全下限时，会先把素材拉到 `tempo=0.35`，再用 `apad` 补齐剩余目标时长。对于真实评测里 0.3-0.6 秒素材被分配到 2 秒、5 秒甚至更长目标时长的场景，这会形成大段静音尾部，正是用户此前反馈的拉伸听感差、拼接连续性弱的根源之一。

新策略 `syllable_formant_expand_with_loop_fill` 保留 Rubber Band 的 `pitch=1:formant=preserved`，但在极限短文本扩展时用 FFmpeg `aloop=loop=-1:size=2147483647:start=0` 循环填充，再用 `atrim` 精确裁到目标时长，并保留短淡入淡出。渲染缓存 key 已通过 `material_render_filter_v6_short_text_loop_fill` 失效旧缓存，避免旧静音尾补结果复用。批量渲染和 DAW 导出都传递 `text_hint`，因此短文本判定在 flat wav 与 DAW timeline 中一致。

诊断层同步更新：per-clip stretch plan 现在会写出 `loop_fill+fade_in_out`，preflight 的 `render_continuity.boundary_conditioning` 会聚合为 `short_text_loop_fill+per_clip_fade_in_out`。自然度评分对 loop fill 略微减轻短文本极限扩展惩罚，但仍保留 `single_syllable_boundary_risk`，因为循环填充只能消除静音尾部，不能保证真正的音素级 formant/F0 连续。

验证结论：

1. 单元验证通过：`compileall -q audio_processor tests packaging`，完整 `unittest discover` 163 项，`audio_processor check`，`git diff --check`（仅 CRLF 提示）。
2. 真实 FFmpeg smoke 证明 0.5 秒输入经 `rubberband + aloop + atrim` 可稳定输出 `2.000000s`。
3. 真实单 case render smoke：`.tmp\loop-fill-render-smoke\real-eval-20260801-160655\summary.json`。`FengZhongYouDuo_CN__newOTTO` 为 `rendered`，`render_validation.status=ok`，输出 `259.276576s`，参考 `259.276190s`，`duration_delta_ratio=1e-06`，`rendered_audio_alignment_score=0.839569`，`stretch_quality_score=0.692922`，`boundary_conditioning=short_text_loop_fill+per_clip_fade_in_out`。

剩余架构风险仍在选材和素材覆盖层：loop fill 避免了静音尾部，但极长单音节目标仍可能出现循环边界感；严格通过失败仍来自 `reference_asr_unverified`、1 个低匹配和素材缺失/极端拉伸风险。下一轮完整 rendered real-eval 应重点比较 JP/vmzJP 最差组，并继续做低匹配候选选择、近音替代和素材缺失诊断，而不是放松 strict thresholds。

## Latest Update - 2026-07-31 极短片段安全路径与失败输出隔离

这轮修复把真实验收里的两个边界问题拆开处理。第一个问题是极短目标片段在 FFmpeg Rubber Band 路径上会失败：`1000nenyikiteru_JP__vmzJP` 中最短片段 `ka1.wav` 的目标时长只有 `0.014074s`，原先直接走 `rubberband` 时触发 `Operation not permitted`。第二个问题是渲染失败后旧输出还留在原路径，`real_eval` 继续 probe 旧 wav 时长，导致“渲染失败却看起来有输出”的假阳性。

现在 `audio_processor.engine` 对 `target_duration <= 0.030s` 的片段不再调用 Rubber Band，而是直接用 `apad + atrim + asetpts` 做安全截断/补齐。这一层仍保持精确时长控制，但避免了极短压缩比下的 FFmpeg 音频过滤器错误。`MaterialStretchClip.stretch_strategy` 会标记为 `tiny_target_direct_trim`，`render_material_stretch_plan()` 也会把 formant 说明切换成 `direct_trim_no_pitch_shift`，让诊断更直观。

`audio_processor.batch` 在 overwrite 模式下会先删除旧输出文件，再启动渲染，并写入 `outputs.stale_removed` 诊断事件。`audio_processor.real_eval._render_validation()` 则对 `render_failed` 的 batch 结果直接跳过输出时长 probe，避免旧文件污染分数。这样渲染失败和真实输出短缺不再混在一起。

验证已经闭环：`python -m unittest discover` 通过 162 项，`python -m audio_processor check` 通过，真实 FFmpeg 烟测中 14ms 目标片段可稳定输出 `0.014083s`，单 case 真实验收 `1000nenyikiteru_JP__vmzJP` 也恢复为 `rendered`，`render_duration_delta_ratio=0.0`，输出 wav 时长重新对齐到原人声 `194.155102s`。说明这次修复真正把“短片段滤镜失败”和“旧输出污染评分”从评分链路里剥离了。

下一轮完整 real-eval 仍要继续盯 `stretch_quality_score`、`continuity_warning_ratio` 和 JP/vmzJP 的长尾单字连续性，但这已经不再受最短片段 FFmpeg 失败和旧输出误判干扰。

## Latest Update - 2026-07-31 渲染时长强校验与短字音 formant 拉伸

本轮把渲染链路从“计划层时长正确”推进到“输出文件真实时长必须正确”。最新自治跑分中 `1000nenyikiteru_JP__vmzJP` 暴露了关键矛盾：timeline 目标总时长已经等于原人声 `194.155102s`，但渲染 wav 实际只有 `123.641062s`。这说明继续只优化 ASR/排序无法解决验收问题，必须在 FFmpeg 输出边界加真实 probe 和自动修复。

实现上，`audio_processor.engine` 现在对每个缓存素材片段、新渲染素材片段、单素材直出和最终拼接成品都执行目标时长校验。缓存文件只有在实际时长落入严格容差内才允许复用；错长缓存会被删除并重渲染。新渲染或最终 concat 输出如果仍有偏差，会先写入同目录临时文件，用 `apad/atrim/asetpts` 做精确时长校正并再次 probe，校验通过后再替换原文件。缓存 key 已随 `MATERIAL_RENDER_FILTER_FORMAT` 升级，避免旧 filter 版本输出继续污染新评估。

听感连续性方面，短字音极端扩展不再只把 Rubber Band 限制到 `tempo=0.75` 后大量补静音；新的 `syllable_formant_expand_with_tail_fill` 使用 `tempo=0.35` 的共振峰保持拉伸，再做尾部补齐。该策略仍会把极端素材短缺标记为风险，因为 0.1-0.5 秒字音硬拉到长音本质上受素材质量限制，但它减少了长音中无声空白的占比，适合下一轮真实输出听感复查。

DAW 导出现在复用同一套 `_ensure_audio_duration()` 保护，避免 flat wav 输出和 DAW timeline clip 的实际时长不一致。验证已经覆盖 mock 层和真实 FFmpeg 小样本：0.5 秒素材按新短字音策略拉伸/补齐到 2.0 秒，probe 输出为 `2.0s`。全量单测在补齐 `pypinyin` 与 `huggingface_hub` 后通过 158 项，`audio_processor check` 通过，`git diff --check` 仅剩 CRLF 提示。

剩余评估重点是完整 rendered real-eval 的实际分数，而不是单个 smoke：下一轮后台自治必须关注 `render_duration_delta_ratio` 是否从约 `0.33` 降到接近 0、`render_validation.status` 是否转为 `ok`，以及 `stretch_quality_score` / `continuity_warning_ratio` 是否随短字音策略改善。如果真实听感仍有明显断裂，下一步应在候选选择层增加“同音/近音但更长、更稳定素材优先”的全局排序约束，并评估跨片段边界平滑。

## Latest Update - 2026-07-31 Japanese Mora Guard and JP ASR Backend Protection

This continuation targeted the Japanese failure mode reported by the user: the previous evaluation path could still route Japanese reference/material analysis through the Chinese FunASR backend, and the timeline layer could split Japanese long vowels into extra clip slots. The runtime now carries CN/JP language hints into reference analysis, material analysis, and cache keys; when a JP hint would otherwise use `funasr`, the backend is guarded over to a multilingual backend instead of reusing the Chinese-only transcript path. The guard is recorded in notes and cache fingerprints so old FunASR Japanese results do not get reused silently.

On the phonetic side, `audio_processor.model_assist` now normalizes Japanese kana/romaji into mora-level units. Long vowels such as `ー`, repeated vowels, and common `ou/ei` lengthening no longer produce extra independent units, and sokuon/促音 no longer becomes a separate clip slot. Japanese kana with dakuten/handakuten are preserved during accent stripping, so `パ` stays `pa` instead of collapsing to `ha`. For JP language hints, compact substring fallback is disabled so a unit like `u` cannot match inside `su`, which was the main cause of the extra `u.wav` and `a.wav` decisions in long-vowel songs.

Verification passed again with `.venv311\Scripts\python.exe -m unittest discover` (155 tests) and `audio_processor check`. A real single-case JP render smoke was attempted against `PlasticLove_JP__vmzJP`, but it timed out before any case completed; the partial report only shows the suite header and planned case count, so it does not yet provide new render evidence. The code path itself is now covered by unit tests and by the direct `model_runtime._transcribe_audio(..., language_hint="JP")` guard test that proves FunASR is skipped for JP.

## Latest Update - 2026-07-31 FunASR Timestamp Resample for Missing Unit Coverage

This continuation closed the remaining `missing_aligned_unit_timing` gap without changing score thresholds or adding corpus-specific exceptions. The failure class was a density mismatch between FunASR timestamp spans and the normalized reference unit lattice: the previous path returned an empty timing tuple when neither direct one-to-one timestamp mapping nor ASCII expansion explained the mismatch. That behavior was too brittle for no-lyrics references, where the original-vocal ASR/aligned pronunciation units are the only target-unit source.

`audio_processor.model_runtime` now keeps the original FunASR timing span as the source authority and resamples its monotonic timestamp boundary lattice onto the normalized reference units. Exact timestamp/unit matches still use the precise direct mapping; the new path only activates when the timestamp count is incompatible with the target unit lattice. The resulting units are tagged as `funasr_timestamp_resampled`, which keeps them gap-sensitive while making diagnostics distinguish precise timestamp mapping from mismatch recovery. The reference cache key was bumped to prevent stale empty unit-timing reports from masking the change.

Full rendered verification at `tests_real\output\real-eval-20260731-102644\summary.json` completed 13/13 rendered cases. Compared with `real-eval-20260731-091722`, suite timing quality improved: `missing_aligned_unit_timing` 1 -> 0, `planning_alignment_score.mean` 0.764292 -> 0.783788, `planning_alignment_score.min` 0.442402 -> 0.695851, `rendered_audio_alignment_score.mean` 0.740518 -> 0.755482, `rendered_audio_alignment_score.min` 0.482704 -> 0.644692, and `render_duration_delta_ratio.mean` 0.330804 -> 0.329436. The worst affected reference `PlasticLove_JP__vmzJP` moved from no aligned timing coverage to `reference_unit_timing_count=361`, `positioned_decision_count=658`, `timed_target_duration_count=658`, `timed_target_duration_ratio=1.0`, and `aligned_timing_score=1.0`.

The change did not materially affect CN groups, and JP/vmzJP remains the worst cohort. Residual issues shifted toward render/stretch duration mismatch and low-confidence material ordering: `strict_render_pass_count` remains 0, `low_match_score` rose 128 -> 133, `moderate_stretch_ratio` rose 1191 -> 1200, `single_syllable_extreme_stretch` improved 1910 -> 1878, and `ambiguous_phonetic_position` remained 0. The next durable improvement should target render bounds and stretch allocation so exported concatenated material audio duration tracks the reference after unit timing coverage is complete.

## Latest Update - 2026-07-31 Timeline Lattice Resample for Target-Unit Coverage

This continuation widened the timing bridge in `audio_processor.model_runtime` beyond the previous lyric-retarget path. When a reference segment has fewer aligned unit timings than the unit lattice implied by its text and positioned decisions, the runtime now resamples the segment-level timing lattice first and then resolves positioned spans against that complete lattice. That keeps positioned timing on the aligned-unit path instead of dropping later decisions to proportional segment split just because the source alignment is coarser than the text lattice.

The change is intentionally general. It does not special-case songs or filenames, and it does not lower duration tolerances. It treats the original aligned timing span as the source truth, but projects it onto the target unit lattice whenever the existing timing density is too sparse for one-to-one coverage. A regression now covers the 4-target-unit / 2-source-unit case so the coverage behavior stays locked in.

Full rendered real-eval verification at `tests_real\output\real-eval-20260731-080741\summary.json` completed 13/13 rendered cases. Suite metrics improved from the prior full run: `planning_alignment_score.mean` 0.736374 -> 0.764292, `rendered_audio_alignment_score.mean` 0.717274 -> 0.740518, `render_duration_delta_ratio.mean` 0.340026 -> 0.330804, and `missing_aligned_unit_timing` 5 -> 1. JP timing coverage improved most: `by_language JP` planning mean 0.56293 -> 0.64081 and render mean 0.559459 -> 0.625362. `1000nenyikiteru_JP__vmzJP`, `kamippoina_JP__vmzJP`, and `LAB=01_JP__vmzJP` now report `timed_target_duration_count == positioned_decision_count`; only `PlasticLove_JP__vmzJP` still lacks aligned unit timings entirely.

## Latest Update - 2026-07-31 Lyric Timing Resample for Target-Unit Authority

This continuation tightened the lyric-to-timing bridge in `audio_processor.model_runtime`. `_retarget_unit_timings_to_text()` now preserves exact source timings when the lyric unit count matches the original aligned timing count, and falls back to a monotonic resample across the source timing span when the counts differ. That means Japanese lyric collapse can still keep one target phrase while expanding to pronunciation units without dropping timing coverage.

The practical effect is that lyric text remains the absolute target-unit authority, while original vocal timing still defines the rendered start/end span for each unit whenever aligned source timing exists. This reduces the chance that annotation collapse, kana/romaji normalization, or coarse source alignment will zero out `unit_timings` and force duration planning back to a weaker proportional split.

Verification passed with the new lyric coverage regression, the full `tests.test_engine.ModelRuntimeTests` class, `python -m unittest discover` with 145 tests, `compileall -q audio_processor tests`, and `audio_processor check`. The remaining architectural gap is segment-level pairing when lyric and transcript segment counts diverge more than the current per-segment retargeting can cover.

## Latest Update - 2026-07-30 Cancellation-Aware Real-Eval Repair

This continuation focused on making the rendered real-eval loop recoverable enough for the next pronunciation-ordering pass. The latest full rendered run reached real model execution but failed 12 of 13 compatible cases because Python/SpeechBrain introspection loaded the optional `speechbrain.integrations.k2_fsa` module and then failed on missing `k2`. The retained compatibility layer keeps unloaded SpeechBrain lazy modules from resolving `__file__` through optional imports, while preserving normal lazy import behavior for real attributes.

The real-eval pipeline now has first-class cancellation semantics. `build_preflight_report()` accepts and forwards `should_cancel`; `audio_processor.real_eval` accepts callback and stop-file cancellation, passes it through analysis and render queue execution, records cancelled cases as `cancelled`, writes exit code 130 into `recommended_exit_code`, and writes report files with atomic replacement. `scripts\run_real_eval_render_full.ps1` accepts `-StopFile` and honors `VOCAL_PROCESS_STOP_FILE`. The project maintenance runner now injects that environment variable into child tasks, writes stdout/stderr directly to log files, polls running children, terminates them on stop-file creation, and records `stopped` rather than waiting for long task timeouts.

Verification passed with compileall, 128 unit tests, `audio_processor check`, PowerShell script parsing, `git diff --check` with only CRLF warnings, and a script-level stop-file smoke that returned 130 with `status_counts={"cancelled":13}`. The repair was committed and pushed to `origin/codex/cancel-phonetic-accuracy` as `4a15239 Improve real eval cancellation recovery`. The next acceptance step is a full `scripts\run_real_eval_render_full.ps1` run to confirm the SpeechBrain lazy import failures are gone, then resume scoring improvements from the resulting `score_summary` and `group_score_summary`.

## Standing Project Rules

The durable project rules are maintained in `PROJECT_RULES.md`. In particular, completed work branches should replace `main` after verification, while the previous `main` is backed up to a separate archive/backup branch first. Major verified changes should be committed and pushed to GitHub automatically unless the user explicitly asks to keep them local. Whenever `main` is updated, `README.md` must include an update log directly after the download section.

## 2026-07-27: Full Portable Release Assets

GitHub Release `v2026.07.27-portable-full-runtime` publishes the two full portable ZIP packages: `VocalProcess-portable.zip` for normal GUI/CLI and local model testing, and `VocalProcess-portable-vst3.zip` for DAW/VST3 bridge testing. The `-lite` ZIPs remain smoke-test-only assets and should not be presented as full functional packages.

## 2026-07-27: Core Ordering v2 Implementation

The core material ordering path now uses a long-term planner shape instead of another local scoring patch. `audio_processor.model_assist` builds a full reference-segment by material-clip score matrix and uses global assignment when there are enough reference segments for one-to-one matching. Each decision records transcript, filename, phonetic, duration, speaker, VAD, evidence count, confidence label, and reference segment index.

Lyrics timestamps are parsed for LRC/SRT, but they are treated as timing priors only. When ASR/acoustic segments exist, acoustic timing remains primary and lyric text is paired onto those segments. Timing conflicts are reported through `lyric_timing_conflict` notes and preflight warnings instead of being silently trusted.

Short material handling now includes pinyin/phonetic matching through `pypinyin` when installed, short-clip evidence gating, and a first syllable-safe stretch strategy. Extreme expansion of short material clips uses `syllable_safe_expand_with_tail_padding`, limiting core Rubber Band stretching and filling the remaining target duration with padding.

Runtime and integration optimizations added in the same architecture pass:

1. Automatic ASR skips uncached Faster Whisper/WhisperX models unless `VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD=1`, avoiding repeated offline Hub lookup delays during manual tests.
2. Normal flat WAV assembly can render clips through `.vocalprocess_render_cache` and reuse exact duplicate source/target/render-option matches before final concatenation.
3. VST3 bridge requests accept `progress_path` and write atomic JSON progress updates while the helper runs.

Verification passed with `.venv311\Scripts\python.exe -m compileall -q audio_processor tests packaging`, `.venv311\Scripts\python.exe -m unittest discover` with 66 tests, `pip check`, `audio_processor check`, and a real CLI `batch` smoke that produced output WAV plus diagnostics containing `score_matrix`, `phonetic_similarity`, `syllable_safe_expand_with_tail_padding`, and `batch.item.completed`.

## 2026-07-23: Python and FFmpeg Environment Setup

### Goal

The project requires normal system access to Python and FFmpeg. The environment must use standard installation and PATH configuration instead of project-level wrappers, bundled binaries, or hard-coded bypasses.

### Initial State

1. `python` and `py` resolved first to WindowsApps execution aliases, which failed in this Codex session.
2. A user-local Python shim existed at `C:\Users\WIN11\AppData\Local\Python\bin\python.exe` and could run Python 3.14.4 when invoked by full path.
3. `ffmpeg` was not installed or not available on PATH.
4. Chocolatey was installed and usable.

### Decision

Use Chocolatey as the standard Windows package manager for this machine:

1. Install Python through the Chocolatey `python` package.
2. Install FFmpeg through the Chocolatey `ffmpeg` package.
3. Rely on normal Machine PATH entries created by the installers.
4. Verify with command-line checks and a Python-to-FFmpeg subprocess call.

### Result

Installed packages include:

1. `python 3.14.6`
2. `python3 3.14.6`
3. `python314 3.14.6`
4. `ffmpeg 8.1.2`

Resolved command paths in a refreshed shell environment:

1. `python`: `C:\Python314\python.exe`
2. `py`: `C:\WINDOWS\py.exe`
3. `ffmpeg`: `C:\ProgramData\chocolatey\bin\ffmpeg.exe`

Verification passed:

1. `python --version`: `Python 3.14.6`
2. `pip --version`: pip from `C:\Python314\Lib\site-packages`
3. `py --version`: `Python 3.14.6`
4. `ffmpeg -version`: `ffmpeg version 8.1.2`
5. Python successfully invoked `ffmpeg -version` through `subprocess.run`.

### Notes

The current Codex process inherited the old PATH before installation. A newly opened terminal should use the updated Machine PATH automatically. For commands run inside this existing Codex session, refreshing PATH from the Machine and User environment variables before verification reflects the new terminal state.

## 2026-07-23: Audio Processor MVP

### Goal

Continue the project with a first usable Python and FFmpeg implementation. The MVP should avoid non-standard workarounds and use normal system tools through PATH.

### Current Context

1. The repository had no application source files yet.
2. Python and FFmpeg were verified at the system level.
3. The branch for this work is `codex/update-python-and-continue-project`.
4. `choco upgrade python -y` reported that Python 3.14.6 is already the latest version available from the configured Chocolatey source.

### Scope

Build a command-line MVP with:

1. Environment check for Python, FFmpeg, and FFprobe.
2. Audio metadata probing through FFprobe JSON output.
3. Audio processing and conversion through FFmpeg.
4. Basic controls for trimming, gain, normalization, high-pass/low-pass filters, sample rate, channels, and codec.
5. Unit tests for command construction and probe summary formatting.

### Project Shape

The MVP uses a small standard Python package:

1. `audio_processor/cli.py` contains the command-line interface.
2. `audio_processor/engine.py` contains FFmpeg/FFprobe integration and validation.
3. `pyproject.toml` defines package metadata and a future console script entry point.
4. `tests/` contains focused unit tests.

### Notes

FFmpeg is invoked with `subprocess.run` using argument lists, not shell strings. This keeps quoting and path handling predictable on Windows.

### Verification

Passed:

1. `python -m unittest discover`
2. `python -m compileall audio_processor tests`
3. `python -m audio_processor check`
4. FFmpeg generated `.tmp\input.wav`
5. `python -m audio_processor process .tmp\input.wav .tmp\output.mp3 --normalize --gain-db -3 --sample-rate 44100 --channels 2 --overwrite`
6. `python -m audio_processor probe .tmp\output.mp3`

## 2026-07-23: GUI, Batch Queue, Config, Progress, and Install Entry

### Goal

Extend the command-line MVP into a usable local desktop workflow while staying with standard Python and system FFmpeg. The requested scope is GUI, batch queue, saved configuration, progress feedback, and install/packaging support.

### Technical Direction

1. Use Tkinter and ttk from the Python standard library for the GUI.
2. Keep FFmpeg as the processing engine and continue invoking it through argument lists.
3. Add FFmpeg progress parsing with `-progress pipe:1` so the GUI can show item and queue progress.
4. Store user defaults as JSON under the normal user config directory.
5. Expose both CLI and GUI entry points through `pyproject.toml`.

### Scope

Implement:

1. File queue with add/remove/clear controls.
2. Shared processing settings for queued files.
3. Sequential batch processing with per-item status.
4. Progress bar and textual status updates.
5. Persistent settings such as output directory, output extension, overwrite flag, gain, normalization, filters, sample rate, channel count, and codec.
6. Standard package entry points: `audio-processor` and `audio-processor-gui`.

### Non-goals For This Pass

1. Single-file Windows EXE bundling. That should be handled later with PyInstaller or a similar packaging tool after the workflow stabilizes.
2. Parallel FFmpeg jobs. Sequential processing is simpler and safer for a first desktop MVP.
3. Per-file custom settings. Shared settings keep the queue predictable for the initial GUI.

### Implementation Result

Added:

1. `audio_processor/gui.py` with a Tkinter desktop interface.
2. `audio_processor/batch.py` with sequential batch queue execution.
3. `audio_processor/settings.py` with JSON-backed user settings.
4. FFmpeg progress parsing in `audio_processor/engine.py`.
5. `audio-processor-gui` package script and `audio-processor gui` CLI subcommand.
6. Expanded tests for progress arguments, settings persistence, queue output naming, and CLI registration.

### Behavior

The GUI supports:

1. Adding multiple files to a queue.
2. Removing selected files and clearing the queue.
3. Shared output and processing settings.
4. Saved settings under the user config directory.
5. Sequential batch processing.
6. Per-item status and progress plus overall queue progress.
7. Cancellation.

The output path generator avoids overwriting the input file when the output extension matches the source extension by appending `_processed`.

### Packaging Notes

Editable install and wheel building are supported through `pyproject.toml`.

During verification, `pip install -e .` initially failed because the isolated build environment could not download `setuptools>=69` from the default source. Chocolatey source search also reported a source access issue. The project-level fix was to install `setuptools` into the virtual environment from official PyPI and run standard local builds with `--no-build-isolation`.

Verified commands:

1. `.venv\Scripts\python -m pip install setuptools -i https://pypi.org/simple`
2. `.venv\Scripts\python -m pip install -e . --no-build-isolation`
3. `.venv\Scripts\audio-processor check`
4. `.venv\Scripts\audio-processor process .tmp\installed_cli_input.wav .tmp\installed_cli_output.mp3 --normalize --overwrite`
5. `.venv\Scripts\audio-processor probe .tmp\installed_cli_output.mp3`
6. `.venv\Scripts\python -m pip wheel . -w .tmp\wheelhouse --no-deps --no-build-isolation`
7. `.venv\Scripts\python -m unittest discover`
8. `.venv\Scripts\python -m compileall audio_processor tests`

## 2026-07-23: GUI Language Switch

### Goal

Add a language switch control to the graphical interface so users can choose Chinese or English. All fixed GUI text should be localizable, and Chinese should be available across the full GUI.

### Scope

Implement:

1. A GUI language switch button/menu.
2. Chinese and English translation resources.
3. Persisted language preference in the existing settings JSON.
4. Runtime refresh of window title, toolbar buttons, table headings, setting labels, action buttons, dialogs, status text, and known queue status messages.
5. Tests for language normalization, settings persistence, and translation coverage for the GUI keys.

### Non-goals

1. Translating raw FFmpeg output or operating-system file dialog chrome.
2. Translating command-line help in this pass. The request is specifically for the graphical interface.

### Implementation Result

Added:

1. `audio_processor/i18n.py` with Chinese and English GUI translation resources.
2. `language` field in `ProcessingSettings`, persisted to the existing settings JSON.
3. A language menu button in the GUI toolbar.
4. Runtime refresh for window title, toolbar buttons, table headings, settings labels, action buttons, dialog titles/messages, status text, and known batch status messages.
5. Tests for language normalization, translation key coverage, translated status labels, and persisted language settings.

### Behavior

The GUI now defaults to Chinese. Users can switch between Chinese and English from the language button. The choice is saved immediately and reused on the next launch.

Raw FFmpeg messages and operating-system file dialog chrome remain controlled by FFmpeg/Windows and are not translated by the application.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover`
2. `.venv\Scripts\python -m compileall -q audio_processor tests`
3. `.venv\Scripts\python -c "from audio_processor.i18n import translate; print(translate('zh','language_menu')); print(translate('en','language_menu'))"`
4. `.venv\Scripts\audio-processor check`
5. `.venv\Scripts\python -m pip wheel . -w .tmp\wheelhouse --no-deps --no-build-isolation`

## 2026-07-24: Split GUI Upload Areas

### Goal

Change the GUI so uploaded inputs are no longer represented as one large combined file area. The interface should clearly separate original audio files, the material set, and the lyrics file into three distinct upload regions, each showing its supported format rules.

### Scope

Implement:

1. Original audio upload region for supported audio files such as `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, `.aac`, `.aiff`, `.alac`, and `.wma`.
2. Material set upload region that accepts a folder only.
3. Lyrics upload region for lyric/document files such as `.txt`, `.doc`, `.docx`, `.lrc`, and `.srt`.
4. Persisted material folder and lyrics file paths in the existing settings JSON.
5. GUI controls for selecting and clearing each source type.
6. Runtime validation that the material set is a directory and the lyrics input is a supported file.
7. Chinese and English GUI text for the new regions and validation messages.

### Non-goals

1. Parsing `.doc` or `.docx` lyrics content in this pass. The GUI should accept and track the file path.
2. Using the material folder to drive audio processing rules in this pass. The current processing queue still operates on original audio files.

### Implementation Result

Added:

1. Three separate GUI upload regions: original audio, material set, and lyrics file.
2. Visible supported-format hints for each region.
3. Material set selection through a folder picker only.
4. Lyrics selection through a file picker filtered to `.txt`, `.doc`, `.docx`, `.lrc`, and `.srt`.
5. `material_directory` and `lyrics_file` fields in persisted settings.
6. Runtime validation for material folder and lyrics file paths before saving settings or starting a batch.
7. Startup/batch log entries that show active material folder and lyrics file when provided.
8. Chinese and English translation keys for the new controls, hints, and validation messages.

### Behavior

Original audio files still create the processing queue and are the only inputs passed to FFmpeg. The material set and lyrics file are now first-class GUI inputs: they are selected separately, displayed separately, validated, persisted, and shown in logs, but they are not parsed or consumed by the audio processing engine yet.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover`
2. `.venv\Scripts\python -m compileall -q audio_processor tests`
3. `.venv\Scripts\python -c "from audio_processor.i18n import TRANSLATIONS; print(len(TRANSLATIONS['zh']), len(TRANSLATIONS['en']))"`
4. `.venv\Scripts\audio-processor check`

## 2026-07-24: Material Audio Stretch Assembly

### Goal

Correct the core processing model. The original audio is the timing/reference target, while audio clips in the material set should be combined and time-stretched/compressed to match that target. The material set must be consumed by the processing engine, not merely saved as a path.

### Critical Audio Constraint

Material clips must not be looped, hard-cut, or truncated to fit. If the material audio is shorter or longer than the target timing, it should be time-stretched or time-compressed. The stretch operation should preserve pitch and formants as much as possible, because those properties strongly affect listening quality, pronunciation recognition, and the perceived identity of single syllables.

### Technical Direction

Use FFmpeg's `rubberband` filter when available:

1. `tempo` controls duration.
2. `pitch=1` preserves pitch.
3. `formant=preserved` preserves formants.
4. Other quality-oriented options should favor intelligibility over speed.

The current machine's FFmpeg build exposes the `rubberband` filter and supports `formant=preserved`.

### Implementation Scope

1. Scan material folders for supported audio files.
2. Sort material files deterministically by filename.
3. Concatenate material clips in order.
4. Compare material total duration against the original audio duration.
5. Apply high-quality time-stretch/compression so the assembled material duration matches the original reference duration.
6. Export a DAW-importable audio file.
7. Keep GUI behavior aligned: original audio is the reference; material set is the source audio to assemble; output is the stretched assembled material.

### Non-goals

1. Perfect phoneme-level alignment in this pass.
2. Full lyric parsing/alignment in this pass.
3. Claiming zero perceptual change; the implementation should minimize audible damage, but extreme stretch ratios will still affect quality.

### Implementation Result

Added:

1. Material folder scanning through the shared supported-audio extension list.
2. Deterministic material ordering by filename.
3. Material assembly FFmpeg command generation using `concat` followed by `rubberband`.
4. One-file material support through the same `rubberband` chain, without a special loop/cut path.
5. Progress-aware material assembly used by the batch queue whenever a material folder is selected.
6. GUI start validation that requires a material folder for assembly, while still allowing settings to be saved before every source is selected.
7. Default `.wav` output and automatic `pcm_s24le` codec for WAV files.
8. GUI trim start and duration fields removed from the assembly workflow to avoid implying direct material truncation.

### Behavior

For each queued original audio file:

1. The original audio is probed only for reference duration.
2. Supported audio files in the material set are concatenated in filename order.
3. The total material duration is compared to the reference duration.
4. FFmpeg `rubberband` applies `tempo = material_duration / reference_duration`.
5. `pitch=1` and `formant=preserved` are used to reduce pitch/formant damage.
6. The graph does not use `stream_loop`, `atrim`, or direct duration truncation.
7. If `rubberband` returns a slightly short result, `apad=whole_dur=<reference_duration>` pads only the tail to the reference duration.

### Verification

Passed:

1. `.venv\Scripts\python -m compileall -q audio_processor tests`
2. `.venv\Scripts\python -m unittest discover`
3. Generated a 4 second reference WAV and two 1 second material WAV files with FFmpeg.
4. Ran the project assembly engine on those files.
5. FFprobe confirmed the assembled output is `format_name=wav`, `codec_name=pcm_s24le`, and `duration=4.000000`.

## 2026-07-24: Portable Windows Package

### Goal

Create a first portable Windows ZIP for users who do not have Python or FFmpeg installed. The expected user workflow is unzip, open the GUI executable, select source files/folders, and process audio without command-line setup.

### Technical Direction

1. Use PyInstaller to build a windowed GUI executable.
2. Ship the real FFmpeg and FFprobe binaries beside the app, not Chocolatey shim executables.
3. Update runtime tool resolution so bundled `bin\ffmpeg.exe` and `bin\ffprobe.exe` are preferred over system PATH.
4. Include FFmpeg license and README files in the portable package.
5. Keep the package as a folder inside a ZIP so all required files stay together.

### Implementation Result

Added:

1. `packaging/vocal_process_gui.py` as a stable PyInstaller GUI entry point.
2. `scripts/build_portable.ps1` to rebuild `dist\VocalProcess-portable.zip`.
3. `packaging/README_PORTABLE.txt` for end users.
4. `packaging/THIRD_PARTY_NOTICES.txt` for bundled FFmpeg notices.
5. Engine runtime lookup for bundled tools in the executable directory or `bin` subdirectory.
6. Test coverage proving bundled `bin\ffmpeg.exe` is preferred when PATH is unavailable.

### Package Layout

The generated ZIP contains:

1. `VocalProcess\VocalProcess.exe`
2. `VocalProcess\bin\ffmpeg.exe`
3. `VocalProcess\bin\ffprobe.exe`
4. `VocalProcess\licenses\FFmpeg-LICENSE.txt`
5. `VocalProcess\licenses\FFmpeg-README.txt`
6. `VocalProcess\README_PORTABLE.txt`
7. `VocalProcess\THIRD_PARTY_NOTICES.txt`
8. PyInstaller `_internal` runtime files.

### Verification

Passed:

1. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`
2. `.venv\Scripts\python -m unittest discover`
3. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1`
4. Package created at `dist\VocalProcess-portable.zip`, size about 87 MB.
5. Bundled `bin\ffmpeg.exe -version` and `bin\ffprobe.exe -version` both run and include `--enable-librubberband`.
6. `VocalProcess.exe` smoke-tested by starting the GUI process for five seconds; it did not exit or crash.
7. ZIP extraction smoke test passed into `.tmp\portable-extract-test`.

### Remaining Test Gap

The package still needs a clean-machine test in Windows Sandbox or another Windows account with no Python/FFmpeg installed. The current machine has development tools installed, so this pass verifies package structure and executable startup, not a fully isolated end-user machine.

## 2026-07-24: Portable FFprobe JSON Error Hardening

### Goal

Fix the portable GUI error reported by two users:

`the JSON object must be str, bytes or bytearray, not NoneType`

The fix should preserve the material stretch assembly workflow and make failures actionable for GUI users.

### Investigation

There are two JSON parsing paths in the project:

1. Settings loading in `audio_processor/settings.py`.
2. FFprobe metadata parsing in `audio_processor/engine.py`.

The settings path reads text from disk before calling `json.loads()`, so it cannot normally pass `None` to `json.loads()`. The FFprobe path called `json.loads(result.stdout)` directly. If FFprobe returned success but produced no captured stdout, or if the packaged/windowed runtime yielded an empty captured output for a specific user input, the raw Python `TypeError` would escape to the GUI.

The relevant runtime path is:

1. GUI starts batch processing.
2. `run_batch_queue()` chooses material assembly when a material folder is selected.
3. `assemble_material_to_reference_with_progress()` probes the reference and material files for duration.
4. `probe_audio()` invokes FFprobe and parses JSON.

Local reproduction with generated WAV files and the bundled `bin\ffprobe.exe` returned valid JSON, so the immediate defect is not the stretch/concat graph. The defect is missing boundary validation around FFprobe JSON output.

### Implementation Result

Changed:

1. `probe_audio()` now rejects `None` or empty FFprobe stdout with `AudioProcessorError`.
2. Invalid JSON output is wrapped as `AudioProcessorError` with a short preview of the returned text.
3. Unexpected non-object JSON output is rejected.
4. Tests now cover both `stdout=None` and invalid JSON.

This does not make unreadable/corrupt user audio magically processable, but it prevents the unhelpful low-level `json.loads(None)` error from reaching users. The GUI will now show a clearer FFprobe metadata error identifying the file path.

### Build Output Cleanup

PyInstaller writes ordinary INFO/WARNING diagnostics to stderr, which the Codex terminal renders as red text. The build itself was successful. The build script now captures PyInstaller output and only prints it when PyInstaller exits with a non-zero code. Successful builds show only the generated ZIP path.

The build script also validates deletion targets before removing old portable output, so generated paths must stay under the project root even when the optional app name parameter is changed.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 21 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`.
3. Generated reference/material WAV files and assembled output through the real FFmpeg workflow.
4. Bundled `bin\ffprobe.exe` confirmed output `format_name=wav`, `codec_name=pcm_s24le`, and `duration=3.000000` for the assembled result.
5. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1` rebuilt `dist\VocalProcess-portable.zip` with clean success output.
6. ZIP extraction structure check passed.
7. Bundled FFmpeg and FFprobe version checks passed and include `--enable-librubberband`.
8. Final post-script-change verification repeated unit tests, compile checks, portable rebuild, and ZIP extraction.
9. `scripts/smoke_portable.ps1` was added as the standard portable smoke test and passed after user approval.

### Remaining Test Gap

The hidden GUI EXE smoke test now passes through `scripts/smoke_portable.ps1`. A clean-machine test is still useful: run the rebuilt ZIP in Windows Sandbox or another Windows account with no Python/FFmpeg installed, using the same original audio and material set that triggered the user report.

## 2026-07-24: Standard Portable Smoke Test

### Goal

Make the portable package verification repeatable and auditable. Every future portable build should be smoke-tested by the assistant before the ZIP is handed to users.

### Implementation Result

Added `scripts/smoke_portable.ps1`.

The script:

1. Verifies `dist\VocalProcess-portable.zip` exists.
2. Extracts it into `.tmp\portable-smoke-test`.
3. Checks for `VocalProcess.exe`.
4. Checks bundled `bin\ffmpeg.exe`.
5. Checks bundled `bin\ffprobe.exe`.
6. Starts `VocalProcess.exe` hidden for a short runtime.
7. Fails if the GUI executable exits immediately.
8. Closes or kills the process after the smoke-test window.

The script validates its extraction directory before deleting old smoke-test output, so the cleanup target must stay under the project root.

### Permission Flow

Launching a GUI executable requires escalated permission in the Codex sandbox. The user approved and saved this command prefix:

`powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1`

Future portable-package work should run this script first after every rebuild.

### Build Auditing

The portable build now includes `BUILD_INFO.txt` in the ZIP. It records:

1. Build time.
2. Git branch.
3. Git commit.
4. Whether the build included uncommitted working-tree changes.

The current confirmed ZIP includes a build marker from branch `codex/fix-portable-json-probe`, commit `4f08ee2`, with uncommitted working-tree changes included. Final confirmed build time: `2026-07-24 12:52:46 +08:00`. Final confirmed SHA256: `246BBD9B0BF762AEC3A48822E6A028470FFB8C7CAF326637270F03B47850B31A`.

### Verification

Passed:

1. `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1`.
2. ZIP extraction and `BUILD_INFO.txt` readback.
3. `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1`.
4. `.venv\Scripts\python -m unittest discover`.
## 2026-07-24: Structured Diagnostics and Model Assisted Vocal Pipeline

### Goal

Address the user-reported failure where material vocal stretch assembly often finished without useful results and sometimes failed silently. The new work should first make failures diagnosable, then prepare the architecture for open-source model-assisted vocal recognition and ordering.

### Technical Direction

1. Add per-run JSONL diagnostics so every batch item writes an auditable trail.
2. Record the processing mode, settings snapshot, reference metadata, material metadata, and exception details.
3. Add a model-assist layer that can represent Demucs, Silero VAD, pyannote.audio, WhisperX, Whisper, and SpeechBrain as optional backends.
4. Keep the current portable package small by making the model backends optional and not bundled by default.
5. Expose the architecture through CLI and documentation before wiring heavy inference into the GUI.

### Implementation Result

Added:

1. `audio_processor/diagnostics.py` for JSONL event logging.
2. Batch-level logging in `audio_processor/batch.py`.
3. `audio_processor/model_assist.py` for candidate models, backend availability checks, transcript-based ordering helpers, and pipeline planning.
4. `audio_processor cli models` for inspection and JSON export.
5. `docs/MODEL_ASSISTED_VOCAL_PIPELINE.md` for root-cause analysis and future architecture.
6. README and portable-user docs updated with the current limitation and log location.

### Verification

Passed:

1. `python -m unittest discover` with 33 tests.
2. `python -m compileall -q audio_processor tests packaging`.
3. Real end-to-end batch run on generated reference and material WAV files.
4. The run produced `reference.diagnostics.jsonl` with stages `batch.item.started`, `inputs.reference`, `inputs.materials`, and `batch.item.completed`.
5. `python -m audio_processor models` listed the candidate open-source backends.
6. `python -m audio_processor models --json` printed the pipeline plan as JSON.

### Notes

The project now has real inference wiring for the core backends: Demucs, Whisper, Silero VAD, and a guarded SpeechBrain speaker-embedding path. The remaining blockers are external:

1. `pyannote.audio` requires Hugging Face authorization/token and has not been installed yet.
2. `whisperx` is currently blocked in this Python 3.14 environment because its published dependency pin expects `ctranslate2==4.4.0`, which is not available for this runtime.
3. SpeechBrain embedding is intentionally cache-gated so user runs do not hang on first-time Hugging Face downloads.

The present change is now a working model-assisted pipeline, not just a placeholder architecture.
## 2026-07-24: Portable Zip Synced With Local Model Runtime

The current user request was to sync and smoke-test the portable zip first, with no size optimization yet, then prepare git upload and record the outcome.

Completed work:

1. The portable build now includes the lazy model runtime packages by copying `.venv\Lib\site-packages` into the frozen app `_internal` folder.
2. The portable build now bundles `.tmp\model-cache` into `VocalProcess\models` so Whisper and Silero caches travel with the package.
3. `audio_processor.model_runtime` now falls back through portable, project-local, config, and temp cache roots instead of trying `%AppData%` first.
4. `scripts/smoke_portable.ps1` now supports `-ReuseExtract` so the assistant can finish the startup check without re-extracting a large zip.

Verified results:

1. `.venv\Scripts\python -m unittest discover` passed with 35 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging` passed.
3. `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1 -ReuseExtract` passed.
4. `dist\VocalProcess-portable.zip` exists and contains the bundled runtime, models, and build marker.

Build artifact:

- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `7F1E9ED10374A01139AD2717A3596B12408467F7EA9BDC365A3ECAB00B451820`
- Size: `753,289,753` bytes
- Build marker branch: `codex/local-pretrained-portable-sync`
## 2026-07-24: Final Artifact Refresh After Commit

The final portable zip was rebuilt after the local commit so the build marker now matches the committed source.

Final artifact:

- Commit: `30a66f3`
- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `9A52C2FFA215E32E87E064F27FB124B12CC5683EE98D2A97568ADD417829A88D`
- Smoke test: `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1 -ReuseExtract -ExtractRoot dist\VocalProcess-portable`
## 2026-07-24: Final Portable Batch Verification

The portable EXE now supports a headless `batch` command through the same wrapper that opens the GUI when no arguments are given. This allowed a real portable model-assisted run, not just a startup smoke test.

Verified portable result:

1. `VocalProcess.exe check` works from the portable package.
2. `VocalProcess.exe batch ...` produced `reference.wav` and `reference.diagnostics.jsonl`.
3. Diagnostics recorded `model.ordering.completed`, `batch.item.completed`, and Demucs vocal separation cache paths.
4. The portable smoke helper `scripts/smoke_portable_model.ps1` now creates TTS fixtures, runs the portable batch command, and checks the generated output.

Final artifact:

- Commit: `fa1935d`
- Zip: `dist\VocalProcess-portable.zip`
- SHA256: `35DBB2AFBD2A79A0D1CF72C8441E4CA19BDF93B87B9A4399470FEC7C2507A68D`
- Smoke command: `powershell -ExecutionPolicy Bypass -File scripts\smoke_portable_model.ps1 -PortableRoot dist\VocalProcess-portable\VocalProcess -WorkRoot .tmp\portable-model-smoke-final`

## 2026-07-25: Robust Diagnostics, Cache, Device, and UI Runtime Work

### Root Cause

The user-provided red error log showed `FFprobe returned no JSON metadata` during `_log_input_diagnostics()`. That diagnostics step was probing every reference/material file before model ordering. When FFprobe returned empty stdout for a WAV file, diagnostics raised `AudioProcessorError`, so rendering could fail before the actual model pipeline or FFmpeg render stage had a chance to run.

### Implementation Result

1. `probe_audio()` now falls back to Python `wave` metadata for WAV files and to parsing FFmpeg stderr when FFprobe JSON is empty, invalid, or the ffprobe command fails.
2. Input diagnostics now record reference/material metadata failures as warning fields instead of aborting the job.
3. Batch items now track elapsed runtime; completion, cancellation, and errors write elapsed seconds into diagnostics.
4. Model ordering accepts `compute_device` and resolves `auto` to CUDA when available, otherwise CPU.
5. Demucs, WhisperX, Whisper, Silero VAD, torch-hub Silero, and SpeechBrain paths now receive the resolved device where supported.
6. Material folders now store `.vocalprocess_material_cache.json`; cache reuse is keyed by file path, suffix, size, mtime, file count, and ASR model.
7. Material ordering now uses filename pronunciation text as a correction signal alongside ASR transcript, helping clips named by syllable/word when ASR is weak.
8. Lyrics are explicitly optional in GUI behavior and logging.
9. GUI now displays elapsed runtime, keeps live progress bars, supports edge resize, and exposes saved window sizes.
10. CLI `batch` now accepts `--compute-device`.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 41 tests.
2. `.venv\Scripts\python -m py_compile audio_processor\model_runtime.py audio_processor\model_assist.py audio_processor\batch.py audio_processor\engine.py audio_processor\gui.py audio_processor\settings.py audio_processor\i18n.py audio_processor\cli.py tests\test_engine.py`.
3. Actual source batch smoke without lyrics: `.venv\Scripts\python -m audio_processor.cli batch .tmp\local-model-smoke-current\reference.wav .tmp\local-model-smoke-current\out\reference.wav --material-directory .tmp\local-model-smoke-current\materials --compute-device cpu --overwrite`.
4. Second source batch run reused `.vocalprocess_material_cache.json` and completed in about 9 seconds.

### Next Tasks

1. Rebuild and smoke-test the portable ZIP from the current source.
2. Commit, push, and refresh release artifact if portable verification passes.
3. Continue VST3/DAW bridge work after this diagnostics/model-stability round is released.

## 2026-07-25: Per-Clip Stretch Planning and VST3 Bridge Helper

### Goal

Respond to manual test feedback that material vocal recognition/order and stretch rendering were still not producing intelligible results consistently. The priority is to make ordering more explainable, reduce avoidable stretch damage to one-syllable/one-character clips, and continue the VST3 bridge path without attempting unsafe real-time Python/FFmpeg processing inside a plug-in callback.

### Technical Decisions

1. Keep model-assisted ordering mandatory when a material folder is selected.
2. Treat filename text as an explicit pronunciation hint because manual tests showed ASR can be weak on short material clips.
3. Score duration closeness separately because extreme stretch ratios are a direct cause of degraded syllable intelligibility.
4. Do not invent speaker similarity when no real embedding is available.
5. Render flat WAV output with per-clip Rubber Band stretching before concatenation, not one global stretch after concatenating all material.
6. Use the same per-clip stretch plan for DAW timeline export.
7. Continue VST3 as an offline bridge first: native VST3 should call a helper process through a small protocol, not embed Python/model inference in the real-time path.

### Implementation Result

1. `audio_processor.model_assist` now records transcript, filename, duration, speaker, and VAD score components.
2. Short-reference scoring weights filename and duration more heavily so one-syllable material can be matched when ASR output is empty or wrong.
3. `audio_processor.model_runtime` attaches per-material target duration hints to ordering decisions and diagnostics reports.
4. `audio_processor.engine` now exposes `MaterialStretchClip`, `plan_material_stretch_clips()`, and `render_material_stretch_plan()`.
5. Flat WAV material assembly now builds a filter graph that stretches each input independently, then concatenates the stretched clips.
6. `audio_processor.batch` writes a `render.stretch_plan` JSONL event before rendering.
7. `audio_processor.daw` now uses the same per-clip target durations and records per-clip tempo and quality warnings in manifest/CSV output.
8. `audio_processor.vst3_bridge` adds a JSON bridge request/response helper.
9. CLI adds `vst3-bridge --template` and `vst3-bridge request.json --response response.json`.
10. Documentation was updated for the current per-clip stretch strategy and VST3 helper boundary.

### Verification

Passed:

1. `.venv\Scripts\python -m unittest discover` with 48 tests.
2. `.venv\Scripts\python -m compileall -q audio_processor tests packaging`.
3. Actual source batch smoke using cached local model analysis.
4. Diagnostics contained `render.stretch_plan` with per-clip tempos and no quality warnings in the smoke sample.
5. FFprobe confirmed the smoke output WAV as `pcm_s24le`, `22050 Hz`, mono, about `5.19s`.
6. `audio_processor.cli vst3-bridge --template` returned a valid request template.
7. Portable ZIP rebuild succeeded.
8. Portable GUI smoke passed after reusing the extracted tree.
9. Portable model smoke initially exposed that a windowed PyInstaller EXE cannot be invoked like a normal console process in PowerShell smoke scripts.
10. `scripts/smoke_portable_model.ps1` now validates Tcl/Tk data folders and uses `Start-Process -PassThru` to wait for the windowed EXE and read its exit code.
11. Portable model smoke passed after the script fix.

### Remaining Work

1. Commit and push this branch after portable verification.
2. Rebuild the portable ZIP once more after commit so `BUILD_INFO.txt` points at the final source commit.
3. Test the JSON bridge helper from a DAW-side script or minimal native VST3 prototype.
4. Keep tuning ordering weights using real manual-test diagnostics, especially bad ASR, bad filename hints, and extreme stretch cases.

## 2026-07-25: Native VST3 Bridge and Preflight Gate

### Goal

Turn the VST3 work from a placeholder helper into a functional bridge path, and reduce manual-test friction by making recognition/order quality inspectable before rendering.

### Implementation Notes

1. The Python bridge helper now supports both blocking single-file requests and a persistent watch loop:
   - request glob: `*.request.json`;
   - response: `<request_id>.response.json`;
   - heartbeat: `bridge.heartbeat.json`;
   - processed request archive: `<request_id>.done.json`.
2. `audio-processor vst3-bridge --contract` exposes the bridge contract for a native plug-in, DAW script, or external controller.
3. `audio-processor analyze` generates `analysis.json` without rendering audio. This report combines:
   - model ordering report;
   - transcript, filename, duration, speaker, and VAD scores;
   - target duration per clip;
   - per-clip Rubber Band tempo;
   - moderate/extreme stretch warnings;
   - low-match and weak-text review warnings.
4. Batch diagnostics now emits `model.ordering.review_required` if the lowest match score is below the safety threshold or if the stretch plan contains warnings.
5. A native JUCE/MSVC VST3 bridge plug-in was added:
   - source: `native\vst3_bridge`;
   - build script: `scripts\build_vst3_bridge.ps1`;
   - output: `build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3`.
6. The plug-in is deliberately a pass-through control surface. It does not run Python, FFmpeg, model inference, or file rendering inside `processBlock`.
7. The plug-in launches the helper process from its editor UI for `analyze` and `render` operations. This is the correct boundary for DAW safety.
8. Portable builds now include the VST3 bundle under `VocalProcess\plugins` if the native bundle has already been built.

### Verification

1. Unit tests passed: 54 tests.
2. Python compile check passed for `audio_processor`, `tests`, and `packaging`.
3. Native VST3 Release build passed with Visual Studio 18/CMake/JUCE 8.0.13.
4. Generated bundle structure was inspected and includes:
   - `Contents\x86_64-win\VocalProcess Bridge.vst3`;
   - `Contents\Resources\moduleinfo.json`.

### Remaining Risk

1. The native VST3 still needs real host validation in REAPER/Cubase/Studio One/Ableton. Build success confirms a loadable VST3 bundle is produced, but host behavior must be tested in each DAW.
2. `analysis.json` improves manual review but does not guarantee perfect ASR on noisy or one-character clips. The review warnings are intended to prevent silent trust in low-quality ordering.
3. JUCE licensing must be respected when distributing the plug-in.

## 2026-07-25: VST3 Host Validation and Melodyne Handling

### Goal

Move the native bridge from "built successfully" to "known loadable in real host paths", while handling Melodyne honestly as a workflow target rather than pretending an unavailable 64-bit host test passed.

### Implementation Notes

1. Added `native\vst3_probe`, a small JUCE host-side executable that scans and instantiates a supplied VST3 bundle.
2. Added `scripts\probe_vst3_bridge.ps1` to build the probe with the same JUCE/MSVC environment used by the bridge and run it against the current `VocalProcess Bridge.vst3`.
3. Added `scripts\host_test_reaper_vst3.ps1` to launch REAPER with an isolated config, point `vstpath64` at the bridge build directory, wait for scanning, and verify `reaper-vstplugins64.ini`.
4. Added `scripts\install_vst3_bridge.ps1` to copy the complete VST3 bundle into the common 64-bit VST3 folder for DAW scanning.
5. Added `scripts\host_test_flstudio_vst3.ps1` to launch FL Studio Plugin Manager and inspect its verified database after a scan attempt.
6. Added `scripts\check_melodyne_context.ps1` to distinguish current 64-bit Melodyne/Celemony availability from legacy 32-bit installs.

### Verification

1. JUCE headless probe passed:
   - found one `VocalProcess Bridge` VST3 description;
   - instantiated it successfully;
   - reported 2 inputs and 2 outputs.
2. REAPER 7.33 x64 passed:
   - used an isolated config under `.tmp\reaper-vst3-host-test`;
   - cached `VocalProcess Bridge (VocalProcess)` in `reaper-vstplugins64.ini`.
3. The bridge was installed to:
   - `C:\Program Files\Common Files\VST3\VocalProcess Bridge.vst3`.
4. FL Studio 2024 Plugin Manager launched, but did not expose a documented non-interactive scan command or create a new automatic verified entry during the scripted run.
5. Melodyne/Celemony detection now reports standard 64-bit paths on `E:\` and legacy 32-bit paths on `D:\`/`E:\`. Melodyne is still not treated as a generic VST3 host for validating the bridge.

### Current DAW Position

1. REAPER is validated as a real VST3 scanner/host path.
2. FL Studio should scan the installed bridge from the common VST3 folder, but the final registration step requires Plugin Manager > Find installed plugins.
3. Melodyne should be treated as an editor/ARA workflow target. The practical supported path is exported PCM WAV or DAW timeline output, then Melodyne editing inside a compatible 64-bit DAW.
4. No hand-written host database edits should be used for FL Studio or Melodyne because that would create avoidable manual-test risk.

## 2026-07-25: Runtime Optimization and Portable Package Split

### Goal

Reduce manual-test friction and runtime without lowering output correctness. The immediate issue is that a 28-second reference vocal can take 7-10 minutes, which would scale poorly to 2-5 minute songs.

### Open-Source Research Direction

The practical candidates for this project are:

1. Faster Whisper / CTranslate2 for faster Whisper-compatible ASR.
2. whisper.cpp for possible future native CPU-first ASR packaging.
3. SpeechBrain ECAPA for speaker embedding and voice similarity.
4. pyannote.audio for diarization when Hugging Face token/model terms are available.
5. Librosa and MSAF for future repeated-section and self-similarity analysis.

### Implemented Decisions

1. Package variants:
   - default no-VST3 package: `VocalProcess-portable.zip`;
   - VST3 host-test package: `VocalProcess-portable-vst3.zip`.
2. `build_portable.ps1` now accepts `-IncludeVst3Bridge`; `build_portable_variants.ps1` builds both variants.
3. The non-VST3 package is compressed with optimal ZIP compression and no longer automatically includes the VST3 bundle.
4. A `source_separation` setting was added:
   - `auto`: current conservative behavior;
   - `never`: skip Demucs when the reference is already isolated vocals;
   - `always`: force source separation when available.
5. GUI exposes source separation as direct radio buttons, because this is a common manual-test decision.
6. CLI and VST3 bridge requests expose the same setting.
7. Reference analysis is now cached by reference file snapshot, lyrics file snapshot, ASR backend, compute device, and source separation mode.
8. Model objects are cached inside the process to avoid reloading Whisper/VAD/SpeechBrain-style models for every material clip.
9. Optional Faster Whisper support is implemented but non-blocking.
10. DAW timeline export reuses exact duplicate stretched clip renders.
11. Preflight analysis now reports safe duplicate render reuse groups and repeated reference text groups.

### Why This Is Safe

1. Skipping Demucs is user-controlled. It is only correct when the reference is already isolated vocals.
2. Model object caching does not change recognition results; it removes repeated model initialization.
3. Reference analysis caching is keyed by source file, lyrics file, ASR backend, compute device, and source-separation mode.
4. Render reuse only applies when source file, target duration, tempo, and render options are identical.
5. Repeated verse/chorus detection is currently a report hint, not an automatic replacement for ASR/text matching.

### Verification

1. Python compile check passed.
2. Unit tests passed with 57 tests.
3. Faster Whisper install was attempted but timed out; current environment still lacks `faster_whisper` and `ctranslate2`.
4. Native VST3 rebuild passed after adding source-separation UI.
5. JUCE headless VST3 probe passed after rebuild.
6. Both portable package variants were built and inspected; the standard package has no VST3 entries, and the VST3 package includes the bridge.
7. Portable GUI smoke passed for both variants using completed extraction directories.
8. REAPER isolated VST3 scan passed after rebuild.
9. Melodyne context script found standard 64-bit Celemony/Melodyne Studio 4 paths plus legacy 32-bit paths.

### Remaining Work

1. Rebuild both portable package variants and run smoke tests.
2. Rebuild the native VST3 after the editor source-separation UI change.
3. Retry Faster Whisper installation with a longer timeout or a Python/runtime version known to have compatible CTranslate2 wheels.
4. Add real music-structure analysis only after the exact/review-only paths are stable. The next safe step is section-timing hints, not automatic lyric substitution.

## 2026-07-26: Runtime Version Direction

### Decision

The project should move away from the machine-default Python 3.14 runtime for production work. The long-term layout should be:

1. Main application/runtime: Python 3.11.
2. UVR headless separation worker: isolated Python 3.10 environment.
3. Native VST3 bridge: remains a JUCE/MSVC plug-in and should call the packaged helper or Python-side bridge by process boundary only.

### Rationale

1. Python 3.11 has better compatibility with the current model stack than Python 3.14, including Whisper-related packages and common PyTorch/audio wheels.
2. `uvr-headless-runner` is not suitable for the main Python 3.11 environment because it targets Python versions below 3.11.
3. Separating UVR into a worker avoids pinning the entire project to Python 3.10 while still allowing UVR5-style model execution through a controlled headless process.

### Implementation Direction

1. Tighten the main project Python requirement to the selected 3.11 line.
2. Add bootstrap scripts for:
   - `.venv311`: main app/runtime and packaging work;
   - `.uvr-worker`: UVR headless runner on Python 3.10.
3. Update build and package scripts to prefer the explicit Python 3.11 runtime instead of accidentally using Python 3.14.
4. Add project-side UVR worker detection and clear diagnostics before enabling automatic separation through UVR.

### Completed So Far

1. Python 3.11.9 is installed and used by `.venv311`.
2. Python 3.10.11 is installed and used by `.uvr-worker`.
3. The main project now requires Python `>=3.11,<3.12`.
4. Heavy model dependencies were moved out of default install requirements so the main runtime environment can be created quickly and predictably.
5. UVR worker setup is isolated in `requirements\uvr-worker-py310.txt` and pins `setuptools<81` for `pkg_resources` compatibility.
6. `audio_processor.uvr_worker` detects, validates, and calls the UVR headless runner by process boundary.
7. Source separation now tries the UVR worker before falling back to in-process Demucs when UVR is available and source separation is not explicitly skipped.
8. `scripts\check_uvr_worker.ps1` validates the Python 3.10 worker, runner entry points, package metadata, and installed-model lists without running full inference.

### Verified State

1. `.venv311` compile check passed.
2. `.venv311` unit tests passed with 60 tests.
3. `audio_processor check` reports `UVR Headless Runner: available`.
4. `audio_processor models` reports `UVR Headless Runner [source_separation]: available`.
5. `scripts\check_uvr_worker.ps1` passed for Demucs, MDX, and VR runner entry points.
6. Interim lightweight no-VST3 portable ZIP was rebuilt from Python 3.11 and smoke tested; current size was about 86 MB.
7. Interim lightweight VST3 portable ZIP was rebuilt from Python 3.11 and smoke tested; current size was about 89 MB and contains the native bridge bundle.
8. The default UVR `htdemucs` model was downloaded, `uvr-demucs --model-info htdemucs` resolves it from cache, and a real 1-second UVR separation smoke test produced a vocals stem.

### Superseded Risk Note

This interim risk state was superseded by the full portable runtime correction below. The tens-of-MB builds were reclassified as lightweight smoke packages, the default portable outputs were rebuilt as full model-runtime packages, and the bundled UVR worker path now has a real separation smoke test.

## 2026-07-26: Full Portable Runtime Correction

### Trigger

The user challenged why the new lightweight packages were only tens of MB and asked whether they were functionally complete. That challenge was valid: the packages built with `-SkipModelRuntime` were startup/UI/VST3 smoke packages, not full local model-runtime packages.

### Corrected Package Semantics

1. Default portable builds are now full model-runtime packages:
   - `dist\VocalProcess-portable.zip`;
   - `dist\VocalProcess-portable-vst3.zip`.
2. Lightweight packages are explicitly marked with `-lite` and must be requested with `scripts\build_portable_variants.ps1 -Lite`.
3. Full packages copy:
   - `.venv311\Lib\site-packages` into the frozen app runtime;
   - `.tmp\model-cache` into `VocalProcess\models`;
   - `.uvr-worker` into `VocalProcess\uvr-worker`.
4. Lite packages do not copy the complete model runtime, model cache, or UVR worker, and should only be used for startup/UI/VST3 packaging checks.

### Current Full Runtime

The Python 3.11 full runtime now includes PyTorch CPU, torchaudio, OpenAI Whisper, Demucs, SpeechBrain, Faster Whisper, Silero VAD, and Librosa. `pyannote.audio` and WhisperX remain outside the default full package because they are authorization-dependent or still experimental in this environment.

### Verified Outputs

1. `dist\VocalProcess-portable.zip`: 1,239,961,593 bytes, no VST3, full model runtime bundled.
2. `dist\VocalProcess-portable-vst3.zip`: 1,242,394,260 bytes, VST3 bridge bundled, full model runtime bundled.
3. `dist\VocalProcess-portable-lite.zip`: 388,107,297 bytes, lightweight startup/package smoke only.
4. `dist\VocalProcess-portable-lite-vst3.zip`: 390,541,294 bytes, lightweight VST3 package smoke only.

### Verification

1. Compile check passed in `.venv311`.
2. Unit tests passed with 60 tests in `.venv311`.
3. `audio_processor check` reports Demucs, UVR, Faster Whisper, OpenAI Whisper, Silero VAD, SpeechBrain, and Librosa available.
4. Standard full portable startup smoke passed.
5. VST3 full portable startup smoke passed.
6. Full portable model smoke passed for the already-isolated source-separation-skip path.
7. Full portable UVR worker smoke passed and produced a vocals stem from the bundled worker.

### Remaining Risk

1. CPU-only model inference can still be slow on full songs.
2. More UVR model variants need broader manual validation, but the bundled default worker/model path has a real smoke test now.

## 2026-07-26: WhisperX and pyannote Runtime Inclusion

### Decision

WhisperX and pyannote should be included in the default full Python 3.11 runtime rather than kept in the experimental requirements file. The latest available pair was not the best project fit because it forced a large PyTorch 2.8 runtime move during packaging. The stable inclusion target is:

1. `whisperx==3.4.5`.
2. `pyannote.audio==3.4.0`.
3. `ctranslate2==4.4.0`.
4. `transformers==4.57.6`.

### Compatibility Note

`pyannote.audio 3.4.0` expects legacy top-level `torchaudio` APIs that are absent from the current `torchaudio 2.11.0+cpu` package. The project now prepares a local compatibility layer before importing WhisperX or pyannote. This preserves the current main model runtime while allowing pyannote/WhisperX imports to succeed.

### Verified So Far

1. The packages are installed in `.venv311`.
2. `pip check` reports no broken requirements.
3. Direct import through the project compatibility layer works for WhisperX and pyannote.audio.
4. `audio_processor check` reports WhisperX and pyannote.audio available.
5. Compile check passed.
6. Unit tests passed with 60 tests.

### Remaining Before Release

1. Rebuild both full portable packages after the expanded runtime.
2. Smoke-test startup, model path, and bundled UVR worker from the rebuilt packages.
3. Commit and push branch `codex/runtime-env-uvr-worker`.

### Rebuild Result

1. `dist\VocalProcess-portable.zip`: 1,413,022,068 bytes.
2. `dist\VocalProcess-portable-vst3.zip`: 1,415,457,165 bytes.
3. Both packages passed startup smoke tests.
4. The no-VST3 full package passed model smoke with source separation skipped for an already-isolated fixture.
5. The no-VST3 full package passed bundled UVR worker separation smoke.
6. Portable `VocalProcess.exe check` reports WhisperX and pyannote.audio available.

## 2026-07-27: Melodyne 3.x Compatibility Target

### Result

1. The local Melodyne 3.2 standalone at `E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe` launches successfully and can be closed cleanly.
2. This machine has no detected Melodyne 5 executable in the checked Celemony paths.
3. Melodyne compatibility work should target Melodyne 3.x here, with 5.x only if a valid local installation becomes available.

### Follow-Up

1. Keep Melodyne smoke tests pointed at the 3.x executable path.
2. Treat Melodyne as an editor/ARA workflow target rather than a generic host for the VST3 bridge.

## 2026-07-27: Timeline Handoff Export for Melodyne and VEGAS

### Decision

The user clarified that the required Melodyne support is not using Melodyne as a VST3 host, but importing rendered audio while preserving the original time-axis relationship. The durable architecture is a shared timeline handoff layer, with host-specific output profiles generated from the same clip timeline data.

### Implementation

1. Added `audio_processor.handoff` as a common host handoff module.
2. Added `export-melodyne`, which renders:
   - `melodyne_full.wav` as the continuous timeline reference;
   - full-length per-clip lane WAVs with leading silence before each clip;
   - `melodyne_handoff.json` and `melodyne_handoff.csv`;
   - the existing REAPER `.rpp`, `timeline.json`, `timeline.csv`, and stretched clip audio for deeper DAW editing.
3. Added `export-vegas`, which renders the same complete reference/lane/manifest outputs and also writes Broadcast Wave timestamp clips under `vegas_bwf`.
4. The BWF `time_reference` value is computed as `round(start_seconds * sample_rate)`, so timestamp placement remains tied to audio sample positions rather than approximate frame labels.

### Verification

1. Added unit tests for Melodyne lane rendering command construction, full-timeline duration trimming, VEGAS BWF timestamp metadata, and CLI command registration.
2. Targeted unit tests passed for `TimelineHandoffTests` and `CliTests`.

### Remaining Host-Side Work

1. Manually import the generated `vegas_bwf` clips into VEGAS and confirm that the installed version places Broadcast Wave audio by timestamp as expected.
2. Manually review Melodyne full-timeline/lane import behavior in Melodyne 3.2 after real user audio is available.

## 2026-07-27: Portable Runtime Failure From Manual Test

### User Report

A remote/manual test on another machine failed during model-assisted material ordering. The first report's console and `.diagnostics.jsonl` both show the same fatal path:

1. `inputs.reference.metadata_failed` appeared first as a warning. This means reference metadata probing failed but the batch was still allowed to continue.
2. The actual fatal failure occurred during reference ASR: `Whisper transcription failed ... No module named 'torch._C'`.
3. The material folder metadata collection had progressed, so the immediate failure was not material discovery; it was the bundled speech-recognition runtime.

The second remote/manual test failed with a bare Windows message: `系统找不到指定的文件`. The diagnostics screenshot shows material metadata had been collected and the GUI displayed a raw system-level file-not-found message, which indicates an unwrapped external process start failure.

### Root Cause Assessment

`torch._C` is PyTorch's native extension. If Python can find the `torch` package but cannot import `torch._C`, the practical causes are:

1. an incomplete portable package;
2. using a `-lite` package for full model testing;
3. copying only `VocalProcess.exe` without `_internal`;
4. an extraction/antivirus failure that removed `_internal\torch\_C.cp311-win_amd64.pyd` or `torch\lib` DLLs.

The local freshly rebuilt full package contains `_internal\torch\_C.cp311-win_amd64.pyd` and `torch\lib`, and the extracted no-VST3 full package passed the real portable model smoke test.

The second failure is most consistent with OpenAI Whisper or WhisperX internally invoking `ffmpeg` by executable name. That call does not use `audio_processor.engine.resolve_tool()`. If a tester's machine has no FFmpeg on system PATH, Whisper can fail with a bare Windows `FileNotFoundError` even though the portable package contains `bin\ffmpeg.exe`.

### Fixes

1. Runtime availability now validates PyTorch's native extension, not just the presence of the `torch` package directory.
2. ASR starts with a speech-runtime preflight. If all usable ASR backends are blocked by a broken native runtime, the batch fails before rendering with a direct portable-runtime message.
3. Batch diagnostics now include `model.runtime.preflight`, recording ASR backend availability, torch native status, and model-cache state.
4. `scripts\build_portable.ps1` now fails full-package builds if required torch/Whisper files are missing.
5. Added `scripts\check_portable_runtime.ps1` for quick ZIP or extracted-directory validation before manual testing.
6. The application now prepends bundled runtime tool directories such as `VocalProcess\bin` to process `PATH`, so third-party ASR libraries can start the packaged FFmpeg.
7. Whisper/WhisperX `FileNotFoundError` is now rewritten as an explicit FFmpeg startup failure with a portable-folder check hint.

### Remaining Validation

1. Rebuild the full no-VST3 and VST3 packages after these changes.
2. Run portable runtime check and model smoke against the rebuilt package.
3. Upload refreshed Release assets so testers do not keep using a package that can produce the `torch._C` failure.

## 2026-07-28: Cancel Semantics and Pronunciation-First Matching Target

### Manual Test Finding

The GUI can show "cancellation requested" while underlying work keeps running. The architectural issue is that cancellation was only reliably checked at queue boundaries and during FFmpeg progress loops. Long model phases, including reference transcription, material transcription, VAD, speaker embedding, and cached ordering preparation, did not receive a shared cancellation signal. FFmpeg progress processing also depended on progress stdout producing another line before the cancellation check ran.

### Correction

Cancellation must be treated as a cross-layer cooperative control signal:

1. GUI owns the cancellation event.
2. Batch passes `should_cancel` into every stage.
3. Model runtime checks before and after expensive operations and while looping material clips.
4. FFmpeg child processes are actively terminated/killed on cancellation.
5. Diagnostics must record `batch.item.cancelled` rather than leaving users with an apparent running task.

### Pronunciation Matching Direction

The current v2 ordering engine already has score matrices, global assignment, and a phonetic score using `pypinyin`, but it is not strict enough for one-character/one-syllable materials. The next architecture target is pronunciation-first matching:

1. Treat material filenames as high-value human labels. Chinese characters, pinyin, and romanized filenames must be converted to pronunciation units before scoring.
2. Match pinyin/romanized filename units against the reference's Chinese text phonetic sequence, not only against literal transcript text.
3. Use phonetic position as an ordering signal, so filenames like `wo.wav`, `shi.wav`, `ni.wav` can align to reference text `我是你`.
4. Keep acoustic timing, duration, VAD, and speaker similarity as independent evidence. Filename pronunciation can be strong evidence, but low or conflicting acoustic evidence should still produce review diagnostics.
5. Do not claim true 100% automatic accuracy. The implementation target is to make failures visible as `review_required` when the evidence is insufficient; real 100% requires manual confirmation or a stronger forced-alignment backend.

### Open-Source Reference Assessment

Typeless/OpenTypeless-like systems are relevant as speech-input product references, especially provider orchestration, personal vocabulary, and post-processing. They are not enough for this project's core because the project needs character-level audio-to-timeline alignment, not only speech-to-text.

More directly relevant references are Chinese ASR and forced-alignment systems:

1. FunASR / Paraformer-style timestamped Chinese ASR for character or sentence timing.
2. Qwen3 ForcedAligner and CTC forced-alignment approaches for aligning known text to audio.
3. Montreal Forced Aligner for dictionary/phoneme-aware forced alignment and confidence boundaries.
4. WeNet-style CTC ASR as a future local Chinese recognition/alignment candidate.
5. WhisperX and Faster Whisper as already-integrated multilingual ASR/alignment backends.

### Active Implementation Plan

1. Complete cancellation propagation through model runtime.
2. Add pronunciation-position scoring for Chinese reference text versus pinyin/romanized material filenames.
3. Raise the weight of filename pronunciation for short references while still requiring corroborating evidence for a strong confidence label.
4. Extend diagnostics and tests around cancellation and pinyin filename ordering.
5. Continue toward forced-alignment integration after this cancellation/matching reliability pass.

### Implementation Result

This pass treated cancellation reliability as required infrastructure for the two long-term core metrics: pronunciation-first material ordering and pronunciation-level stretch alignment to the reference timeline.

Completed changes:

1. Cancellation is now a shared cooperative signal across batch, model runtime, ASR, VAD, speaker embedding, source separation, and external FFmpeg/UVR subprocess boundaries.
2. FFmpeg progress processes and UVR headless worker processes are actively terminated when cancellation is requested. In-process third-party model calls still cannot be interrupted mid-call, but every practical boundary before and after those calls now checks the signal.
3. Material filename pronunciation evidence was upgraded from general phonetic similarity to phonetic position matching. Chinese reference text is converted to pinyin units and matched against Chinese, pinyin, or romanized material filename hints.
4. Pinyin/romanized filename units are normalized by removing tone numbers and accepting common `v/u-umlaut/u` variants.
5. The ordering diagnostics continue to expose `phonetic_score`, `text_position`, `reference_segment_index`, confidence labels, and target durations. A pinyin filename can now provide both a pronunciation match and an ordering position.
6. Single reference segments matched to multiple per-character/per-syllable materials now produce split target durations based on their text/phonetic positions. This is the first durable bridge from ordering accuracy to pronunciation-level stretch alignment.
7. Score-matrix diagnostics now include exact pinyin/phonetic units, phonetic positions, and pronunciation-position candidate counts. Repeated or homophone positions are downweighted instead of being treated as unique strong matches.
8. Preflight warnings now include `ambiguous_phonetic_position` when a filename pronunciation matches multiple reference positions.
9. Ordering reports include a `timeline_alignment` summary that identifies split reference segments.
10. Added regression tests for:
   - pinyin filename ordering against Chinese reference text;
   - tone-number pinyin filename ordering;
   - repeated/homophone phonetic position downweighting;
   - ambiguous phonetic preflight warnings;
   - cancellation stopping material analysis before VAD/speaker embedding continues;
   - per-syllable target duration splitting from one reference segment;
   - phonetic position/unit diagnostics and timeline split summaries.

Verification:

1. `.venv311\Scripts\python.exe -m compileall -q audio_processor tests packaging` passed.
2. `.venv311\Scripts\python.exe -m unittest discover` passed with 80 tests.
3. `.venv311\Scripts\python.exe -m audio_processor check` passed and reported FFmpeg, UVR Headless Runner, Demucs, Faster Whisper, OpenAI Whisper, Silero VAD, SpeechBrain, WhisperX, pyannote.audio, and Librosa availability in the local Python 3.11 runtime.
4. `git diff --check` reported no whitespace errors, only CRLF conversion warnings.
5. Git commit is blocked in the active runtime because Git cannot create `.git\index.lock` even though a simple `.git` root write probe succeeds.
6. The refreshed no-VST3 portable package was rebuilt and passed:
   - portable runtime check;
   - extracted startup smoke;
   - model-assisted smoke with source separation skipped;
   - bundled UVR worker smoke.
7. The first VST3 portable ZIP build was interrupted by the 30-minute tool timeout after producing an incomplete about-234-MB ZIP. That file was renamed to `dist\VocalProcess-portable-vst3.broken-20260728-061407.zip` and must not be published.
8. A later VST3-only portable build completed and produced `dist\VocalProcess-portable-vst3.zip`; that ZIP passed portable runtime check, extracted startup smoke, model smoke, and bundled UVR worker smoke.
9. Native VST3 bridge probing is still blocked on this machine/session: `scripts\probe_vst3_bridge.ps1` fails during CMake compiler detection because MSBuild `Microsoft.Build.Utilities.FileTracker` raises `UnauthorizedAccessException` / `E_ACCESSDENIED`.

Current architectural limits:

1. Pinyin matching currently normalizes tone information for filename matching, so homophones such as `shi` still require acoustic/ASR/duration evidence or manual review.
2. Target duration splitting assumes the available reference segment duration should be distributed by recognized text/pronunciation unit spans. This is a useful bridge, but true character start/end accuracy still requires stronger forced alignment.
3. Third-party in-process ASR/Demucs calls cannot be killed mid-function safely. External FFmpeg and UVR worker processes can now be stopped promptly; future model workers should prefer subprocess or persistent-helper boundaries where cancellation must be hard.
4. Full VST3 portable packaging has passed package-level runtime/model/UVR smoke, but host/native VST3 probing still needs an MSBuild-permission-clean environment before Release asset replacement.

Next architecture direction:

1. Add a stronger character-timing backend or optional stage, likely CTC/forced-alignment style, for Chinese text-to-audio alignment.
2. Keep filename pronunciation as high-value human label evidence, but require corroborating acoustic evidence for high-confidence automatic decisions in ambiguous homophone cases.
3. Expand diagnostics so manual testers can inspect exact pinyin units, position matches, and duration splits before rendering.

### 2026-07-28 Real Test Follow-up

1. The project now has a persistent real-test harness under `tests_real/` with tracked docs/examples and ignored audio/output directories, so real samples can be kept in place without polluting source control.
2. `audio_processor.real_eval` now treats `origin_vocal` and `material_set` as a cross-product case source, while preserving CN/JP labeling from filenames and keeping caches under per-case output folders.
3. The cache redirection matters operationally: material analysis cache no longer has to live beside the source audio, which keeps the real test corpus cleaner and makes it easier to swap or refresh audio without incidental cache churn.
4. A real JP smoke case on `PlasticLove_JP__vmzJP` finished as `review_required`. That is a useful baseline rather than a defect: it shows the pipeline is conservative on live data and still expects review on many homophone-heavy material clips.
5. The latest verification pass reached 86 unit/integration tests plus one full real smoke run, so the pronunciation-ordering and timeline-splitting changes are now covered by both synthetic and real corpus checks.

### 2026-07-28 Cache Routing Follow-up

1. Normal model-assisted ordering now routes material analysis caches into the work cache tree instead of writing beside the source material directory. That keeps both the app flow and future real-test corpora cleaner.
2. The explicit cache-directory override remains in place for the `tests_real` harness and other controlled cases, so cache placement can still be forced when a caller wants it.
3. The user-facing copy was updated to match the new behavior, which reduces the risk that the GUI or README implies source-folder mutation where none should happen.
4. The current regression surface is now 87 tests, and the change stayed within the existing pronunciation-ordering/time-alignment architecture rather than adding a separate caching subsystem.

### 2026-07-28 Maintenance Session Runner

1. A single chat/API turn cannot be treated as a reliable 10-hour daemon because the model call has a bounded request lifecycle. Durable autonomy needs an external local process plus resumable state.
2. The repository now includes a maintenance-session runner that uses a JSON plan, heartbeat, state file, event log, task stdout/stderr logs, and a stop-file contract. This gives future long work a local process that can keep running after the chat turn ends.
3. `scripts/start_maintenance_session.ps1` launches that runner with `Start-Process -WindowStyle Hidden`, satisfying the local hidden-background-service requirement while keeping all state under ignored `.tmp\maintenance_sessions`.
4. This does not make the model itself reason unattended for 10 hours; it creates the missing process-supervision layer so long deterministic checks, real-test loops, and future resumable agent orchestration have a stable substrate.
5. The runner is covered by maintenance-plan and one-cycle execution tests, and the full regression suite now passes with 90 tests.

### 2026-07-29: Real-Test Evidence and Long-Run Plan

1. The user explicitly clarified that the relevant evidence is the project software's own real test outputs, not `.codex` maintenance logs.
2. The actual project evidence came from `tests_real/origin_vocal`, `tests_real/material_set`, and `tests_real/output/real-eval-20260728-170800/summary.json` plus `analysis.json` for `PlasticLove_JP__vmzJP`.
3. That real case remained `review_required`, with the main risk signals being ambiguous phonetic positions, low match scores, and excessive stretch ratios.
4. A reusable long-run plan template now exists at `%USERPROFILE%\.codex\tmp\demo-long-run.plan.json` and includes compile, unit test, audio check, a real-eval smoke, and git status.
5. This plan is suitable as the next autonomy entry point for `E:\Workplace\demo` because it keeps real-test feedback in the loop before each cycle.

### 2026-07-29: Partial Timeline Targets and Cancellation Boundary

1. Pronunciation-level timeline accuracy now handles mixed-confidence ordering better: when only some material decisions have text/phonetic positions, the known syllable targets keep their segment-derived durations and unresolved materials receive the remaining reference time.
2. The render planner now fits model target durations through a shared duration allocator and clamps them to Rubber Band tempo bounds. This prevents impossible sub-millisecond targets from producing invalid render commands while preserving the total reference duration when the requested bounds are feasible.
3. Stretch strategy labels now distinguish full-clip stretch, syllable-safe expansion with tail padding, max-compression floor, and max-expansion ceiling. These labels are useful diagnostics for real cases where pronunciation alignment would otherwise be hidden behind a generic extreme-stretch warning.
4. Cancellation coverage now includes a stdout-idle child-process regression. The progress runner polls cancellation independently from FFmpeg progress output and explicitly closes stdout/stderr handles after termination.
5. Verification for this continuation passed `compileall`, 94 unit tests, `audio_processor check`, and `git diff --check` with only CRLF conversion warnings.

### 2026-07-29: Rendered Audio Acceptance Metrics

1. Real-eval is no longer treated as a feasibility-only harness. The acceptance target is the exported concatenated material wav, not just whether the analysis stage can build an ordering plan.
2. The new real-eval scorecard separates planning quality from rendered-output quality:
   - `match_ordering_score` tracks material ordering confidence from per-decision match scores and review-required decisions;
   - `positioned_decision_ratio` tracks how many materials have text/phonetic positions suitable for pronunciation-level timeline placement;
   - `target_duration_alignment_score` tracks whether planned clip target durations sum to the original vocal duration;
   - `rendered_audio_alignment_score` adds actual rendered wav duration validation when `--render` is enabled.
3. `strict_render_pass` is intentionally conservative: it requires a rendered output, <=1% output-duration error, <=1% target-duration total error, no error warnings/review-required matches, minimum match score above the low-score threshold, and at least 95% positioned decisions.
4. Long rendered suites now flush `summary.json` and `summary.md` after each case, including `analysis_failed` records, so interrupted autonomous runs still leave reviewable scoring evidence.
5. The suite now reports `group_score_summary` by language, reference vocal, material set, and split. This matters because the current real corpus is finite: progress should be accepted only when overall score and worst groups improve or remain stable, not when one case improves by exploiting a narrow filename or song pattern.
6. Post-score improvement strategy for autonomous rounds:
   - pick recurring warning/failure classes and worst score groups first;
   - prefer general changes in phonetic normalization, candidate ranking, timing allocation, render bounds, diagnostics, or forced-alignment plumbing;
   - reject case-specific song/material hard-coding, threshold relaxation, or warning suppression unless the full rendered suite and worst groups do not regress.
7. This gives the autonomous loop numeric metrics to improve across repeated runs: aggregate score summary, group score summary, rendered audio score, strict pass/fail counts, render duration deltas, matching warnings, and timeline warnings.
8. Remaining architectural limit: duration matching and ordering scores are still proxy evidence unless reference analysis produces aligned unit timings. The next acceptance boundary must treat missing unit timing as a failure, not as a deferred enhancement.

### 2026-07-29: Forced Unit Timing Implementation

1. The project now has a concrete unit-timing path in the render pipeline:
   - WhisperX character alignment produces `VoiceUnitTiming` records;
   - `VoiceSegment.unit_timings` carries those records through reference analysis and cache reuse;
   - model runtime duration allocation uses aligned unit start/end for positioned material decisions;
   - rendered material stretch durations therefore consume original-vocal unit durations when coverage exists.
2. Strict acceptance was tightened accordingly. A rendered case cannot pass strict validation only because total wav duration matches; it must also have high `timed_target_duration_ratio` / `aligned_timing_score` and must avoid `missing_aligned_unit_timing`.
3. `missing_aligned_unit_timing` is intentionally an error, because proportional segment splitting is not sufficient for the user's goal of matching each character's actual duration.
4. The autonomous rendered full eval now runs through `scripts/run_real_eval_render_full.ps1`, forcing WhisperX rather than allowing the default ASR fallback to skip character alignment when the WhisperX model is not cached.
5. The current hard problem is no longer "add a forced-alignment hook"; that hook is now in the duration path. The next concrete failures to solve must come from real rendered runs: model download/runtime failures, language support gaps, incomplete char coverage, bad positioned matching, or stretch/render limits surfaced by `missing_aligned_unit_timing`, `timed_target_duration_ratio`, and group score summaries.

### 2026-07-29: Real-Eval Infrastructure Gate

The rendered real-eval gate now distinguishes quality evidence from infrastructure absence. Because strict pronunciation-level timing requires WhisperX character alignment, a run where WhisperX cannot load its model is not a failed ordering experiment; it is an environment blocker.

Current machine evidence:

1. `audio_processor check` shows WhisperX is installed, but `WhisperX model cached: False` and `Faster Whisper model cached: False`.
2. `scripts/run_real_eval_render_full.ps1` forces `VOCAL_PROCESS_ASR_BACKEND=whisperx` and `VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD=1`.
3. The current environment cannot download `Systran/faster-whisper-base` from Hugging Face because HTTPS certificate verification fails.
4. Latest generated report: `tests_real\output\real-eval-20260729-213550\summary.json`.
5. The report records `analysis_failed=1`, `analysis_blocked=27`, `asr_model_download_failed=28`, `infrastructure_blocker.blocked=true`, and `recommended_exit_code=2`.

Architecture result:

1. `audio_processor.real_eval` classifies shared ASR/model/tool failures as infrastructure warning kinds rather than generic case failures.
2. When every planned case is blocked before real pronunciation/timeline analysis, the real-eval CLI returns non-zero so autonomous runners stop treating the task as OK.
3. After the first shared infrastructure blocker, remaining cases are marked `analysis_blocked` instead of repeating the same model download failure. Group counts stay visible, but the suite no longer spends time producing duplicate non-evidence.
4. This preserves the stricter acceptance rule: no silent fallback to segment-only ASR and no lowering of timing thresholds. The next valid quality-improvement cycle must first restore WhisperX model availability or pre-populate the required cache, then rerun rendered real-eval.

Next technical target:

1. Repair the WhisperX/Faster-Whisper model-cache path or machine certificate trust for Hugging Face access.
2. Once rendered eval reaches real analysis again, compare `timed_target_duration_ratio`, `aligned_timing_score`, `missing_aligned_unit_timing`, match scores, and group summaries before changing ordering or timing algorithms.
3. Continue improving pinyin/romanized filename matching and pronunciation-level timeline allocation only against actual rendered-eval evidence, not against infrastructure-blocked summaries.

### 2026-07-29: CN/JP Language Compatibility Boundary

The matching pipeline must not treat Chinese and Japanese material sets as interchangeable evidence. CN/JP material-language compatibility is now a gating condition before expensive material analysis and before real-eval scoring.

Current behavior:

1. `build_model_ordering()` checks reference/material language compatibility before material-library analysis. If both sides are confidently identified as different CN/JP languages, it raises `AudioProcessorError` with a `Language mismatch` message.
2. Reference language evidence can come from explicit filename markers, lyrics text, ASR language notes, or transcript text. Material-set evidence can come from directory markers, kana/CJK filenames, pinyin tone markers, and distinctive Chinese/Japanese romanized filename patterns.
3. Unknown language remains allowed instead of being hard-failed, because false positives would block valid custom assets. The hard gate applies only when both sides have confident CN/JP evidence and disagree.
4. `real_eval` filters automatically discovered mismatches into `skipped_cases` and writes the skip table to the Markdown report, so skipped combinations remain auditable without spending ASR/render time.
5. Current real corpus discovery result: 13 executable compatible cases, 15 skipped `language_mismatch` cases. This prevents `PlasticLove_JP`, `kamippoina_JP`, `LAB=01_JP`, and `1000nenyikiteru_JP` from being evaluated against Chinese material sets.

Acceptance impact:

1. Future ordering/timing score improvements must be measured on language-compatible groups only.
2. A user-created mismatch during normal GUI/CLI/batch testing should fail fast with a language mismatch error instead of producing a misleading rendered output.
3. Long autonomous runs should include skipped-case counts in human-readable reports so reviewers can distinguish intentional filtering from missing test coverage.

### 2026-07-30: WhisperX Runtime Gate Reopened

本轮把真实渲染评测从“模型/运行时阻塞”推进到“实际排序与拉伸质量不足”的阶段。核心修复不是降低验收标准，而是让 WhisperX 路径真正可运行、可缓存、可复查。

已完成的架构修复：

1. `run_real_eval_render_full.ps1` 现在强制 WhisperX，并为后台自治设置项目模型缓存、可覆盖的 Hugging Face 镜像端点、下载超时、禁用 Xet、UTF-8 Python 输出环境。
2. WhisperX/Pyannote 在当前 PyTorch 安全加载策略下可通过受限 safe-globals 白名单加载 checkpoint；没有使用 `weights_only=False`。
3. 运行时会把 stdout/stderr 重配为 UTF-8 replace，避免 Windows GBK 控制台把 WhisperX 对齐日志中的中/日/韩字符变成 fatal exception。
4. 素材分析缓存现在校验 ASR backend。旧 `whisper` 或未记录 backend 的素材缓存不会再污染 WhisperX 真实验收。
5. real-eval 的退出码更适合后台自治：基础设施阻塞为 2，执行失败为 1，质量未达标但已生成真实跑分则保持 0。

当前真实证据：

1. `audio_processor check` 显示 Whisper、Faster-Whisper、WhisperX、Silero VAD、SpeechBrain 缓存均为 True。
2. `tests_real\output\real-eval-20260730-040430\summary.json` 是 `render=True` 单用例真实报告，已生成拼接音频 `tests_real\output\1000nenyikiteru_JP\vmzJP\1000nenyikiteru_JP.wav`。
3. 单用例跑分为：`planning_alignment_score.mean=0.480188`，`rendered_audio_alignment_score.mean=0.608193`，`match_ordering_score.mean=0.110427`，`render_duration_delta_ratio.mean=0.007794`，`strict_render_pass_count=0`。
4. 下一阶段优化应直接针对 `low_match_score`、`ambiguous_phonetic_position`、`extreme_stretch_ratio`、`timed_target_duration_ratio`、`aligned_timing_score` 和各 group score，不再停留在可行性验证或模型下载修复。

后续自治注意事项：

1. 完整 13 个兼容用例的 `render=True` 套件会比之前慢很多，因为旧素材缓存被正确失效后需要 WhisperX 重算；这属于真实验收成本，不应回退到 segment-only ASR。
2. 后台轮次应优先比较 `summary.json` 里的 `score_summary` 与 `group_score_summary`，按最差 reference/material/language 组做通用算法修复，避免对单个文件名或歌曲硬编码。
### 2026-07-30: 字音序列驱动与 ASR/对齐层拆分判断

本轮把核心验收目标进一步收窄到“参考人声字音序列、素材选择顺序、目标持续时长和最终拼接 wav 时长”四个可量化面。实现结果证明，仅靠素材数量排序会系统性低估长参考人声的字音需求；更合理的结构是由参考字音序列生成决策，素材样本可以复用，但每次复用都必须绑定到明确的参考 phonetic position 和目标 unit duration。

当前 ASR 判断：

1. WhisperX 继续用于本轮真实跑分，不是因为它是最强中文 ASR，而是因为它已经提供项目需要的字符级 forced-alignment 时间轴。
2. FunASR、SenseVoice、Paraformer 这类模型应作为后续识别前端或 timestamp adapter 接入，不能简单替换 WhisperX 文本输出路径；否则会丢失严格验收依赖的 per-character duration。
3. 本机当前未安装 `funasr` / `modelscope`，因此本轮不把真实验收切到不可复现的新依赖链路。下一步如果接入，应先实现后端能力探测、模型缓存状态报告、统一 `TranscriptSegment.unit_timings` 转换，再纳入真实跑分。

本轮架构修改：

1. `model_assist` 新增参考 phonetic unit sequence 排序路径。中文/日文参考文本按参考字音顺序选择素材，允许素材复用，精确同音/同音调优先，缺失素材时才低分 fallback。
2. `model_runtime` 保留 aligned unit timing 的真实目标时长，不再把未覆盖间隙强行分摊给已定位单字。
3. `engine` 对完整显式目标时长不再按参考总时长二次缩放；大量 rendered clip 拼接使用 FFmpeg concat list；目标 clip 渲染在 `apad` 后增加 `atrim`，保证最终 wav 总时长不被高压缩 clip 累积拉长。

真实验收结果：

1. `FengZhongYouDuo_CN__newOTTO` 非渲染评分：`planning_alignment_score=0.785077`，`match_ordering_score=0.502006`，决策数 `304`，定位/定时覆盖 `1.0`，低匹配告警 `7`。
2. 同一案例渲染评分：`status=rendered`，`rendered_audio_alignment_score=0.838808`，`render_duration_delta_ratio=0.000001`，最终输出 wav 时长误差约 `0.00025s`。
3. 对比旧基线，该案例规划分从 `0.35795` 提升到 `0.785077`，匹配排序分从约 `0.04384` 提升到 `0.502006`，说明本轮不是单纯跑分，而是根据失败类型修复了排序覆盖、目标时长分配和渲染拼接。

剩余风险：

1. 严格验收仍失败，主要剩余项是素材缺失或近音 fallback 造成的 `weak_text_signal`、`low_match_score` 和 `extreme_stretch_ratio`。
2. 参考字音数 `309` 与当前决策数 `304` 仍有差距，需要继续检查多音节/近似匹配 span 是否过宽，避免把单字目标合并。
3. 下一轮应优先做两个通用改进：接入可选 FunASR/SenseVoice timestamp adapter；输出素材缺失报告，明确哪些参考拼音没有同音素材、哪些位置只能近音替代。

### 2026-07-30: FunASR 后端接入与再次真实验收

本轮把 FunASR/Paraformer 接入为可选中文识别前端，并把真实验收脚本改成可切换 ASR 后端。第一次 FunASR 真实验收暴露出一个关键缺陷：只按发声片段累计字级时长会把输出音频压缩到 81.324 秒，导致渲染分只有 `0.55701`，不适合作为人工参考。

后续修正了字位槽时长分配和总时长归一化逻辑，第二次 FunASR 复跑恢复到接近原始时长，`render_duration_delta_ratio=0.000001`，输出 wav 时长 `259.276576` 秒，对应原人声 `259.27619` 秒。该结果说明 FunASR 现在能作为可用的中文识别前端接入，但严格验收仍受素材缺失和近音 fallback 影响。

当前判断：

1. 功能层面已完成“安装需要的模型并整合后端”的要求，且真实验收能产出可人工试听的输出音频。
2. 评分层面已经不是单纯跑分，能反映排序、对齐和渲染质量，但 `weak_text_signal` / `extreme_stretch_ratio` 仍提示素材覆盖不足。
3. 下一步应继续围绕素材缺失诊断、近音替代策略和更细的 timestamp adapter 做通用优化，而不是退回到只做文本 ASR。
### 2026-07-30: 素材标签权威化与参考 ASR 严格验收

这次修改的核心不是再换一个 ASR，而是把“谁有资格定义文本”拆清楚：

1. 素材侧的单字/短音频不再把 ASR 幻觉当成最终文本。对 CN/JP 素材集，且 clip 足够短时，文件名/OTO 标签成为权威文本，`material_text`、缓存和排序决策都以它为准。
2. 这样做的直接效果是把旧报告里的素材错字挡在决策链外，避免 `猪。 | filename: zhong1`、`中国。 | filename: zun` 之类的错误继续参与排序与验收。
3. 参考侧不再默认“ASR 输出就算完成”。如果没有歌词或其他验证文本，而参考段落又来自 ASR，就会触发 `reference_asr_unverified` 错误，严格验收必须失败。
4. 这次还把素材缓存格式升级，并把文件名标签策略写入缓存键，确保旧缓存不会继续复用已经被判定错误的转写结果。
5. 单测已经覆盖了素材幻觉覆盖、重复显示折叠、缓存失效重建、参考未验证告警和歌词豁免；`unittest discover` 140 项通过。

后续要继续提准的重点会更清楚：一方面继续压低素材侧弱文本信号，另一方面在有歌词/标注文本的案例里把参考识别彻底切到外部真值上，而不是单靠 ASR 猜文本。

### 2026-07-31: 歌词文本权威与 JP/CN 发音标准化

本轮把“文本来源”和“时间来源”进一步拆清楚：歌词文件是目标字音文本的权威输入，原人声 ASR/forced alignment 是目标时间轴来源。无歌词时继续使用原人声 ASR 识别出的字音序列；有歌词时，歌词文本覆盖参考段落文本，但只有原人声 unit timing 覆盖足够时才把该字音视为有真实持续时长。

实现要点：

1. `VoiceSegment`、`MaterialAnalysis`、`MaterialOrderDecision` 和 `MaterialScoreBreakdown` 都携带 `language_hint`，排序和诊断不再在 CN/JP 之间隐式猜测。
2. JP 语言提示下，日文汉字/假名进入 Janome tokenizer，取发音/读音后转成项目内统一 romaji phonetic units；CN 语言提示下继续走 `pypinyin`。
3. 当歌词语言不确定但素材集语言明确，且用户提供了歌词文件时，素材集语言可用于消歧 CJK-only 歌词，解决日文汉字歌词被当成中文的实际风险。
4. 歌词解析增加注音折叠：括号注音、ruby `<rt>` 注音、斜杠分隔的罗马音/假名注音、以及相邻同音异写行都会被识别为同一句的注释，而不是新的目标字音。
5. 折叠规则保守处理：完全相同的重复歌词不折叠；两个都含 CJK 但只是同音的不同写法不折叠，避免把真正重复或不同词误删。
6. 时间轴诊断中的 `reference_text_units`、`reference_phonetic_units`、素材文件名 units 和 `reference_segment_unit_count` 都使用相同语言提示，避免报告和实际排序口径不一致。
7. GUI 增加帮助/更新日志弹窗，中英文说明明确写出歌词优先、原人声时长优先、CN 拼音、JP 汉字/假名/罗马音匹配和注音折叠规则。

验证结果：

1. 新增测试覆盖 JP 汉字歌词按 Janome 发音匹配罗马音素材、CN hint 不被 Janome 路径误伤、日文歌词行内/相邻注音折叠、歌词文本 retarget 到原人声 unit timing 后保留每个字音 duration。
2. `.venv311\Scripts\python.exe -m unittest discover` 共 144 项通过。
3. `.venv311\Scripts\python.exe -m audio_processor check` 通过，当前输出包含 `Janome: available`，Whisper/Faster Whisper/WhisperX/FunASR 模型缓存均为 True。
4. `git diff --check` 仅报告 Windows CRLF 转换提示。

下一轮自治重点：

1. 优先跑完整 rendered real-eval，直接用 `timed_target_duration_ratio`、`aligned_timing_score`、`missing_aligned_unit_timing`、`rendered_audio_alignment_score` 和 `render_duration_delta_ratio` 判断时间轴对齐。
2. 修改必须来自真实失败分布和最差 group，不允许针对歌曲名、素材名或单个案例硬编码。
3. 若真实跑分仍显示时间轴问题，优先检查 unit timing 覆盖、歌词单元数与 ASR timing 数不一致、multi-unit span 合并过宽、以及 extreme stretch 是否来自素材缺失而不是渲染 bug。
### 2026-07-31: 渲染连续性与拉伸质量进入验收指标

本轮把“拼接音频连续性差”从主观听感问题拆成三个可度量层：渲染 filter 是否保持共振峰并处理边界、素材相对目标时长的自然拉伸程度、以及短字音/极端变速导致的边界风险。现有 Rubber Band `formant=preserved` 已经存在，因此架构上不应把问题误判为“未启用共振峰保护”；更直接的风险来自单字素材被过度扩展或压缩、clip 边界硬切、素材覆盖不足导致近音 fallback。

已落实的设计决策：

1. 渲染链路继续使用 Rubber Band 共振峰保持拉伸，并在 exact-duration `apad` / `atrim` 之后加入短淡入淡出。该处理是通用边界缓冲，不依赖歌曲名、素材名或语言特例。
2. `stretch_naturalness_score` 与 `continuity_warning` 成为 `stretch_plan` 的一等诊断字段。后续 full rendered real-eval 可以直接统计它们，而不是只看 `extreme_stretch_ratio` 和人工听感。
3. real-eval 新增 `stretch_quality_score`、`stretch_naturalness_score`、`continuity_warning_ratio` 等套件和分组指标，能判断某次修改是否真正改善了拉伸/连续性，同时仍保留严格的匹配、时长、timed unit coverage 验收门槛。
4. 排序侧只在同发音候选或 fallback 候选中使用自然拉伸因素，不允许听感分覆盖发音匹配。这是为了降低过拟合有限素材集和错字音排序的风险。

剩余风险：

1. 淡入淡出只能降低硬切边界，不等同于真正的音素级共振峰轨迹连续，也不能修复素材本身缺失目标字音的问题。
2. 多 clip 的真正 crossfade、能量/F0/formant 轨迹平滑、voiced-core 级别拉伸仍未实现；这些需要在完整 rendered real-eval 的 worst group 上验证后再推进。
3. 如果 full suite 仍显示大量 `single_syllable_boundary_risk` 或 `stretch_naturalness_score` 低分，下一步应优先做素材缺失报告、近音替代策略和更细粒度的目标音素/音节切分，而不是放松 strict pass。

验证结论：

1. 编译、149 项单测、`audio_processor check` 和 `git diff --check` 已通过。
2. 临时双素材 FFmpeg 渲染 smoke 输出 2.000000 秒，说明新 filter 在当前 FFmpeg 环境可执行。
3. 完整 rendered real-eval 仍需由后台自治恢复运行，用新增 `stretch_quality_score`、`stretch_naturalness_score`、`continuity_warning_ratio`、`rendered_audio_alignment_score`、`render_duration_delta_ratio` 和 worst group 共同判断是否比上一轮改善。
### 2026-08-02: Filename-Label-First Becomes the Next Architecture Target

The latest user feedback changes the next highest-value direction. Current rendered audio is still not listenable even after duration alignment, loop-fill, and segment-lattice target coverage fixes. The remaining failure is not only timeline coverage; it is that material analysis can still let ASR hallucinations compete with or pollute filename-labeled syllable evidence, especially in PlasticLove / JP diagnostics.

Current evidence:

1. Autonomous session `C:\Users\WIN11\.codex\tmp\agent_runs\20260801-164741-demo-long-run` was stopped intentionally for task closeout at `2026-08-02T00:37:27+08:00`.
2. Cycle 1 completed and pushed `521a51b Fix segment lattice target ordering`. Its progress note recorded that cached PlasticLove recomputation recovered `411/411` positioned and timed target coverage.
3. Full rendered suite `tests_real\output\real-eval-20260801-164819\summary.md` rendered 13/13 compatible cases, but strict pass remained `0/13`. Suite means: `match_ordering_score=0.561014`, `stretch_quality_score=0.437509`, `stretch_naturalness_score=0.481325`, `continuity_warning_ratio=0.560929`.
4. Stopped partial suite `tests_real\output\real-eval-20260801-232614\summary.md` rendered 8/13 cases before stop. Suite means improved slightly but still failed strict: `match_ordering_score=0.579279`, `stretch_quality_score=0.484238`, `stretch_naturalness_score=0.526732`, `continuity_warning_ratio=0.512938`.
5. User inspected PlasticLove JSONL diagnostics and observed that Japanese/cross-language material can still be recognized as Chinese. This confirms that material ASR remains too authoritative or too expensive for the intended workflow.

Architecture decision:

1. For material clips with parseable filename labels, filename-derived syllable/pronunciation units should become the primary material text authority before ASR runs.
2. Material ASR should be skipped or demoted for trusted filename labels. It may remain useful only when filenames are unparseable, purely numeric, too long/ambiguous, or when the user explicitly asks for material-content verification.
3. The target side remains unchanged: with lyrics, lyrics text defines target units and original-vocal alignment defines unit timing; without lyrics, original-vocal ASR/alignment defines target units and timing.
4. Whole-sentence or phrase semantic matching is not a practical main path for this product. The application scenario is often Japanese targets with Chinese voice material or cross-language phonetic reuse, so matching should be phonetic-unit-first, not semantic-phrase-first.
5. The correct cross-language model is to separate reference language, material filename label system, and material audio speaker/source. A Chinese-voice clip named with a Japanese/romaji syllable label should be allowed to serve a JP target if the filename phonetic units match.

Next implementation requirements:

1. Add an early material-analysis path that creates `MaterialAnalysis` from filename labels before calling `_transcribe_audio()`.
2. Record diagnostics such as `material_text_source=filename_label_authority`, `asr_skipped_for_filename_label=true`, and the parsed filename phonetic units.
3. Upgrade/invalidate material cache keys so old ASR-first caches do not re-enter ordering or JSONL diagnostics as trusted material text.
4. Keep ASR transcript out of primary ordering when filename labels are trusted; ASR can be stored as optional verification evidence only if it was explicitly requested or needed as fallback.
5. Re-run rendered real-eval after implementation and compare not only coverage, but also `low_match_score`, `weak_text_signal`, `single_syllable_extreme_stretch`, `continuity_warning_ratio`, and JP/vmzJP group scores.
### 2026-08-13: Adaptive Signalsmith Session Closeout

The current Signalsmith Stretch implementation now prefers reliable acoustic voiced boundaries for the vowel core and applies adaptive attack/coda limits based on both absolute duration and material length. This removes the previous dependence on narrow filename-derived vowel spans when the waveform provides a stronger boundary signal.

Evidence:

1. `167` unit tests passed.
2. `compileall`, `audio_processor check`, and `git diff --check` passed.
3. Rendered smoke output preserved requested durations for `chi.wav`, `bo.wav`, and `shi.wav`.
4. The autonomous full rendered suite was stopped during SpeechBrain initialization before any compatible case completed. Its partial summary must not be used to claim an ordering or listening-quality improvement.
5. The feature branch and commit are pushed as `codex/adaptive-signalsmith-stretch` / `b46f0ee`; promotion to `main` remains pending full rendered evaluation and human listening review.

### Tempo-Safe Audible Slots And Source Windows

This continuation addresses a rendering defect that directly affected pronunciation timing and listenability: a duration slot can include real reference silence, but stretching the source clip to fill that entire slot causes the syllable body and vowel to absorb silence that should remain silent.

Architecture changes:

1. Keep the complete timeline slot, audible target duration, pre-silence, and post-silence as separate values. The renderer stretches only the audible target and pads to the complete slot afterward.
2. When reference segments have absolute timing, preserve gaps between segments as explicit silence assigned to adjacent clip slots. This keeps the concatenated output aligned with reference time without making a short syllable unnaturally long.
3. For short labeled material under compression, detect the active PCM window, trim inactive lead/tail audio, and calculate render tempo from the trimmed source window. The source window is part of cache identity and DAW/timeline diagnostics.
4. Do not use Signalsmith vowel-core regions on a trimmed source window until the backend accepts an explicit time offset or receives re-based acoustic boundaries. Planning intentionally selects regular Rubber Band for that case so declared backend metadata matches the executed path.

Validation evidence:

1. Full regression suite passed with 172 tests, along with `compileall`, runtime checks, and diff validation.
2. Synthetic and real single-material FFmpeg smokes demonstrated exact output duration, source-window compression, bounded audible expansion, and preserved post-silence.
3. The real `chickenOTTO/shi.wav` smoke compressed to `0.100000s` through a `0.120s` source window using Rubber Band. Its expanded `2.000000s` slot used Signalsmith only for the `0.535192s` audible region and left about `1.465s` as measured trailing silence.

Residual risk and promotion gate:

1. These tests validate duration and silence semantics, not end-to-end order quality or subjective continuity across a full song.
2. Before `main` promotion, run a rendered real-eval on compatible corpus cases and manually inspect the generated audio. Compare `stretch_quality_score`, `stretch_naturalness_score`, `continuity_warning_ratio`, match ordering metrics, and worst group regressions without relaxing strict thresholds.

### Tiny Audible Target Render Failure

The 2026-08-14 rendered run exposed a distinct FFmpeg boundary case. A model plan can reserve a long timeline slot while assigning only a few milliseconds of audible material and the rest as silence. The previous filter builder decided whether to invoke Rubber Band from the complete slot duration, so a 13.695 ms source window was still sent through Rubber Band for an 11.413 ms audible target. FFmpeg returned `-1 (Operation not permitted)` from the filter graph and the batch marked the case as failed.

The renderer now makes the direct-trim decision from the audible target duration. Tiny audible clips bypass Rubber Band and use exact trim/pad plus the existing pre-silence/post-silence slot conditioning. This preserves the timeline contract while avoiding an unsupported Rubber Band input size. Cache identity uses `material_render_filter_v11_tiny_audible_direct_trim`.

Evidence:

1. Exact failing FFmpeg command reproduced the error; the same command without Rubber Band returned zero and produced `13.351417 s`.
2. The project render entry point produced `13.351417 s` for the real `vmzJP/a1.wav` case.
3. `.venv311\Scripts\python.exe -m unittest discover` passed all `174` tests.
4. A real rerun of `1000nenyikiteru_JP__vmzJP` completed with `status=rendered`, output duration `194.155102 s`, and zero new render-error entries in the post-fix time window.

### JP MGRoid Short-Region And Stereo Shape Strategy

The latest manual listening feedback clarified that JP output quality is now limited by three practical rendering faults rather than only by timeline duration: stereo phase/channel mismatch, repeated consonants from stretching short CV material, and audible timbre drift when formant-sensitive regions are over-processed.

Architecture decisions:

1. Stereo coherence must be enforced before final concat. Rubber Band and SoundTouch both support/require stereo-aware processing for phase-coherent material, so project render-cache clips now inherit the reference audio shape when the user has not supplied explicit `sample_rate` or `channels`. This prevents mixed MGRoid mono/stereo and 44.1/48 kHz assets from entering the final concat with inconsistent shape.
2. Ultra-short vocal regions are not reliable phase-vocoder material. MGRoid exposed a real failure on a roughly `52 ms` source window and `44 ms` audible target. The renderer now bypasses Rubber Band for source or target regions at or below `60 ms`, using exact trim/pad with no pitch shift instead.
3. Diagnostics must match execution. Direct-trim regions are now reported as `tiny_target_direct_trim` and `direct_trim_no_pitch_shift`, so a human reviewing `stretch_plan` can distinguish "not stretched to preserve timbre" from "Rubber Band formant-preserved stretch".
4. The Rubber Band path remains `formant=preserved` with `channels=together`; the change is not a pitch/formant transform. The goal is to reduce avoidable timbre movement by shrinking the set of clips that go through time-stretching at all.
5. Signalsmith remains useful only for moderate vowel-core cases. For short labeled material above a `1.5x` stretch/compression ratio, the implementation keeps it off the Signalsmith path because the current Python binding does not expose sharper monophonic formant controls.

Validation evidence:

1. Unit coverage increased to `178` passing tests. New regressions cover MGRoid-style short source-window direct trim and reference-shaped render-cache output.
2. The real `MGRoid/ha.wav` smoke produced `.tmp\mgroid-short-region-smoke\ha_short_direct.wav` without `rubberband=`, duration `0.043628 s`, `44100 Hz`, and `2` channels.
3. The v14 JP/MGRoid run completed all four cases with `status_counts={"rendered":4}` and no task failures. Report: `tests_real\output\jp-mgroid-v14\reports\real-eval-20260814-222936\summary.json`.
4. v14 suite means were `planning_alignment_score=0.520347`, `rendered_audio_alignment_score=0.640260`, `match_ordering_score=0.393758`, `stretch_quality_score=0.825313`, `stretch_naturalness_score=0.801262`, `continuity_warning_ratio=0.154826`, and `render_duration_delta_ratio=0.0`.
5. The four v14 output WAVs are isolated under `tests_real\output\jp-mgroid-v14\audio` and were verified as `44100 Hz` stereo: `1000nenyikiteru_JP`, `kamippoina_JP`, `LAB=01_JP`, and `PlasticLove_JP`.

Residual risk:

1. Strict pass remains `0`; this change fixes concrete render failures and audio-shape hazards, but does not solve weak phonetic matching by itself.
2. `PlasticLove_JP__MGRoid` still has the worst continuity count and naturalness score in this subset (`continuity_warning_count=105`, `stretch_naturalness_score_mean=0.7240467`). It should be the next manual-listening focus.
3. Future improvement should target JP phonetic unit matching and missing-material diagnostics before adding more DSP engines. WORLD/PSOLA-like analysis may be useful for formant/F0 diagnostics, but changing timbre or pitch remains out of scope for the current project goal.

### JP Render Timing Gate And Direct-Trim Microfade

Manual listening invalidated the v14 JP/MGRoid run. The important correction is that v14 generated real wav files but did not provide real character-level timing evidence: the direct real-eval invocation used a backend path that produced `reference_unit_timing_count=0`, so the renderer fell back to proportional segment splitting. This is incompatible with the project goal, where target units must follow the original vocal's per-character or per-pronunciation start/end/duration.

Architecture changes:

1. Rendered real-eval is now gated by aligned unit timing. If positioned decisions exist but `timed_target_duration_count < positioned_decision_count`, the suite records `render_blocked` and does not render audio. This prevents future background runs from filling the review folder with files that only match total duration.
2. `real_eval --render` defaults the ASR backend to WhisperX when the environment is unset or `auto`; direct command-line runs therefore follow the same timing contract as the long-run script. The backend is still explicit and overridable through `--asr-backend`.
3. The official full-render script now accepts `-Split`, `-Case`, and `-MaxCases`, so focused JP/MGRoid validation can be run through the project entry point instead of ad hoc commands.
4. Absolute timeline fallback now preserves positioned active durations before silence-slot assembly. Segment-local syllable duration is no longer normalized to the whole reference duration first.
5. Direct-trim short regions now receive a very small fade envelope. This is a boundary repair only; it does not pitch shift, formant shift, or route the clip through a timbre-changing processor.

Validation evidence:

1. `96` focused render/runtime/real-eval tests passed.
2. Full unit discovery passed with `182` tests.
3. `compileall`, `audio_processor check`, and `git diff --check` passed; diff check only reported CRLF conversion warnings.
4. Real MGRoid `ha.wav` microfade smoke produced a stereo `44.1 kHz` output of `0.043628 s` with no Rubber Band filter and with the expected `afade` filters.

Residual risks:

1. This does not by itself prove JP listening quality. It prevents known-bad timing outputs and removes hard-cut short-region edges.
2. If the overnight JP rendered suite is `render_blocked`, the next task is reference alignment repair, not DSP tuning.
3. If it renders but remains unpleasant, the next likely areas are JP phonetic candidate ordering, missing-material diagnostics, and per-unit continuity/crossfade policy. Pitch/formant-changing engines remain out of scope unless the product goal changes.

### Short-Region Rubber Band Boundary Policy

The first post-microfade JP rendered validation did recover aligned timing coverage but failed at the render layer. All eight JP cases reported `timed_target_duration_ratio=1.0`, yet the final wav was not produced because Rubber Band failed on short source windows around 70-90 ms. This confirms that the remaining blocker is not the character-timing gate; it is the DSP backend boundary for very small vocal regions.

Policy update:

1. For labeled short material, source or target render regions at or below `100 ms` are not Rubber Band material. They are direct-trim/pad plus microfade regions.
2. This follows the project goal more closely than forcing a phase-vocoder path: direct trim does not alter pitch or formant trajectory, while a tiny Rubber Band window can fail outright or smear consonant/vowel structure.
3. A runtime fallback exists for short labeled regions up to `150 ms`: if Rubber Band returns `Operation not permitted` or a filter error, the renderer removes the partial cache file and retries direct trim.
4. The fallback is deliberately constrained to short labeled regions so normal longer vocal material still uses the established pitch/formant-preserving stretch path.
5. Cache identity now includes the short-region fallback policy, avoiding reuse of cache files from the failed 60 ms threshold pass.

Validation:

1. The exact failed MGRoid-style shape `de.wav`, `92.532 ms` source window to `77.110 ms` target now builds a filter without `rubberband=` and renders a stereo `44.1 kHz` wav of `0.077120 s`.
2. Regression tests cover both proactive sub-100 ms direct trim and reactive Rubber Band failure retry.
3. Full unit tests now pass at `184` tests, and runtime checks remain clean.

Next validation:

1. Re-run JP rendered real-eval under a fresh output root.
2. If final wav files are produced, manual listening should focus on whether microfade/direct-trim reduced tail pops without making timing worse.
3. If render still fails, inspect the next shortest Rubber Band failure and either tighten the short-region policy or move the fallback closer to the cache clip renderer diagnostics.

### Coherent Mono Material Render Policy

The stereo mismatch feedback exposed a separate issue from output file shape. Making every cache clip inherit the reference `sample_rate` and `channels` ensures concat compatibility, but it does not fix source assets whose left/right channels already carry different timing, phase, or transient content. For syllable vocal material, stereo width is not the core signal; phonetic timing and timbre consistency are.

Policy update:

1. Material clips now enter the render chain through `aformat=channel_layouts=mono` before stretch/direct-trim processing. This folds mixed stereo material into one coherent vocal signal before any time-domain or phase-vocoder operation.
2. Final output shape is still reference-shaped. When the reference is stereo, FFmpeg expands the coherent mono material back to stereo at the output/cache boundary.
3. Rubber Band remains configured for pitch/formant preservation and linked-channel processing. The mono fold reduces left/right mismatch and tail artifacts without deliberately changing pitch, formants, or singer identity.
4. The render plan exposes `channel_coherence=material_mono_fold_then_reference_channels`, so real-eval reports can be checked for this policy during manual review.
5. The cache key includes the new channel policy through `material_render_filter_coherent_mono_short_region_fallback_v1`, preventing stale stereo-cache reuse.

Validation:

1. Full `.venv311` unit discovery remains at `184` passing tests.
2. A deliberately mismatched stereo FFmpeg smoke rendered to a `0.220000 s`, `44100 Hz`, `2` channel wav while using `aformat=channel_layouts=mono` in the material chain.
3. The first full test command using system Python 3.10 failed only because that environment lacks `numpy`; the project `.venv311` test run passed and is the relevant verification environment.

Next validation:

1. Start a fresh JP rendered real-eval output root after this policy change.
2. Manual listening should compare whether left/right confusion is reduced first, then judge consonant tails and remaining formant drift.
3. If formant drift remains audible after mono fold and direct-trim guards, the next likely improvement is to reduce the set of syllable regions eligible for any stretch, not to introduce a pitch/formant-changing engine.

### Consonant-Safe JP Timing And De-Click Policy

The coherent-mono run proved that channel folding alone is insufficient. The user reported two remaining audible failures: electric/click noise and missing consonants. Objective analysis matched the report: rejected rendered files had peak values at full scale, measurable clipping, and sample-to-sample jumps at the maximum possible range.

Root causes:

1. JP unit timing was too granular for material rendering. WhisperX alignment created many `6-20 ms` target slots. A standalone Japanese CV/phonetic unit cannot be rendered intelligibly at that length from 250-900 ms source material.
2. The prior short-region policy incorrectly treated these slots as direct-trim candidates. This avoided some Rubber Band failures but made consonant loss inevitable.
3. A simple final limiter removed over-peak values but left hard positive/negative transitions, which still sound like electric clicks.

Policy update:

1. JP positioned active durations are smoothed per reference segment before timeline slot assembly. Short JP decisions receive a consonant-safe floor around `45 ms`; time is borrowed from longer units in the same segment so the segment total is preserved.
2. Source-window trimming is disabled below the consonant-safe threshold and, when enabled, uses at least a `90 ms` source window. This prevents the renderer from feeding 10-20 ms source fragments to either Rubber Band or direct trim.
3. Proactive direct trim is now reserved for truly tiny regions below `35 ms`. Short but potentially intelligible 43-90 ms regions try Rubber Band first and only fallback to direct trim if FFmpeg fails.
4. Final concat uses a safety chain: `lowpass=f=12000`, `adeclick`, and a softer `0.90` limiter. This is a click/noise guard, not a pitch/formant transform.
5. Cache identity includes the new policy via `material_render_filter_consonant_safe_declick_v1`.

Validation:

1. Full `.venv311` unit discovery passed with `185` tests.
2. The focused `1000nenyikiteru_JP__MGRoid` real render showed direct trims dropping from `417` to `0`, sub-45 ms audible units dropping from `219` to `1`, and minimum audible target rising from `0.0114 s` to `0.045 s`.
3. The rejected coherent-mono file measured `peak=1.0`, `clip_ratio=0.00368229`, `jump999=2.0`, and `hf9k=0.150613`.
4. The review output after the new safety chain measured `peak=0.89999`, `clip_ratio=0`, `jump999=0.11084`, and `hf9k=0.000458`.

Residual risk:

1. This should reduce missing consonants and electric clicks, but it does not guarantee acceptable lyric/material matching quality.
2. Lowpass at `12 kHz` may slightly reduce air/noise brightness; it should not change F0 or vocal formant trajectory, but manual listening remains required.
3. If consonants are still weak, the next architectural change should be unit grouping or overlap-aware timeline rendering, not lower direct-trim thresholds.

### Mono Output And Short-CV Atempo Policy

The user confirmed that post-processing alone did not remove the electric sound and requested direct stereo-to-mono output. This supersedes the earlier "material mono fold then reference channels" policy.

Root cause update:

1. Short-CV Rubber Band rendering can itself create full-scale impulse discontinuities even after material input is folded to mono.
2. A real `MGRoid/gi.wav` smoke showed the same source window and target duration producing `jump999=1.999969` with Rubber Band but `jump999=0.112218` with FFmpeg `atempo`.
3. Therefore, electric noise is not only a final clipping problem; it can originate in the short-window time-stretch backend before concat.

Policy update:

1. Render-cache clips and final material-assembled output are now forced to mono (`channels=1`). The reference sample rate is still inherited.
2. Reports use `channel_coherence=material_mono_fold_then_mono_output`.
3. Short labeled material within a safe tempo range uses `atempo` rather than Rubber Band. The plan reports this as `stretch_backend=atempo`, `rubberband_profile=atempo_short_cv`, and `formant_preservation=atempo_pitch_preserved_short_cv`.
4. Rubber Band remains available for longer material and vowel-core cases where short-window impulse risk is lower.
5. Cache identity is now `material_render_filter_mono_atempo_short_cv_declick_v1`.

Validation:

1. Full `.venv311` unit discovery remains green at `185` tests.
2. Real single-case output `tests_real\output\jp-mono-atempo-smoke-20260815\audio\1000nenyikiteru_JP\MGRoid\1000nenyikiteru_JP.wav` is `44100 Hz`, mono, duration `194.157211 s`.
3. Compared with the rejected coherent-mono/stereo-output file, objective metrics improved: `peak=0.9`, `clip_ratio=0`, `jump999=0.096502`, `jumpmax=0.290299`, `hf9k=0.000189`.
4. Backend distribution in that case was `atempo=676`, `rubberband=61`, `signalsmith=7`; direct trim remained `0`.

Residual risk:

1. `atempo` is a better short-CV default than Rubber Band on the tested material, but manual listening must confirm that it does not introduce unacceptable time-domain smearing.
2. The full JP suite has not yet been rerun under this mono+atempo policy.
3. Remaining quality problems may still come from phonetic ordering or unit grouping rather than DSP.

### Reference Vocal Truth Gate And OTO-Guided Source Windows

The user's correction narrows the current failure to the target side: the generated audio can be unrelated to the original/reference vocal because the reference transcript itself was ASR-only and unverified. This is more severe than a material-selection defect. If the target text is wrong, better material matching and cleaner stretching will still assemble the wrong content with accurate timing.

Policy update:

1. Rendered real-eval now requires verified reference text before producing formal review audio. A lyrics file, subtitle file, or another trusted transcript can provide target content; original-vocal ASR/alignment can still provide timing.
2. ASR-only reference text remains useful for diagnostics but is not enough for acceptance WAV generation. Such cases are blocked as `render_blocked_unverified_reference_text`.
3. The explicit `--allow-unverified-reference-render` switch keeps controlled DSP testing possible, but those outputs must be treated as non-content-valid experiments.
4. This makes future manual review cleaner: if a WAV exists from the default rendered path, it should at least be based on a trusted target-text source rather than a hallucinated reference transcript.

Render policy update:

1. MGRoid UTAU `oto.ini` entries now guide short-material source windows. This is a general UTAU-compatible source-window mechanism, not a song-specific exception.
2. OTO lookup is deliberately conservative. Whole aliases, stems, and compact forms may match; longer labels are not decomposed into single phonetic units for lookup, preventing `kan/kai/yan` from inheriting unrelated `ka` timing.
3. Short-CV atempo can now cover higher ratios through chained `atempo` filters, keeping each filter inside FFmpeg's legal `0.5..2.0` range.
4. Cache identity is `material_render_filter_mono_atempo_chain_utau_oto_v1`, so older mono/atempo cache clips are not silently reused.

Verification:

1. Full `.venv311` unit discovery passed with `189` tests.
2. `compileall`, `audio_processor check`, `git diff --check`, and `dev_preflight.ps1 -FullGitProbe` passed.
3. The strict no-lyrics JP/MGRoid command produced a blocker report at `tests_real\output\jp-reference-trust-gate-20260815-rerun\reports\real-eval-20260815-201712\summary.json` and no review WAVs.
4. The atempo-chain smoke file `.tmp\mgroid-atempo-chain-smoke\ga_chain.wav` measured cleanly enough for a backend smoke: mono `44100 Hz`, duration `0.099819 s`, `peak=0.528753`, `jump999=0.083761`, `jumpmax=0.086546`.

Residual risk:

1. This does not solve the content-valid JP suite by itself; it prevents producing misleading files until verified reference text exists.
2. Real listening validation must resume from lyrics/subtitle-backed cases, otherwise matching scores can still reward the wrong target sequence.

### Strict Reference Timeline Gate

The project now treats "has unit timing" and "has exact trustworthy unit timing" as different states. The user is correct that time-axis failure remains possible even after text hallucination is blocked.

Policy update:

1. `timed_target_duration_count` is no longer enough for acceptance. Resampled or interpolated timing can still sound off because individual syllable boundaries are not the original vocal boundaries.
2. `exact_timed_target_duration_count` and `resampled_timing_lattice_count` are now recorded in `timeline_alignment`.
3. `aligned_timing_score` and strict render pass logic now use exact timing coverage.
4. `resampled_aligned_unit_timing` is an error in preflight and blocks rendered real-eval.
5. `lyric_timing_conflict` is also an error and blocks rendered real-eval when timestamped lyrics disagree with ASR/acoustic timing.
6. These rules are language-agnostic. CN ASR recognition errors are handled by the same verified-text and exact-timing gates as JP.

Residual risk:

1. This is a trust-gate repair, not a final timeline-reconstruction algorithm. It prevents bad outputs from being accepted and makes reports more honest.
2. The next algorithmic step is to produce exact timing from a better forced-alignment path when lyrics are available, instead of relying on retargeted/resampled ASR timings.

### Manual Lyrics Mode Boundary

The no-lyrics path remains a first-class product workflow. The correct design is not "lyrics required everywhere"; it is an explicit mode switch:

1. Manual lyrics disabled: use original/reference vocal ASR and alignment for ordering and timing. This preserves the user's original requirement and avoids the cost of preparing lyrics for every run.
2. Manual lyrics enabled: require a selected lyrics/subtitle file and use it as target text authority.
3. Strict real-eval blockers remain useful for formal score artifacts, but they should not be confused with the everyday GUI batch workflow.
4. Stale settings are now safer because a saved lyrics path has no effect unless `manual_lyrics_enabled` is true.
5. External callers keep compatibility: CLI and VST3 bridge automatically enable manual lyrics when they supply `lyrics_file`.

Residual risk:

1. No-lyrics mode still inherits ASR recognition risk in both CN and JP.
2. Manual lyrics mode reduces text errors but still needs exact forced alignment before timeline quality can be trusted.

### CN/JP Lyrics And Channel-Independent Render Policy

The verified-lyrics workflow now separates three concerns that were previously conflated:

1. Text identity: Japanese kana, romaji, bracketed readings, and inline readings are normalized and collapsed before matching. Non-vocal lyric markers are filtered.
2. Timing authority: lyric text supplies the trusted target sequence, while original-vocal acoustic/ASR segments supply start/end timing. Unmatched acoustic segments are recorded as skipped residue instead of becoming material targets.
3. Channel authority: a stereo original vocal is split into independent mono lanes before model ordering and material assembly. Each lane is rendered separately with forced mono output. The base output name is suffixed with `_left`, `_right`, or `_chN`.

The normal no-lyrics workflow remains unchanged unless `split_reference_channels` is enabled. Rendered real-eval uses the verified lyrics discovered beside each reference and enables channel splitting automatically. Its validation requires all lane files to exist and each lane duration to match the reference.

Verification:

1. The seven current files in `tests_real/lyrics` parse without adjacent Japanese annotation duplicates.
2. Stereo lane extraction was tested with real FFmpeg and both extracted files probe as one channel.
3. Full unit discovery passed with `202` tests.
4. `compileall` and `audio_processor check` passed.

Residual risk:

1. Lyrics improve target text trust but do not replace exact forced alignment; resampled/interpolated timing remains blocked by the existing strict real-eval gates.
2. Residual instrument suppression is implemented as lyric/acoustic mismatch filtering, not as a guarantee that every separation artifact is inaudible.
3. The next manual-listening evidence should come from the new CN and JP lane-split render roots.

### Channel Topology And Strict Manual-Lyrics Timing

The 2026-08-16 lane-split implementation treated physical stereo as equivalent to independent vocal lanes. That was too broad. Same-content stereo, doubled vocals, and harmony/effect stems can have two channels while still representing one lyric/timing lane. Splitting those files forces two independent ASR/alignment passes and can make both material ordering and audio artifacts worse.

Updated policy:

1. `split_reference_channels` is now gated by content topology, not only `ffprobe` channel count.
2. The topology check measures waveform correlation, small-lag correlation, amplitude-envelope similarity, envelope difference, side/mid energy, RMS balance, and active duration.
3. Physical stereo is split only when the channels look independently timed/content-bearing. Same-content stereo/harmony, near-duplicate stereo, delayed stereo effects, weak channels, and unsupported multichannel layouts are treated as one reference lane.
4. Batch diagnostics now write `reference.channels.split_skipped` with `channel_topology`, so future regressions can be audited from the JSONL log instead of inferred from output filenames.
5. `prepare_reference_channel_lanes()` repeats the topology guard internally so direct callers cannot bypass it accidentally.

Current real-corpus topology evidence:

1. `LAB=01_JP.wav` is classified as `independent_channel_content` and remains eligible for `_left/_right` mono lane outputs.
2. `1000nenyikiteru_JP.wav`, `AiRenTongZhi_CN.wav`, `FengZhongYouDuo_CN.wav`, `kamippoina_JP.wav`, `PlasticLove_JP.wav`, and `ShenHua_CN.wav` are classified as same-content stereo/harmony and should render as a single mono material assembly, not lane pairs.

Manual lyrics timing policy is also stricter:

1. Lyrics text is target-text authority, but the original vocal remains the timing authority.
2. If lyrics cannot be matched to original-vocal acoustic/ASR segments, the system must fail instead of pairing by equal counts or falling back to ASR text.
3. If lyric retargeting requires resampled/interpolated unit timing, the system must not produce a review WAV. Total duration matching is not enough; every positioned material decision needs exact original-vocal unit timing.
4. Existing 2026-08-16 CN/JP lane-split outputs are rejected regression evidence. They include measurable large sample jumps and should not be used as manual-listening acceptance files.

### Exact Pronunciation Text And Unit-Timing Lattice

The manual-lyrics path now separates display text from pronunciation/timeline text. This is necessary for Japanese lyrics where the written line can contain kanji, numbers, punctuation, and annotation syntax while the sung units are kana or romaji mora.

Updated model:

1. `VoiceSegment.text` remains the verified lyric/display text.
2. `VoiceSegment.alignment_text` carries verified pronunciation text extracted from inline readings, bracketed readings, adjacent kana/romaji annotation lines, or forced-alignment target selection.
3. Forced alignment uses pronunciation text when it improves alignment, but pure-kana JP text is preferred over low-quality Latin annotation targets.
4. Phonetic ordering, reference unit durations, and timeline reports use pronunciation text when present, so examples like `1000年 -> センネン` and `晒し者 -> さらしもの` no longer force resampled timing.
5. JP pure-kana unitization bypasses Janome. This keeps mora spans consistent between phonetic ordering and char alignment, especially for yoon/gemination such as `いっしょう -> i/sho`.
6. Non-vocal units such as digits, spaces, and punctuation are not part of JP vocal-unit timing. They remain in display text but not in strict timing counts.
7. If a selected material span overruns the verified reference segment unit count, the timeline clamps it to the exact reference boundary instead of expanding the timing lattice. Expanding exact reference timing would reintroduce interpolation under a different name.

Real evidence:

1. CN `AiRenTongZhi_CN__chickenOTTO` now has zero resampled timing lattice units. Its remaining block is a final lyric line without aligned unit timing, which is a valid strict blocker.
2. JP `1000nenyikiteru_JP__MGRoid` now reaches exact aligned timing in real analysis: `timed=525/525`, `exact=525/525`, `resampled=0` in `jp-analysis-v9`.
3. A pre-final full render `jp-smoke-v7` also produced status `rendered` with `resampled=0`, proving the render path can proceed once the timing gate is satisfied.

Residual risk:

1. JP/MGRoid strict pass is still false because stretch quality is poor for many single-syllable clips. This is now a material/stretch-quality problem, not a reference timeline trust problem.
2. The final code after the tuple/list and overlong-span fixes has been verified analysis-only, not with another full render, because the previous full-render run wrote a valid report but did not return cleanly before the tool timeout.
3. Next work should focus on reducing extreme stretch and boundary risk after exact timing is preserved, rather than weakening the strict timing gate.

### Short-Target OTO Window Policy

The JP/MGRoid strict blocker after exact timing was not a text-ordering failure. It was a render-planning quality failure: many verified lyric units are intentionally very short, while UTAU OTO windows describe a larger recorded syllable region. Compressing the entire OTO window into a 20-150 ms slot can create an extreme stretch ratio even when the slot itself is exactly aligned.

Updated policy:

1. For short labeled material targets at or below `150 ms`, OTO/RMS source windows are capped to `1.95x` the audible target duration.
2. The cap keeps OTO start/overlap guidance but does not require preserving an OTO consonant/preutterance window that physically cannot fit into the reference unit.
3. Tiny direct-trim clips with a short vocal text hint are not counted as stretch-quality errors, because no time-stretch filter is applied. Unknown-text tiny targets still report extreme stretch.
4. This preserves the strict timing gate: total target duration, audible target duration, pre-silence, and exact aligned unit timings remain unchanged. Only the render source window and stretch-quality classification change.

Real evidence:

1. JP analysis v12 for `1000nenyikiteru_JP__MGRoid` reached `status=ok`, `error_warning_count=0`, `extreme_stretch_count=0`, `timed=525/525`, `exact=525/525`, and `resampled=0`.
2. Full JP render v13 at `tests_real\output\cn-jp-topology-fix-20260817\jp-render-v13` reached `strict_render_pass=true`, `rendered_audio_alignment_score=0.830973`, and `duration_delta_ratio=0.000144`.
3. Remaining JP warnings are moderate stretch/boundary risks. They should guide manual listening and later DSP tuning, but they no longer block automated strict acceptance.

Residual risk:

1. The OTO cap is a conservative render-planning policy, not a full phoneme-level model. Very short units can still sound clipped or softened; manual listening remains necessary.
2. The current accepted JP case uses WhisperX character alignment. Running the same command with Faster Whisper alone produces no strict unit timings and is not equivalent validation.
3. The next known blocker is the CN case whose final lyric line still lacks exact aligned unit timing.

### CN Exact-Timing Repair And Vowel-Core Atempo Safety

The CN `AiRenTongZhi_CN__chickenOTTO` blocker was a forced-alignment edge case, not a lyric text or material ordering failure. WhisperX could align almost every lyric line, but failed the final repeated `爱人同志` line and kept only a tiny segment-level span. That must remain a strict blocker unless another aligner can supply exact unit timings.

Updated timing policy:

1. WhisperX remains the primary lyrics forced-aligner for the CN/JP verified-lyrics workflow.
2. For Chinese lyrics only, missing or incomplete lyric unit timings may be repaired from FunASR when FunASR provides a contiguous exact unit sequence matching the expected lyric units.
3. The repair is bounded by neighboring lyric timings, so repeated phrases are matched by timeline context rather than by text alone.
4. FunASR resampled timestamps are explicitly not accepted as exact timing. The strict gate still requires real unit timings and still reports missing/resampled timing as errors.
5. Reference analysis caches include the FunASR exact-repair policy version, preventing older missing-line caches from passing through future runs.

Updated render policy:

1. Short vowel-core expansion now shares the short-CV `atempo` safety path when the core source/target region is small enough. This prevents FFmpeg Rubber Band from receiving unstable sub-100 ms vowel-core windows such as the `ju.wav` `~89 ms -> ~256 ms` failure.
2. Chained `atempo` is allowed down to `0.25` for short text material, so ratios like `0.35` can be represented as legal FFmpeg filters instead of requiring Rubber Band.
3. Longer vowel-core regions still use the existing Rubber Band or Signalsmith path according to the existing planning rules.

Real evidence:

1. CN analysis v12 reached `status=ok`, `error_warning_count=0`, `timed=351/351`, `exact=351/351`, and `resampled=0`. The report records `lyrics_funasr_timing_repair: exact_unit_timing_lines=40/40`.
2. CN render v12 failed on a Rubber Band `Operation not permitted` error for a short `ju.wav` vowel-core region. That is preserved as failure evidence for the old render policy.
3. CN render v13 reached `status=rendered`, `strict_render_pass=true`, `rendered_audio_alignment_score=0.900868`, `error_warning_count=0`, `timed=351/351`, `exact=351/351`, and `resampled=0`.
4. The v13 output WAV is mono `44100 Hz` `pcm_s24le` with duration `256.968707 s`, matching the reference duration.

Residual risk:

1. CN still has non-blocking warnings: moderate stretch, boundary risk, and weak text-signal warnings. They no longer invalidate the strict timing/render gate, but they remain useful targets for manual listening and future material-selection work.
2. The FunASR repair is currently Chinese-specific. Japanese continues to rely on WhisperX plus verified pronunciation text because FunASR's default Chinese Paraformer path is not suitable for JP.
### CN/JP Phonetic Candidate Ranking And Filename Variant Policy (2026-08-18)

The remaining low-match findings in the v13 real reports were a ranking-policy defect rather than a missing material inventory. In the CN corpus, `zhi1.wav` was present, but the sequence selector could choose `zha`, `zhui`, or `zhu` for other `zhi` positions. Two mechanisms combined to produce this: filename-authority suffixes such as `zhi1` were interpreted as tone 1, and the selector rewarded an unused material before comparing exact local phonetic-unit identity. JP short syllables also received artificially low match scores because the whole-reference score and tiny target duration dominated the score even after the current unit was correctly positioned.

Durable policy:

1. Filename numeric suffixes are variant indices only when all of the following hold: the language is CN, the material is a single filename-authority token, and the suffix-bearing filename token is the authoritative parsed token. This keeps ordinary tone-number Pinyin inputs such as `shi4` tone-aware.
2. In reference phonetic-unit sequence mode, candidate priority is exact local unit match, then selection score, duration fit, unused-material preference, reuse count, and filename tie-break. Diversity cannot override a phonetic mismatch.
3. A selected exact local unit match contributes directly to the decision phonetic score. Exact matches are reported as `phonetic_score=1.0` and receive a conservative score floor of `0.82`; stretch duration and boundary quality remain independent warning dimensions.
4. Compact matching remains available for multi-unit material phrases and non-JP matching, but it cannot win over an exact same-unit candidate merely because it is unused.

Evidence:

1. Replaying the v13 CN analysis metadata after the policy change moved every `zhi` position to `zhi1.wav`, removed weak-text candidates, and raised the reconstructed minimum/mean ordering scores to `0.82` / `0.871834` before the final render report.
2. Replaying the v13 JP analysis metadata produced minimum/mean scores `0.82` / `0.828675`, with exact unit matches reported as strong instead of weak.
3. Fresh CN v14 and JP v14 renders both passed the strict render gate with exact timing coverage and no error warnings. Their remaining warnings are quality-review warnings from stretch and boundary handling, not text-position or timing-trust failures.

Next architecture priority:

Continue improving source-window and boundary planning for very short CN/JP units while preserving the current ordering invariant: local phonetic identity is authoritative, exact original-vocal unit timing is authoritative for the time axis, and stretch quality is reported separately rather than hidden inside match confidence.

### Short Direct-Trim Boundary Warning Refinement (2026-08-18)

The short-target review separated audio behavior from warning classification. Replacing direct trims with `atempo` for 15-35 ms UTAU windows was tested by replaying the accepted CN and JP ordering/timing metadata. It changed 68 CN clips and 61 JP clips, but the output-level jump metrics were effectively unchanged and several clip-boundary samples worsened. That replacement is rejected.

The retained policy is conservative:

1. Keep `tiny_target_direct_trim` for the existing short-target path. This preserves the v14 audio behavior and exact target durations.
2. Pass render strategy and boundary-conditioning metadata into `_stretch_continuity_warning`.
3. Suppress the warning only for short vocal material with a source window bounded to `1.95x` the audible target, a minimum edge fade, and an audible target above `0.015 s`.
4. Preserve warnings for unbounded direct trims, source windows wider than the compressed-window policy, and targets at or below `0.015 s`.

This is a reporting correction, not a claim that all tiny clips are perceptually safe. It reduces conservative false positives after the source window and fades have already conditioned the clip, while keeping the strict timing gate and audio path unchanged.

Evidence:

1. Replanning the accepted CN v14 ordering produced `43` continuity warnings: `18` moderate and `25` single-syllable. The prior v14 report had `111` continuity warnings.
2. Replanning the accepted JP v14 ordering produced `271` continuity warnings: `259` moderate and `12` single-syllable. The prior v14 report had `320` continuity warnings.
3. Full tests pass with `226` tests, the focused material suite passes with `36`, compileall passes, and `audio_processor check` passes.
4. The attempted full CN v15 real-eval was blocked by WhisperX/Hugging Face alignment-model access and is not used as algorithm acceptance. The accepted v14 render reports remain authoritative for CN/JP audio and strict timing.

Next work should measure actual adjacent-clip boundary samples and only then introduce a sample-level smoothing operation. Do not weaken exact unit timing, local phonetic identity, or strict render blockers to reduce warning counts.

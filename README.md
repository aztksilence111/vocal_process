# vocal_process

## 下载

便携包和历史版本现在放在 GitHub Releases：

- 最新便携包：<https://github.com/aztksilence111/vocal_process/releases/latest>
- 当前主线：`main`
- 归档旧基线：`archive/basic-mvp-before-model-runtime`

下载后请进入 Release 的 Assets，取 `VocalProcess-portable.zip`，解压后直接运行 `VocalProcess.exe`。
如果你是克隆仓库后再取包，Release 页面里的资产和源码压缩包是分开的，不要把源码 zip 当成便携包。

## 更新日志

### 2026-08-21: Rendered boundary diagnostics and real-output closeout

1. Final rendered clip joins now have read-only sample-jump diagnostics, including mean, p95, maximum, output time, and adjacent clip context. Real-eval reports expose the worst measured boundary for manual review.
2. Verified CN and JP real renders completed with exact original-vocal unit timing, zero resampled timing, and strict render passes. The closeout evidence is stored under `tests_real\output\real-output-boundary-metrics`.
3. No new release asset was added; the existing `VocalProcess-portable.zip` release workflow remains unchanged.
4. Manual testing should review the generated WAV together with `summary.md` and the boundary context before considering any future audio-changing smoothing work.

### 2026-08-04: Signalsmith vowel-core stretch and main promotion

1. Short filename-labeled material with a detected vowel core now uses a Signalsmith Stretch backend through `python-stretch`, while Rubber Band remains the fallback for unsupported or failed renders.
2. Material render plans, DAW exports, preflight reports, cache keys, portable packaging, and third-party notices now record the selected `stretch_backend`.
3. `codex/cancel-phonetic-accuracy` is promoted as the completed work branch for this main update; the previous `main` is backed up under `archive/main-before-signalsmith-vowel-core`.
4. Verification before promotion: 166 unit tests passed, `audio_processor check` passed, and a 0.5s-to-2.0s Signalsmith render smoke produced exact duration.

### 2026-07-31: 歌词优先与日文注音处理

1. 有歌词文件时，歌词文本作为目标字音最高优先项，原人声 ASR/对齐时间轴继续负责每个字音的起点、终点和持续时长；无歌词时继续按原人声识别字音拼接。
2. 接入 Janome，把日文汉字/假名歌词转换为可匹配素材文件名的发音单元；中文仍使用拼音匹配。
3. 日文歌词中的假名/罗马音注音行和行内注音会先折叠，避免同一句歌词的不同写法被当成重复目标。
4. GUI 新增“帮助”和“更新日志”弹窗，`audio_processor check` 会显示 Janome 是否可用。

### 2026-07-27: 便携包 torch._C / Whisper FFmpeg 人工测试修复

1. 针对人工测试中 `Whisper transcription failed ... No module named 'torch._C'` 的失败，模型运行时预检现在会真实验证 PyTorch 原生扩展，而不是只检查 `torch` 包目录是否存在。
2. 批处理 diagnostics 新增 `model.runtime.preflight` 事件；如果便携包不完整、用错轻量包或解压时丢失 `_internal\torch`，日志会在排序前明确提示运行时问题。
3. 完整便携包构建脚本现在会强制检查 `_internal\torch\_C.cp311-win_amd64.pyd`、`torch_cpu.dll`、`torch_python.dll` 等关键文件；缺失时构建直接失败。
4. 新增 `scripts\check_portable_runtime.ps1`，用于人工测试前快速检查解压后的便携包或 ZIP 是否包含完整模型运行时。
5. 便携启动时会把 `VocalProcess\bin` 注入进程 `PATH`，解决 OpenAI Whisper/WhisperX 这类第三方库自行调用 `ffmpeg` 时找不到包内 FFmpeg、只显示“系统找不到指定的文件”的问题。

### 2026-07-27: Melodyne/Vegas 时间轴交接导出

1. 新增 `export-melodyne` 命令，输出可直接打开的完整时间轴 WAV，并生成每个素材独立的全长 lane WAV。lane WAV 从 0 秒开始，素材开始前补静音，用于在 Melodyne 这类不读取外部 clip offset 的流程中保留听感时间轴。
2. 新增 `export-vegas` 命令，在上述完整 WAV/lane WAV 之外额外写入 Broadcast Wave timestamp 片段，供 VEGAS 按音频时间戳放回原始时间轴位置。
3. 新增 `audio_processor.handoff` 通用交接层，Melodyne、VEGAS 和后续宿主导出都复用同一套时间轴 manifest、CSV、lane 渲染和完整参考混音逻辑。

### 2026-07-27: Melodyne 3.x 适配目标

1. Melodyne 验证目标改为本机可用的 Melodyne 3.x，不再把本机未正确安装或删除不完整的 Melodyne 4 当成适配结论。
2. 已验证 `E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe` 可以正常启动并关闭。
3. 新增 `scripts\test_melodyne_3x.ps1`，用于重复执行 Melodyne 3.x 启动烟测。

### 2026-07-27: 核心排序 v2 与短字安全渲染

1. 素材排序从逐段贪心选择升级为全局评分矩阵和一一对应分配，`analysis.json` 与 diagnostics 会记录 `ordering_strategy`、`score_matrix`、分项得分和低置信度标记。
2. 新增中文短字/单音节发音匹配层，安装 `pypinyin` 后会把中文和文件名拼音提示纳入 `phonetic_score`，用于改善“一个字”素材的一一对应排序。
3. LRC/SRT 时间戳会被解析和校验，但不会作为最高优先级真值；当歌词时间戳与 ASR/声学时间冲突时，会记录 `lyric_timing_conflict` 或相关 timing note。
4. 短字素材大幅拉长时启用 `syllable_safe_expand_with_tail_padding`，限制人声核心过度拉伸并用尾部补白补足目标时长。
5. 普通 WAV 输出也开始使用片段渲染缓存；相同源素材、目标时长、Rubber Band 参数和处理参数会复用已渲染片段。
6. VST3 bridge 请求支持 `progress_path`，helper 会持续写入 JSON 进度，便于 DAW UI 后续读取状态。
7. 自动/可选 ASR 模式会跳过未缓存的 Faster Whisper/WhisperX/FunASR 模型，除非显式允许下载，避免人工测试时反复等待离线 Hub 查找失败。

### 2026-07-27: 完整便携包与 VST3 包发布更新

1. Release 资产应同时提供两个完整便携包：
   - `VocalProcess-portable.zip`：不含 VST3，适合普通 GUI/CLI 人工测试和本地模型处理。
   - `VocalProcess-portable-vst3.zip`：包含 `plugins\VocalProcess Bridge.vst3`，适合 DAW 宿主插件测试。
2. 两个完整包都包含 Python 3.11 模型运行时、本地模型缓存、UVR worker、Faster Whisper、WhisperX、FunASR/ModelScope 和 pyannote.audio。
3. 非 VST3 版体积略小，测试变量更少；VST3 版只在需要宿主软件扫描/加载插件时使用。
4. 带 `-lite` 的压缩包只用于启动、GUI 或 VST3 包装烟测，不作为完整功能发布包。
5. 本次完整包 Release：`v2026.07.27-portable-full-runtime`，地址为 <https://github.com/aztksilence111/vocal_process/releases/tag/v2026.07.27-portable-full-runtime>。

项目基础协作规则记录在 `PROJECT_RULES.md`。继续开发前应先阅读该文件、`CONVERSATION_LOG.md` 和 `PROJECT_ANALYSIS.md`。

VocalProcess 是一个本地人声素材处理工具，提供命令行入口和桌面 GUI。当前主流程面向“用一组素材人声匹配原音频结构后拉伸拼接”的测试场景：原音频负责提供参考人声、时长和时间轴，素材集负责提供可替换的人声音频。

## 当前能力

1. GUI 批量处理原音频、素材集、歌词文件和输出目录。
2. 使用本地预训练模型辅助分析，不使用在线推理计费。
3. UVR headless worker 和 Demucs 用于原音频人声分离；可用时优先通过独立 UVR worker 运行，失败时回退到 Demucs。
4. Faster Whisper、WhisperX、FunASR/Paraformer 和 OpenAI Whisper 用于原音频和素材音频转写/对齐；默认优先尝试本地加速后端，失败时回退。
5. Silero VAD 用于检测素材中的人声区域。
6. SpeechBrain 说话人特征接口已接入；默认只在模型缓存命中时启用，避免用户首次运行时长时间等待 Hugging Face 下载。
7. 生成结构化 `.diagnostics.jsonl`，用于定位无报错失败、模型转写失败、FFprobe 元数据失败等问题。
8. 输出普通 WAV，或导出 REAPER `.rpp`、`timeline.json`、`timeline.csv` 和独立素材片段 WAV，方便在 DAW 中继续编辑每个素材 item。
9. 提供 `batch` 命令，便于在便携包里直接跑一条真实模型辅助输出。
10. 素材分析缓存默认写入工作缓存目录，同一素材目录未变化时复用分析结果；如需定向缓存，可由调用方单独指定。
11. 原音频分析会生成参考缓存，同一原音频、歌词、计算设备和人声分离策略不变时复用 Demucs/ASR/声纹结果。
12. GUI 显示运行时长，支持 CPU/GPU 计算设备选择，并提供“自动判断 / 已是人声跳过分离 / 强制分离”按钮。
13. 完整模型运行时包含 `faster-whisper`、`whisperx`、`funasr`、`modelscope` 和 `pyannote.audio`；pyannote 预训练模型仍需要 Hugging Face token 和模型条款授权。
14. 排序诊断包含全局评分矩阵、拼音/发音分数、证据计数和低置信度标记，便于人工测试前复核。
15. 提供 Melodyne 和 VEGAS 时间轴交接导出；可生成完整参考 WAV、全长 lane WAV、manifest/CSV，并为 VEGAS 写入 BWF timestamp 片段。

## 环境要求

源码运行建议：

1. 主程序使用 Python 3.11.x，建议通过 `.venv311` 运行；不要依赖本机默认 Python 3.14。
2. FFmpeg 和 FFprobe 可从 PATH 调用，或使用便携包内置版本。
3. 基础项目安装不再默认拉取完整模型栈；完整模型运行时可通过 `requirements\model-full-py311.txt` 安装。
4. UVR headless runner 使用独立 Python 3.10 worker：`.uvr-worker`。

本机已验证：

1. 主程序：Python 3.11.9 in `.venv311`。
2. UVR worker：Python 3.10.11 in `.uvr-worker`，`uvr-headless-runner` 1.1.0。
3. FFmpeg 8.1.2。
4. UVR runner 入口可用，默认 `htdemucs` 模型已下载并通过实际 1 秒人声分离烟测。

## 常用命令

启动图形界面：

```powershell
python -m audio_processor gui
```

检查运行环境和模型缓存：

```powershell
python -m audio_processor check
```

查看音频信息：

```powershell
python -m audio_processor probe input.mp3
python -m audio_processor probe input.mp3 --json
```

处理或转码单个音频：

```powershell
python -m audio_processor process input.wav output.mp3 --normalize --gain-db -3 --sample-rate 44100 --channels 2 --overwrite
```

导出 DAW 时间轴工程：

```powershell
python -m audio_processor export-daw reference.wav materials reference_daw\reference.rpp --overwrite
python -m audio_processor batch reference.wav output.wav --material-directory materials --compute-device cpu --source-separation never --overwrite
```

导出 Melodyne/VEGAS 时间轴交接文件：

```powershell
python -m audio_processor export-melodyne reference.wav materials handoff_melodyne --overwrite
python -m audio_processor export-melodyne reference.wav materials handoff_melodyne --overwrite --open-melodyne --melodyne-exe "E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe"
python -m audio_processor export-vegas reference.wav materials handoff_vegas --overwrite
```

查看模型候选和状态：

```powershell
python -m audio_processor models
python -m audio_processor models --json
```

便携版真实模型烟测：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_portable_runtime.ps1 -ZipPath dist\VocalProcess-portable.zip
powershell -ExecutionPolicy Bypass -File scripts\smoke_portable_model.ps1
powershell -ExecutionPolicy Bypass -File scripts\smoke_portable_uvr_worker.ps1
```

## GUI 流程

1. 选择原音频。
2. 选择素材集文件夹。
3. 可选选择歌词文件。
4. 选择输出目录和输出格式。
5. 选择计算设备。
6. 选择原音频人声模式：自动判断、已是人声跳过分离、强制分离。
7. 选择是否导出 DAW 时间轴工程和窗口尺寸。
8. 点击“开始批量处理”。

当选择素材集后，模型辅助排序是核心流程，不提供关闭按钮。处理链路会先分析原音频和素材人声，再把排序结果交给现有的拉伸拼接或 DAW 时间轴导出模块。

## 诊断日志

每个批处理任务都会生成结构化 JSONL 日志：

1. 普通 WAV 输出：`<输出文件名>.diagnostics.jsonl`。
2. DAW 时间轴工程：工程文件夹内的 `diagnostics.jsonl`。

日志会记录处理模式、输入路径、设置快照、参考音频元数据、素材清单、模型排序结果、完成状态和异常堆栈。人工测试失败时，应优先收集这个文件。
如果 FFprobe 对某个 WAV 返回空 JSON，程序会先尝试 Python WAV/FFmpeg stderr 兜底；诊断阶段的素材元数据失败会写入 warning，不再直接中断整批任务。失败和取消记录会包含运行耗时。

## 便携版

构建 Windows 便携 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

输出位置：

```text
dist\VocalProcess-portable.zip
```

默认构建会生成不含 VST3 的标准完整包 `dist\VocalProcess-portable.zip`，并使用 ZIP optimal 压缩。完整包包含 Python 3.11 模型运行依赖、`.tmp\model-cache` 本地模型缓存和 `uvr-worker` 独立 UVR 运行时。生成含 VST3 的完整宿主测试包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -IncludeVst3Bridge
```

输出位置：

```text
dist\VocalProcess-portable-vst3.zip
```

一次生成两个完整变体：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable_variants.ps1
```

构建会收集模型运行依赖，并把 `.tmp\model-cache` 复制到便携包的 `models` 文件夹。便携运行时会优先读取 `VocalProcess\models`，从而使用本地预训练模型缓存。

当前本机完整包大小：

```text
dist\VocalProcess-portable.zip       1,413,022,068 bytes
dist\VocalProcess-portable-vst3.zip  1,415,457,165 bytes
```

只做启动、GUI 或 VST3 包装烟测时，可显式生成轻量包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable_variants.ps1 -Lite
```

轻量包输出为 `dist\VocalProcess-portable-lite.zip` 和 `dist\VocalProcess-portable-lite-vst3.zip`。它们不会复制完整 `site-packages`、模型缓存或 UVR worker，不能作为完整模型功能发布包。

便携版自动冒烟测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1
```

冒烟测试会解压 ZIP，检查 `VocalProcess.exe`、内置 `ffmpeg.exe`、内置 `ffprobe.exe`，并隐藏启动 GUI 以确认程序不会启动即崩溃。

## DAW 时间轴导出

启用 DAW 时间轴导出后，输出目录类似：

```text
reference_daw\
  reference.rpp
  timeline.json
  timeline.csv
  diagnostics.jsonl
  audio\
    0001_clip.wav
    0002_clip.wav
```

`reference.rpp` 是 REAPER 工程文件，包含参考音频轨和独立素材片段轨；`timeline.json` 和 `timeline.csv` 记录每个素材片段的源文件、输出文件、开始时间、目标时长和排序依据。
如果 DAW 导出中出现完全相同的源素材、目标时长、Rubber Band tempo 和渲染参数，后续片段会复用已渲染 WAV，避免重复拉伸计算。

Melodyne 时间轴交接导出会生成：

```text
handoff_melodyne\
  melodyne_full.wav
  melodyne_handoff.json
  melodyne_handoff.csv
  timeline.rpp
  audio\
    0001_clip.wav
  melodyne_lanes\
    0001_clip_melodyne_timeline.wav
```

`melodyne_full.wav` 是完整连续参考文件，可直接用 Melodyne 打开；`melodyne_lanes` 中的 WAV 是全时间轴长度，每个文件在素材进入前补静音，因此即使 Melodyne 不读取外部 clip offset，导入后也能按听感保留位置。

VEGAS 时间轴交接导出会在同样的完整 WAV、lane WAV、manifest/CSV 之外生成：

```text
handoff_vegas\
  vegas_full.wav
  vegas_handoff.json
  vegas_handoff.csv
  vegas_bwf\
    0001_clip_vegas_timestamp.wav
```

`vegas_bwf` 中的 WAV 写入 Broadcast Wave `time_reference` 元数据，用于 VEGAS 的按时间戳放置流程；如果宿主或导入设置没有启用该流程，则使用 `vegas_lanes` 作为保留时间轴的兜底导入方式。

## 已知限制

1. 当前默认 ASR 模型是 Whisper `base`，在复杂歌曲、混响、伴奏很强或非清晰人声素材上仍可能转写不准。
2. WhisperX 已纳入完整模型运行时；首次使用对齐模型时可能需要下载对应语言的对齐模型缓存。
3. pyannote.audio 已纳入完整模型运行时；使用 Hugging Face 预训练 diarization pipeline 仍需要授权 token 和模型条款确认。
4. 首次完整模型推理在 CPU 上可能较慢；便携包内置缓存可以减少下载等待，但不能消除推理耗时。
5. `faster-whisper` 已包含在完整模型运行时；CPU 推理仍可能明显慢于 GPU。

## 测试

```powershell
.venv311\Scripts\python -m unittest discover
.venv311\Scripts\python -m compileall -q audio_processor tests packaging
```

## Runtime Version Layout

The production runtime is now split by dependency compatibility:

1. Main app/runtime: Python 3.11 in `.venv311`.
2. UVR headless worker: Python 3.10 in `.uvr-worker`.
3. Native VST3 bridge: JUCE/MSVC binary that launches the helper process; it does not embed Python in the audio callback.

Bootstrap the main environment:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_py311_env.ps1 -DevTools
```

Bootstrap the UVR worker:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_uvr_worker.ps1
```

Check the UVR worker without running separation inference:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_uvr_worker.ps1
```

The helper writes `.tmp\uvr-worker-env.ps1`, which sets `VOCAL_PROCESS_UVR_PYTHON`, `VOCAL_PROCESS_SOURCE_SEPARATOR`, `VOCAL_PROCESS_UVR_ARCH`, and `VOCAL_PROCESS_UVR_MODEL`. Source separation uses the UVR worker first when it is available, then falls back to in-process Demucs when applicable.

`scripts\build_portable.ps1` prefers `VOCAL_PROCESS_PYTHON`, then `.venv311`, then the legacy `.venv`. Portable builds fail fast if the selected runtime is not Python 3.11.x.

## Recent Runtime Notes

The model-assisted material workflow now writes a `render.stretch_plan` event to diagnostics. This records every material clip's source duration, target duration, Rubber Band tempo, and quality warning. Flat WAV output and DAW timeline export both use per-clip stretching before concatenation or timeline placement.

The VST3 bridge work has started as an offline helper protocol:

```powershell
python -m audio_processor vst3-bridge --template
python -m audio_processor vst3-bridge request.json --response response.json
python -m audio_processor vst3-bridge --contract
python -m audio_processor vst3-bridge --watch requests --responses responses --once
python -m audio_processor analyze reference.wav materials --output analysis.json --compute-device cpu --source-separation never
```

The native VST3 bridge plug-in now lives in `native\vst3_bridge`. It is a JUCE/MSVC VST3 control surface that passes audio through unchanged and calls `VocalProcess.exe` as a helper process for render/analyze work. Build it with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_vst3_bridge.ps1
```

The generated bundle is:

```text
build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3
```

Use `scripts\build_portable.ps1` for the standard no-VST3 full package, and `scripts\build_portable.ps1 -IncludeVst3Bridge` for the VST3 full host-test package. The VST3 package includes the bundle under `VocalProcess\plugins`.

Runtime speed controls:

1. In the GUI, set Reference Vocals / 原音频人声 to `已是人声，跳过分离` when the reference file is already an isolated vocal stem.
2. The process reuses loaded ASR/VAD/speaker models within the same run.
3. Reference analysis is cached by file snapshot, lyrics snapshot, compute device, ASR backend, source separation mode, and selected separation backend/model.
4. DAW timeline export reuses exact duplicate stretched clip renders.

Host-side validation helpers:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\probe_vst3_bridge.ps1
powershell -ExecutionPolicy Bypass -File scripts\install_vst3_bridge.ps1 -Force
powershell -ExecutionPolicy Bypass -File scripts\host_test_reaper_vst3.ps1
powershell -ExecutionPolicy Bypass -File scripts\host_test_flstudio_vst3.ps1
powershell -ExecutionPolicy Bypass -File scripts\check_melodyne_context.ps1
powershell -ExecutionPolicy Bypass -File scripts\test_melodyne_3x.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_maintenance_session.ps1
```

The maintenance session helper writes heartbeat, state, and task logs under `.tmp\maintenance_sessions\<timestamp>-<name>\`, and it can be pointed at a custom plan file when you want a longer background run.

Current local host results:

1. JUCE headless VST3 probe scanned and instantiated `VocalProcess Bridge.vst3`.
2. REAPER 7.33 x64 scanned the bridge in an isolated config and cached it successfully.
3. The bridge was installed to `C:\Program Files\Common Files\VST3\VocalProcess Bridge.vst3` for hosts that scan the common VST3 folder.
4. FL Studio 2024 Plugin Manager can be launched, but no documented non-interactive scan command was found; use Plugin Manager > Find installed plugins for final FL registration.
5. Melodyne 3.2 at `E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe` launches successfully on this machine and can open a generated full-timeline WAV argument. Melodyne 3.x is the local compatibility target, and Melodyne remains a WAV/DAW/ARA workflow target rather than a generic host for this 64-bit VST3 bridge.
6. VEGAS support is file-handoff based: `export-vegas` writes BWF timestamp clips plus full-length fallback lane WAVs for manual import validation.

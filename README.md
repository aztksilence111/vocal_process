# vocal_process

## 下载

便携包和历史版本现在放在 GitHub Releases：

- 最新便携包：<https://github.com/aztksilence111/vocal_process/releases/latest>
- 当前主线：`main`
- 归档旧基线：`archive/basic-mvp-before-model-runtime`

下载后请进入 Release 的 Assets，取 `VocalProcess-portable.zip`，解压后直接运行 `VocalProcess.exe`。
如果你是克隆仓库后再取包，Release 页面里的资产和源码压缩包是分开的，不要把源码 zip 当成便携包。

VocalProcess 是一个本地人声素材处理工具，提供命令行入口和桌面 GUI。当前主流程面向“用一组素材人声匹配原音频结构后拉伸拼接”的测试场景：原音频负责提供参考人声、时长和时间轴，素材集负责提供可替换的人声音频。

## 当前能力

1. GUI 批量处理原音频、素材集、歌词文件和输出目录。
2. 使用本地预训练模型辅助分析，不使用在线推理计费。
3. Demucs 用于原音频人声分离。
4. OpenAI Whisper 用于原音频和素材音频转写。
5. Silero VAD 用于检测素材中的人声区域。
6. SpeechBrain 说话人特征接口已接入；默认只在模型缓存命中时启用，避免用户首次运行时长时间等待 Hugging Face 下载。
7. 生成结构化 `.diagnostics.jsonl`，用于定位无报错失败、模型转写失败、FFprobe 元数据失败等问题。
8. 输出普通 WAV，或导出 REAPER `.rpp`、`timeline.json`、`timeline.csv` 和独立素材片段 WAV，方便在 DAW 中继续编辑每个素材 item。
9. 提供 `batch` 命令，便于在便携包里直接跑一条真实模型辅助输出。

## 环境要求

源码运行建议：

1. Python 3.11 或更新版本。
2. FFmpeg 和 FFprobe 可从 PATH 调用，或使用便携包内置版本。
3. 已安装项目依赖：`torch`、`torchaudio`、`openai-whisper`、`demucs`、`speechbrain`。

本机已验证：

1. Python 3.14.6。
2. FFmpeg 8.1.2。
3. CPU 版 PyTorch、Whisper、Demucs、SpeechBrain。

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
```

查看模型候选和状态：

```powershell
python -m audio_processor models
python -m audio_processor models --json
```

便携版真实模型烟测：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke_portable_model.ps1
```

## GUI 流程

1. 选择原音频。
2. 选择素材集文件夹。
3. 可选选择歌词文件。
4. 选择输出目录和输出格式。
5. 选择是否导出 DAW 时间轴工程。
6. 点击“开始批量处理”。

当选择素材集后，模型辅助排序是核心流程，不提供关闭按钮。处理链路会先分析原音频和素材人声，再把排序结果交给现有的拉伸拼接或 DAW 时间轴导出模块。

## 诊断日志

每个批处理任务都会生成结构化 JSONL 日志：

1. 普通 WAV 输出：`<输出文件名>.diagnostics.jsonl`。
2. DAW 时间轴工程：工程文件夹内的 `diagnostics.jsonl`。

日志会记录处理模式、输入路径、设置快照、参考音频元数据、素材清单、模型排序结果、完成状态和异常堆栈。人工测试失败时，应优先收集这个文件。

## 便携版

构建 Windows 便携 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

输出位置：

```text
dist\VocalProcess-portable.zip
```

默认构建会收集模型运行依赖，并把 `.tmp\model-cache` 复制到便携包的 `models` 文件夹。便携运行时会优先读取 `VocalProcess\models`，从而使用本地预训练模型缓存。

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

## 已知限制

1. 当前默认 ASR 模型是 Whisper `base`，在复杂歌曲、混响、伴奏很强或非清晰人声素材上仍可能转写不准。
2. WhisperX 在当前 Python 3.14 环境中受依赖版本限制，未作为默认后端启用。
3. pyannote.audio 需要 Hugging Face 授权 token 和模型条款确认，当前仅保留可选接入路径。
4. 首次完整模型推理在 CPU 上可能较慢；便携包内置缓存可以减少下载等待，但不能消除推理耗时。

## 测试

```powershell
.venv\Scripts\python -m unittest discover
.venv\Scripts\python -m compileall -q audio_processor tests packaging
```

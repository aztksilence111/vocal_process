VocalProcess Portable

包类型：

- `VocalProcess-portable.zip`：标准完整包，不含 VST3，包含模型运行依赖、模型缓存和 UVR worker。
- `VocalProcess-portable-vst3.zip`：标准完整包，另含 `plugins\VocalProcess Bridge.vst3`，用于 DAW 宿主插件测试。
- `VocalProcess-portable-lite.zip` / `VocalProcess-portable-lite-vst3.zip`：轻量烟测包，只用于启动、GUI 或 VST3 包装检查；不应当用于完整模型功能测试。

使用方法：

1. 解压整个 `VocalProcess-portable.zip`。
2. 打开解压后的 `VocalProcess` 文件夹。
3. 双击 `VocalProcess.exe`。
4. 在图形界面中选择：
   - 原音频：作为人声识别、时长和时间轴参考的音频文件。
   - 素材集：包含待匹配素材人声音频的文件夹。
   - 歌词文件：可选，支持 `.txt`、`.docx`、`.lrc`、`.srt`。
   - 输出目录：保存 WAV 或 DAW 时间轴工程的位置。
   - 原音频人声：如果原音频已经是干人声，选择“已是人声，跳过分离”可以减少 Demucs 耗时。
5. 输出扩展名建议保持 `.wav`。
6. 点击“开始批量处理”。

注意：

- 便携版已内置 `ffmpeg.exe` 和 `ffprobe.exe`，普通用户不需要安装 Python 或 FFmpeg。
- 便携版会优先使用同目录下的 `models` 文件夹作为本地模型缓存。
- 当前核心流程会使用本地预训练模型进行人声分离、转写、VAD 和素材排序；不使用在线推理计费。
- 完整包包含 Faster Whisper、WhisperX 和 pyannote.audio；pyannote 预训练模型仍需要用户提供 Hugging Face token 并接受模型条款。
- 首次处理会比普通 FFmpeg 转码慢很多，尤其是在 CPU 上运行 Demucs 和 Whisper 时。
- 同一素材集未变化时会复用 `.vocalprocess_material_cache.json`；同一原音频和设置未变化时会复用参考分析缓存。
- 标准完整包分为两个：`VocalProcess-portable.zip` 不含 VST3，适合普通人工测试；`VocalProcess-portable-vst3.zip` 含 `plugins\VocalProcess Bridge.vst3`，适合 DAW 宿主插件测试。带 `-lite` 的包只用于启动/包装烟测，不代表完整模型功能。
- 请不要只复制 `VocalProcess.exe`；需要保留同目录的 `_internal`、`bin`、`licenses`、`models` 等文件夹。
- 默认 WAV 输出使用 `pcm_s24le`，适合导入常见 DAW 宿主软件。
- 启用 DAW 时间轴导出时，会生成 REAPER `.rpp`、`timeline.json`、`timeline.csv` 和独立素材片段 WAV，方便在宿主里继续单独编辑。
- 每次处理都会在输出旁边生成 `.diagnostics.jsonl` 诊断日志。普通 WAV 输出对应 `<输出文件名>.diagnostics.jsonl`；DAW 工程输出对应工程文件夹中的 `diagnostics.jsonl`。

Troubleshooting:

- If the app cannot find FFmpeg, make sure `bin\ffmpeg.exe` and `bin\ffprobe.exe` still exist beside `VocalProcess.exe`.
- If diagnostics reports `系统找不到指定的文件` during Whisper/ASR, it usually means a third-party ASR library could not start FFmpeg. Keep the full `VocalProcess` folder together; do not launch a copied EXE by itself; verify `bin\ffmpeg.exe` exists.
- If diagnostics or the console reports `No module named 'torch._C'`, the model runtime is incomplete or was not extracted correctly. Use the full package, not a `-lite` package; extract the whole ZIP; keep `_internal\torch`, `models`, and `uvr-worker` beside `VocalProcess.exe`; then run `scripts\check_portable_runtime.ps1 -PortableRoot <解压目录>\VocalProcess` from the source repo when available.
- If Windows SmartScreen warns about the app, it is because this local build is not code-signed.
- If processing fails or the output is not meaningful, send the `.diagnostics.jsonl` file together with the original audio, material folder description, and screenshots of the selected settings.

Advanced bridge usage:

- `VocalProcess.exe vst3-bridge --template` prints a JSON request template for future VST3/native host integration.
- `VocalProcess.exe vst3-bridge request.json --response response.json` runs the offline renderer through that bridge request.
- `VocalProcess.exe vst3-bridge --contract` prints the persistent helper file contract.
- `VocalProcess.exe vst3-bridge --watch requests --responses responses` runs the persistent request watcher.
- `VocalProcess.exe analyze reference.wav materials --output analysis.json` writes an ordering/stretch preflight report before rendering.
- If bundled, `plugins\VocalProcess Bridge.vst3` is the native VST3 control plug-in. Set its Helper field to this package's `VocalProcess.exe`.
- The VST3 bridge plug-in does not run model inference or FFmpeg inside the real-time audio callback; it starts the helper process for offline render/analyze work.
- For system host scans, copy or install the whole `VocalProcess Bridge.vst3` bundle into the 64-bit common VST3 folder, normally `C:\Program Files\Common Files\VST3`.
- Melodyne workflows should use the generated PCM WAV or DAW timeline output. The bridge itself is a 64-bit DAW VST3 control plug-in, not a Melodyne ARA extension.
- Bridge/analyze commands accept `--source-separation never` when the reference is already isolated vocals.

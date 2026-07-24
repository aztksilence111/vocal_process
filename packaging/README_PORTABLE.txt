VocalProcess Portable

使用方法：

1. 解压整个 `VocalProcess-portable.zip`。
2. 打开解压后的 `VocalProcess` 文件夹。
3. 双击 `VocalProcess.exe`。
4. 在图形界面中选择：
   - 原音频：作为人声识别、时长和时间轴参考的音频文件。
   - 素材集：包含待匹配素材人声音频的文件夹。
   - 歌词文件：可选，支持 `.txt`、`.docx`、`.lrc`、`.srt`。
   - 输出目录：保存 WAV 或 DAW 时间轴工程的位置。
5. 输出扩展名建议保持 `.wav`。
6. 点击“开始批量处理”。

注意：

- 便携版已内置 `ffmpeg.exe` 和 `ffprobe.exe`，普通用户不需要安装 Python 或 FFmpeg。
- 便携版会优先使用同目录下的 `models` 文件夹作为本地模型缓存。
- 当前核心流程会使用本地预训练模型进行人声分离、转写、VAD 和素材排序；不使用在线推理计费。
- 首次处理会比普通 FFmpeg 转码慢很多，尤其是在 CPU 上运行 Demucs 和 Whisper 时。
- 请不要只复制 `VocalProcess.exe`；需要保留同目录的 `_internal`、`bin`、`licenses`、`models` 等文件夹。
- 默认 WAV 输出使用 `pcm_s24le`，适合导入常见 DAW 宿主软件。
- 启用 DAW 时间轴导出时，会生成 REAPER `.rpp`、`timeline.json`、`timeline.csv` 和独立素材片段 WAV，方便在宿主里继续单独编辑。
- 每次处理都会在输出旁边生成 `.diagnostics.jsonl` 诊断日志。普通 WAV 输出对应 `<输出文件名>.diagnostics.jsonl`；DAW 工程输出对应工程文件夹中的 `diagnostics.jsonl`。

Troubleshooting:

- If the app cannot find FFmpeg, make sure `bin\ffmpeg.exe` and `bin\ffprobe.exe` still exist beside `VocalProcess.exe`.
- If Windows SmartScreen warns about the app, it is because this local build is not code-signed.
- If processing fails or the output is not meaningful, send the `.diagnostics.jsonl` file together with the original audio, material folder description, and screenshots of the selected settings.

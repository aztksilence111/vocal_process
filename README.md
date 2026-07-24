# vocal_process

这是一个基于 Python 和 FFmpeg 的本地音频处理 MVP。当前版本提供命令行入口和桌面图形界面，用于检查环境、批量队列处理、读取音频元数据、处理和转码音频文件。

## 环境要求

1. Python 3.14 或更新的稳定版
2. FFmpeg 和 FFprobe 可从 PATH 直接调用

本机已通过 Chocolatey 安装并验证：

1. Python 3.14.6
2. FFmpeg 8.1.2

## 常用命令

启动图形界面：

```powershell
python -m audio_processor gui
```

检查运行环境：

```powershell
python -m audio_processor check
```

查看音频信息：

```powershell
python -m audio_processor probe input.mp3
```

输出完整 FFprobe JSON：

```powershell
python -m audio_processor probe input.mp3 --json
```

处理并转码音频：

```powershell
python -m audio_processor process input.wav output.mp3 --normalize --gain-db -3 --sample-rate 44100 --channels 2 --overwrite
```

截取片段：

```powershell
python -m audio_processor process input.wav clip.wav --trim-start 00:00:10 --duration 30 --overwrite
```

基础滤波：

```powershell
python -m audio_processor process input.wav cleaned.wav --highpass-hz 80 --lowpass-hz 12000 --overwrite
```

导出 DAW 时间轴工程：

```powershell
python -m audio_processor export-daw reference.wav materials reference_daw\reference.rpp --overwrite
```

## 图形界面

图形界面支持：

1. 分区上传原音频、素材集和歌词文件
2. 原音频支持 `.wav`、`.mp3`、`.flac`、`.m4a`、`.ogg`、`.opus`、`.aac`、`.aiff`、`.alac`、`.wma`
3. 素材集必须选择文件夹
4. 歌词文件支持 `.txt`、`.doc`、`.docx`、`.lrc`、`.srt`
5. 设置统一输出目录和输出扩展名
6. 设置覆盖、增益、响度标准化、高通、低通、采样率、声道数和编码器
7. 顺序处理原音频队列
8. 显示当前文件进度、总进度、状态和日志
9. 保存并重新加载配置
10. 可选择导出 DAW 时间轴工程，让每个拉伸后的素材片段在宿主中仍然是独立 item
11. 使用语言按钮在中文和英文之间切换，语言偏好会随配置保存

## 素材拼接处理逻辑

图形界面的当前主流程是：

1. 原音频作为参考音频，用来提供目标时长。
2. 素材集文件夹中的受支持音频文件会按文件名排序后顺序拼接。
3. 拼接后的素材音频会用 FFmpeg `rubberband` 做整体拉伸或压缩，使它匹配原音频时长。
4. 处理链路使用 `pitch=1` 和 `formant=preserved`，尽量保持音高、共振峰和单音节发音辨识度。
5. 不循环播放素材，也不因素材过长直接裁切素材。
6. 如果拉伸算法产生毫秒级短缺，只在尾部补静音到目标时长，不裁切素材内容。
7. 默认输出 `.wav`，WAV 输出默认使用 `pcm_s24le`，便于导入 DAW 宿主软件。

## DAW 时间轴导出

启用“导出 DAW 时间轴工程”后，项目不会只输出一个扁平化 WAV，而是输出一个工程文件夹：

```text
reference_daw\
  reference.rpp
  timeline.json
  timeline.csv
  audio\
    0001_clip.wav
    0002_clip.wav
```

导出逻辑：

1. 原音频仍然只作为参考时长。
2. 素材集中的每个音频文件会按文件名排序。
3. 每个素材文件会分别用同一个全局 `rubberband` 比例拉伸或压缩。
4. 每个拉伸结果都会保存为独立 WAV 文件。
5. `timeline.json` 和 `timeline.csv` 记录每个片段的源文件、输出文件、开始时间和目标时长。
6. `reference.rpp` 是 REAPER 工程文件，包含参考音频轨和独立素材片段轨。

这一步解决的是“导入 DAW 后仍可单独编辑各个素材片段”。VST3 桥接属于后续插件/宿主集成层，需要单独的 C++/SDK 构建链路，不和当前 Python GUI 便携版混在同一层实现。

## 安装

建议先创建虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install setuptools -i https://pypi.org/simple
.venv\Scripts\python -m pip install -e . --no-build-isolation
```

安装后可用命令：

```powershell
.venv\Scripts\audio-processor check
.venv\Scripts\audio-processor probe input.mp3
.venv\Scripts\audio-processor process input.wav output.mp3 --overwrite
.venv\Scripts\audio-processor-gui
```

构建 wheel 包：

```powershell
.venv\Scripts\python -m pip wheel . -w dist --no-deps --no-build-isolation
```

构建 Windows 便携版 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

便携版输出位置：

```text
dist\VocalProcess-portable.zip
```

便携版会内置 GUI 可执行文件、`ffmpeg.exe`、`ffprobe.exe` 和第三方许可证说明。普通用户解压后双击 `VocalProcess.exe` 即可使用，不需要单独安装 Python 或 FFmpeg。

便携版自动冒烟测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke_portable.ps1
```

该脚本会从 ZIP 解压便携包，检查 `VocalProcess.exe`、内置 `ffmpeg.exe`、内置 `ffprobe.exe`，并隐藏启动 GUI 5 秒确认程序不会启动即崩溃。

## 测试

```powershell
.venv\Scripts\python -m unittest discover
```

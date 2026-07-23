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

## 图形界面

图形界面支持：

1. 添加多个音频文件到批量队列
2. 设置统一输出目录和输出扩展名
3. 设置覆盖、截取、增益、响度标准化、高通、低通、采样率、声道数和编码器
4. 顺序处理队列
5. 显示当前文件进度、总进度、状态和日志
6. 保存并重新加载配置
7. 使用语言按钮在中文和英文之间切换，语言偏好会随配置保存

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

## 测试

```powershell
.venv\Scripts\python -m unittest discover
```

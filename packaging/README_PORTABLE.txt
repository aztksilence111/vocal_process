VocalProcess Portable

使用方法：

1. 解压整个 VocalProcess-portable.zip。
2. 打开解压后的 VocalProcess 文件夹。
3. 双击 VocalProcess.exe。
4. 在图形界面中选择：
   - 原音频：作为参考时长的音频文件。
   - 素材集：包含待拼接素材音频的文件夹。
   - 歌词文件：可选的歌词/文本文件。
   - 输出目录：保存结果的位置。
5. 输出扩展名建议保持 .wav。
6. 点击“开始批量处理”。

注意：

- 便携版已内置 ffmpeg.exe 和 ffprobe.exe，普通用户不需要安装 Python 或 FFmpeg。
- 请不要只复制 VocalProcess.exe；需要保留同目录的 bin 文件夹。
- 默认 WAV 输出使用 pcm_s24le，适合导入常见 DAW 宿主软件。
- 当前版本会将素材集音频按文件名顺序拼接，然后整体拉伸/压缩到原音频时长。
- 素材不会循环播放，也不会因为过长被直接裁切。

Troubleshooting:

- If the app cannot find FFmpeg, make sure bin\ffmpeg.exe and bin\ffprobe.exe still exist beside VocalProcess.exe.
- If Windows SmartScreen warns about the app, it is because this local build is not code-signed.

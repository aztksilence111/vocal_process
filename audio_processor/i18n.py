from __future__ import annotations


DEFAULT_LANGUAGE = "zh"
SUPPORTED_LANGUAGES = {"zh", "en"}


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "app_title": "Audio Processor",
        "language_menu": "Language: English",
        "language_zh": "Chinese",
        "language_en": "English",
        "add_files": "Add Files",
        "source_inputs": "Source Inputs",
        "original_audio": "Original Audio",
        "material_set": "Material Set",
        "lyrics_file": "Lyrics File",
        "manual_lyrics_enabled": "Manually add lyrics file",
        "select_original_audio": "Select Original Audio",
        "select_material_folder": "Select Material Folder",
        "select_lyrics_file": "Select Lyrics File",
        "clear_material": "Clear Material",
        "clear_lyrics": "Clear Lyrics",
        "supported_formats": "Supported: {formats}",
        "audio_format_hint": ".wav, .mp3, .flac, .m4a, .ogg, .opus, .aac, .aiff, .alac, .wma",
        "material_format_hint": "folder only",
        "lyrics_format_hint": ".txt, .doc, .docx, .lrc, .srt (used only when enabled)",
        "lyrics_optional_active": "Lyrics file: not selected; reference ASR transcript will be used.",
        "lyrics_original_vocal_mode_active": "Lyrics mode: disabled; ordering will use original-vocal ASR/alignment.",
        "material_directory": "Material Folder",
        "lyrics_path": "Lyrics File",
        "remove_selected": "Remove Selected",
        "clear": "Clear",
        "check_tools": "Check Tools",
        "help": "Help",
        "changelog": "Changelog",
        "help_title": "Help",
        "changelog_title": "Changelog",
        "dialog_close": "Close",
        "start_batch": "Start Batch",
        "cancel": "Cancel",
        "input": "Input",
        "output": "Output",
        "status": "Status",
        "progress": "Progress",
        "elapsed": "Elapsed",
        "elapsed_value": "Elapsed: {elapsed}",
        "output_directory": "Output Directory",
        "browse": "Browse",
        "extension": "Extension",
        "overwrite": "Overwrite",
        "daw_timeline_export": "Export DAW timeline project",
        "split_reference_channels": "Split reference stereo channels into mono outputs",
        "compute_device": "Compute Device",
        "source_separation": "Reference Vocals",
        "source_separation_auto": "Auto",
        "source_separation_never": "Already vocal",
        "source_separation_always": "Force separate",
        "window_geometry": "Window Size",
        "gain_db": "Gain dB",
        "normalize": "Normalize",
        "highpass_hz": "High-pass Hz",
        "lowpass_hz": "Low-pass Hz",
        "sample_rate": "Sample Rate",
        "channels": "Channels",
        "codec": "Codec",
        "save_settings": "Save Settings",
        "reload_settings": "Reload Settings",
        "log": "Log",
        "ready": "Ready",
        "processing": "Processing",
        "cancelling": "Cancelling",
        "failed": "Failed",
        "done": "Done",
        "queued": "Queued",
        "cancelled": "Cancelled",
        "complete": "Complete",
        "empty_queue_title": "Empty Queue",
        "empty_queue_message": "Add one or more audio files first.",
        "invalid_material_directory": "Material set must be an existing folder: {path}",
        "missing_material_directory": "Select a material set folder before starting assembly.",
        "empty_material_directory": "Material set does not contain supported audio files: {path}",
        "invalid_lyrics_file": "Lyrics file must be an existing supported file: {path}",
        "missing_lyrics_file_when_enabled": "Manual lyrics is enabled; select a supported lyrics/subtitle file or disable manual lyrics.",
        "unsupported_lyrics_format": "Lyrics file format is not supported: {path}",
        "invalid_compute_device": "Compute device must be auto, cpu, or cuda.",
        "invalid_source_separation": "Reference vocal mode must be auto, always, or never.",
        "tool_check_title": "Tool Check",
        "tool_check_failed_title": "Tool Check Failed",
        "invalid_settings_title": "Invalid Settings",
        "batch_failed_title": "Batch Failed",
        "audio_files": "Audio files",
        "all_files": "All files",
        "added_files": "Added {count} file(s)",
        "material_selected": "Material folder selected: {path}",
        "material_cleared": "Material folder cleared",
        "lyrics_selected": "Lyrics file selected: {path}",
        "lyrics_cleared": "Lyrics file cleared",
        "material_active": "Material folder: {path}",
        "assembly_mode_active": "Assembly mode: material clips will be model-ordered and time-stretched to the original audio duration.",
        "model_assisted_ordering_active": "Model-assisted ordering: local pretrained models analyze vocals before assembly; online inference billing is not used.",
        "material_cache_enabled": "Material analysis cache: reused from the work cache when the folder is unchanged.",
        "compute_device_active": "Compute device: {device}",
        "source_separation_active": "Reference vocal separation: {mode}",
        "daw_timeline_mode_active": "DAW timeline export: each stretched clip will remain a separate editable audio item.",
        "lyrics_active": "Lyrics file: {path}",
        "settings_saved": "Settings saved: {path}",
        "settings_reloaded": "Settings reloaded",
        "batch_started": "Batch started",
        "cancel_requested": "Cancellation requested",
        "batch_failed_log": "Batch failed: {error}",
        "summary": "Done: {completed} completed, {failed} failed, {cancelled} cancelled",
        "must_be_number": "{label} must be a number",
        "must_be_integer": "{label} must be an integer",
        "status_processing_item": "Processing: {name}",
        "status_item": "{status}: {name}",
        "queue_complete": "{index}/{total}: complete",
        "queue_failed": "{index}/{total}: failed",
        "queue_progress": "{index}/{total}: {message}",
        "cancelled_before_processing": "Cancelled before processing",
        "processing_cancelled": "Processing cancelled",
        "no_queued_files": "No queued files",
        "help_body": (
            "VocalProcess assembles a new vocal track from a material folder while preserving the reference vocal timeline.\n\n"
            "1. Select one or more original vocal files, then select a material folder. Each material clip should be a short vocal unit or phrase; clear filename labels such as wo.wav, shi4.wav, a.wav, ai.wav, or kana names improve matching.\n\n"
            "2. Leave manual lyrics disabled to use the original vocal ASR/alignment result as the target unit sequence. Material clips are matched, ordered, and stretched to the recognized units.\n\n"
            "3. With a lyrics file, lyrics text has priority for unit selection. The original vocal ASR/alignment result remains the timing source, so each lyric unit is mapped to the reference vocal start, end, and duration whenever aligned unit timings are available.\n\n"
            "4. Chinese lyrics/material labels are normalized through pinyin. Japanese lyrics may use kanji, kana, or romaji; kanji/kana are converted to pronunciation units before matching file names or material transcripts. Adjacent kana/romaji annotation lines and inline readings are collapsed so the same Japanese phrase is not treated as duplicated lyrics.\n\n"
            "5. Use Reference Vocals to choose whether the original audio needs separation, choose output options, then start the batch. Enable DAW timeline export when you need every stretched material clip as an editable item."
        ),
        "changelog_body": (
            "Latest development changes:\n\n"
            "- Lyrics-priority ordering: lyrics text now drives target unit selection when provided, while original vocal alignment remains the duration source.\n"
            "- Japanese normalization: Janome is integrated to convert Japanese kanji/kana lyrics into pronunciation units for romaji/kana filename matching.\n"
            "- Lyric annotation cleanup: Japanese kana/romaji readings beside the same phrase are collapsed before ordering.\n"
            "- Language safety: Chinese and Japanese reference/material mismatches are detected and reported before model-assisted ordering wastes a run.\n"
            "- Material filename authority: short labeled material clips can override unreliable ASR hallucinations during matching.\n"
            "- Timeline diagnostics: exported analysis records reference units, material units, phonetic positions, aligned duration source, and target duration ratios for review."
        ),
    },
    "zh": {
        "app_title": "音频处理器",
        "language_menu": "语言：中文",
        "language_zh": "中文",
        "language_en": "英文",
        "add_files": "添加文件",
        "source_inputs": "输入素材",
        "original_audio": "原音频",
        "material_set": "素材集",
        "lyrics_file": "歌词文件",
        "manual_lyrics_enabled": "手动添加歌词文件",
        "select_original_audio": "选择原音频",
        "select_material_folder": "选择素材集文件夹",
        "select_lyrics_file": "选择歌词文件",
        "clear_material": "清除素材集",
        "clear_lyrics": "清除歌词",
        "supported_formats": "支持格式：{formats}",
        "audio_format_hint": ".wav、.mp3、.flac、.m4a、.ogg、.opus、.aac、.aiff、.alac、.wma",
        "material_format_hint": "仅支持文件夹",
        "lyrics_format_hint": ".txt、.doc、.docx、.lrc、.srt（勾选后使用）",
        "lyrics_optional_active": "歌词文件：未选择，将使用原音频 ASR 转写结果。",
        "lyrics_original_vocal_mode_active": "歌词模式：未手动添加歌词，将按原人声 ASR/对齐结果排序。",
        "material_directory": "素材集文件夹",
        "lyrics_path": "歌词文件",
        "remove_selected": "移除选中",
        "clear": "清空",
        "check_tools": "检查工具",
        "help": "帮助",
        "changelog": "更新日志",
        "help_title": "帮助",
        "changelog_title": "更新日志",
        "dialog_close": "关闭",
        "start_batch": "开始批量处理",
        "cancel": "取消",
        "input": "输入文件",
        "output": "输出文件",
        "status": "状态",
        "progress": "进度",
        "elapsed": "运行时长",
        "elapsed_value": "运行时长：{elapsed}",
        "output_directory": "输出目录",
        "browse": "浏览",
        "extension": "扩展名",
        "overwrite": "覆盖输出",
        "daw_timeline_export": "导出 DAW 时间轴工程",
        "split_reference_channels": "拆分原人声左右声道并分别输出单声道",
        "compute_device": "计算设备",
        "source_separation": "原音频人声",
        "source_separation_auto": "自动判断",
        "source_separation_never": "已是人声，跳过分离",
        "source_separation_always": "强制分离",
        "window_geometry": "窗口尺寸",
        "gain_db": "增益 dB",
        "normalize": "响度标准化",
        "highpass_hz": "高通 Hz",
        "lowpass_hz": "低通 Hz",
        "sample_rate": "采样率",
        "channels": "声道数",
        "codec": "编码器",
        "save_settings": "保存设置",
        "reload_settings": "重新加载",
        "log": "日志",
        "ready": "就绪",
        "processing": "处理中",
        "cancelling": "正在取消",
        "failed": "失败",
        "done": "完成",
        "queued": "排队中",
        "cancelled": "已取消",
        "complete": "完成",
        "empty_queue_title": "队列为空",
        "empty_queue_message": "请先添加一个或多个音频文件。",
        "invalid_material_directory": "素材集必须是已存在的文件夹：{path}",
        "missing_material_directory": "开始拼接前必须先选择素材集文件夹。",
        "empty_material_directory": "素材集中没有受支持的音频文件：{path}",
        "invalid_lyrics_file": "歌词必须是已存在且受支持的文件：{path}",
        "missing_lyrics_file_when_enabled": "已勾选手动添加歌词文件，请选择受支持的歌词/字幕文件，或取消勾选以按原人声识别排序。",
        "unsupported_lyrics_format": "不支持该歌词文件格式：{path}",
        "invalid_compute_device": "计算设备必须是 auto、cpu 或 cuda。",
        "invalid_source_separation": "原音频人声模式必须是 auto、always 或 never。",
        "tool_check_title": "工具检查",
        "tool_check_failed_title": "工具检查失败",
        "invalid_settings_title": "设置无效",
        "batch_failed_title": "批量处理失败",
        "audio_files": "音频文件",
        "all_files": "所有文件",
        "added_files": "已添加 {count} 个文件",
        "material_selected": "已选择素材集文件夹：{path}",
        "material_cleared": "已清除素材集文件夹",
        "lyrics_selected": "已选择歌词文件：{path}",
        "lyrics_cleared": "已清除歌词文件",
        "material_active": "素材集文件夹：{path}",
        "assembly_mode_active": "拼接模式：素材音频将按模型排序拼接，并整体拉伸或压缩到原音频时长。",
        "model_assisted_ordering_active": "模型辅助排序：拼接前使用本地预训练模型分析原音频和素材人声，不使用在线推理计费。",
        "material_cache_enabled": "素材分析缓存：素材集未变化时复用工作缓存中的分析结果。",
        "compute_device_active": "计算设备：{device}",
        "source_separation_active": "原音频人声分离：{mode}",
        "daw_timeline_mode_active": "DAW 时间轴导出：拉伸后的每个素材片段都会保留为可独立编辑的音频 item。",
        "lyrics_active": "歌词文件：{path}",
        "settings_saved": "设置已保存：{path}",
        "settings_reloaded": "设置已重新加载",
        "batch_started": "批量处理已开始",
        "cancel_requested": "已请求取消",
        "batch_failed_log": "批量处理失败：{error}",
        "summary": "完成：{completed} 个完成，{failed} 个失败，{cancelled} 个取消",
        "must_be_number": "{label} 必须是数字",
        "must_be_integer": "{label} 必须是整数",
        "status_processing_item": "处理中：{name}",
        "status_item": "{status}：{name}",
        "queue_complete": "{index}/{total}：完成",
        "queue_failed": "{index}/{total}：失败",
        "queue_progress": "{index}/{total}：{message}",
        "cancelled_before_processing": "处理前已取消",
        "processing_cancelled": "处理已取消",
        "no_queued_files": "没有排队文件",
        "help_body": (
            "VocalProcess 会使用素材集拼接新的声轨，同时尽量保留原人声的字音顺序和时间轴。\n\n"
            "1. 先选择一个或多个原人声音频，再选择素材集文件夹。素材最好是较短的字音或短词；文件名建议写清楚读音，例如 wo.wav、shi4.wav、a.wav、ai.wav 或假名文件名。\n\n"
            "2. 不勾选手动添加歌词文件时，软件以原人声 ASR/对齐后识别出的字音序列作为目标序列，再对素材进行匹配、排序和拉伸。\n\n"
            "3. 添加歌词文件时，歌词文本是选取字音的最高优先项；原人声 ASR/对齐结果作为时间轴来源。只要存在对齐到字音的时间，软件会把每个歌词字音映射到原人声的起点、终点和实际持续时长。\n\n"
            "4. 中文歌词和素材标签会转成拼音匹配。日文歌词可以写汉字、假名或罗马音；汉字/假名会先转为日文发音单元，再和素材文件名或素材转写结果匹配。相邻的假名/罗马音注音行、以及行内注音会先折叠，避免把同一句日文的不同写法当成重复歌词。\n\n"
            "5. 根据原音频选择人声分离模式，设置输出格式后开始批量处理；需要在 DAW 中继续编辑每个素材片段时，勾选导出 DAW 时间轴工程。"
        ),
        "changelog_body": (
            "最新开发变更：\n\n"
            "- 歌词优先排序：存在歌词文件时，以歌词文本决定目标字音；原人声对齐结果继续负责每个字音的时长。\n"
            "- 日文标准化：接入 Janome，把日文汉字/假名歌词转换为发音单元，用于匹配罗马音、假名文件名和素材转写。\n"
            "- 歌词注音清理：同一句日文旁边的假名/罗马音注释会在排序前折叠，不再当成重复目标。\n"
            "- 语言安全检查：中文/日文原人声与素材集明显错配时，会在模型辅助排序前提醒或报错，避免浪费测试轮次。\n"
            "- 素材文件名优先：短素材带有明确文件名标签时，可压过不可靠的 ASR 幻觉文本。\n"
            "- 时间轴诊断增强：analysis 中记录参考字音、素材字音、发音位置、对齐时长来源和目标时长比例，便于人工复查。"
        ),
    },
}


STATUS_KEYS = {
    "Queued": "queued",
    "Processing": "processing",
    "Done": "done",
    "Failed": "failed",
    "Cancelled": "cancelled",
}


MESSAGE_KEYS = {
    "Complete": "complete",
    "complete": "complete",
    "failed": "failed",
    "Processing cancelled": "processing_cancelled",
    "Cancelled before processing": "cancelled_before_processing",
    "No queued files": "no_queued_files",
}


def normalize_language(value: str | None) -> str:
    if value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


def translate(language: str, key: str, **values: object) -> str:
    normalized_language = normalize_language(language)
    template = TRANSLATIONS[normalized_language].get(key, TRANSLATIONS["en"].get(key, key))
    if values:
        return template.format(**values)
    return template


def translate_status(language: str, status: str) -> str:
    return translate(language, STATUS_KEYS.get(status, status))


def translate_message(language: str, message: str) -> str:
    return translate(language, MESSAGE_KEYS.get(message, message))

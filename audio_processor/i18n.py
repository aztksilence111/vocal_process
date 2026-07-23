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
        "remove_selected": "Remove Selected",
        "clear": "Clear",
        "check_tools": "Check Tools",
        "start_batch": "Start Batch",
        "cancel": "Cancel",
        "input": "Input",
        "output": "Output",
        "status": "Status",
        "progress": "Progress",
        "output_directory": "Output Directory",
        "browse": "Browse",
        "extension": "Extension",
        "overwrite": "Overwrite",
        "trim_start": "Trim Start",
        "duration": "Duration",
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
        "tool_check_title": "Tool Check",
        "tool_check_failed_title": "Tool Check Failed",
        "invalid_settings_title": "Invalid Settings",
        "batch_failed_title": "Batch Failed",
        "audio_files": "Audio files",
        "all_files": "All files",
        "added_files": "Added {count} file(s)",
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
    },
    "zh": {
        "app_title": "音频处理器",
        "language_menu": "语言：中文",
        "language_zh": "中文",
        "language_en": "英文",
        "add_files": "添加文件",
        "remove_selected": "移除选中",
        "clear": "清空",
        "check_tools": "检查工具",
        "start_batch": "开始批量处理",
        "cancel": "取消",
        "input": "输入文件",
        "output": "输出文件",
        "status": "状态",
        "progress": "进度",
        "output_directory": "输出目录",
        "browse": "浏览",
        "extension": "扩展名",
        "overwrite": "覆盖输出",
        "trim_start": "截取起点",
        "duration": "持续时间",
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
        "tool_check_title": "工具检查",
        "tool_check_failed_title": "工具检查失败",
        "invalid_settings_title": "设置无效",
        "batch_failed_title": "批量处理失败",
        "audio_files": "音频文件",
        "all_files": "所有文件",
        "added_files": "已添加 {count} 个文件",
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


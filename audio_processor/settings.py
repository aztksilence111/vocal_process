from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .engine import ProcessOptions
from .i18n import DEFAULT_LANGUAGE, normalize_language


APP_NAME = "AudioProcessorMVP"
COMPUTE_DEVICE_OPTIONS = ("auto", "cpu", "cuda")
SOURCE_SEPARATION_OPTIONS = ("auto", "always", "never")
WINDOW_GEOMETRY_OPTIONS = ("980x680", "1280x800", "1440x900", "1600x1000")


@dataclass(frozen=True)
class ProcessingSettings:
    language: str = DEFAULT_LANGUAGE
    material_directory: str = ""
    manual_lyrics_enabled: bool = False
    lyrics_file: str = ""
    split_reference_channels: bool = False
    daw_timeline_export: bool = False
    compute_device: str = "auto"
    source_separation: str = "auto"
    window_geometry: str = "980x680"
    output_directory: str = ""
    render_cache_directory: str = ""
    diagnostics_directory: str = ""
    output_extension: str = ".wav"
    overwrite: bool = True
    trim_start: str | None = None
    duration: str | None = None
    gain_db: float | None = None
    normalize: bool = False
    highpass_hz: float | None = None
    lowpass_hz: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None

    def output_path_for(self, input_path: Path) -> Path:
        if self.daw_timeline_export and self.material_directory:
            output_dir = Path(self.output_directory).expanduser() if self.output_directory else input_path.parent
            project_dir = output_dir / f"{input_path.stem}_daw"
            return project_dir / f"{input_path.stem}.rpp"

        extension = normalize_extension(self.output_extension, fallback=input_path.suffix)
        output_dir = Path(self.output_directory).expanduser() if self.output_directory else input_path.parent
        output_path = output_dir / f"{input_path.stem}{extension}"
        if output_path.resolve() == input_path.expanduser().resolve():
            return output_dir / f"{input_path.stem}_processed{extension}"
        return output_path

    def to_process_options(self, input_path: Path, output_path: Path | None = None) -> ProcessOptions:
        return ProcessOptions(
            input_path=input_path,
            output_path=output_path or self.output_path_for(input_path),
            overwrite=self.overwrite,
            trim_start=self.trim_start,
            duration=self.duration,
            gain_db=self.gain_db,
            normalize=self.normalize,
            highpass_hz=self.highpass_hz,
            lowpass_hz=self.lowpass_hz,
            sample_rate=self.sample_rate,
            channels=self.channels,
            codec=self.codec,
        )

    def effective_lyrics_file(self) -> str:
        return self.lyrics_file.strip() if self.manual_lyrics_enabled else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessingSettings:
        defaults = cls()
        return cls(
            language=normalize_language(optional_string(data.get("language"))),
            material_directory=str(data.get("material_directory") or defaults.material_directory),
            manual_lyrics_enabled=bool(data.get("manual_lyrics_enabled", bool(data.get("lyrics_file")))),
            lyrics_file=str(data.get("lyrics_file") or defaults.lyrics_file),
            split_reference_channels=bool(
                data.get("split_reference_channels", defaults.split_reference_channels)
            ),
            daw_timeline_export=bool(data.get("daw_timeline_export", defaults.daw_timeline_export)),
            compute_device=normalize_compute_device(data.get("compute_device")),
            source_separation=normalize_source_separation(data.get("source_separation")),
            window_geometry=normalize_window_geometry(data.get("window_geometry")),
            output_directory=str(data.get("output_directory") or defaults.output_directory),
            render_cache_directory=str(data.get("render_cache_directory") or defaults.render_cache_directory),
            diagnostics_directory=str(data.get("diagnostics_directory") or defaults.diagnostics_directory),
            output_extension=normalize_extension(
                str(data.get("output_extension") or defaults.output_extension),
                fallback=defaults.output_extension,
            ),
            overwrite=bool(data.get("overwrite", defaults.overwrite)),
            trim_start=optional_string(data.get("trim_start")),
            duration=optional_string(data.get("duration")),
            gain_db=optional_float(data.get("gain_db")),
            normalize=bool(data.get("normalize", defaults.normalize)),
            highpass_hz=optional_float(data.get("highpass_hz")),
            lowpass_hz=optional_float(data.get("lowpass_hz")),
            sample_rate=optional_int(data.get("sample_rate")),
            channels=optional_int(data.get("channels")),
            codec=optional_string(data.get("codec")),
        )


def normalize_extension(value: str, *, fallback: str = ".wav") -> str:
    extension = value.strip() if value else fallback
    if not extension:
        extension = fallback
    if not extension.startswith("."):
        extension = f".{extension}"
    return extension.lower()


def normalize_compute_device(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in COMPUTE_DEVICE_OPTIONS:
        return text
    return "auto"


def normalize_source_separation(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in SOURCE_SEPARATION_OPTIONS:
        return text
    return "auto"


def normalize_window_geometry(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "980x680"
    match = re.fullmatch(r"([1-9]\d{2,4})x([1-9]\d{2,4})", text)
    if not match:
        return "980x680"
    width = min(max(int(match.group(1)), 800), 3840)
    height = min(max(int(match.group(2)), 560), 2160)
    return f"{width}x{height}"


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_float(value: Any) -> float | None:
    text = optional_string(value)
    if text is None:
        return None
    return float(text)


def optional_int(value: Any) -> int | None:
    text = optional_string(value)
    if text is None:
        return None
    return int(text)


def get_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    return Path.home() / ".config" / "audio-processor-mvp"


def get_config_path() -> Path:
    return get_config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> ProcessingSettings:
    config_path = path or get_config_path()
    if not config_path.exists():
        return ProcessingSettings()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProcessingSettings()

    if not isinstance(data, dict):
        return ProcessingSettings()

    try:
        return ProcessingSettings.from_dict(data)
    except (TypeError, ValueError):
        return ProcessingSettings()


def save_settings(settings: ProcessingSettings, path: Path | None = None) -> Path:
    config_path = path or get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path

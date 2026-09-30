"""Versioned local preferences and portable, path-free settings exports."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from platformdirs import user_config_path, user_log_path

from .core import DEFAULT_BODY, StorageOptions

ACTION_LABELS = {
    "start": "开始收集", "stop": "结束收集", "pause": "暂停／恢复",
    "note": "个人感想", "undo": "撤销最近一项",
    "show": "显示／隐藏窗口", "capture_once": "单次收集",
}
WINDOWS_KEYS = {
    "start": "Ctrl+Alt+F9", "stop": "Ctrl+Alt+F10", "pause": "Ctrl+Alt+F8",
    "note": "Ctrl+Alt+F11", "undo": "Ctrl+Alt+F12", "show": "", "capture_once": "",
}
MAC_KEYS = {
    "start": "Cmd+Option+1", "stop": "Cmd+Option+2", "pause": "Cmd+Option+5",
    "note": "Cmd+Option+3", "undo": "Cmd+Option+4", "show": "", "capture_once": "",
}


def current_platform() -> str:
    return "macos" if sys.platform == "darwin" else "windows"


@dataclass
class Config:
    version: int = 1
    vault_path: str = ""
    recent_vaults: list[str] = field(default_factory=list)
    capture_directory: str = "00-inbox/待整理资料"
    group_by_month: bool = True
    timestamp_prefix: bool = True
    template_body: str = DEFAULT_BODY
    hotkeys: dict = field(default_factory=lambda: {
        "windows": dict(WINDOWS_KEYS), "macos": dict(MAC_KEYS)})
    always_on_top: bool = False
    show_on_start: bool = True
    launch_at_login: bool = False
    notifications: bool = True
    notification_ms: int = 1400
    open_on_stop: bool = True
    poll_ms: int = 100
    duplicate_ms: int = 2000
    consecutive_duplicates: bool = True
    ignored_apps: dict = field(default_factory=lambda: {
        "windows": ["Typeless.exe"], "macos": ["Typeless"]})
    keep_raw_html: bool = True
    machine_metadata: bool = True
    recent_count: int = 15

    def storage(self) -> StorageOptions:
        return StorageOptions(self.capture_directory, self.group_by_month,
                              self.timestamp_prefix, self.template_body)

    def clone(self) -> Config:
        return copy.deepcopy(self)

    def validate(self, check_path: bool = False) -> None:
        from .hotkeys import validate_bindings
        if self.version != 1:
            raise ValueError("不支持的配置版本，请使用对应版本的收集器。")
        base = Path(self.vault_path) if self.vault_path else Path.cwd()
        self.storage().capture_root(base)
        if check_path and (not self.vault_path or not base.is_dir()):
            raise ValueError("请选择存在的 Vault 或 Markdown 文件夹。")
        for name, low, high in (
            ("poll_ms", 50, 2000), ("duplicate_ms", 0, 10000),
            ("notification_ms", 500, 5000), ("recent_count", 3, 100),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须介于 {low} 和 {high} 之间。")
        for name in ("group_by_month", "timestamp_prefix", "always_on_top", "show_on_start",
                     "launch_at_login", "notifications", "open_on_stop",
                     "consecutive_duplicates", "keep_raw_html", "machine_metadata"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须是布尔值。")
        if not isinstance(self.template_body, str) or len(self.template_body) > 100_000:
            raise ValueError("模板正文过长或不是文字。")
        if "<!-- gs-" in self.template_body:
            raise ValueError("模板正文不能包含 gs- 机器标记；它们由收集器自动生成。")
        if not isinstance(self.recent_vaults, list) or not all(isinstance(x, str) for x in self.recent_vaults):
            raise ValueError("最近资料库列表格式无效。")
        for platform in ("windows", "macos"):
            if not isinstance(self.hotkeys, dict) or not isinstance(self.ignored_apps, dict):
                raise ValueError("平台设置应是对象。")
            validate_bindings(self.hotkeys.get(platform, {}), platform)
            apps = self.ignored_apps.get(platform)
            if not isinstance(apps, list) or not all(isinstance(x, str) for x in apps):
                raise ValueError("忽略程序必须是程序名／应用标识列表。")


def from_dict(data: dict) -> Config:
    if not isinstance(data, dict):
        raise ValueError("配置应当是 JSON 对象。")
    allowed = {f.name for f in fields(Config)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("无法识别的配置字段：" + "、".join(sorted(unknown)))
    result = Config(**data)
    result.validate()
    return result


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def config_directory() -> Path:
    override = os.environ.get("GSC_CONFIG_DIR")
    return Path(override) if override else user_config_path("GoldenStoneCollectorStandalone", appauthor=False)


def log_directory() -> Path:
    override = os.environ.get("GSC_CONFIG_DIR")
    return Path(override) / "logs" if override else user_log_path("GoldenStoneCollectorStandalone", appauthor=False)


class ConfigStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or config_directory()
        self.path = self.directory / "settings.json"

    def load(self) -> Config:
        if not self.path.is_file():
            return Config()
        return from_dict(json.loads(self.path.read_text(encoding="utf-8-sig")))

    def save(self, config: Config, backup: bool = False) -> None:
        config.validate()
        if backup and self.path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / f"settings.before-{stamp}.json").write_bytes(self.path.read_bytes())
        atomic_json(self.path, asdict(config))

    def export(self, path: Path, config: Config) -> None:
        data = asdict(config)
        for key in ("vault_path", "recent_vaults", "launch_at_login"):
            data.pop(key)
        atomic_json(path, {"format": "golden-stone-collector", "version": 1, "settings": data})

    def import_file(self, path: Path) -> Config:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if "启动快捷键" in data:
            return import_legacy(data)
        if data.get("format") != "golden-stone-collector" or data.get("version") != 1:
            raise ValueError("请选择本程序导出的配置，或旧版连续收集设置 JSON。")
        result = from_dict(data["settings"])
        result.vault_path = ""
        result.recent_vaults = []
        result.launch_at_login = False
        return result


def import_legacy(data: dict) -> Config:
    config = Config()
    key_map = {"启动快捷键": "start", "停止快捷键": "stop", "暂停恢复快捷键": "pause",
               "备注快捷键": "note", "撤销快捷键": "undo"}
    for key, action in key_map.items():
        if key in data:
            config.hotkeys["windows"][action] = data[key]
    settings_map = {
        "剪贴板轮询毫秒": "poll_ms", "技术性重复抑制毫秒": "duplicate_ms",
        "连续精确重复抑制": "consecutive_duplicates", "保留原始富文本": "keep_raw_html",
        "写入机器元数据": "machine_metadata", "最近资料卡数量": "recent_count",
        "收集提示显示毫秒": "notification_ms", "窗口置顶": "always_on_top",
        "停止后打开卡片": "open_on_stop",
    }
    for old, new in settings_map.items():
        if old in data:
            setattr(config, new, data[old])
    if "忽略剪贴板程序" in data:
        config.ignored_apps["windows"] = data["忽略剪贴板程序"]
    config.validate()
    return config

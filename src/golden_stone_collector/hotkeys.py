"""Canonical hotkeys and transactional native registration."""
from __future__ import annotations
from dataclasses import dataclass

MODIFIERS = ("Ctrl", "Alt", "Shift", "Win", "Cmd", "Option")
KEYS = {chr(i) for i in range(65, 91)} | {str(i) for i in range(10)}
KEYS |= {f"F{i}" for i in range(1, 13)} | {"Space", "Home", "End", "PageUp", "PageDown",
                                         "Insert", "Delete", "Up", "Down", "Left", "Right"}


@dataclass(frozen=True)
class Shortcut:
    modifiers: tuple[str, ...]
    key: str

    @property
    def text(self):
        return "+".join((*self.modifiers, self.key))


def parse_shortcut(text: str, platform: str) -> Shortcut:
    if not isinstance(text, str):
        raise ValueError("快捷键必须是文字。")
    raw = [part.strip() for part in text.split("+")]
    if len(raw) < 2 or any(not part for part in raw):
        raise ValueError("请使用修饰键加一个字母、数字、F1–F12 或导航键。")
    aliases = {s.lower(): s for s in (*MODIFIERS, *KEYS)}
    aliases.update({"control": "Ctrl", "command": "Cmd", "meta": "Cmd" if platform == "macos" else "Win"})
    try:
        parts = [aliases[p.lower()] for p in raw]
    except KeyError:
        raise ValueError(f"不支持的组合键：{text}") from None
    mods, key = parts[:-1], parts[-1]
    if platform == "macos":
        mods = ["Option" if m == "Alt" else m for m in mods]
        allowed = {"Cmd", "Ctrl", "Option", "Shift"}
        primary = {"Cmd", "Ctrl"}  # Avoid Option-only restrictions on recent macOS.
    else:
        allowed = {"Ctrl", "Alt", "Shift", "Win"}
        primary = {"Ctrl", "Alt", "Win"}
    if key not in KEYS or len(set(mods)) != len(mods) or not set(mods) <= allowed or not set(mods) & primary:
        raise ValueError("Windows 需要 Ctrl/Alt/Win，Mac 需要 ⌘/Control，再加一个主键。")
    return Shortcut(tuple(m for m in MODIFIERS if m in mods), key)


def validate_bindings(bindings: dict, platform: str) -> None:
    from .config import ACTION_LABELS
    if not isinstance(bindings, dict) or set(bindings) != set(ACTION_LABELS):
        raise ValueError("快捷键配置缺少动作或包含无法识别的动作。")
    seen = set()
    for action, text in bindings.items():
        if not isinstance(text, str):
            raise ValueError("快捷键必须是文字。")
        if not text:
            continue
        shortcut = parse_shortcut(text, platform).text
        if shortcut in seen:
            raise ValueError(f"快捷键重复：{shortcut}。每个动作必须使用不同组合键。")
        seen.add(shortcut)


class HotkeyManager:
    def __init__(self, backend, platform: str):
        self.backend = backend
        self.platform = platform
        self.bindings: dict[str, str] = {}
        self.registered: dict[str, str] = {}
        self.suspended = False

    def replace(self, desired: dict) -> None:
        validate_bindings(desired, self.platform)
        previous = dict(self.bindings)
        self.backend.clear()
        self.registered = {}
        try:
            for action, text in desired.items():
                if text:
                    self.backend.register(action, parse_shortcut(text, self.platform))
                    self.registered[action] = text
        except Exception as exc:
            self.backend.clear()
            self.registered = {}
            failed = []
            for action, text in previous.items():
                if text:
                    try:
                        self.backend.register(action, parse_shortcut(text, self.platform))
                        self.registered[action] = text
                    except Exception:
                        failed.append(action)
            self.bindings = previous
            details = f"；旧快捷键恢复失败：{', '.join(failed)}，请使用窗口按钮" if failed else "；已保留原配置"
            raise RuntimeError(str(exc) + details) from exc
        self.bindings = dict(desired)
        self.suspended = False

    def suspend(self):
        self.backend.clear()
        self.registered = {}
        self.suspended = True

    def resume(self):
        self.replace(self.bindings)

    def close(self):
        self.backend.clear()
        self.registered = {}
        self.backend.close()

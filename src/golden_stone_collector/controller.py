"""GUI-independent collection state machine. All card operations run serially."""
from __future__ import annotations
import logging
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer
from .config import Config, ConfigStore, current_platform
from .core import CaptureSession, ClipboardBusy, payload_fingerprint, should_suppress_exact_duplicate
from .services import set_startup

LOGGER = logging.getLogger(__name__)


class Controller(QObject):
    changed = Signal()
    saved = Signal(str)
    error = Signal(str)

    def __init__(self, config: Config, store: ConfigStore, clipboard, hotkeys):
        super().__init__()
        self.config, self.store = config, store
        self.clipboard, self.hotkeys = clipboard, hotkeys
        self.session = None
        self.paused = False
        self.modal_depth = 0
        self.last_sequence = None
        self.last_fingerprint = ""
        self.last_accepted_at = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(config.poll_ms)

    @property
    def active(self):
        return bool(self.session and self.session.active)

    def baseline(self):
        self.last_sequence = self.clipboard.sequence()

    def enter_dialog(self):
        self.modal_depth += 1

    def leave_dialog(self):
        self.modal_depth = max(0, self.modal_depth - 1)
        self.baseline()

    def start(self, card: Path | None = None):
        if self.active:
            raise RuntimeError("请先结束当前资料卡。")
        self.config.validate(check_path=True)
        kwargs = dict(keep_raw_html=self.config.keep_raw_html,
                      write_machine_metadata=self.config.machine_metadata, options=self.config.storage())
        root = Path(self.config.vault_path)
        self.baseline()  # Do not read content already on the clipboard.
        if card:
            self.session = CaptureSession.resume(root, card, **kwargs)
        else:
            session = CaptureSession(root, **kwargs)
            session.start()
            self.session = session
        self.last_fingerprint = ""
        self.last_accepted_at = 0.0
        self.paused = False
        self.changed.emit()

    def toggle_pause(self):
        if not self.active:
            raise RuntimeError("尚未开始收集。")
        self.baseline()
        self.paused = not self.paused
        self.changed.emit()

    def poll(self, manual=False):
        if not self.active or self.modal_depth or (self.paused and not manual):
            return
        try:
            sequence = self.clipboard.sequence()
            if sequence == self.last_sequence and not manual:
                return
            source = self.clipboard.source()
            ignored = self.config.ignored_apps[current_platform()]
            if not manual and source and any(name.casefold() == source.casefold() for name in ignored):
                self.last_sequence = sequence
                return
            payload = self.clipboard.read()
            foreground, title = self.clipboard.foreground()
            if self.clipboard.sequence() != sequence:
                return  # Clipboard changed mid-read; retry the new sequence on the next tick.
            if payload is None or (payload.kind in ("text","markdown","rich_text") and not str(payload.value).strip()):
                self.last_sequence = sequence
                if manual:
                    self.error.emit("当前剪贴板没有可收集的文字、图片或文件。")
                return
            fingerprint = payload_fingerprint(payload)
            now = time.monotonic()
            if not manual and should_suppress_exact_duplicate(fingerprint,self.last_fingerprint,now,
                    self.last_accepted_at,self.config.duplicate_ms / 1000,self.config.consecutive_duplicates):
                self.last_sequence = sequence
                return
            count = self.session.item_count
            self.session.add_payload(payload, source_app_hint=source, foreground_app_hint=foreground,
                                     foreground_window_title_hint=title,
                                     capture_method="manual" if manual else "automatic")
            self.last_sequence = sequence
            if self.session.item_count > count:
                self.last_fingerprint, self.last_accepted_at = fingerprint, now
                self.saved.emit(f"已保存 · 本轮 {self.session.item_count} 项")
                self.changed.emit()
        except ClipboardBusy:
            return  # Keep sequence pending while another application owns the clipboard lock.
        except Exception as exc:
            self.paused = True
            LOGGER.warning("capture paused after failure: %s", type(exc).__name__)
            self.error.emit("收集已暂停，未显示保存成功。\n" + str(exc))
            self.changed.emit()

    def add_note(self, value):
        if not self.active:
            raise RuntimeError("尚未开始收集。")
        self.session.add_note(value)
        self.baseline()
        self.saved.emit("个人感想已保存")
        self.changed.emit()

    def undo(self):
        if not self.active:
            raise RuntimeError("只能撤销当前这一轮收集的内容。")
        self.session.undo_last()
        self.last_fingerprint = ""
        self.baseline()
        self.saved.emit("已撤销本轮最近一项及其感想")
        self.changed.emit()

    def finish(self, title=""):
        if not self.active:
            raise RuntimeError("尚未开始收集。")
        if title.strip():
            self.session.rename(title)
        path = self.session.stop()
        self.paused = False
        self.changed.emit()
        return path

    def apply_config(self, desired: Config, backup=False):
        desired.validate(check_path=True)
        old = self.config
        if self.active and (desired.vault_path != old.vault_path or desired.storage() != old.storage()):
            raise ValueError("请先结束收集，再修改资料库、目录或模板。")
        desired.recent_vaults = list(dict.fromkeys([desired.vault_path, *desired.recent_vaults]))[:10]
        previous_bindings = dict(self.hotkeys.bindings)
        self.hotkeys.replace(desired.hotkeys[current_platform()])
        changed_startup = desired.launch_at_login != old.launch_at_login
        try:
            if changed_startup:
                set_startup(desired.launch_at_login)
            self.store.save(desired, backup=backup)
        except Exception:
            if changed_startup:
                try:
                    set_startup(old.launch_at_login)
                except Exception:
                    LOGGER.exception("startup rollback failed")
            if previous_bindings:
                self.hotkeys.replace(previous_bindings)
            else:
                self.hotkeys.backend.clear()
                self.hotkeys.bindings = {}
            raise
        self.config = desired
        self.timer.setInterval(desired.poll_ms)
        self.changed.emit()

    def recent_cards(self):
        if not self.config.vault_path:
            return []
        root = self.config.storage().capture_root(Path(self.config.vault_path))
        if not root.is_dir():
            return []
        candidates = []
        for path in root.rglob("*.md"):
            if "附件" in path.relative_to(root).parts or not path.resolve().is_relative_to(root):
                continue
            try:
                with path.open(encoding="utf-8") as file:
                    prefix = file.read(16384)
                if "<!-- gs-card " in prefix or ("状态:" in prefix and "**收集内容**" in prefix):
                    candidates.append((path.stat().st_mtime,path))
            except (OSError,UnicodeError):
                continue
        return [path for _, path in sorted(candidates,reverse=True)[:self.config.recent_count]]

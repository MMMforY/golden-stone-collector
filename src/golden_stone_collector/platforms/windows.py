"""Windows native clipboard and registered global hotkeys."""
from __future__ import annotations
import ctypes
import re
from ctypes import wintypes
from pathlib import Path
from PIL import Image, ImageGrab
from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication
from ..core import (ClipboardPayload, ClipboardBusy, decode_clipboard_bytes, parse_cf_html, html_to_markdown)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
CF_UNICODETEXT, CF_HDROP, CF_BITMAP, CF_DIB, CF_DIBV5 = 13, 15, 2, 8, 17
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardOwner.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT
CF_HTML = int(user32.RegisterClipboardFormatW("HTML Format"))
CF_MARKDOWN_FORMATS = tuple(int(user32.RegisterClipboardFormatW(fmt)) for fmt in ("text/markdown", "Markdown"))

def open_clipboard() -> None:
    for _ in range(8):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.025)
    raise ClipboardBusy("剪贴板正被其他程序占用")


def read_file_list() -> list[Path]:
    open_clipboard()
    try:
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths: list[Path] = []
        for index in range(count):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buffer, length + 1)
            paths.append(Path(buffer.value))
        return paths
    finally:
        user32.CloseClipboard()


def read_unicode_text() -> str:
    open_clipboard()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def read_global_bytes(format_id: int) -> bytes:
    open_clipboard()
    try:
        handle = user32.GetClipboardData(format_id)
        if not handle:
            return b""
        size = int(kernel32.GlobalSize(handle))
        if size < 1:
            return b""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return b""
        try:
            return ctypes.string_at(pointer, size).rstrip(b"\x00")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def window_process_name(hwnd: wintypes.HWND) -> str:
    if not hwnd:
        return ""
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def clipboard_owner_process_name() -> str:
    return window_process_name(user32.GetClipboardOwner())


def foreground_window_context() -> tuple[str, str]:
    """Return a best-effort app and title hint for the currently focused window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    process_name = window_process_name(hwnd)
    title_length = user32.GetWindowTextLengthW(hwnd)
    if title_length <= 0:
        return process_name, ""
    buffer = ctypes.create_unicode_buffer(title_length + 1)
    if not user32.GetWindowTextW(hwnd, buffer, len(buffer)):
        return process_name, ""
    title = re.sub(r"[\x00-\x1f]+", " ", buffer.value).strip()
    return process_name, title[:300]


class ClipboardReader:
    @staticmethod
    def sequence() -> int:
        return int(user32.GetClipboardSequenceNumber())

    @staticmethod
    def read() -> ClipboardPayload | None:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            files = read_file_list()
            return ClipboardPayload("files", files, raw_format="CF_HDROP") if files else None

        if any(user32.IsClipboardFormatAvailable(fmt) for fmt in (CF_DIBV5, CF_DIB, CF_BITMAP)):
            try:
                value = ImageGrab.grabclipboard()
            except OSError as exc:
                raise ClipboardBusy(str(exc)) from exc
            if isinstance(value, Image.Image):
                return ClipboardPayload("image", value.copy(), raw_format="image/png")
            if isinstance(value, list):
                return ClipboardPayload("files", [Path(item) for item in value], raw_format="CF_HDROP")

        for format_id in CF_MARKDOWN_FORMATS:
            if user32.IsClipboardFormatAvailable(format_id):
                markdown = decode_clipboard_bytes(read_global_bytes(format_id)).strip()
                if markdown:
                    return ClipboardPayload("markdown", markdown, raw_format="text/markdown")

        if CF_HTML and user32.IsClipboardFormatAvailable(CF_HTML):
            fragment, _full_html, source_url = parse_cf_html(read_global_bytes(CF_HTML))
            markdown = html_to_markdown(fragment)
            if markdown:
                return ClipboardPayload(
                    "rich_text",
                    markdown,
                    raw_html=fragment,
                    source_url=source_url,
                    raw_format="CF_HTML",
                )

        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            text = read_unicode_text()
            return ClipboardPayload("text", text, raw_format="CF_UNICODETEXT") if text else None
        return None


class WindowsClipboard(ClipboardReader):
    def source(self):
        return clipboard_owner_process_name()

    def foreground(self):
        return foreground_window_context()


class WindowsHotkeys(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.ids = {}
        self.next_id = 0x1000 + (id(self) % 0x5000)
        QCoreApplication.instance().installNativeEventFilter(self)

    def register(self, action, shortcut):
        modifiers = 0x4000  # MOD_NOREPEAT
        for modifier in shortcut.modifiers:
            modifiers |= {"Alt": 1, "Ctrl": 2, "Shift": 4, "Win": 8}[modifier]
        key = shortcut.key
        nav = {"Space": 0x20, "Home": 0x24, "End": 0x23, "PageUp": 0x21,
               "PageDown": 0x22, "Insert": 0x2D, "Delete": 0x2E, "Up": 0x26,
               "Down": 0x28, "Left": 0x25, "Right": 0x27}
        if len(key) == 1:
            code = ord(key)
        elif key.startswith("F") and key[1:].isdigit():
            code = 0x6F + int(key[1:])
        else:
            code = nav[key]
        self.next_id += 1
        if not user32.RegisterHotKey(None, self.next_id, modifiers, code):
            raise RuntimeError(f"快捷键 {shortcut.text} 无法注册，可能被旧版收集器或其他软件占用")
        self.ids[self.next_id] = action

    def clear(self):
        for key_id in list(self.ids):
            user32.UnregisterHotKey(None, key_id)
        self.ids.clear()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x0312 and msg.wParam in self.ids:
                self.callback(self.ids[msg.wParam])
                return True, 0
        return False, 0

    def close(self):
        self.clear()
        QCoreApplication.instance().removeNativeEventFilter(self)


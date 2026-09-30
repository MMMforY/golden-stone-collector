"""OS APIs are loaded only on their own platform."""
import sys

def clipboard_backend():
    if sys.platform == "win32":
        from .windows import WindowsClipboard
        return WindowsClipboard()
    if sys.platform == "darwin":
        from .macos import MacClipboard
        return MacClipboard()
    raise RuntimeError("首版支持 Windows 与 macOS。")

def hotkey_backend(callback):
    if sys.platform == "win32":
        from .windows import WindowsHotkeys
        return WindowsHotkeys(callback)
    if sys.platform == "darwin":
        from .macos import MacHotkeys
        return MacHotkeys(callback)
    raise RuntimeError("首版支持 Windows 与 macOS。")

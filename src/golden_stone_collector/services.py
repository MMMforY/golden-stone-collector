"""Explicit OS integration; no startup registration until the user enables it."""
from __future__ import annotations
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

STARTUP_NAME = "GoldenStoneCollectorStandalone"
LAUNCH_LABEL = "com.goldenstone.collector"


def open_card(root: Path, card: Path) -> bool:
    if (root / ".obsidian").is_dir():
        from urllib.parse import urlencode
        if QDesktopServices.openUrl(QUrl("obsidian://open?" + urlencode({"path": str(card.resolve())}))):
            return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(card.parent.resolve())))


def startup_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "golden_stone_collector"]


def set_startup(enabled: bool):
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(startup_command()))
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        path = Path.home() / "Library/LaunchAgents" / (LAUNCH_LABEL + ".plist")
        if not enabled:
            if path.exists():
                with path.open("rb") as handle:
                    if plistlib.load(handle).get("Label") != LAUNCH_LABEL:
                        raise RuntimeError("登录项不是本程序创建的，未移除。")
                path.unlink()
            return
        if "/AppTranslocation/" in sys.executable or "/Volumes/" in sys.executable:
            raise ValueError("请先将程序复制到本机 Applications 文件夹后再开启登录启动。")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd,"wb") as handle:
                plistlib.dump({"Label": LAUNCH_LABEL,"ProgramArguments": startup_command(), "RunAtLoad":True},handle)
            os.replace(temporary,path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        raise RuntimeError("首版仅支持 Windows 和 macOS。")

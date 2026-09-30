"""Deterministic installed-app check; never reads clipboard contents or actual notes."""
import json
import platform
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PIL import Image
from .core import CaptureSession
from .platforms import clipboard_backend,hotkey_backend
from .hotkeys import parse_shortcut
from . import __version__


def run(report: Path):
    app = QApplication.instance() or QApplication([])
    result={"version":__version__,"system":platform.system(),"machine":platform.machine(),"passed":False}
    try:
        with tempfile.TemporaryDirectory(prefix="golden-stone-package-test-") as folder:
            session=CaptureSession(Path(folder))
            session.start()
            session.add_text("合成资料；非个人笔记")
            session.add_image(Image.new("RGB",(12,12),"white"))
            session.add_note("合成感想")
            session.undo_last()
            session.rename("打包测试")
            path=session.stop()
            assert path.exists() and "合成资料" in path.read_text(encoding="utf8")
        clipboard=clipboard_backend()
        assert isinstance(clipboard.sequence(),int)
        backend=hotkey_backend(lambda _:None)
        try:
            os_key="macos" if platform.system()=="Darwin" else "windows"
            backend.register("show",parse_shortcut("Cmd+Option+Shift+9" if os_key=="macos" else "Ctrl+Alt+Shift+F9",os_key))
        finally:
            backend.close()
        from .theme import icon
        assert not icon().isNull()
        result["passed"]=True
        result["checks"]=["synthetic-card","image","note","undo","rename","native-clipboard-sequence","native-hotkey-register","icon"]
    except Exception as exc:
        result["error"]=str(exc)
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    return 0 if result["passed"] else 1

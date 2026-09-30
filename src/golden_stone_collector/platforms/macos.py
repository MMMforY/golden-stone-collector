"""Native macOS pasteboard polling and Carbon registered (not monitored) hotkeys."""
from __future__ import annotations
import ctypes as C
import io
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse
import AppKit
from PIL import Image
from ..core import ClipboardPayload, html_to_markdown


class MacClipboard:
    def __init__(self):
        self.board = AppKit.NSPasteboard.generalPasteboard()

    def sequence(self):
        return int(self.board.changeCount())

    def _check_permission(self):
        # These APIs exist only on newer macOS; 14 has no per-app pasteboard switch.
        denied = getattr(AppKit, "NSPasteboardAccessBehaviorAlwaysDeny", None)
        if denied is not None and self.board.respondsToSelector_("accessBehavior"):
            if self.board.accessBehavior() == denied:
                raise PermissionError("系统拒绝读取剪贴板。请在系统设置中允许本应用粘贴，再点击恢复。")

    def source(self):
        # This optional publisher-provided marker is NOT the frontmost application.
        return str(self.board.stringForType_("org.nspasteboard.source") or "")

    def foreground(self):
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return (str(app.bundleIdentifier() or app.localizedName() or ""), "") if app else ("", "")

    def read(self):
        self._check_permission()
        types = set(str(t) for t in (self.board.types() or []))
        files = []
        for item in self.board.pasteboardItems() or []:
            uri = item.stringForType_("public.file-url")
            if uri:
                parsed = urlparse(str(uri))
                if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                    files.append(Path(unquote(parsed.path)))
        if files:
            return ClipboardPayload("files", files, raw_format="public.file-url")
        for kind in ("public.png", "public.tiff"):
            if kind in types:
                data = self.board.dataForType_(kind)
                if data is not None:
                    with Image.open(io.BytesIO(bytes(data))) as source:
                        return ClipboardPayload("image", source.convert("RGBA"), raw_format=kind)
        for kind in ("net.daringfireball.markdown", "public.markdown", "text/markdown"):
            if kind in types:
                value = self.board.stringForType_(kind)
                if value:
                    return ClipboardPayload("markdown", str(value), raw_format=kind)
        if "public.html" in types:
            value = self.board.stringForType_("public.html")
            if value:
                raw = str(value)
                plain = html_to_markdown(raw)
                if plain.strip():
                    return ClipboardPayload("rich_text", plain, raw_html=raw, raw_format="public.html")
        for kind in ("public.utf8-plain-text", "public.url"):
            if kind in types:
                value = self.board.stringForType_(kind)
                if value and str(value).strip():
                    return ClipboardPayload("text", str(value), raw_format=kind)
        self._check_permission()
        supported = {"public.png", "public.tiff", "public.file-url", "public.html", "public.utf8-plain-text", "public.url"}
        if types & supported:
            # Failed advertised reads can be permissions or an unresponsive clipboard provider.
            raise PermissionError("剪贴板内容暂时不可读，可能是粘贴权限被拒绝或来源应用未提供数据。允许粘贴后点击恢复，并重新复制。")
        return None


def fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


class EventType(C.Structure):
    _fields_ = [("eventClass", C.c_uint32), ("eventKind", C.c_uint32)]


class HotkeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


KEY_CODES = {"A":0,"S":1,"D":2,"F":3,"H":4,"G":5,"Z":6,"X":7,"C":8,"V":9,
             "B":11,"Q":12,"W":13,"E":14,"R":15,"Y":16,"T":17,
             "1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"9":25,"7":26,"8":28,"0":29,
             "O":31,"U":32,"I":34,"P":35,"L":37,"J":38,"K":40,"N":45,"M":46}
KEY_CODES.update({"Space":49, "Home":115, "End":119, "PageUp":116, "PageDown":121,
                  "Insert":114, "Delete":117, "Left":123,"Right":124,"Down":125,"Up":126})
KEY_CODES.update(dict(zip([f"F{i}" for i in range(1,13)], [122,120,99,118,96,97,98,100,101,109,103,111])))


class MacHotkeys:
    def __init__(self, callback):
        self.callback = callback
        self.lib = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self.ids, self.refs, self.down = {}, {}, set()
        self.next_id = 1
        self.signature = fourcc("GSC1")
        lib = self.lib
        lib.GetApplicationEventTarget.restype = C.c_void_p
        lib.RegisterEventHotKey.argtypes = [C.c_uint32,C.c_uint32,HotkeyID,C.c_void_p,C.c_uint32,C.POINTER(C.c_void_p)]
        lib.RegisterEventHotKey.restype = C.c_int32
        lib.UnregisterEventHotKey.argtypes = [C.c_void_p]
        lib.GetEventParameter.argtypes = [C.c_void_p,C.c_uint32,C.c_uint32,C.c_void_p,C.c_uint32,C.c_void_p,C.c_void_p]
        lib.GetEventParameter.restype = C.c_int32
        lib.GetEventKind.argtypes = [C.c_void_p]
        lib.GetEventKind.restype = C.c_uint32
        self.handler_type = C.CFUNCTYPE(C.c_int32,C.c_void_p,C.c_void_p,C.c_void_p)
        self.handler_callback = self.handler_type(self._event)
        lib.InstallEventHandler.argtypes = [C.c_void_p,self.handler_type,C.c_uint32,C.POINTER(EventType),C.c_void_p,C.POINTER(C.c_void_p)]
        lib.InstallEventHandler.restype = C.c_int32
        lib.RemoveEventHandler.argtypes = [C.c_void_p]
        self.handler = C.c_void_p()
        kinds = (EventType * 2)(EventType(fourcc("keyb"),5), EventType(fourcc("keyb"),6))
        status = lib.InstallEventHandler(lib.GetApplicationEventTarget(),self.handler_callback,2,kinds,None,C.byref(self.handler))
        if status:
            raise RuntimeError(f"无法安装 Mac 热键处理器（{status}）。")

    def _event(self, _next, event, _data):
        try:
            identifier = HotkeyID()
            result = self.lib.GetEventParameter(event,fourcc("----"),fourcc("hkid"),None,C.sizeof(identifier),None,C.byref(identifier))
            if result or identifier.signature != self.signature or identifier.id not in self.ids:
                return -9874
            if self.lib.GetEventKind(event) == 6:
                self.down.discard(identifier.id)
            elif identifier.id not in self.down:
                self.down.add(identifier.id)
                self.callback(self.ids[identifier.id])
            return 0
        except Exception:
            logging.getLogger(__name__).exception("Mac hotkey event failed")
            return -9874

    def register(self, action, shortcut):
        modifiers = sum({"Cmd":256,"Shift":512,"Option":2048,"Ctrl":4096}[m] for m in shortcut.modifiers)
        self.next_id += 1
        reference = C.c_void_p()
        result = self.lib.RegisterEventHotKey(KEY_CODES[shortcut.key],modifiers,HotkeyID(self.signature,self.next_id),self.lib.GetApplicationEventTarget(),0,C.byref(reference))
        if result:
            raise RuntimeError(f"{shortcut.text} 注册失败（{result}），请更换组合键或检查系统快捷键。")
        self.refs[self.next_id], self.ids[self.next_id] = reference, action

    def clear(self):
        for ref in self.refs.values():
            self.lib.UnregisterEventHotKey(ref)
        self.refs.clear()
        self.ids.clear()
        self.down.clear()

    def close(self):
        self.clear()
        if self.handler:
            self.lib.RemoveEventHandler(self.handler)
            self.handler = None

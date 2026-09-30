import pytest
from golden_stone_collector.config import Config,ConfigStore,current_platform
from golden_stone_collector.controller import Controller
from golden_stone_collector.hotkeys import HotkeyManager
from golden_stone_collector.core import ClipboardPayload


class FakeClipboard:
    count = 1
    payload = ClipboardPayload("text","旧剪贴板")
    owner = ""
    def sequence(self): return self.count
    def source(self): return self.owner
    def foreground(self): return ("foreground-only","")
    def read(self): return self.payload
    def put(self, text, owner=""):
        self.count += 1
        self.payload = ClipboardPayload("text",text)
        self.owner = owner


class FakeKeys:
    def __init__(self):
        self.registered = {}
        self.fail = ""
    def register(self, action, shortcut):
        if shortcut.text == self.fail:
            raise RuntimeError("模拟占用")
        self.registered[action] = shortcut.text
    def clear(self): self.registered.clear()
    def close(self): self.clear()


@pytest.fixture
def controller(qapp,tmp_path):
    config = Config(vault_path=str(tmp_path))
    clipboard = FakeClipboard()
    manager = HotkeyManager(FakeKeys(),current_platform())
    manager.replace(config.hotkeys[current_platform()])
    c = Controller(config,ConfigStore(tmp_path / "config"),clipboard,manager)
    c.timer.stop()
    yield c
    c.timer.stop()
    manager.close()

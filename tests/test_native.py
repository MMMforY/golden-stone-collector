"""Registration can be tested without sending keys or changing the real clipboard."""
import sys
import pytest
from golden_stone_collector.platforms import clipboard_backend,hotkey_backend
from golden_stone_collector.hotkeys import parse_shortcut


def test_native_sequence_and_hotkey_conflict(qapp):
    if sys.platform not in ("win32","darwin"):
        pytest.skip("desktop OS required")
    clipboard=clipboard_backend()
    assert isinstance(clipboard.sequence(),int)
    first=hotkey_backend(lambda action:None)
    second=hotkey_backend(lambda action:None)
    platform="windows" if sys.platform=="win32" else "macos"
    key=parse_shortcut("Ctrl+Alt+Shift+F7" if platform=="windows" else "Cmd+Option+Shift+7",platform)
    try:
        first.register("show",key)
        with pytest.raises(RuntimeError): second.register("show",key)
    finally:
        first.close()
        second.close()

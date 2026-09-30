from PySide6.QtCore import Qt
from golden_stone_collector.ui import MainWindow
from golden_stone_collector.settings_ui import SettingsDialog,ShortcutEdit


def test_window_and_settings(qtbot,controller):
    window=MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.isVisible)
    window.dispatch("start")
    assert controller.active
    controller.clipboard.put("合成界面测试")
    controller.poll()
    assert "1 项" in window.status.text()
    dialog=SettingsDialog(controller,window)
    qtbot.addWidget(dialog)
    assert not dialog.root.isEnabled()
    assert dialog.keys["start"].isEnabled()
    dialog.reject()
    controller.finish()
    window.showMinimized()
    assert window.isMinimized()
    window.reveal()
    qtbot.waitUntil(lambda:not window.isMinimized())
    window.close()
    assert not window.quitting
    window.tray.hide()
    window.toast.close()
    window.quitting=True
    window.close()


def test_key_recorder(qtbot):
    edit=ShortcutEdit("")
    qtbot.addWidget(edit)
    edit.show()
    qtbot.keyClick(edit,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier)
    assert "A" in edit.text()
    qtbot.keyClick(edit,Qt.Key.Key_Backspace)
    assert edit.text()==""

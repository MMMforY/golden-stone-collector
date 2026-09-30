from __future__ import annotations
import argparse
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from PySide6.QtCore import QTimer, QLockFile, QObject, Signal, QEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox
from . import __version__
from .config import Config, ConfigStore, current_platform, log_directory
from .controller import Controller
from .hotkeys import HotkeyManager
from .platforms import clipboard_backend, hotkey_backend
from .theme import STYLESHEET, icon
from .ui import MainWindow


class Dispatcher(QObject):
    action = Signal(str)


class DockReopen(QObject):
    def __init__(self, window):
        super().__init__()
        self.window = window

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ApplicationActivate and not self.window.isVisible():
            self.window.reveal()
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version",action="version",version=__version__)
    parser.add_argument("--config-dir",type=Path)
    parser.add_argument("--smoke-test",type=Path,help="isolated synthetic package test; output report path")
    args = parser.parse_args()
    if args.config_dir:
        os.environ["GSC_CONFIG_DIR"] = str(args.config_dir.resolve())
    if args.smoke_test:
        from .smoke import run
        return run(args.smoke_test)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Golden Stone 独立收集器")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("GoldenStone")
    app.setWindowIcon(icon())
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLESHEET)
    store = ConfigStore()
    store.directory.mkdir(parents=True,exist_ok=True)
    identity = hashlib.sha256(str(store.directory.resolve()).encode()).hexdigest()[:24]
    server_name = "golden-stone-" + identity
    lock = QLockFile(str(store.directory / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(1000):
            socket.write(b"show")
            socket.waitForBytesWritten(1000)
        else:
            QMessageBox.warning(None,"程序已经运行","请从托盘／菜单栏打开已运行的收集器。")
        return 0
    logs = log_directory()
    logs.mkdir(parents=True,exist_ok=True)
    handler = RotatingFileHandler(logs / "collector.log",maxBytes=1048576,backupCount=2,encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger("golden_stone_collector").addHandler(handler)
    logging.getLogger("golden_stone_collector").setLevel(logging.INFO)
    errors = []
    try:
        config = store.load()
    except Exception as exc:
        config = Config()
        errors.append("无法读取设置。原文件未覆盖；请检查或重新导入。\n" + str(exc))
    dispatcher = Dispatcher()
    try:
        hotkeys = HotkeyManager(hotkey_backend(dispatcher.action.emit),current_platform())
        clipboard = clipboard_backend()
    except Exception as exc:
        QMessageBox.critical(None,"系统接口初始化失败",str(exc))
        return 1
    try:
        hotkeys.replace(config.hotkeys[current_platform()])
    except Exception as exc:
        errors.append(str(exc))
    controller = Controller(config,store,clipboard,hotkeys)
    window = MainWindow(controller)
    dispatcher.action.connect(window.dispatch)
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    server.listen(server_name)
    def awaken():
        connection = server.nextPendingConnection()
        if connection:
            connection.close()
            connection.deleteLater()
        window.reveal()
    server.newConnection.connect(awaken)
    dock = DockReopen(window)
    if sys.platform == "darwin":
        app.installEventFilter(dock)
    if config.show_on_start or not config.vault_path or errors or not window.tray.isSystemTrayAvailable():
        window.show()
    if errors:
        QTimer.singleShot(200,lambda:window.show_error("\n\n".join(errors)))
    elif not config.vault_path:
        QTimer.singleShot(150,window.settings)
    app.aboutToQuit.connect(controller.timer.stop)
    app.aboutToQuit.connect(hotkeys.close)
    app.aboutToQuit.connect(lock.unlock)
    return app.exec()

from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QListWidget,QListWidgetItem,QInputDialog,QMessageBox,QDialog,
    QPlainTextEdit,QDialogButtonBox,QSystemTrayIcon,QMenu,QFileDialog)
from .config import ACTION_LABELS, current_platform
from .services import open_card
from .settings_ui import SettingsDialog
from .theme import icon


class Toast(QLabel):
    def __init__(self):
        super().__init__(None,Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background:#100F0F;color:#FFFCF0;padding:16px 24px;border-radius:8px;font-size:15px;")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def notify(self, text, duration):
        self.setText(text)
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right()-self.width()-28,screen.bottom()-self.height()-24)
        self.show()
        self.timer.start(duration)


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.quitting = False
        self.setWindowTitle("Golden Stone · 独立连续收集器")
        self.setWindowIcon(icon())
        self.resize(840,620)
        self.setMinimumSize(680,520)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28,24,28,22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("Golden Stone")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        settings = QPushButton("设置")
        settings.clicked.connect(self.settings)
        header.addWidget(settings)
        layout.addLayout(header)
        subtitle = QLabel("连续保存此刻看到的、想到的。整理与解读，留到以后。")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        self.status = QLabel()
        self.status.setObjectName("status")
        layout.addWidget(self.status)
        self.card_label = QLabel()
        self.card_label.setWordWrap(True)
        self.card_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.card_label)
        self.buttons = {}
        for group in (("start","pause","stop"),("capture_once","note","undo")):
            row = QHBoxLayout()
            for action in group:
                button = QPushButton(ACTION_LABELS[action])
                if action == "start":
                    button.setObjectName("primary")
                button.clicked.connect(lambda _checked=False,a=action:self.dispatch(a))
                self.buttons[action] = button
                row.addWidget(button)
            layout.addLayout(row)
        self.hotkey_status = QLabel()
        self.hotkey_status.setObjectName("muted")
        self.hotkey_status.setWordWrap(True)
        layout.addWidget(self.hotkey_status)
        recent_header = QHBoxLayout()
        recent_header.addWidget(QLabel("最近资料卡 · 双击打开"))
        recent_header.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_recent)
        recent_header.addWidget(refresh)
        layout.addLayout(recent_header)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda _:self.open_selected())
        layout.addWidget(self.recent,1)
        row = QHBoxLayout()
        for text,slot in (("打开",self.open_selected),("续写选中卡片",self.resume_selected),
                          ("统一重命名",self.rename_selected),("选择旧卡…",self.choose_card),("所在文件夹",self.open_folder)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        footer = QLabel("最小化保留任务栏／Dock；关闭窗口留在托盘／菜单栏。右键菜单选择「退出」才结束程序。")
        footer.setWordWrap(True)
        footer.setObjectName("muted")
        layout.addWidget(footer)
        self.setCentralWidget(body)
        self.toast = Toast()
        self.tray = QSystemTrayIcon(icon(),self)
        menu = QMenu()
        for text,slot in (("显示窗口",self.reveal),("开始收集",lambda:self.dispatch("start")),
                          ("暂停／恢复",lambda:self.dispatch("pause")),("结束收集",lambda:self.dispatch("stop")),
                          ("个人感想",lambda:self.dispatch("note")),("撤销最近一项",lambda:self.dispatch("undo")),
                          ("设置",self.settings),("退出",self.quit_app)):
            menu.addAction(text,slot)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(lambda reason:self.reveal() if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()
        self.c.changed.connect(self.refresh)
        self.c.saved.connect(self.notify)
        self.c.error.connect(self.show_error)
        self.refresh()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def dialog(self, callback):
        self.c.enter_dialog()
        try:
            return callback()
        finally:
            self.c.leave_dialog()

    def show_error(self, text):
        self.reveal()
        self.dialog(lambda: QMessageBox.warning(self,"需要处理",text))

    def safe(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.show_error(str(exc))
            self.refresh()

    def notify(self, text):
        if self.c.config.notifications:
            self.toast.notify(text,self.c.config.notification_ms)

    def refresh(self):
        top = self.c.config.always_on_top
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != top:
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,top)
            if visible:
                self.show()
        active = self.c.active
        if active:
            self.status.setText(("已暂停" if self.c.paused else "正在收集") + f" · 本轮 {self.c.session.item_count} 项")
        else:
            self.status.setText("准备开始" if self.c.config.vault_path else "欢迎 · 请先在设置中选择保存位置")
        path = self.c.session.card_path if self.c.session else None
        self.card_label.setText(str(path) if path else (self.c.config.vault_path or "可选择 Obsidian Vault，也可以只使用普通文件夹。"))
        self.buttons["start"].setEnabled(not active)
        for action in ("stop","pause","capture_once","note","undo"):
            self.buttons[action].setEnabled(active)
        self.buttons["pause"].setText("恢复收集" if self.c.paused else "暂停收集")
        self.tray.setToolTip("Golden Stone · " + self.status.text())
        actual = self.c.hotkeys.registered
        self.hotkey_status.setText("热键已注册：" + "  ·  ".join(f"{ACTION_LABELS[a]} {key}" for a,key in actual.items() if key) if actual and any(actual.values()) else "没有已注册的全局快捷键，可使用按钮或到设置中更改。")
        for action,button in self.buttons.items():
            button.setToolTip(actual.get(action,"") or "未绑定快捷键")
        self.refresh_recent()

    def refresh_recent(self):
        selected = self.selected_path()
        self.recent.clear()
        try:
            for path in self.c.recent_cards():
                item = QListWidgetItem(path.stem + "\n" + path.parent.name)
                item.setData(Qt.ItemDataRole.UserRole,str(path))
                item.setToolTip(str(path))
                self.recent.addItem(item)
                if path == selected:
                    self.recent.setCurrentItem(item)
        except OSError:
            self.status.setText("保存目录暂时不可用，请在设置中检查。")

    def selected_path(self):
        item = self.recent.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def target_path(self):
        return self.selected_path() or (self.c.session.card_path if self.c.session else None)

    def open_selected(self):
        path = self.target_path()
        if path:
            self.safe(lambda:open_card(Path(self.c.config.vault_path),path))

    def open_folder(self):
        path = self.target_path()
        directory = path.parent if path else Path(self.c.config.vault_path)
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def resume_selected(self):
        path = self.selected_path()
        if path:
            self.safe(lambda:self.c.start(path))

    def choose_card(self):
        filename,_ = self.dialog(lambda:QFileDialog.getOpenFileName(self,"选择原有资料卡",self.c.config.vault_path,"Markdown (*.md)"))
        if filename:
            self.safe(lambda:self.c.start(Path(filename)))

    def rename_selected(self):
        from .core import CaptureSession
        path = self.target_path()
        if not path:
            return
        title,okay = self.dialog(lambda:QInputDialog.getText(self,"统一命名","文件夹与笔记将一起命名：",text=path.stem))
        if okay and title.strip():
            def rename():
                if self.c.session and self.c.session.card_path == path:
                    session = self.c.session
                else:
                    session = CaptureSession.resume(Path(self.c.config.vault_path),path,options=self.c.config.storage())
                session.rename(title)
                self.refresh()
            self.safe(rename)

    def dispatch(self, action):
        if self.c.modal_depth:
            return
        if action == "show":
            self.hide() if self.isVisible() and not self.isMinimized() else self.reveal()
        elif action == "start":
            if not self.c.config.vault_path:
                self.settings()
            else:
                self.safe(self.c.start)
        elif action == "pause":
            self.safe(self.c.toggle_pause)
        elif action == "capture_once":
            self.c.poll(manual=True)
        elif action == "undo":
            self.safe(self.c.undo)
        elif action == "note":
            self.note()
        elif action == "stop":
            self.stop()

    def stop(self):
        if not self.c.active:
            return
        self.reveal()
        title,okay = self.dialog(lambda:QInputDialog.getText(self,"结束并命名","输入标题；文件夹与笔记一起命名。取消则继续收集：",text=self.c.session.card_path.stem))
        if okay:
            def finish():
                path = self.c.finish(title)
                if self.c.config.open_on_stop:
                    open_card(Path(self.c.config.vault_path),path)
            self.safe(finish)

    def note(self):
        if not self.c.active or not self.c.session.entries:
            self.show_error("请先收集一项内容，再添加个人感想。")
            return
        self.reveal()
        dialog = QDialog(self)
        dialog.setWindowTitle("个人感想 · 接在最近一项之后")
        dialog.resize(540,330)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setPlaceholderText("写下当时的想法。再次添加时会接续同一段感想。")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存感想（Ctrl／⌘＋Enter）")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"),dialog)
        shortcut.activated.connect(dialog.accept)
        if self.dialog(dialog.exec) == QDialog.DialogCode.Accepted and text.toPlainText().strip():
            self.safe(lambda:self.c.add_note(text.toPlainText()))

    def settings(self):
        if self.c.modal_depth:
            return
        self.reveal()
        self.c.hotkeys.suspend()
        try:
            self.dialog(lambda:SettingsDialog(self.c,self).exec())
        finally:
            if self.c.hotkeys.suspended:
                if self.c.hotkeys.bindings:
                    self.safe(self.c.hotkeys.resume)
                else:
                    self.c.hotkeys.suspended = False
            self.refresh()

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        else:
            event.ignore()
            if self.tray.isSystemTrayAvailable():
                self.hide()
            else:
                self.showMinimized()

    def quit_app(self):
        if self.c.modal_depth:
            return
        if self.c.active:
            answer = self.dialog(lambda:QMessageBox.question(self,"退出收集器","结束当前收集并退出？已保存的资料不会删除。"))
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.c.finish()
            except Exception as exc:
                self.show_error(str(exc))
                return
        self.quitting = True
        self.c.timer.stop()
        self.c.hotkeys.close()
        self.toast.close()
        self.tray.hide()
        QApplication.quit()

"""Chinese settings editor. Global registrations are suspended during key recording."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,QWidget,
    QLabel,QPushButton,QLineEdit,QComboBox,QCheckBox,QSpinBox,QPlainTextEdit,QTextBrowser,
    QDialogButtonBox,QFileDialog,QMessageBox,QGroupBox)
from .config import ACTION_LABELS, WINDOWS_KEYS, MAC_KEYS, current_platform
from .core import DEFAULT_BODY, render_template
from .hotkeys import parse_shortcut


class ShortcutEdit(QLineEdit):
    def __init__(self, value, parent=None):
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setPlaceholderText("点击后按组合键；退格清空")
        self.setToolTip("仅支持一组组合键。Mac 的 Fn／媒体键不作为独立快捷键。")

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Control,Qt.Key.Key_Shift,Qt.Key.Key_Alt,Qt.Key.Key_Meta):
            return
        if key in (Qt.Key.Key_Backspace,Qt.Key.Key_Delete) and mods == Qt.KeyboardModifier.NoModifier:
            self.clear()
            return
        if key == Qt.Key.Key_Tab and mods == Qt.KeyboardModifier.NoModifier:
            return super().keyPressEvent(event)
        labels = []
        # Qt maps physical Command to ControlModifier on macOS and physical Control to MetaModifier.
        mac = sys.platform == "darwin"
        for flag, label in ((Qt.KeyboardModifier.ControlModifier,"Cmd" if mac else "Ctrl"),
                            (Qt.KeyboardModifier.AltModifier,"Option" if mac else "Alt"),
                            (Qt.KeyboardModifier.ShiftModifier,"Shift"),
                            (Qt.KeyboardModifier.MetaModifier,"Ctrl" if mac else "Win")):
            if mods & flag:
                labels.append(label)
        nav = {Qt.Key.Key_PageUp:"PageUp",Qt.Key.Key_PageDown:"PageDown",Qt.Key.Key_Space:"Space"}
        main = nav.get(key, QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText))
        try:
            self.setText(parse_shortcut("+".join([*labels,main]),current_platform()).text)
            self.setStyleSheet("")
        except ValueError as exc:
            self.setToolTip(str(exc))
            self.setStyleSheet("border: 1px solid #AF3029;")
        event.accept()


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.draft = controller.config.clone()
        self.imported = False
        self.setWindowTitle("设置 · Golden Stone 独立收集器")
        self.resize(750,690)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.fields = {}
        self.storage_widgets = []
        self.path_tab()
        self.key_tab()
        self.template_tab()
        self.general_tab()
        self.message = QLabel("设置保存在本机，不写入资料库。保存后生效。")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.import_button = QPushButton("导入配置…")
        self.import_button.clicked.connect(self.import_config)
        export = QPushButton("导出配置…")
        export.clicked.connect(self.export_config)
        row.addWidget(self.import_button)
        row.addWidget(export)
        row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并生效")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        layout.addLayout(row)
        self.load_values()
        if controller.active:
            self.message.setText("当前正在收集：可以改快捷键和界面；请结束收集后再改目录、模板或导入配置。")
            for widget in self.storage_widgets:
                widget.setEnabled(False)
            self.import_button.setEnabled(False)

    def tab_form(self, name):
        tab = QWidget()
        form = QFormLayout(tab)
        form.setSpacing(14)
        self.tabs.addTab(tab,name)
        return tab, form

    def path_tab(self):
        tab, form = self.tab_form("保存位置")
        intro = QLabel("选择现有 Obsidian Vault 根目录，或任意 Markdown 文件夹。\n不会修改原有笔记，也不会自动执行 AI 入库。")
        intro.setWordWrap(True)
        form.addRow(intro)
        path_row = QWidget()
        row = QHBoxLayout(path_row)
        row.setContentsMargins(0,0,0,0)
        self.root = QComboBox()
        self.root.setEditable(True)
        browse = QPushButton("选择…")
        browse.clicked.connect(self.choose_root)
        row.addWidget(self.root,1)
        row.addWidget(browse)
        form.addRow("资料库／文件夹",path_row)
        self.relative = QLineEdit()
        form.addRow("库内收集目录",self.relative)
        self.grouping = QCheckBox("按年／月分组")
        self.prefix = QCheckBox("文件夹使用日期＋时分前缀（不含秒）")
        form.addRow(self.grouping)
        form.addRow(self.prefix)
        self.path_preview = QLabel()
        self.path_preview.setWordWrap(True)
        form.addRow("示例",self.path_preview)
        self.root.currentTextChanged.connect(self.update_path)
        self.relative.textChanged.connect(self.update_path)
        self.grouping.toggled.connect(self.update_path)
        self.prefix.toggled.connect(self.update_path)
        self.storage_widgets.extend([path_row,self.relative,self.grouping,self.prefix])

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self,"选择资料库根目录",self.root.currentText())
        if path:
            self.root.setCurrentText(path)

    def update_path(self):
        now = datetime.now()
        grouping = now.strftime("/%Y/%m") if self.grouping.isChecked() else ""
        prefix = now.strftime("%Y-%m-%d %H%M - ") if self.prefix.isChecked() else ""
        self.path_preview.setText(f"{self.root.currentText() or '[本机资料库]'}/{self.relative.text()}{grouping}/{prefix}资料标题/资料标题.md")

    def key_tab(self):
        _, form = self.tab_form("快捷键")
        form.addRow(QLabel("点击输入框后直接按键；每项可清空。保存时会实际向系统注册。"))
        self.keys = {}
        for action, label in ACTION_LABELS.items():
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0,0,0,0)
            edit = ShortcutEdit("")
            edit.setAccessibleName(label + "快捷键")
            clear = QPushButton("清空")
            clear.clicked.connect(edit.clear)
            row.addWidget(edit,1)
            row.addWidget(clear)
            self.keys[action] = edit
            form.addRow(label,row_widget)
        reset = QPushButton("恢复当前平台默认快捷键")
        reset.clicked.connect(self.reset_keys)
        form.addRow(reset)
        note = QLabel("录入期间暂时释放本程序热键；取消会恢复。\n若旧版收集器占用了默认键，请退出旧版，或给独立版选择另一组键。\n注册失败将保留原设置，不会静默忽略冲突。")
        note.setWordWrap(True)
        form.addRow(note)

    def reset_keys(self):
        values = MAC_KEYS if current_platform() == "macos" else WINDOWS_KEYS
        for action, edit in self.keys.items():
            edit.setText(values[action])

    def template_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("只编辑正文。五项属性和收集标记自动生成；标题只来自文件名。"))
        self.template = QPlainTextEdit()
        self.template.setMinimumHeight(155)
        layout.addWidget(self.template)
        row = QHBoxLayout()
        for title, slot in (("导入正文模板…",self.import_template),("恢复默认正文",lambda:self.template.setPlainText(DEFAULT_BODY)),("预览",self.preview_template)):
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        layout.addWidget(self.preview,1)
        self.tabs.addTab(tab,"模板")
        self.storage_widgets.append(tab)

    def import_template(self):
        filename,_ = QFileDialog.getOpenFileName(self,"导入正文模板", "", "Markdown (*.md);;文字 (*.txt)")
        if not filename:
            return
        try:
            body = Path(filename).read_text(encoding="utf-8-sig")
            if body.startswith("---\n"):
                raise ValueError("请选择仅含正文的模板。五项属性和 gs 标记由程序生成，不应在模板里重复。")
            if "<!-- gs-" in body or len(body) > 100000:
                raise ValueError("模板含机器标记或内容过长。")
            self.template.setPlainText(body)
            self.preview_template()
        except Exception as exc:
            QMessageBox.warning(self,"无法导入",str(exc))

    def preview_template(self):
        self.preview.setMarkdown(render_template(self.template.toPlainText(),"2026-01-01 12:00","preview"))

    def checkbox(self, form, label, name):
        checkbox = QCheckBox(label)
        self.fields[name] = checkbox
        form.addRow(checkbox)

    def spin(self, form, label, name, low, high, suffix=""):
        spin = QSpinBox()
        spin.setRange(low,high)
        spin.setSuffix(suffix)
        self.fields[name] = spin
        form.addRow(label,spin)

    def general_tab(self):
        _, form = self.tab_form("界面与高级")
        for label,name in (("窗口置顶","always_on_top"),("启动时显示窗口","show_on_start"),
                           ("开启无声保存提示","notifications"),("结束后打开资料卡","open_on_stop"),
                           ("登录电脑时自动启动（默认关闭）","launch_at_login")):
            self.checkbox(form,label,name)
        self.spin(form,"提示持续时间","notification_ms",500,5000," ms")
        advanced_toggle = QPushButton("▸ 展开高级选项")
        form.addRow(advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0,0,0,0)
        self.ignore = QLineEdit()
        advanced.addRow("忽略程序（逗号分隔）",self.ignore)
        hint = QLabel("Mac 仅在剪贴板提供来源标识时忽略；前台应用不等于写入者。\n语音输入干扰时，暂停或改用单次收集。")
        hint.setWordWrap(True)
        advanced.addRow(hint)
        self.spin(advanced,"技术重复抑制","duplicate_ms",0,10000," ms")
        self.checkbox(advanced,"抑制连续完全相同的内容","consecutive_duplicates")
        self.checkbox(advanced,"保留原始富文本旁证","keep_raw_html")
        self.checkbox(advanced,"写入机器来源元数据","machine_metadata")
        self.spin(advanced,"最近资料卡数量","recent_count",3,100)
        self.spin(advanced,"剪贴板轮询间隔","poll_ms",50,2000," ms")
        self.advanced.hide()
        form.addRow(self.advanced)
        def toggle():
            visible = not self.advanced.isVisible()
            self.advanced.setVisible(visible)
            advanced_toggle.setText("▾ 收起高级选项" if visible else "▸ 展开高级选项")
        advanced_toggle.clicked.connect(toggle)

    def load_values(self):
        config = self.draft
        self.root.clear()
        self.root.addItems(config.recent_vaults)
        self.root.setCurrentText(config.vault_path)
        self.relative.setText(config.capture_directory)
        self.grouping.setChecked(config.group_by_month)
        self.prefix.setChecked(config.timestamp_prefix)
        self.template.setPlainText(config.template_body)
        for action, edit in self.keys.items():
            edit.setText(config.hotkeys[current_platform()][action])
        for name, widget in self.fields.items():
            if isinstance(widget,QCheckBox):
                widget.setChecked(getattr(config,name))
            else:
                widget.setValue(getattr(config,name))
        self.ignore.setText(", ".join(config.ignored_apps[current_platform()]))
        self.preview_template()

    def values(self):
        config = self.draft.clone()
        config.vault_path = self.root.currentText().strip()
        config.capture_directory = self.relative.text().strip()
        config.group_by_month = self.grouping.isChecked()
        config.timestamp_prefix = self.prefix.isChecked()
        config.template_body = self.template.toPlainText()
        config.hotkeys[current_platform()] = {a:e.text() for a,e in self.keys.items()}
        config.ignored_apps[current_platform()] = [x.strip() for x in self.ignore.text().replace("，",",").split(",") if x.strip()]
        for name, widget in self.fields.items():
            setattr(config,name,widget.isChecked() if isinstance(widget,QCheckBox) else widget.value())
        return config

    def import_config(self):
        filename,_ = QFileDialog.getOpenFileName(self,"导入独立版或旧版 JSON 配置","","JSON (*.json)")
        if not filename:
            return
        try:
            self.draft = self.controller.store.import_file(Path(filename))
            self.imported = True
            self.load_values()
            self.tabs.setCurrentIndex(0)
            self.message.setText("已读入配置，尚未应用。请重新选择本机资料库；保存时会备份原配置。开机启动保持关闭。")
        except Exception as exc:
            QMessageBox.warning(self,"导入失败",str(exc))

    def export_config(self):
        filename,_ = QFileDialog.getSaveFileName(self,"导出可迁移配置","collector-config.json","JSON (*.json)")
        if filename:
            try:
                config = self.values()
                config.validate()
                self.controller.store.export(Path(filename),config)
                self.message.setText("已导出。文件不包含本机资料库路径、最近目录或开机启动设置。")
            except Exception as exc:
                QMessageBox.warning(self,"导出失败",str(exc))

    def save(self):
        try:
            self.controller.apply_config(self.values(),backup=self.imported)
        except Exception as exc:
            self.message.setText("保存失败：" + str(exc))
            self.message.setStyleSheet("color: #AF3029;")
            return
        self.accept()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QCheckBox, QPushButton,
                               QLabel, QLineEdit, QProgressBar, QTextEdit, QButtonGroup,
                               QRadioButton, QMessageBox, QWidget)
from PySide6.QtCore import Qt, QThread, Signal

# ================== 自动检测方向所需繁体字符集（常用繁体字）==================
TRADITIONAL_CHARS = set('為著畫會學體萬關開關車馬龍風雲電個後麼麵幹鬱龜憂龍範齊灑歲曆歸獻環羅門裡貝鳥麥黃點黨萬時將從動愛臺灣準當說種還書長鬥')  # 示例

def is_traditional(text):
    """简单判断文本是否主要为繁体（前100字中繁体比例高）"""
    if not text:
        return False
    total = 0
    trad_count = 0
    for ch in text[:100]:
        if '\u4e00' <= ch <= '\u9fff':  # 基本汉字
            total += 1
            if ch in TRADITIONAL_CHARS:
                trad_count += 1
    if total == 0:
        return False
    return (trad_count / total) > 0.5

# ================== 例外词保护/恢复 ==================
def protect_exceptions(text, exceptions):
    """
    将例外词替换为占位符，返回 (新文本, 映射字典)
    exceptions: list of strings (原始例外词，保持原样不转换)
    """
    placeholder_map = {}
    # 按长度降序排序，避免短词匹配干扰长词
    exceptions_sorted = sorted(exceptions, key=len, reverse=True)
    for i, ex in enumerate(exceptions_sorted):
        placeholder = f"__PROTECTED_{i}__"
        # 转义正则特殊字符
        escaped = re.escape(ex)
        # 注意：例外词本身可能包含繁简混合，但用户输入的是什么就保护什么
        # 使用全词匹配？不，直接普通替换
        text = text.replace(ex, placeholder)
        placeholder_map[placeholder] = ex
    return text, placeholder_map

def restore_protected(text, placeholder_map):
    """将占位符还原为原始例外词"""
    for placeholder, original in placeholder_map.items():
        text = text.replace(placeholder, original)
    return text

# ================== 后台转换线程 ==================
class ConvertThread(QThread):
    progress = Signal(int, str)  # value, filename
    finished = Signal(int)        # 0=success, -1=error
    log = Signal(str)

    def __init__(self, bk, files, direction, exceptions, opencc_module):
        super().__init__()
        self.bk = bk
        self.files = files          # list of (manifest_id, href)
        self.direction = direction  # 't2s' or 's2t'
        self.exceptions = exceptions
        self.opencc = opencc_module

    def run(self):
        try:
            # 初始化 OpenCC 转换器
            if self.direction == 't2s':
                cc = self.opencc.OpenCC('t2s')
            else:
                cc = self.opencc.OpenCC('s2t')

            total = len(self.files)
            for idx, (file_id, href) in enumerate(self.files):
                # 读取内容：bk.readfile() 返回 str（已解码）或 bytes
                raw = self.bk.readfile(file_id)
                if isinstance(raw, bytes):
                    try:
                        content = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        self.log.emit(f"跳过非UTF-8文件: {href}")
                        self.progress.emit(int((idx+1)/total*100), href)
                        continue
                else:
                    content = raw   # 已经是 str

                # 1. 保护例外词
                protected_content, placeholder_map = protect_exceptions(content, self.exceptions)

                # 2. OpenCC 转换
                converted = cc.convert(protected_content)

                # 3. 还原例外词
                restored = restore_protected(converted, placeholder_map)

                # 4. 写回
                self.bk.writefile(file_id, restored.encode('utf-8'))
                self.log.emit(f"已转换: {href}")
                self.progress.emit(int((idx+1)/total*100), href)

            self.finished.emit(0)
        except Exception as e:
            self.log.emit(f"错误: {str(e)}")
            self.finished.emit(-1)

# ================== 主 GUI 对话框 ==================
class MainDialog(QDialog):
    def __init__(self, bk, plugin_dir, parent=None):
        super().__init__(parent)
        self.bk = bk
        self.plugin_dir = plugin_dir
        self.setWindowTitle("繁简转换插件 t2s")
        self.resize(600, 500)

        # 布局
        main_layout = QHBoxLayout(self)

        # 左侧：文件列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("选择要转换的 XHTML/HTML 文件:"))
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.NoSelection)
        left_layout.addWidget(self.file_list)

        # 全选/反选按钮
        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.deselect_all_btn = QPushButton("反选")
        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.deselect_all_btn)
        left_layout.addLayout(btn_layout)

        # 右侧：选项
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # 方向选择
        right_layout.addWidget(QLabel("转换方向:"))
        self.dir_group = QButtonGroup(self)
        self.radio_t2s = QRadioButton("繁体 → 简体")
        self.radio_s2t = QRadioButton("简体 → 繁体")
        self.dir_group.addButton(self.radio_t2s)
        self.dir_group.addButton(self.radio_s2t)
        right_layout.addWidget(self.radio_t2s)
        right_layout.addWidget(self.radio_s2t)

        # 例外词
        right_layout.addWidget(QLabel("例外词（不转换，逗号分隔）:"))
        self.exceptions_edit = QLineEdit()
        self.exceptions_edit.setPlaceholderText("例如: 司馬,臺北,不知所措")
        right_layout.addWidget(self.exceptions_edit)

        # 进度条
        right_layout.addWidget(QLabel("转换进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        right_layout.addWidget(self.progress_bar)

        # 日志显示
        right_layout.addWidget(QLabel("日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        right_layout.addWidget(self.log_text)

        # 确定取消按钮
        button_box = QHBoxLayout()
        self.ok_btn = QPushButton("开始转换")
        self.cancel_btn = QPushButton("取消")
        button_box.addWidget(self.ok_btn)
        button_box.addWidget(self.cancel_btn)
        right_layout.addLayout(button_box)

        # 组装左右
        main_layout.addWidget(left_widget, 2)
        main_layout.addWidget(right_widget, 1)

        # 连接信号
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        self.ok_btn.clicked.connect(self.start_conversion)
        self.cancel_btn.clicked.connect(self.reject)

        # 加载文件列表并默认全选
        self.load_files()
        self.auto_detect_direction()
        self.select_all()

    def load_files(self):
        """从 bk 中获取所有 xhtml/html 文件，添加到列表"""
        self.file_items = []  # 存储 (manifest_id, href, checkbox)
        for file_id, href in self.bk.text_iter():
            item = QListWidgetItem(href)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.file_list.addItem(item)
            self.file_items.append((file_id, href, item))

    def select_all(self):
        for _, _, item in self.file_items:
            item.setCheckState(Qt.Checked)

    def deselect_all(self):
        for _, _, item in self.file_items:
            item.setCheckState(Qt.Unchecked)

    def auto_detect_direction(self):
        """读取第一个 xhtml 文件的前 100 个字符，判断繁简"""
        # 获取第一个选中的文件（还未选择，直接取第一个文件）
        if not self.file_items:
            return
        file_id = self.file_items[0][0]
        raw = self.bk.readfile(file_id)
        try:
            content = raw.decode('utf-8')[:200]  # 多读一点
            # 去除标签，只提取文本
            text = re.sub(r'<[^>]+>', '', content)
            if is_traditional(text):
                self.radio_t2s.setChecked(True)
            else:
                self.radio_s2t.setChecked(True)
        except:
            # 出错则默认繁转简
            self.radio_t2s.setChecked(True)

    def start_conversion(self):
        # 获取选中的文件
        selected = []
        for file_id, href, item in self.file_items:
            if item.checkState() == Qt.Checked:
                selected.append((file_id, href))
        if not selected:
            QMessageBox.warning(self, "警告", "没有选中任何文件。")
            return

        # 获取方向
        direction = 't2s' if self.radio_t2s.isChecked() else 's2t'

        # 解析例外词
        exceptions_text = self.exceptions_edit.text().strip()
        exceptions = [x.strip() for x in exceptions_text.split(',') if x.strip()]

        # 禁用界面按钮
        self.ok_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)

        # 导入 opencc
        try:
            sys.path.insert(0, self.plugin_dir)
            import opencc
        except ImportError:
            QMessageBox.critical(self, "错误", "无法导入 opencc 模块。请确保已将 opencc-python-reimplemented 文件夹复制到插件目录下，并命名为 'opencc'。")
            self.ok_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            return

        # 启动线程
        self.thread = ConvertThread(self.bk, selected, direction, exceptions, opencc)
        self.thread.progress.connect(self.update_progress)
        self.thread.log.connect(self.append_log)
        self.thread.finished.connect(self.conversion_finished)
        self.thread.start()

    def update_progress(self, value, filename):
        self.progress_bar.setValue(value)
        self.append_log(f"进度: {value}% - {filename}")

    def append_log(self, msg):
        self.log_text.append(msg)
        # 滚动到底部
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def conversion_finished(self, code):
        self.ok_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        if code == 0:
            QMessageBox.information(self, "完成", "转换已完成。")
            self.accept()
        else:
            QMessageBox.critical(self, "错误", "转换过程中出现错误，请查看日志。")

# ================== 插件主入口 ==================
def run(bk):
    # 获取插件目录
    plugin_dir = bk._w.plugin_dir
    # 创建 Qt 应用
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    dialog = MainDialog(bk, plugin_dir)
    result = dialog.exec()
    if result == QDialog.Accepted:
        return 0
    else:
        return -1
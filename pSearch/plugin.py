#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
import traceback
from lxml import etree
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTextEdit,
                               QMessageBox, QProgressBar)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QClipboard, QColor, QCursor, QPalette

# ------------------------------------------------------------
# 辅助函数：截取上下文
# ------------------------------------------------------------
def truncate_context(text, limit=250):
    if len(text) <= 2 * limit:
        return text
    return text[:limit] + " … " + text[-limit:]

# ------------------------------------------------------------
# 安全提取元素文本（兼容无 text_content 方法的环境）
# ------------------------------------------------------------
def get_element_text(elem):
    """返回元素的纯文本内容，等价于 elem.text_content()"""
    if hasattr(elem, 'text_content'):
        return elem.text_content()
    # fallback: 使用 itertext
    return ''.join(elem.itertext())

# ------------------------------------------------------------
# 从 root 中提取页面标题（<title> 或 <h1>）
# ------------------------------------------------------------
def extract_page_title(root, max_len=20):
    """返回页面标题字符串，优先 title，其次 h1，否则返回 '无标题'"""
    title_elem = root.xpath('//title')
    if title_elem:
        title = get_element_text(title_elem[0]).strip()
        if title:
            if len(title) > max_len:
                title = title[:max_len] + "..."
            return title
    h1_elem = root.xpath('//h1')
    if h1_elem:
        h1 = get_element_text(h1_elem[0]).replace('\r',' ').replace('\n',' ').strip()
        # print('title_h1',repr(h1))
        if h1:
            if len(h1) > max_len:
                h1 = h1[:max_len] + "..."
            return h1
    return "无标题"

# ------------------------------------------------------------
# 从 OPF metadata 中提取 dc:title（书名）
# ------------------------------------------------------------
def get_epub_title(bk):
    """返回 EPUB 书名，若失败返回 '未知书名'"""
    try:
        metadata_xml = bk.getmetadataxml()
        # 使用正则快速提取 dc:title
        match = re.search(r'<dc:title[^>]*>([^<]+)</dc:title>', metadata_xml)
        if match:
            return match.group(1).strip()
        # 兼容无命名空间写法
        match = re.search(r'<title[^>]*>([^<]+)</title>', metadata_xml)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return "未知书名"

# ------------------------------------------------------------
# 搜索函数（返回两个字符串：gui显示版、复制版）
# ------------------------------------------------------------
def search_in_epub(bk, keyword, status_callback=None):
    """
    执行搜索，返回 (gui_output, copy_output) 两个字符串
    status_callback 用于更新进度状态
    """
    keyword = keyword.strip().lower()
    if not keyword:
        return "【错误】关键词不能为空！", ""

    # 获取书名
    book_title = get_epub_title(bk)

    # 准备存储：每个文件的匹配信息
    # file_info 结构: { 'filename': str, 'page_title': str, 'matches': [(paragraph_text,), ...] }
    files_matches = []
    total_matches = 0

    text_files = list(bk.text_iter())
    total_files = len(text_files)

    for idx, (manifest_id, href) in enumerate(text_files, 1):
        if status_callback:
            status_callback(f"正在处理: {os.path.basename(href)} ({idx}/{total_files})")

        try:
            content = bk.readfile(manifest_id)
        except Exception as e:
            print(f"读取失败 {href}: {e}")
            continue

        # 安全解析 HTML
        try:
            parser = etree.HTMLParser(encoding='utf-8', recover=True)
            root = etree.fromstring(content.encode('utf-8'), parser=parser)
        except Exception:
            try:
                root = etree.HTML(content.encode('utf-8'))
            except Exception:
                print(f"解析失败（跳过）: {href}")
                continue

        if root is None:
            continue

        # 提取页面标题
        page_title = extract_page_title(root)

        paragraphs = root.xpath('.//p')
        if not paragraphs:
            continue

        filename = os.path.basename(href)
        file_matches_text = []

        for p in paragraphs:
            text = get_element_text(p).strip()
            if not text:
                continue
            if keyword in text.lower():
                total_matches += 1
                shown_text = truncate_context(text)
                shown_text = shown_text.replace('\n', ' ').replace('\r', '')
                file_matches_text.append(shown_text)

        if file_matches_text:
            files_matches.append({
                'filename': filename,
                'page_title': page_title,
                'matches': file_matches_text
            })

    # 构建输出字符串（GUI版 和 复制版）
    gui_lines = []
    copy_lines = []

    # 顶部信息（共用）
    gui_lines.append("---\n")
    gui_lines.append(f"book: {book_title}\n")
    gui_lines.append(f"搜索关键词：「{keyword}」\n")
    gui_lines.append("---\n")
    gui_lines.append("\n")  # 空行

    copy_lines.extend(gui_lines)  # 复制版开头相同

    # 总结果统计（仅GUI显示）
    gui_lines.append(f"✅ 总共找到 {total_matches} 个匹配段落\n")
    gui_lines.append("\n")
    # 复制版不添加此行

    for fi in files_matches:
        count = len(fi['matches'])
        # 标题行：### 页面标题 (文件名) (X result)  —— GUI 版带 result 计数
        gui_lines.append(f"### {fi['page_title']} ({fi['filename']}) ({count} result)")
        # 复制版：### 页面标题 (文件名)
        # copy_lines.append(f"### {fi['page_title']} ({fi['filename']})")
        copy_lines.append(f"### {fi['page_title']}")
        gui_lines.append('\n\n')
        copy_lines.append('\n\n')

        # 段落内容（两个版本相同）
        for para in fi['matches']:
            gui_lines.append(f"  {para}")
            copy_lines.append(f"  {para}")

            gui_lines.append('\n\n')
            copy_lines.append('\n\n')
        # gui_lines.append("")
        # copy_lines.append("")

    if total_matches == 0:
        gui_lines.append("❌ 未找到包含关键词的段落。")
        copy_lines.append("未找到包含关键词的段落。")

    gui_output = "".join(gui_lines)
    copy_output = "".join(copy_lines)
    return gui_output, copy_output

# ------------------------------------------------------------
# 主对话框 GUI
# ------------------------------------------------------------
class SearchDialog(QDialog):
    def __init__(self, bk, parent=None):
        super().__init__(parent)
        self.bk = bk
        self.setWindowTitle("段落搜索插件")
        self.resize(850, 650)

        # 控件
        self.label_info = QLabel(
            "📖 插件功能：在当前 EPUB 中搜索指定关键词，提取所有 <p> 段落\n"
            "   • 段落超过500字符时自动截取前后各250字符\n"
            "   • 显示每个页面的标题（优先 <title>，其次 <h1>）\n"
            "   • 结果按文件分组，GUI 显示匹配数量，复制时自动移除统计信息"
        )
        self.label_info.setWordWrap(True)
        self.label_info.setStyleSheet("background-color: #f0f0f0; padding: 8px; border-radius: 4px;")

        self.label_keyword = QLabel("关键词：")
        self.edit_keyword = QLineEdit()
        self.edit_keyword.setPlaceholderText("输入要搜索的词，例如：米饭")
        self.btn_search = QPushButton("🔍 开始搜索")
        self.btn_copy = QPushButton("📋 复制结果")
        self.btn_close = QPushButton("✖ 关闭")
        self.text_result = QTextEdit()
        self.set_color(self.text_result)

        self.text_result.setReadOnly(True)
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        # 布局
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.label_keyword)
        top_layout.addWidget(self.edit_keyword)
        top_layout.addWidget(self.btn_search)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.label_info)
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.text_result)
        main_layout.addWidget(self.progress)

        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.btn_copy)
        bottom_layout.addWidget(self.btn_close)
        main_layout.addLayout(bottom_layout)

        self.setLayout(main_layout)

        # 信号连接
        self.btn_search.clicked.connect(self.start_search)
        self.btn_copy.clicked.connect(self.copy_results)
        self.btn_close.clicked.connect(self.accept)

        # 存储当前搜索结果（复制专用）
        self.current_copy_text = ""

        self._restore_timer = None

    def set_color(self,text_area:QTextEdit):
        # 获取当前调色板
        palette = text_area.palette()
        # 使用 QColor 定义颜色，这里选择一个浅蓝色
        highlight_color = QColor(200, 220, 255) # 浅蓝色 （参考某网页 204，226，255）
        # QPalette.Highlight 角色用于设置选中项的背景颜色
        palette.setColor(QPalette.ColorRole.Highlight, highlight_color)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        text_area.setPalette(palette)

    def start_search(self):
        keyword = self.edit_keyword.text()
        if not keyword.strip():
            QMessageBox.warning(self, "警告", "请输入搜索关键词！")
            return

        self.btn_search.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.text_result.clear()
        self.current_copy_text = ""

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))

        try:
            def update_status(msg):
                print(msg)

            gui_output, copy_output = search_in_epub(self.bk, keyword, status_callback=update_status)
            self.text_result.setPlainText(gui_output)
            self.current_copy_text = copy_output
        except Exception as e:
            error_detail = traceback.format_exc()
            QMessageBox.critical(self, "搜索错误",
                                 f"执行搜索时发生异常：\n{str(e)}\n\n详细信息：\n{error_detail}")
            self.text_result.setPlainText(f"❌ 搜索失败：{str(e)}")
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.setVisible(False)
            self.btn_search.setEnabled(True)
            self.btn_copy.setEnabled(True)

    def copy_results(self):
        if not self.current_copy_text:
            QMessageBox.information(self, "提示", "没有可复制的结果。请先执行搜索。")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self.current_copy_text)
        # QMessageBox.information(self, "复制成功", "结果已复制到剪贴板（不含统计计数）。")
        self.btn_copy.setText("✅ 已复制")
        # 600 毫秒后恢复原文本（可根据需要调整时长）
        self._restore_timer = QTimer.singleShot(600, lambda: self.btn_copy.setText("📋 复制结果"))

# ------------------------------------------------------------
# 插件入口
# ------------------------------------------------------------
def run(bk):
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = SearchDialog(bk)
    dialog.exec_()
    return 0
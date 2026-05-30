#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
import io
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse
from hashlib import md5
from PIL import Image
from lxml import etree, html as lxml_html
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QDoubleSpinBox, QPushButton, QProgressDialog,
    QCheckBox, QMessageBox, QWidget
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage

# ----------------------------------------------------------------------
# 工作线程：下载图片
# ----------------------------------------------------------------------
class DownloadWorker(QThread):
    finished_single = Signal(str, bytes, str, bool, str)
    progress = Signal(int, int)
    log = Signal(str)

    def __init__(self, url_list, delay_sec):
        super().__init__()
        self.url_list = url_list   # [(original_url,), ...]
        self.delay_sec = delay_sec
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.url_list)
        for idx, (url,) in enumerate(self.url_list):
            if self._is_cancelled:
                break
            self.progress.emit(idx, total)
            self.log.emit(f"Downloading: {url}")
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                if not data:
                    raise Exception("Empty response")

                ext = self._get_extension(data)
                if not ext:
                    raise Exception("Cannot identify image format")
                self.finished_single.emit(url, data, ext, True, "")
                self.log.emit(f"Success: {url} -> .{ext}")
            except Exception as e:
                self.finished_single.emit(url, b'', '', False, str(e))
                self.log.emit(f"Failed: {url} - {e}")

            if idx < total - 1 and not self._is_cancelled:
                time.sleep(self.delay_sec)

        self.progress.emit(total, total)
        self.log.emit("Download worker finished.")

    def _get_extension(self, data):
        try:
            img = Image.open(io.BytesIO(data))
            fmt = img.format
            if fmt:
                return fmt.lower()
            return None
        except Exception:
            return None

# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class OfflineImagesDialog(QDialog):
    def __init__(self, bk):
        super().__init__()
        self.bk = bk
        self.all_urls = []               # [(url,)]
        self.selected_urls = set()
        self.url_to_bookpath = {}
        self.download_worker = None
        self.progress_dialog = None
        self.changes_made = False        # 标记是否有修改

        # 偏好设置
        try:
            prefs = bk.getPrefs()
            self.delay_sec = float(prefs.get('delay', 0.5))
        except:
            self.delay_sec = 0.5

        self.init_ui()
        self.refresh_url_list()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def init_ui(self):
        self.setWindowTitle("OfflineImages - Download External Images")
        self.resize(700, 500)
        layout = QVBoxLayout(self)

        self.list_label = QLabel("<b>External image URLs found:</b>")
        layout.addWidget(self.list_label)

        self.url_list_widget = QListWidget()
        self.url_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.url_list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.url_list_widget)

        opt_layout = QHBoxLayout()
        self.single_mode_cb = QCheckBox("Only process selected URL (test mode)")
        opt_layout.addWidget(self.single_mode_cb)
        opt_layout.addStretch()
        opt_layout.addWidget(QLabel("Delay (seconds):"))
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 10.0)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setValue(self.delay_sec)
        self.delay_spin.setSuffix(" s")
        opt_layout.addWidget(self.delay_spin)
        layout.addLayout(opt_layout)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh List")
        self.refresh_btn.clicked.connect(self.refresh_url_list)
        self.start_btn = QPushButton("Start Download & Replace")
        self.start_btn.clicked.connect(self.start_processing)
        self.exit_btn = QPushButton("Exit & Save Changes")   # 关键修改
        self.exit_btn.clicked.connect(self.accept_and_close)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.exit_btn)
        layout.addLayout(btn_layout)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # 扫描外链
    # ------------------------------------------------------------------
    def refresh_url_list(self):
        self.url_list_widget.clear()
        self.all_urls = []
        url_set = set()
        pattern = r'src=["\'](https?://[^"\']+)["\']'

        for text_id, _ in self.bk.text_iter():
            try:
                content = self.bk.readfile(text_id)
                if isinstance(content, bytes):
                    content = content.decode('utf-8', errors='ignore')
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    url = match.group(1)
                    url_set.add(url)
            except Exception as e:
                print(f"Error scanning {self.bk.id_to_bookpath(text_id)}: {e}")

        self.all_urls = [(url,) for url in sorted(url_set)]
        for url, in self.all_urls:
            item = QListWidgetItem(url)
            item.setData(Qt.UserRole, url)
            self.url_list_widget.addItem(item)

        if not self.all_urls:
            self.status_label.setText("No external image URLs found.")
        else:
            self.status_label.setText(f"Found {len(self.all_urls)} external image URLs.")

    def on_selection_changed(self):
        selected = self.url_list_widget.selectedItems()
        if selected:
            url = selected[0].data(Qt.UserRole)
            self.selected_urls = {url}
        else:
            self.selected_urls = set()

    # ------------------------------------------------------------------
    # 处理流程
    # ------------------------------------------------------------------
    def start_processing(self):
        if self.single_mode_cb.isChecked():
            if not self.selected_urls:
                QMessageBox.warning(self, "Warning", "Please select a URL first.")
                return
            urls_to_process = [(url,) for url in self.selected_urls]
        else:
            if not self.all_urls:
                QMessageBox.warning(self, "Warning", "No URLs to process.")
                return
            urls_to_process = self.all_urls.copy()

        self.delay_sec = self.delay_spin.value()
        try:
            prefs = self.bk.getPrefs()
            prefs['delay'] = self.delay_sec
            self.bk.savePrefs(prefs)
        except:
            pass

        self.progress_dialog = QProgressDialog("Preparing download...", "Cancel", 0, len(urls_to_process), self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.canceled.connect(self.cancel_download)
        self.progress_dialog.setValue(0)

        self.url_to_bookpath = {}
        self.download_worker = DownloadWorker(urls_to_process, self.delay_sec)
        self.download_worker.finished_single.connect(self.on_image_downloaded)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.log.connect(self.print_log)
        self.download_worker.finished.connect(self.on_all_downloads_finished)
        self.download_worker.start()

        self.start_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Downloading images...")

    def cancel_download(self):
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.wait()
        if self.progress_dialog:
            self.progress_dialog.close()
        self.status_label.setText("Cancelled by user.")
        self.start_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def update_progress(self, current, total):
        if self.progress_dialog:
            self.progress_dialog.setValue(current)
            self.progress_dialog.setLabelText(f"Downloading {current+1} of {total}")

    def print_log(self, msg):
        print(msg)

    def on_image_downloaded(self, url, data, ext, success, error_msg):
        if not success:
            print(f"Download failed: {url} - {error_msg}")
            return

        url_hash = md5(url.encode('utf-8')).hexdigest()
        filename = f"{url_hash}.{ext}"
        img_folders = self.bk.group_to_folders('Images')
        img_dir = img_folders[0] if img_folders else 'Images'
        bookpath = f"{img_dir}/{filename}"

        existing_id = self.bk.bookpath_to_id(bookpath)
        if existing_id:
            self.url_to_bookpath[url] = bookpath
            print(f"Image already exists: {bookpath}")
            return

        manifest_id = f"img_{url_hash}"
        counter = 1
        while self.bk.bookpath_to_id(bookpath):
            manifest_id = f"img_{url_hash}_{counter}"
            counter += 1

        mime_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'webp': 'image/webp', 'svg': 'image/svg+xml'
        }
        mime = mime_map.get(ext, 'application/octet-stream')

        try:
            self.bk.addbookpath(manifest_id, bookpath, data, mime)
            self.url_to_bookpath[url] = bookpath
            self.changes_made = True
            print(f"Added image: {bookpath} (from {url})")
        except Exception as e:
            print(f"Failed to add {bookpath}: {e}")

    def on_all_downloads_finished(self):
        if self.progress_dialog:
            self.progress_dialog.close()

        if not self.url_to_bookpath:
            self.status_label.setText("No images were successfully downloaded.")
            self.start_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            return

        self.status_label.setText("Updating image references in XHTML files...")
        print("Starting to replace image src references...")

        replaced_count = 0
        for text_id, _ in self.bk.text_iter():
            try:
                content = self.bk.readfile(text_id)
                if isinstance(content, bytes):
                    content = content.decode('utf-8', errors='ignore')
                new_content, cnt = self.replace_src_in_html(content, text_id)
                if new_content is not None:
                    self.bk.writefile(text_id, new_content)
                    replaced_count += cnt
                    self.changes_made = True
            except Exception as e:
                print(f"Error updating {self.bk.id_to_bookpath(text_id)}: {e}")

        print("\n" + "=" * 50)
        print("OfflineImages Conversion Report")
        print(f"  Total URLs processed: {len(self.all_urls)}")
        print(f"  Successfully downloaded: {len(self.url_to_bookpath)}")
        print(f"  Image references replaced: {replaced_count}")
        print("=" * 50 + "\n")

        self.status_label.setText(f"Done! {len(self.url_to_bookpath)} images offline, {replaced_count} references updated.")
        self.start_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.refresh_url_list()   # 更新列表显示剩余外链

    def replace_src_in_html(self, content, text_id):
        """返回 (new_content, replaced_count)"""
        prefix = ""
        doctype = ""
        body = content

        if body.lstrip().startswith('<?xml'):
            match = re.match(r'\s*(<\?xml[^>]*\?>)', body)
            if match:
                prefix = match.group(1)
                body = body[match.end():]

        doctype_match = re.match(r'\s*(<!DOCTYPE[^>]*>)', body, re.IGNORECASE)
        if doctype_match:
            doctype = doctype_match.group(1)
            body = body[doctype_match.end():]

        try:
            parser = etree.XMLParser(recover=True, remove_blank_text=False)
            tree = etree.fromstring(body.encode('utf-8'), parser)
            is_xml = True
        except Exception:
            tree = lxml_html.fromstring(body)
            is_xml = False

        html_bookpath = self.bk.id_to_bookpath(text_id)
        changed = False
        replaced = 0

        # 处理 <img>
        for img in tree.xpath('//*[local-name()="img"]'):
            src = img.get('src')
            if not src:
                continue
            # 规范化 URL（去除查询参数？不，应该保留原样匹配）
            # 直接匹配原始 URL
            if src in self.url_to_bookpath:
                local_path = self.url_to_bookpath[src]
                rel_path = self.bk.get_relativepath(html_bookpath, local_path)
                if rel_path:
                    img.set('src', rel_path)
                    changed = True
                    replaced += 1
            # 兼容带查询参数的 URL（例如 ?width=100）
            else:
                base_src = src.split('?')[0]
                if base_src in self.url_to_bookpath:
                    local_path = self.url_to_bookpath[base_src]
                    rel_path = self.bk.get_relativepath(html_bookpath, local_path)
                    if rel_path:
                        img.set('src', rel_path)
                        changed = True
                        replaced += 1

        # 处理 SVG image 标签
        for image_elem in tree.xpath('//*[local-name()="image"]'):
            href = image_elem.get('{http://www.w3.org/1999/xlink}href') or image_elem.get('href')
            if href:
                if href in self.url_to_bookpath:
                    local_path = self.url_to_bookpath[href]
                    rel_path = self.bk.get_relativepath(html_bookpath, local_path)
                    if rel_path:
                        if '{http://www.w3.org/1999/xlink}href' in image_elem.attrib:
                            image_elem.set('{http://www.w3.org/1999/xlink}href', rel_path)
                        else:
                            image_elem.set('href', rel_path)
                        changed = True
                        replaced += 1

        if not changed:
            return None, 0

        if is_xml:
            body_str = etree.tostring(tree, encoding='unicode')
        else:
            body_str = lxml_html.tostring(tree, encoding='unicode')

        result = ""
        if prefix:
            result += prefix + "\n"
        if doctype:
            result += doctype + "\n"
        result += body_str
        return result, replaced

    # ------------------------------------------------------------------
    # 正确退出：接受更改
    # ------------------------------------------------------------------
    def accept_and_close(self):
        """用户点击 Exit & Save 时调用，标记接受更改"""
        if self.download_worker and self.download_worker.isRunning():
            reply = QMessageBox.question(self, "Download in Progress",
                                         "Download is still running. Cancel and exit?",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.cancel_download()
            else:
                return
        self.accept()   # 重要：使 run() 返回 0，Sigil 合并更改

    def closeEvent(self, event):
        """点击窗口 X 时的行为"""
        if self.download_worker and self.download_worker.isRunning():
            reply = QMessageBox.question(self, "Download in Progress",
                                         "Download is still running. Cancel and exit?",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.cancel_download()
                event.accept()
                self.accept()   # 仍然保存已做出的更改
            else:
                event.ignore()
        else:
            event.accept()
            self.accept()

# ----------------------------------------------------------------------
# 插件入口
# ----------------------------------------------------------------------
def run(bk):
    print("OfflineImages plugin started.")
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = OfflineImagesDialog(bk)
    result = dialog.exec()
    if result == QDialog.Accepted:
        print("Plugin finished successfully. Changes saved.")
        return 0
    else:
        print("Plugin cancelled or no changes made.")
        return -1

def main():
    pass

if __name__ == "__main__":
    main()
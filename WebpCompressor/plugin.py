#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, os, io, re, copy
from PIL import Image
from lxml import etree, html as lxml_html
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QSlider, QSpinBox, QPushButton, QProgressDialog,
    QSplitter, QWidget, QCheckBox
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage

class ImgToWebPDialog(QDialog):
    def __init__(self, bk, image_list, parent=None):
        super().__init__(parent)
        self.bk = bk
        self.image_list = image_list  # [(manifest_id, href, media_type, bookpath, size_orig)]
        self.current_preview_id = None
        self.quality = 80  # 默认质量
        self.preview_cache = {}  # 缓存不同质量的预览数据

        # 尝试读取保存的偏好设置
        try:
            prefs = bk.getPrefs()
            if 'quality' in prefs:
                self.quality = int(prefs['quality'])
        except:
            pass

        self.init_ui()
        self.load_image_list()

    def init_ui(self):
        self.setWindowTitle("ImgToWebP - Convert Images to WebP")
        self.resize(900, 600)

        main_layout = QHBoxLayout(self)

        # 左侧列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(QLabel("Images to convert:"))
        left_layout.addWidget(self.list_widget)

        # 右侧预览区
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        preview_splitter = QSplitter(Qt.Horizontal)
        self.original_preview = QLabel()
        self.original_preview.setAlignment(Qt.AlignCenter)
        self.original_preview.setMinimumSize(200, 200)
        self.original_preview.setStyleSheet("border: 1px solid gray;")

        self.converted_preview = QLabel()
        self.converted_preview.setAlignment(Qt.AlignCenter)
        self.converted_preview.setMinimumSize(200, 200)
        self.converted_preview.setStyleSheet("border: 1px solid gray;")

        preview_splitter.addWidget(self.original_preview)
        preview_splitter.addWidget(self.converted_preview)
        right_layout.addWidget(preview_splitter)

        # 信息标签
        self.info_label = QLabel()
        right_layout.addWidget(self.info_label)

        # 质量控制
        quality_layout = QHBoxLayout()
        quality_layout.addWidget(QLabel("Quality:"))
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(self.quality)
        self.quality_slider.valueChanged.connect(self.on_quality_changed)
        quality_layout.addWidget(self.quality_slider)

        self.quality_spinbox = QSpinBox()
        self.quality_spinbox.setRange(0, 100)
        self.quality_spinbox.setValue(self.quality)
        self.quality_spinbox.valueChanged.connect(self.on_spinbox_changed)
        quality_layout.addWidget(self.quality_spinbox)

        right_layout.addLayout(quality_layout)

        # 按钮
        button_layout = QHBoxLayout()
        self.convert_btn = QPushButton("Start Conversion")
        self.convert_btn.clicked.connect(self.on_convert)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.convert_btn)
        button_layout.addWidget(self.cancel_btn)
        right_layout.addLayout(button_layout)

        main_layout.addWidget(left_widget, 1)
        main_layout.addWidget(right_widget, 2)

    def load_image_list(self):
        """填充图片列表（带复选框）"""
        self.list_widget.clear()
        for mid, href, mime, bookpath, size in self.image_list:
            item = QListWidgetItem(f"{os.path.basename(bookpath)} ({mime}, {size} bytes)")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, mid)  # 存储 manifest_id
            self.list_widget.addItem(item)

    def on_selection_changed(self):
        """选中图片时刷新预览"""
        selected = self.list_widget.selectedItems()
        if not selected:
            self.current_preview_id = None
            self.original_preview.clear()
            self.converted_preview.clear()
            self.info_label.setText("")
            return

        item = selected[0]
        mid = item.data(Qt.UserRole)
        self.current_preview_id = mid
        self.update_preview(mid)

    def update_preview(self, manifest_id):
        """根据当前质量生成预览"""
        # 获取原始图片数据
        try:
            raw_data = self.bk.readfile(manifest_id)
        except:
            self.original_preview.setText("Can't read image")
            return

        # 显示原图
        orig_pixmap = self.create_pixmap(raw_data)
        if orig_pixmap:
            self.original_preview.setPixmap(orig_pixmap.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.original_preview.setText("Unsupported format")

        # 转换并显示压缩预览
        try:
            webp_data = self.convert_to_webp(raw_data, self.quality)
            conv_pixmap = self.create_pixmap(webp_data)
            if conv_pixmap:
                self.converted_preview.setPixmap(conv_pixmap.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                orig_size = len(raw_data)
                new_size = len(webp_data)
                orig_size_kb = orig_size / 1024.0
                new_size_kb = new_size / 1024.0
                reduction = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
                self.info_label.setText(
                    f"Original: {orig_size_kb:.2f} KB  →  WebP: {new_size_kb:.2f} KB  "
                    f"({reduction:.1f}% reduction)  Quality: {self.quality}"
                )
            else:
                self.converted_preview.setText("WebP display error")
        except Exception as e:
            self.converted_preview.setText(f"Error: {str(e)}")

    def create_pixmap(self, data):
        """从字节数据创建 QPixmap"""
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGBA")  # 转换为兼容格式
            qimage = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888)
            return QPixmap.fromImage(qimage)
        except:
            return None

    def convert_to_webp(self, raw_data, quality):
        """PIL 转换图片为 WebP，返回字节"""
        img = Image.open(io.BytesIO(raw_data))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='WEBP', quality=quality)
        return buf.getvalue()

    def on_quality_changed(self, value):
        self.quality = value
        self.quality_spinbox.blockSignals(True)
        self.quality_spinbox.setValue(value)
        self.quality_spinbox.blockSignals(False)
        if self.current_preview_id:
            self.update_preview(self.current_preview_id)

    def on_spinbox_changed(self, value):
        self.quality_slider.blockSignals(True)
        self.quality_slider.setValue(value)
        self.quality_slider.blockSignals(False)
        self.quality = value
        if self.current_preview_id:
            self.update_preview(self.current_preview_id)

    def on_convert(self):
        """用户确认转换"""
        # 保存质量偏好
        try:
            prefs = self.bk.getPrefs()
            prefs['quality'] = self.quality
            self.bk.savePrefs(prefs)
        except:
            pass

        # 收集勾选的图片
        checked = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                checked.append(item.data(Qt.UserRole))

        if not checked:
            return

        # 进度对话框
        progress = QProgressDialog("Converting images...", "Cancel", 0, len(checked), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        # 转换映射：旧 bookpath -> 新 bookpath, manifest_id
        img_map = {}
        for idx, manifest_id in enumerate(checked):
            if progress.wasCanceled():
                break
            progress.setValue(idx)
            progress.setLabelText(f"Converting {self.bk.id_to_bookpath(manifest_id)}")

            try:
                raw_data = self.bk.readfile(manifest_id)
                webp_data = self.convert_to_webp(raw_data, self.quality)

                old_bookpath = self.bk.id_to_bookpath(manifest_id)
                # 生成新 bookpath
                base, _ = os.path.splitext(old_bookpath)
                new_bookpath = base + ".webp"
                # 避免重名（简单加序号，一般不会冲突）
                counter = 1
                while self.bk.bookpath_to_id(new_bookpath):
                    new_bookpath = f"{base}_{counter}.webp"
                    counter += 1

                # 删除旧图，添加新图（用相同 manifest_id）
                self.bk.deletefile(manifest_id)
                self.bk.addbookpath(manifest_id, new_bookpath, webp_data, 'image/webp')
                img_map[old_bookpath] = new_bookpath
            except Exception as e:
                print(f"Error converting {manifest_id}: {e}")

        progress.setValue(len(checked))

        # 更新 HTML 引用
        if img_map:
            progress.setLabelText("Updating image references in HTML...")
            self.update_html_refs(img_map)
        progress.close()
        self.accept()

    def update_html_refs(self, img_map):
        """遍历所有文本文件，替换 img 标签的 src 属性"""
        for text_id, _ in self.bk.text_iter():
            try:
                content = self.bk.readfile(text_id)
                if not content:
                    continue
                modified_content = self.process_xhtml(content, text_id, img_map)
                if modified_content is not None:
                    self.bk.writefile(text_id, modified_content)
            except Exception as e:
                print(f"Error processing {text_id}: {e}")

    def process_xhtml(self, content, text_id, img_map):
        """修改 HTML 内容，替换图片路径，保留 XML 声明和 DOCTYPE"""
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        prefix = ""
        doctype = ""
        body = content

        # 提取 XML 声明
        if body.lstrip().startswith('<?xml'):
            match = re.match(r'\s*(<\?xml[^>]*\?>)', body)
            if match:
                prefix = match.group(1)
                body = body[match.end():]

        # 提取 DOCTYPE
        doctype_match = re.match(r'\s*(<!DOCTYPE[^>]*>)', body, re.IGNORECASE)
        if doctype_match:
            doctype = doctype_match.group(1)
            body = body[doctype_match.end():]

        # 解析 HTML/XHTML 片段
        try:
            parser = etree.XMLParser(recover=True, remove_blank_text=False)
            tree = etree.fromstring(body.encode('utf-8'), parser)
            is_xml = True
        except Exception:
            tree = lxml_html.fromstring(body)
            is_xml = False

        # 替换 img src
        changed = False
        html_bookpath = self.bk.id_to_bookpath(text_id)
        for img in tree.xpath('//*[local-name()="img"]'):
            src = img.get('src')
            if src:
                # 计算完整 bookpath
                starting_dir = self.bk.get_startingdir(html_bookpath)
                img_bookpath = self.bk.build_bookpath(src, starting_dir)
                if img_bookpath in img_map:
                    new_bookpath = img_map[img_bookpath]
                    new_src = self.bk.get_relativepath(html_bookpath, new_bookpath)
                    img.set('src', new_src)
                    changed = True

        if not changed:
            return None  # 未修改，返回 None 避免不必要的写入

        # 序列化
        if is_xml:
            body_str = etree.tostring(tree, encoding='unicode')
        else:
            body_str = lxml_html.tostring(tree, encoding='unicode')

        # 重新组合
        result = ""
        if prefix:
            result += prefix + "\n"
        if doctype:
            result += doctype + "\n"
        result += body_str
        return result

def run(bk):
    # 扫描所有图片，过滤掉已经是 WebP 的
    image_list = []
    for mid, href, mime in bk.image_iter():
        if mime == 'image/webp':
            continue
        bookpath = bk.id_to_bookpath(mid)
        try:
            data = bk.readfile(mid)
            size = len(data)
        except:
            size = 0
        image_list.append((mid, href, mime, bookpath, size))

    if not image_list:
        print("No non-WebP images found.")
        return 0

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = ImgToWebPDialog(bk, image_list)
    if dialog.exec() == QDialog.Accepted:
        print("Conversion completed successfully.")
        return 0
    else:
        print("Cancelled by user.")
        return -1

def main():
    # 仅用于独立测试，Sigil 不会调用 main
    pass

if __name__ == "__main__":
    main()
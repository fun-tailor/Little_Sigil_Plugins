#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, os, io, re, copy
from PIL import Image
from lxml import etree, html as lxml_html
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QSlider, QSpinBox, QPushButton, QProgressDialog,
    QSplitter, QWidget, QCheckBox, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QPixmap, QImage, QColor

# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def format_size_kb(size_bytes):
    """字节转 KB 字符串，保留两位小数"""
    return f"{size_bytes / 1024:.2f} KB"

def build_pixmap(data, max_width=400, max_height=400):
    """从图片数据创建缩放到预览大小的 QPixmap"""
    pix = QPixmap()
    if pix.loadFromData(data):
        pix = pix.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pix
    return None

def convert_to_webp(raw_data, quality):
    """PIL 转换图片为 WebP 字节"""
    img = Image.open(io.BytesIO(raw_data))
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGBA')
    else:
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=quality)
    return buf.getvalue()

# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class ImgToWebPDialog(QDialog):
    def __init__(self, bk, image_list, svg_count=0):
        super().__init__()
        self.bk = bk
        self.image_list = image_list   # [(mid, href, mime, bookpath, size), ...]
        self.svg_count = svg_count

        # 自定义质量字典 {manifest_id: quality}
        self.custom_qualities = {}

        # 全局默认质量
        self.global_quality = 80

        # 缓存：{(mid, quality): (webp_bytes, pixmap)} 和 {mid: original_pixmap}
        self.preview_cache = {}
        self.original_cache = {}

        # 定时器用于延迟刷新
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._do_update_preview)

        # 当前选中的图片 id
        self.current_manifest_id = None

        # 尝试读取保存的偏好设置（全局质量）
        try:
            prefs = bk.getPrefs()
            if 'quality' in prefs:
                self.global_quality = int(prefs['quality'])
        except:
            pass

        self.init_ui()
        self.load_image_list()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def init_ui(self):
        self.setWindowTitle("ImgToWebP - Convert Images to WebP")
        self.resize(980, 650)

        main_layout = QHBoxLayout(self)

        # ----- 左侧列表 -----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("<b>Images to convert:</b>"))
        self.info_label_svg = QLabel()
        if self.svg_count > 0:
            self.info_label_svg.setText(f"Found {self.svg_count} SVG image(s), ignored (not supported)")
        else:
            self.info_label_svg.setVisible(False)
        left_layout.addWidget(self.info_label_svg)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(self.list_widget)

        # ----- 右侧预览区 -----
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        preview_splitter = QSplitter(Qt.Horizontal)
        self.original_label = QLabel()
        self.original_label.setAlignment(Qt.AlignCenter)
        self.original_label.setMinimumSize(200, 200)
        self.original_label.setStyleSheet("border: 1px solid gray;")
        self.converted_label = QLabel()
        self.converted_label.setAlignment(Qt.AlignCenter)
        self.converted_label.setMinimumSize(200, 200)
        self.converted_label.setStyleSheet("border: 1px solid gray;")
        preview_splitter.addWidget(self.original_label)
        preview_splitter.addWidget(self.converted_label)
        right_layout.addWidget(preview_splitter)

        # 信息标签
        self.info_text = QLabel("Select an image to preview")
        right_layout.addWidget(self.info_text)

        # ----- 全局质量控制 -----
        quality_layout = QHBoxLayout()
        quality_layout.addWidget(QLabel("Quality:"))
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(self.global_quality)
        self.quality_slider.valueChanged.connect(self.on_quality_slider)
        quality_layout.addWidget(self.quality_slider)

        self.quality_spinbox = QSpinBox()
        self.quality_spinbox.setRange(0, 100)
        self.quality_spinbox.setValue(self.global_quality)
        self.quality_spinbox.valueChanged.connect(self.on_quality_spinbox)
        quality_layout.addWidget(self.quality_spinbox)
        right_layout.addLayout(quality_layout)

        # ----- 自定义质量控件 -----
        custom_layout = QHBoxLayout()
        self.custom_checkbox = QCheckBox("Custom quality for this image")
        self.custom_checkbox.toggled.connect(self.on_custom_checkbox)
        custom_layout.addWidget(self.custom_checkbox)

        self.custom_spinbox = QSpinBox()
        self.custom_spinbox.setRange(0, 100)
        self.custom_spinbox.setEnabled(False)
        self.custom_spinbox.setValue(self.global_quality)
        self.custom_spinbox.valueChanged.connect(self.on_custom_value_changed)
        custom_layout.addWidget(self.custom_spinbox)
        right_layout.addLayout(custom_layout)

        # ----- 操作按钮 -----
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
        """填充图片列表（带复选框），并用颜色标记有自定义质量的图片"""
        self.list_widget.clear()
        for mid, href, mime, bookpath, size in self.image_list:
            base = os.path.basename(bookpath)
            item = QListWidgetItem(f"{base}  ({mime}, {format_size_kb(size)})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, mid)

            # 颜色标记：如果此图片已有自定义质量（从缓存中恢复，初次加载时没有）
            # 这里在加载时还没有自定义设置，但可以保持默认颜色。
            # 之后会通过 update_item_color 动态刷新。
            # 为了统一，默认颜色为普通文本颜色，后面由 update_item_color 管理。
            self.list_widget.addItem(item)

    def update_item_color(self, manifest_id):
        """根据是否有自定义质量刷新列表项的文字颜色"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == manifest_id:
                if manifest_id in self.custom_qualities:
                    item.setForeground(QColor("#0077ff"))  # 蓝色
                else:
                    item.setForeground(QColor("#000000"))  # 默认黑色
                break

    # ------------------------------------------------------------------
    # 交互逻辑
    # ------------------------------------------------------------------
    def on_selection_changed(self):
        """选中图片时，用延迟定时器触发预览更新，避免连续快速切换卡顿"""
        self.refresh_timer.start(200)  # 200ms 延迟

    def _do_update_preview(self):
        """实际执行预览更新"""
        selected = self.list_widget.selectedItems()
        if not selected:
            self.current_manifest_id = None
            self.original_label.clear()
            self.converted_label.clear()
            self.info_text.setText("Select an image to preview")
            self.custom_checkbox.setChecked(False)
            self.custom_spinbox.setEnabled(False)
            return

        item = selected[0]
        mid = item.data(Qt.UserRole)
        self.current_manifest_id = mid

        # 更新自定义控件
        if mid in self.custom_qualities:
            self.custom_checkbox.blockSignals(True)
            self.custom_checkbox.setChecked(True)
            self.custom_checkbox.blockSignals(False)
            self.custom_spinbox.setEnabled(True)
            self.custom_spinbox.setValue(self.custom_qualities[mid])
        else:
            self.custom_checkbox.blockSignals(True)
            self.custom_checkbox.setChecked(False)
            self.custom_checkbox.blockSignals(False)
            self.custom_spinbox.setEnabled(False)
            self.custom_spinbox.setValue(self.global_quality)

        self.update_preview(mid)

    def update_preview(self, manifest_id):
        """生成原图和 WebP 预览，使用缓存"""
        # 获取原图 Pixmap 缓存
        orig_pix = self.original_cache.get(manifest_id)
        if orig_pix is None:
            try:
                raw_data = self.bk.readfile(manifest_id)
                orig_pix = build_pixmap(raw_data)
                self.original_cache[manifest_id] = orig_pix
            except Exception:
                orig_pix = None
        if orig_pix:
            self.original_label.setPixmap(orig_pix)
        else:
            self.original_label.setText("Unsupported format")

        # 决定当前使用的质量
        quality = self.custom_qualities.get(manifest_id, self.global_quality)

        # 从缓存获取 WebP 预览
        cache_key = (manifest_id, quality)
        if cache_key in self.preview_cache:
            webp_bytes, webp_pix = self.preview_cache[cache_key]
        else:
            try:
                raw_data = self.bk.readfile(manifest_id)
                webp_bytes = convert_to_webp(raw_data, quality)
                webp_pix = build_pixmap(webp_bytes)
                self.preview_cache[cache_key] = (webp_bytes, webp_pix)
            except Exception as e:
                webp_bytes = None
                webp_pix = None

        if webp_pix:
            self.converted_label.setPixmap(webp_pix)
            orig_size = len(self.bk.readfile(manifest_id))
            new_size = len(webp_bytes)
            reduction = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
            self.info_text.setText(
                f"Original: {format_size_kb(orig_size)}  →  WebP: {format_size_kb(new_size)}  "
                f"({reduction:.1f}% reduction)  Quality: {quality}"
            )
        else:
            self.converted_label.setText("Conversion error")

    def on_quality_slider(self, value):
        self.global_quality = value
        self.quality_spinbox.blockSignals(True)
        self.quality_spinbox.setValue(value)
        self.quality_spinbox.blockSignals(False)
        # 如果当前图片没有自定义质量，刷新预览
        if self.current_manifest_id and self.current_manifest_id not in self.custom_qualities:
            self.refresh_timer.start(200)

    def on_quality_spinbox(self, value):
        self.quality_slider.blockSignals(True)
        self.quality_slider.setValue(value)
        self.quality_slider.blockSignals(False)
        self.global_quality = value
        if self.current_manifest_id and self.current_manifest_id not in self.custom_qualities:
            self.refresh_timer.start(200)

    def on_custom_checkbox(self, checked):
        if not self.current_manifest_id:
            return
        if checked:
            # 启用自定义，复制当前显示的值（可能是全局或之前自定义）
            q = self.custom_spinbox.value()
            self.custom_qualities[self.current_manifest_id] = q
            self.custom_spinbox.setEnabled(True)
        else:
            # 取消自定义
            self.custom_qualities.pop(self.current_manifest_id, None)
            self.custom_spinbox.setEnabled(False)
            self.custom_spinbox.setValue(self.global_quality)
        self.update_item_color(self.current_manifest_id)
        self.refresh_timer.start(200)

    def on_custom_value_changed(self, value):
        if self.current_manifest_id and self.custom_checkbox.isChecked():
            self.custom_qualities[self.current_manifest_id] = value
            self.refresh_timer.start(200)

    # ------------------------------------------------------------------
    # 执行转换
    # ------------------------------------------------------------------
    def on_convert(self):
        """执行转换，并统计报告"""
        # 保存全局质量偏好
        try:
            prefs = self.bk.getPrefs()
            prefs['quality'] = self.global_quality
            self.bk.savePrefs(prefs)
        except:
            pass

        # 收集勾选的图片
        checked_mids = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                checked_mids.append(item.data(Qt.UserRole))

        if not checked_mids:
            return

        # 进度对话框
        progress = QProgressDialog("Converting images...", "Cancel", 0, len(checked_mids), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        # 转换映射：旧 bookpath -> 新 bookpath, manifest_id
        img_map = {}
        converted_count = 0
        errors = []

        for idx, manifest_id in enumerate(checked_mids):
            if progress.wasCanceled():
                break
            old_bookpath = self.bk.id_to_bookpath(manifest_id)
            progress.setLabelText(f"Converting {old_bookpath} ...")
            try:
                raw_data = self.bk.readfile(manifest_id)
                quality = self.custom_qualities.get(manifest_id, self.global_quality)
                webp_data = convert_to_webp(raw_data, quality)

                # 生成新 bookpath
                base, _ = os.path.splitext(old_bookpath)
                new_bookpath = base + ".webp"
                counter = 1
                while self.bk.bookpath_to_id(new_bookpath):
                    new_bookpath = f"{base}_{counter}.webp"
                    counter += 1

                # 删除旧图，添加新图（保持 manifest id）
                self.bk.deletefile(manifest_id)
                self.bk.addbookpath(manifest_id, new_bookpath, webp_data, 'image/webp')
                img_map[old_bookpath] = new_bookpath
                converted_count += 1
            except Exception as e:
                errors.append(f"  {old_bookpath}: {e}")
            progress.setValue(idx + 1)

        progress.setLabelText("Updating references in HTML and SVG files...")

        # 更新引用并获取统计
        total_updates, unreferenced = self.update_all_references(img_map)

        progress.close()

        # 输出报告
        print("\n" + "=" * 50)
        print("ImgToWebP Conversion Report")
        print(f"Images converted: {converted_count}")
        print(f"HTML/SVG references updated: {total_updates}")
        if unreferenced:
            print(f"Images converted but not referenced: {len(unreferenced)}")
            for ub in unreferenced:
                print(f"  {ub}")
        if errors:
            print(f"Errors ({len(errors)}):")
            for err in errors:
                print(err)
        print("=" * 50 + "\n")

        self.accept()
    
    def update_all_references(self, img_map):
        """遍历所有文本和 SVG 文件，更新图片引用，返回 (更新次数, 未被引用列表)"""
        referenced = set()
        total_updates = 0

        # 处理 XHTML 文件
        for text_id, _ in self.bk.text_iter():
            try:
                content = self.bk.readfile(text_id)
                if not content:
                    continue
                updated, cnt, refs = self.process_xhtml(content, text_id, img_map)
                if updated is not None:
                    self.bk.writefile(text_id, updated)
                total_updates += cnt        # cnt 是 int
                referenced.update(refs)     # refs 是 set
            except Exception as e:
                print(f"Error processing {self.bk.id_to_bookpath(text_id)}: {e}")

        # 处理独立的 SVG 文件
        for mid, _, mime in self.bk.manifest_iter():
            if mime == 'image/svg+xml':
                try:
                    content = self.bk.readfile(mid)
                    if not content:
                        continue
                    updated, cnt, refs = self.process_svg_file(content, mid, img_map)
                    if updated is not None:
                        self.bk.writefile(mid, updated)
                    total_updates += cnt
                    referenced.update(refs)
                except Exception as e:
                    print(f"Error processing SVG {self.bk.id_to_bookpath(mid)}: {e}")

        # 未被引用的图片
        unreferenced = [p for p in img_map if p not in referenced]

        return total_updates, unreferenced

    def process_xhtml(self, content, text_id, img_map):
        """处理 XHTML/HTML 文件，返回 (修改后内容, 更新的引用集合)"""
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

        # 解析
        try:
            parser = etree.XMLParser(recover=True, remove_blank_text=False)
            tree = etree.fromstring(body.encode('utf-8'), parser)
            is_xml = True
        except Exception:
            tree = lxml_html.fromstring(body)
            is_xml = False

        html_bookpath = self.bk.id_to_bookpath(text_id)
        updated_refs = set()
        changed = False

        # 替换 <img> 标签
        tag_count = 0
        for img in tree.xpath('//*[local-name()="img"]'):
            src = img.get('src')
            if src:
                img_bookpath = self.bk.build_bookpath(src, self.bk.get_startingdir(html_bookpath))
                if img_bookpath in img_map:
                    new_src = self.bk.get_relativepath(html_bookpath, img_map[img_bookpath])
                    img.set('src', new_src)
                    updated_refs.add(img_bookpath)
                    tag_count += 1
                    changed = True

        # 替换 SVG <image> 标签（嵌入在 HTML 中）
        for image_elem in tree.xpath('//*[local-name()="image"]'):
            # 尝试获取 xlink:href 或 href
            href = image_elem.get('{http://www.w3.org/1999/xlink}href') or image_elem.get('href')
            if href:
                img_bookpath = self.bk.build_bookpath(href, self.bk.get_startingdir(html_bookpath))
                if img_bookpath in img_map:
                    new_href = self.bk.get_relativepath(html_bookpath, img_map[img_bookpath])
                    # 设置正确的属性
                    if '{http://www.w3.org/1999/xlink}href' in image_elem.attrib:
                        image_elem.set('{http://www.w3.org/1999/xlink}href', new_href)
                    else:
                        image_elem.set('href', new_href)
                    updated_refs.add(img_bookpath)
                    tag_count += 1
                    changed = True

        if not changed:
            return None, 0, set()

        # 序列化
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
        return result, tag_count, updated_refs

    def process_svg_file(self, content, svg_id, img_map):
        """处理独立的 SVG 文件，返回 (修改后内容, 更新引用集合)"""
        if isinstance(content, bytes):
            content = content.decode('utf-8')

        prefix = ""
        body = content
        # 提取 XML 声明
        if body.lstrip().startswith('<?xml'):
            match = re.match(r'\s*(<\?xml[^>]*\?>)', body)
            if match:
                prefix = match.group(1)
                body = body[match.end():]

        try:
            root = etree.fromstring(body.encode('utf-8'))
        except Exception:
            return None, set()

        svg_bookpath = self.bk.id_to_bookpath(svg_id)
        ns = {'svg': 'http://www.w3.org/2000/svg', 'xlink': 'http://www.w3.org/1999/xlink'}
        updated_refs = set()
        changed = False

        tag_count = 0
        for image_elem in root.xpath('//svg:image', namespaces=ns):
            href = image_elem.get('{http://www.w3.org/1999/xlink}href')
            if href:
                img_bookpath = self.bk.build_bookpath(href, self.bk.get_startingdir(svg_bookpath))
                if img_bookpath in img_map:
                    new_href = self.bk.get_relativepath(svg_bookpath, img_map[img_bookpath])
                    image_elem.set('{http://www.w3.org/1999/xlink}href', new_href)
                    updated_refs.add(img_bookpath)
                    tag_count += 1
                    changed = True

        if not changed:
            return None,0, set()

        body_str = etree.tostring(root, encoding='unicode')
        result = ""
        if prefix:
            result += prefix + "\n"
        result += body_str
        return result, tag_count, updated_refs

# ----------------------------------------------------------------------
# 插件入口
# ----------------------------------------------------------------------
def run(bk):
    # 扫描图片，排除 SVG 和 WebP
    image_list = []
    svg_count = 0
    for mid, href, mime in bk.image_iter():
        if mime == 'image/webp':
            continue
        if mime == 'image/svg+xml':
            svg_count += 1
            continue
        bookpath = bk.id_to_bookpath(mid)
        try:
            data = bk.readfile(mid)
            size = len(data)
        except:
            size = 0
        image_list.append((mid, href, mime, bookpath, size))

    if not image_list:
        if svg_count > 0:
            print(f"No convertible images found. {svg_count} SVG image(s) were ignored.")
        else:
            print("No non-WebP images found.")
        return 0

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = ImgToWebPDialog(bk, image_list, svg_count)
    if dialog.exec() == QDialog.Accepted:
        return 0
    else:
        return -1

def main():
    pass

if __name__ == "__main__":
    main()
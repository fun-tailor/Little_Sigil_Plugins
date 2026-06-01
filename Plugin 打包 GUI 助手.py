#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Plugin 打包 GUI 助手
用于扫描仓库根目录下的插件文件夹，并一键打包为 zip。
"""

import os
import sys
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import messagebox, ttk

# ==================== 可编辑配置区 ====================
# 打包时需要跳过的目录名（通常用于资源目录、缓存等）
EXCLUDE_DIRS = {"assets", ".git", "__pycache__"}

# 特殊处理规则：键为插件文件夹名，值为处理函数
# 函数签名：处理函数(插件源目录, 临时工作目录) -> 返回打包源目录路径
# 临时工作目录由调用方创建并负责清理，规则函数只需在其中完成准备工作
SPECIAL_RULES = {}


def rule_t2s(src_dir, tmp_dir):
    """
    t2s 插件特殊处理：
    将 src_dir 内容复制到 tmp_dir，解压 opencc.zip 到 opencc/，然后删除 opencc.zip。
    """
    dest = os.path.join(tmp_dir, "t2s")
    # 复制整个插件目录到临时位置
    shutil.copytree(src_dir, dest)

    opencc_zip = os.path.join(dest, "opencc.zip")
    if os.path.isfile(opencc_zip):
        # 解压到 dest/ , opencc/ 压缩包自带
        extract_dir = dest
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(opencc_zip, "r") as zf:
            zf.extractall(extract_dir)
        # 删除原 zip 文件
        os.remove(opencc_zip)

    return dest  # 返回实际要打包的目录


SPECIAL_RULES["t2s"] = rule_t2s
# ==================== 配置区结束 ====================


class PluginPackagerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Plugin 打包助手")
        self.root.geometry("700x500")
        self.root.minsize(500, 400)

        # 顶部说明
        self.top_frame = ttk.Frame(self.root, padding=10)
        self.top_frame.pack(fill=tk.X)
        ttk.Label(
            self.top_frame,
            text="扫描当前目录下的插件文件夹，点击“打包”按钮生成对应 zip 文件。\n"
                 "需确保 plugin.xml 中的 <name> 与文件夹名一致。",
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        # 主区域：可滚动的插件列表
        self.canvas_frame = ttk.Frame(self.root)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(self.canvas_frame, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self.canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview
        )
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮支持
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # 底部控制栏
        self.bottom_frame = ttk.Frame(self.root, padding=5)
        self.bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.refresh_btn = ttk.Button(
            self.bottom_frame, text="刷新列表", command=self.refresh_plugins
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(
            self.bottom_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_label.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)

        # 存储插件卡片组件的引用，用于打包时禁用/启用
        self.plugin_widgets = []

        # 初次扫描
        self.refresh_plugins()

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def refresh_plugins(self):
        """重新扫描目录并刷新界面"""
        # 清空现有卡片
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.plugin_widgets.clear()
        self.status_var.set("正在扫描...")

        root_dir = os.path.dirname(os.path.abspath(__file__))
        plugins = self._scan_plugins(root_dir)

        if not plugins:
            ttk.Label(
                self.scrollable_frame,
                text="未检测到插件",
                font=("", 12),
            ).pack(pady=20)
            self.status_var.set("未检测到插件")
            return

        # 创建插件卡片
        for p in plugins:
            self._create_plugin_card(p)

        self.status_var.set(f"扫描完成，共 {len(plugins)} 个插件")

    def _scan_plugins(self, root_dir):
        """扫描根目录下的有效插件，返回插件信息列表"""
        plugins = []
        try:
            entries = os.listdir(root_dir)
        except OSError as e:
            messagebox.showerror("错误", f"无法读取目录：{e}")
            return []

        for entry in entries:
            folder_path = os.path.join(root_dir, entry)
            # 跳过非目录、排除目录、隐藏文件
            if not os.path.isdir(folder_path):
                continue
            if entry.startswith(".") or entry in EXCLUDE_DIRS:
                continue

            xml_path = os.path.join(folder_path, "plugin.xml")
            if not os.path.isfile(xml_path):
                continue

            # 解析 XML
            try:
                tree = ET.parse(xml_path)
                xml_root = tree.getroot()
            except Exception as e:
                # 插件损坏，跳过
                continue

            name_elem = xml_root.find("name")
            if name_elem is None or not (name_elem.text or "").strip():
                # 缺少 name，视为无效
                continue

            xml_name = name_elem.text.strip()
            description_elem = xml_root.find("description")
            description = (
                (description_elem.text or "").strip()
                if description_elem is not None
                else ""
            )
            version_elem = xml_root.find("version")
            version = (
                (version_elem.text or "").strip()
                if version_elem is not None
                else None
            )

            status = "正常" if xml_name == entry else f"名称不匹配：XML 中为 {xml_name}"
            valid = xml_name == entry

            plugins.append(
                {
                    "folder": entry,
                    "path": folder_path,
                    "xml_name": xml_name,
                    "description": description or "无描述",
                    "version": version,
                    "status": status,
                    "valid": valid,
                }
            )
        return plugins

    def _create_plugin_card(self, plugin):
        """为单个插件创建一行卡片"""
        card = ttk.Frame(self.scrollable_frame, relief=tk.RIDGE, padding=5)
        card.pack(fill=tk.X, pady=2, padx=5)

        # 文件夹名
        name_label = ttk.Label(card, text=plugin["folder"], font=("", 10, "bold"))
        name_label.grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        # 版本号（可选）
        ver_text = f"v{plugin['version']}" if plugin["version"] else "无版本"
        ver_label = ttk.Label(card, text=ver_text, foreground="gray")
        ver_label.grid(row=0, column=1, sticky=tk.W, padx=(0, 10))

        # 描述
        desc_label = ttk.Label(card, text=plugin["description"], wraplength=300)
        desc_label.grid(row=0, column=2, sticky=tk.W, padx=(0, 10))

        # 状态
        status_color = "green" if plugin["valid"] else "red"
        status_label = ttk.Label(
            card, text=plugin["status"], foreground=status_color
        )
        status_label.grid(row=0, column=3, sticky=tk.W, padx=(0, 10))

        # 打包按钮
        pack_btn = ttk.Button(
            card,
            text="打包",
            command=lambda p=plugin, b=None: self._on_pack(p),
        )
        pack_btn.grid(row=0, column=4, sticky=tk.E, padx=(10, 0))
        if not plugin["valid"]:
            pack_btn.configure(state=tk.DISABLED)

        # 让卡片列自适应宽度
        card.columnconfigure(2, weight=1)

        # 保存按钮引用以便统一禁用
        self.plugin_widgets.append(pack_btn)

    def _on_pack(self, plugin):
        """处理打包按钮点击"""
        # 禁用所有按钮，避免重复操作
        self._set_buttons_state(tk.DISABLED)
        self.status_var.set(f"正在打包 {plugin['folder']} ...")

        try:
            self._pack_plugin(plugin)
            zip_name = self._get_zip_name(plugin)
            messagebox.showinfo("成功", f"打包成功：{zip_name}")
            self.status_var.set(f"完成：{zip_name}")
        except Exception as e:
            messagebox.showerror("打包失败", f"打包 {plugin['folder']} 时出错：\n{e}")
            self.status_var.set("打包失败")
        finally:
            self._set_buttons_state(tk.NORMAL)

    def _get_zip_name(self, plugin):
        """根据插件信息生成输出 zip 文件名"""
        if plugin["version"]:
            return f"{plugin['folder']}_v{plugin['version']}.zip"
        else:
            return f"{plugin['folder']}.zip"

    def _pack_plugin(self, plugin):
        """执行具体的打包操作"""
        folder_name = plugin["folder"]
        src_dir = plugin["path"]
        output_dir = os.path.dirname(os.path.abspath(__file__))
        zip_name = self._get_zip_name(plugin)
        output_path = os.path.join(output_dir, zip_name)

        # 检查是否有特殊处理规则
        tmp_dir = None
        work_dir = src_dir  # 默认直接使用源目录

        if folder_name in SPECIAL_RULES:
            rule_fn = SPECIAL_RULES[folder_name]
            tmp_dir = tempfile.mkdtemp(prefix="plugin_pack_")
            try:
                work_dir = rule_fn(src_dir, tmp_dir)
            except Exception:
                # 清理临时目录
                if tmp_dir and os.path.isdir(tmp_dir):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                raise

        try:
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(work_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 计算相对于打包源目录的相对路径
                        rel = os.path.relpath(file_path, work_dir)
                        # 排除规则：仅当不是特殊处理时检查（特殊处理已在临时目录处理好）
                        # 但即使特殊处理也做一次检查，保证安全
                        if rel.split(os.sep)[0] in EXCLUDE_DIRS:
                            continue
                        # zip 内路径以插件文件夹名为根
                        arcname = os.path.join(folder_name, rel)
                        zf.write(file_path, arcname)
        finally:
            # 清理临时目录
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _set_buttons_state(self, state):
        """设置所有打包按钮的状态"""
        for btn in self.plugin_widgets:
            try:
                btn.configure(state=state)
            except tk.TclError:
                # 按钮可能已被销毁，忽略
                pass
        self.refresh_btn.configure(state=state)


def main():
    root = tk.Tk()
    app = PluginPackagerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()